# 03 — API reference

Every public function of the Luau module, with exact parameters, return values,
errors and examples.

- [Loading the module](#loading-the-module)
- [Server API](#server-api)
  - [`start(config)`](#startconfig)
  - [`stop()`](#stop)
  - [`emit(type, data)`](#emittype-data)
  - [`emitTo(player, type, data)`](#emittoplayer-type-data)
  - [`optIn(player)`](#optinplayer)
  - [`optOut(player)`](#optoutplayer)
  - [`isOptedIn(player)`](#isoptedinplayer)
  - [`getSession(player)`](#getsessionplayer)
  - [`metrics()`](#metrics)
  - [`audit()`](#audit)
  - [`setEnabled(value)`](#setenabledvalue)
- [Client API](#client-api)
  - [`emit(type, data)`](#client-emit)
  - [`onEvent(callback)`](#client-oneventcallback)
  - [`optIn()` / `optOut()`](#client-optin--optout)
  - [`getSession()`](#client-getsession)
- [Constants](#constants)
- [Error and reason strings](#error-and-reason-strings)
- [Metrics and audit shapes](#metrics-and-audit-shapes)

## Loading the module

The same require works on the server and on the client:

```lua
local ClientBridge = require(game.ReplicatedStorage.ClientBridge)
```

The facade picks the right side automatically:

| Environment | Exposed functions |
|---|---|
| `Script` in ServerScriptService (server) | `start`, `stop`, `emit`, `emitTo`, `optIn`, `optOut`, `isOptedIn`, `getSession`, `metrics`, `audit`, `setEnabled` |
| `LocalScript` (client) | `emit`, `onEvent`, `optIn`, `optOut`, `getSession` |

> Call `start` from the **server** only. On the client the facade exposes just
> the client functions (`emit`, `onEvent`, `optIn`, `optOut`, `getSession`);
> `start` is `nil` there.

## Server API

### `start(config)`

Boots the module. Call it once, from a server Script, usually at the top of
your main server file.

**Signature**

```lua
ClientBridge.start(config: table?) → (ok: boolean, error: string?)
```

**Config** — every key is optional; see [Configuration](04-configuration.md)
for the full reference. The important ones:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `events` | table? | `nil` | schema per event type |
| `endpoint` | string? | `nil` | HTTP endpoint (auto-enables HTTP) |
| `transports` | table? | `{console=true,http=false}` | choose channels |
| `secretName` | string? | `nil` | Creator Hub secret for the `Authorization` header |
| `signingKey` | string? | `nil` | HMAC key for batch signatures |
| `allowClientOrigin` | boolean | `true` | accept client-emitted events |
| `studioPolicyBypass` | boolean | `false` | Studio-only opt-in testing |

**Returns**

- `true` — started. HTTP may still be off; check `metrics().http.running`.
- `false, reason` — nothing started. Common reasons: an invalid schema
  (`"invalid spec: ..."`), `"events must be a table"`, `"missing Remote"`.

**What it does**

1. compiles and validates the schema;
2. merges the config with the defaults;
3. resolves `ReplicatedStorage.ClientBridge.Remote`;
4. configures the HTTP queue and, if `transports.http` is on with an endpoint,
   starts the flush loop (unless `HttpService.HttpEnabled` is false);
5. connects the `Remote` listener and the `PlayerRemoving` cleanup;
6. starts the 30-second session-cleanup heartbeat.

**HTTP failures are not fatal.** If the endpoint is invalid or HTTP requests
are disabled, `start` still returns `true`; console keeps working and the
reason lands in `audit()` (`http_disabled`). This is deliberate: a game must
never fail to boot because an external service is down.

**Calling twice**

A second `start` while already started returns `true` immediately and does not
apply the new config. `stop()` fully disconnects the listeners, so a later
`start` can be used again (for example in tests).

**Example**

```lua
local ClientBridge = require(game.ReplicatedStorage.ClientBridge)

local ok, err = ClientBridge.start({
	-- endpoint = "https://api.example.com/clientbridge",
	-- secretName = "CB_TOKEN",
	events = {
		biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
		roll = { scope = "player", data = { aura = "string:32", rarity = "number" } },
	},
})

if not ok then
	warn("ClientBridge: " .. tostring(err))
end
```

---

### `stop()`

Stops the bridge: disconnects listeners, cancels the heartbeat, flushes pending
HTTP events (waiting up to 2 seconds) and marks the module as stopped.

**Signature**

```lua
ClientBridge.stop() → ()
```

After `stop`, `emit`/`emitTo` return `false, "not_started"` and `optIn` returns
`nil, nil`. Existing session tokens stay in memory until expiry or until
`start` is called again; they are not revoked by `stop`.

**Example**

```lua
ClientBridge.stop()
```

---

### `emit(type, data)`

Emits a server event. The scope comes from the declared schema (default
`"server"`), the origin is always `"server"`.

**Signature**

```lua
ClientBridge.emit(type: string, data: table) → (ok: boolean, error: string?)
```

**Parameters**

| Name | Type | Description |
|---|---|---|
| `type` | string | event type, `[a-z0-9_.-]`, 1–32 chars |
| `data` | table | payload matching the declared schema |

**Returns**

- `true` — validated, counted and dispatched.
- `false, reason` — rejected. The reason is also recorded in the audit ring.

**Example**

```lua
local ok, reason = ClientBridge.emit("biome", {
	name = "Glitch",
	endsAt = os.time() + 300,
})

if not ok then
	warn("event rejected: " .. reason)
end
```

**Failure reasons** (see [the full list](#error-and-reason-strings)):
`not_started`, `disabled`, `invalid_table`, `undeclared field: X`,
`expected string`, `string too long`, `event_too_large`, `forbidden_field:...`,
`pii_email`, `text_too_long`, and the other schema/redaction messages.

**Performance note:** `emit` is cheap: one schema pass, one redaction pass and
one JSON encode. It performs no network request; HTTP events only enter a
queue, which a background task flushes every `http.flushSec` seconds.

---

### `emitTo(player, type, data)`

Emits a **player-scoped** event for one player. The scope is forced to
`"player"`, the origin is always `"server"`.

**Signature**

```lua
ClientBridge.emitTo(player: Player, type: string, data: table) → (ok: boolean, error: string?)
```

**Behaviour**

| Channel | What happens |
|---|---|
| Console | only that player's client receives and prints the line |
| HTTP | queued **only if** the player has an active session; the batch event carries their token |

If the player has not opted in, the console line still works; the HTTP copy is
silently skipped (nothing is stored anywhere).

**Example**

```lua
local function onAuraObtained(player: Player, auraName: string, rarity: number)
	ClientBridge.emitTo(player, "roll", { aura = auraName, rarity = rarity })
end
```

**Edge cases**

- `player` is not required to be in the game at call time by the type system,
  but the console delivery is a no-op for a player who left; the HTTP delivery
  only happens if the session still exists.
- The schema must declare a `player` scope for type checking to match your
  intent. If it declares `server`, `emitTo` still sends it as `player` — this is
  the safety rule described in [Concepts](02-concepts.md#scope).

---

### `optIn(player)`

Creates (or replaces) the player's HTTP session token.

**Signature**

```lua
ClientBridge.optIn(player: Player) → (token: string?, expiresAt: number?)
```

**Returns**

| Result | Meaning |
|---|---|
| `token, expiresAt` | session created; `expiresAt` is epoch seconds |
| `nil, nil` | not started **or** the player is not allowed to use external tools |

The token is also pushed to that player's client, where
`ClientBridge.getSession()` returns it. The call is idempotent enough to be
used by a UI button; calling it again revokes the previous token.

**Policy gate**

`PolicyService:GetPolicyInfoForPlayerAsync` must report at least one allowed
external link reference. This is Roblox's own age/eligibility gate for
external links — the module does not invent its own age check.

- Published experiences: players whose account may not see external links are
  refused; show them a friendly message.
- Studio: test players report zero allowed links, so opt-in is refused. For
  local testing only, start with `studioPolicyBypass = true`
  ([Configuration](04-configuration.md#studiopolicybypass)). The bypass is
  ignored outside Studio.
- Blocked attempts appear in `audit()` as `optin_blocked` (reason `policy`).

**Example (server side)**

```lua
local token, expiresAt = ClientBridge.optIn(player)
if token then
	print("token valid until", os.date("%H:%M:%S", expiresAt))
end
```

**Example (player-facing UI)**

```lua
-- LocalScript
local ClientBridge = require(game.ReplicatedStorage.ClientBridge)

local function requestToken()
	ClientBridge.optIn()
	for _ = 1, 30 do
		task.wait(0.1)
		local token = ClientBridge.getSession()
		if token then
			showTokenToPlayer(token) -- your UI / copyable TextBox
			return
		end
	end
	warn("opt-in was refused or timed out")
end
```

> Why polling? The token arrives asynchronously through a RemoteEvent. The
> loop above is the simplest reliable pattern; your UI can call it from a
> single button.

---

### `optOut(player)`

Revokes the player's token and tells their client to forget it.

**Signature**

```lua
ClientBridge.optOut(player: Player) → ()
```

After this, `emitTo` still produces console lines for that player, but no HTTP
events are queued for them. A subsequent `optIn` creates a new token.

**Example**

```lua
ClientBridge.optOut(player)
```

---

### `isOptedIn(player)`

**Signature**

```lua
ClientBridge.isOptedIn(player: Player) → boolean
```

Returns `true` only when the player has an unexpired active token. Expired
tokens are removed lazily and also by the 30-second cleanup heartbeat.

---

### `getSession(player)`

Reads the token without creating one.

**Signature**

```lua
ClientBridge.getSession(player: Player) → (token: string?, expiresAt: number?)
```

Server-side `getSession` never creates a session; use `optIn` for that. The
token is the same one the client receives, returned for server-side UI (for
example a custom “copy token” panel).

---

### `metrics()`

**Signature**

```lua
ClientBridge.metrics() → table
```

Returns a snapshot of counters. The exact shape:

```lua
{
	version = 1,
	started = true,
	enabled = true,
	stats = {
		emitted = 0,  -- events that passed validation
		blocked = 0,  -- events rejected by schema/redaction/size
		console = 0,  -- console deliveries attempted
		http = 0,     -- events queued for the HTTP channel
	},
	http = {
		running = false,
		configured = false,
		queue = 0,
		sent = 0,
		failed = 0,
		dropped = 0,
		lastError = nil,      -- string?
		usedThisMinute = 0,
	},
	sessions = 0,             -- active tokens
	audits = 0,               -- total audit entries recorded (not just retained)
	lastError = nil,          -- last start-time error, string?
}
```

`stats` and HTTP counters are cumulative for the lifetime of the server
instance; they are not reset by `stop()` or a later `start()`.

**Example**

```lua
local m = ClientBridge.metrics()
print(("emitted=%d blocked=%d http_queue=%d sessions=%d"):format(
	m.stats.emitted, m.stats.blocked, m.http.queue, m.sessions
))
```

---

### `audit()`

**Signature**

```lua
ClientBridge.audit() → { AuditEntry }
```

Returns up to 100 recent entries, **newest first**, from a 200-entry ring
buffer. Entries contain metadata only — never event field values.

```lua
{
	at = 1762000000,          -- epoch seconds
	kind = "blocked",         -- what happened
	-- plus small metadata, e.g.:
	type = "biome",
	reason = "pii_email at $.name",
}
```

Kinds used by the module:

| Kind | When |
|---|---|
| `start` | successful start (`http = true/false`) |
| `start_fail` | schema compile failure (`error`) |
| `stop` | stop called |
| `blocked` | event rejected (`type`, `reason`) |
| `http_disabled` | HTTP requested but unavailable (`reason`) |
| `http_drop` | batch discarded after retries (`count`, `error`) |
| `optin` | session created |
| `optin_blocked` | policy refused the opt-in |
| `optin_policy_bypass` | Studio bypass used (Studio only) |
| `optout` | session revoked |

`Audit.clear()` exists internally for the test suite; it is not exposed on the
facade.

---

### `setEnabled(value)`

Global kill switch for **emitting**.

**Signature**

```lua
ClientBridge.setEnabled(value: boolean) → ()
```

- `false` — `emit`/`emitTo` return `false, "disabled"`; nothing goes to the
  console or the HTTP queue; the HTTP flush loop pauses (queued events stay
  queued).
- `true` — emitting resumes and queued HTTP events flush on the next tick.

This does **not** revoke sessions, disconnect players, or delete stored data.

**Example**

```lua
-- e.g. from an admin command while investigating an incident
ClientBridge.setEnabled(false)
-- later
ClientBridge.setEnabled(true)
```

---

## Client API

The client-side facade is available in a `LocalScript` (usually through the
bootstrap at `src/client-bootstrap.client.luau`). Its job is to keep the console
listener alive and to be a thin wrapper over the RemoteEvent.

### Client `emit`

Emits an event **as this client**. The server receives it as:

- `scope = "player"` (always),
- `origin = "client"` (always),
- attributed to that player.

**Signature**

```lua
ClientBridge.emit(type: string, data: table) → ()
```

No return value: the RemoteEvent is fire-and-forget. The server validates like
any other event and drops it silently if the schema rejects it (the rejection
is visible in `audit()` as a `blocked` entry and in `metrics().stats.blocked`).

Client events are only accepted when `allowClientOrigin = true` (default).

**Example**

```lua
-- LocalScript
local ClientBridge = require(game.ReplicatedStorage.ClientBridge)

ClientBridge.emit("ping", { screen = "inventory" })
```

> Treat client events as hints. A modified client can emit anything the schema
> allows. Never drive server-side logic from them.

---

### Client `onEvent(callback)`

Subscribes to envelopes received from the server (both server-scoped
broadcasts and this player's player-scoped events).

**Signature**

```lua
ClientBridge.onEvent(callback: (envelope: table) -> ()) → (disconnect: () -> ())
```

The callback receives the **decoded envelope table**, not the raw line. The
line printing is handled internally, so your UI does not need to parse JSON.

**Example**

```lua
local disconnect = ClientBridge.onEvent(function(envelope)
	if envelope.type == "biome" then
		print("new biome:", envelope.data.name)
		updateBiomeLabel(envelope.data.name, envelope.data.endsAt)
	end
end)

-- later
disconnect()
```

---

### Client `optIn` / `optOut`

Ask the server to create/revoke this player's token.

**Signature**

```lua
ClientBridge.optIn()  → ()
ClientBridge.optOut() → ()
```

Fire-and-forget. The result arrives asynchronously:

- success: `ClientBridge.getSession()` starts returning a token;
- refusal: it keeps returning `nil`.

If your UI needs a definite answer, poll `getSession()` for a short period as
shown in [`optIn(player)`](#optinplayer).

---

### Client `getSession`

**Signature**

```lua
ClientBridge.getSession() → (token: string?, expiresAt: number?)
```

Returns the token the server pushed to this client, or `nil, nil` when there is
none. This is a local cache; it does not contact the server. The value is
cleared when the server sends an opt-out (`token = nil`).

**Example**

```lua
local token = ClientBridge.getSession()
if token then
	-- show a copyable TextBox with this token
end
```

---

## Constants

| Constant | Value | Meaning |
|---|---|---|
| `ClientBridge.VERSION` | `"1.1.0"` | module version |
| `ClientBridge.PROTOCOL` | `1` | wire protocol version |

`VERSION` changes with module releases; `PROTOCOL` changes only when the wire
format breaks. See [Versioning](17-versioning.md).

---

## Error and reason strings

All failures are plain strings returned as the second value (or recorded in
the audit ring). Match on the prefix, not on the whole sentence.

### Lifecycle

| String | Cause |
|---|---|
| `not_started` | `emit` before `start`, or after `stop` |
| `disabled` | `setEnabled(false)` is active |
| `invalid_table` | `data` argument is not a table |
| `missing Remote` | `ReplicatedStorage.ClientBridge.Remote` does not exist |
| `not supported` / module load errors | missing folders or wrong names |

### Schema

| String | Cause |
|---|---|
| `invalid type` | type name fails `^[a-z0-9_.-]{1,32}$` |
| `data must be a table` | payload is not a table |
| `keys must be strings` | non-string key in `data` |
| `undeclared field: X` | declared schema does not contain `X` |
| `expected string` | value is not a string |
| `string too long` | string exceeds the `string:N` limit |
| `expected finite number` | NaN/infinite number |
| `expected integer` | non-whole number for `"integer"` |
| `expected boolean` | wrong type for `"boolean"` |
| `value not in enum` | value not listed in an enum map |
| `too many keys` | more than 24 keys on an undeclared type |
| `unsupported type in X` | nested table/function on an undeclared type |
| `invalid number in X` | NaN/inf on an undeclared type |
| `event_too_large` | serialized envelope exceeds `maxEventBytes` (4096) |

`start` schema errors look like:

| String | Cause |
|---|---|
| `events must be a table` | `events` is not a table |
| `invalid type: X` | bad event type name in the schema |
| `spec for X must be a table` | schema entry is not a table |
| `invalid scope on X` | scope is not `"server"`/`"player"` |
| `invalid field on X` | non-string field name |
| `invalid spec: X (type.field)` | unsupported field spec (e.g. `"string<24>"`) |

### Redaction

| String | Cause |
|---|---|
| `forbidden_field:key at $.key` | key is on the forbidden list (e.g. `username`) |
| `text_too_long at $.path` | string longer than 64 characters |
| `pii_email at $.path` | value looks like an e-mail |
| `pii_url at $.path` | value contains a URL |
| `pii_ipv4`, `pii_cpf`, `pii_phone`, `pii_phone_intl`, `pii_discord_tag` | matching patterns |
| `non_string_key at $.path` | non-string key inside nested data |
| `invalid_number at $.path` | NaN/inf inside nested data |
| `unsupported_type:type at $.path` | function/userdata inside nested data |

See [Security & privacy](09-security-privacy.md#what-is-removed-redaction) for the exact
patterns.

---

## Metrics and audit shapes

### `metrics()` full example

```lua
{
	version = 1,
	started = true,
	enabled = true,
	stats = { emitted = 412, blocked = 3, console = 412, http = 380 },
	http = {
		running = true,
		configured = true,
		queue = 12,
		sent = 368,
		failed = 0,
		dropped = 0,
		lastError = nil,
		usedThisMinute = 41,
	},
	sessions = 7,
	audits = 55,
	lastError = nil,
}
```

Fields worth alerting on:

| Field | Healthy value | What it means when it moves |
|---|---|---|
| `http.failed` | stays flat | endpoint rejecting or network failing |
| `http.dropped` | stays flat | events lost after `maxRetries` |
| `http.queue` | small, oscillating | growing queue = endpoint slow or budget hit |
| `stats.blocked` | low | a game bug or a player sending junk |
| `http.lastError` | `nil` | last HTTP error message (`429`, `request_error`, ...) |

### `audit()` full example

```lua
{
	{ at = 1762000301, kind = "optin" },
	{ at = 1762000299, kind = "blocked", type = "biome", reason = "pii_email at $.name" },
	{ at = 1762000290, kind = "start", http = true },
}
```

Audit is a debugging aid, not a persistence layer. Export metrics to your own
observability stack if you need history.

---

Next: [Configuration](04-configuration.md) for every option, or
[Event schema](05-event-schema.md) to write good schemas.

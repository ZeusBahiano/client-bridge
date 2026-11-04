# 04 — Configuration

Every option accepted by `ClientBridge.start{...}`, what it does, its default,
and how the options interact.

- [How config is merged](#how-config-is-merged)
- [Top-level options](#top-level-options)
- [Transport options](#transport-options)
- [HTTP options](#http-options)
- [Session options](#session-options)
- [Recipes](#recipes)
- [Tuning guide](#tuning-guide)

## How config is merged

```lua
ClientBridge.start({
	endpoint = "https://api.example.com/clientbridge",
	transports = { console = true, http = true },
	http = { flushSec = 2 },
})
```

Rules:

1. The module starts from the defaults.
2. Your top-level keys override them.
3. `transports`, `http` and `session` are merged **one level deep** (`http = {
   flushSec = 2 }` keeps the other `http` defaults).
4. If `endpoint` is set and you did **not** explicitly set
   `transports.http`, HTTP is enabled automatically. Setting
   `transports.http = false` wins over the automatic behaviour.
5. Unknown keys are ignored (they are copied but never read). Typos are silent,
   so double-check names against the table below.
6. Config is read **once**, at `start`. Changing it later requires `stop()`
   then `start()`; prefer configuring before players join.

Defaults live in [`src/ClientBridge/Config.luau`](../src/ClientBridge/Config.luau).

## Top-level options

| Key | Type | Default | Description |
|---|---|---|---|
| `events` | table? | `nil` | schema per event type — see [Event schema](05-event-schema.md) |
| `endpoint` | string? | `nil` | absolute URL the server POSTs batches to |
| `secretName` | string? | `nil` | name of a Creator Hub **secret** whose value is sent as the `Authorization` header |
| `signingKey` | string? | `nil` | HMAC-SHA256 key used to sign every batch |
| `allowClientOrigin` | boolean | `true` | accept events emitted by `LocalScript`s |
| `studioPolicyBypass` | boolean | `false` | allow opt-in for Studio test players (Studio only) |
| `maxEventBytes` | integer | `4096` | maximum serialized size of one envelope |

### `endpoint`

```lua
endpoint = "https://api.example.com/clientbridge"
```

- Must start with `https://` in a published game.
- In Studio, `http://127.0.0.1[:port][/path]` and
  `http://localhost[:port][/path]` are also accepted for local testing.
- The module does **not** append a path; use the exact URL your API expects.
- Anything else fails validation: HTTP stays off, `audit()` records
  `http_disabled` with `reason = "endpoint must be https (http only for
  localhost/Studio)"`, and `start` still returns `true` (console unaffected).

### `secretName`

```lua
secretName = "CB_TOKEN"
```

The literal name of a secret created in **Creator Hub → your experience →
Secrets** (formerly “API keys”), bound to the place. At send time the module
calls `HttpService:GetSecret("CB_TOKEN")` and puts the returned value **verbatim**
into the `Authorization` header.

So the secret value should be the whole header value:

```
Bearer 7f3c9a1e2b...      ← store this in Creator Hub
```

Some backends expect only the token (`7f3c9a1e2b...`); both work with the
[reference API](08-python-api.md), which accepts either form. Check your
backend’s docs.

If the secret does not exist, `GetSecret` fails inside a `pcall` and the
request is sent **without** an `Authorization` header. The endpoint will
usually answer 401; the failure shows up in `http.failed` and
`http.lastError`.

> Secrets are read on **every** flush, so rotating the secret in Creator Hub
> takes effect without restarting the server.

### `signingKey`

```lua
signingKey = "a long random string that only your backend knows"
```

When set, every batch is signed:

```
sig = base64url(HMAC-SHA256(signingKey, canonical_json({v, universeId, placeId, sentAt, events})))
```

The exact algorithm is in [Protocol v1](06-protocol-v1.md#signature). The key
never leaves the server Script and is never sent over the network. Keep it
long, random and different from the `Authorization` token. The module sends the
signature in the body (`sig`), so the backend can verify it even if the
`Authorization` header is absent.

### `allowClientOrigin`

| Value | Behaviour |
|---|---|
| `true` (default) | `ClientBridge.emit` from a LocalScript is accepted (as `origin = "client"`, `scope = "player"`) |
| `false` | client-emitted events are ignored completely |

Turning it off is a good idea for competitive games: client-reported facts can
be forged. Server-emitted events are unaffected.

### `studioPolicyBypass`

| Value | Behaviour |
|---|---|
| `false` (default) | `optIn` obeys `PolicyService`; Studio test players have no external links, so opt-in is refused |
| `true` | **In Studio only**, skip the policy gate so you can test the HTTP flow locally |

The bypass is compiled in as `RunService:IsStudio()`, so setting it in a
published game has no effect. It records an `optin_policy_bypass` audit entry
every time it is used. Never ship a place with it on; it exists for local
development.

### `maxEventBytes`

Maximum size of one serialized envelope, in bytes. The module encodes the
envelope and rejects it with `event_too_large` above this value. The protocol
default is 4096 (4 KiB). Lower it if your backend has tight limits; raising it
is rarely useful because payload strings are capped at 64 characters anyway.

## Transport options

```lua
transports = {
	console = true,   -- default true
	http = false,     -- default false, auto-true when endpoint is set
}
```

| Combination | Result |
|---|---|
| `{console=true, http=false}` | console only (zero setup) |
| `{console=true, http=true}` + `endpoint` | both channels |
| `{console=false, http=true}` + `endpoint` | HTTP only (no client log lines) |
| `{console=false, http=false}` | nothing is delivered (useful to disable while keeping validation) |
| `endpoint` set, `transports` omitted | HTTP is enabled automatically, console stays on |

Disabling console also disables the client-visible lines and the
`onEvent` callbacks on the client (the client receives nothing). It does not
disable validation or the HTTP channel.

## HTTP options

```lua
http = {
	batchSize = 100,       -- events per POST
	flushSec = 5,          -- seconds between flushes
	budgetPerMinute = 400, -- HTTP requests per minute, per server instance
	maxRetries = 4,        -- attempts per batch before giving up
}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `batchSize` | integer ≥ 1 | `100` | maximum events packed into one POST |
| `flushSec` | number > 0 | `5` | how often the background task flushes the queue |
| `budgetPerMinute` | integer ≥ 1 | `400` | hard cap of outbound HTTP requests per minute |
| `maxRetries` | integer ≥ 0 | `4` | how many times a failed batch is retried |

### How a flush works

1. Every `flushSec`, the task takes up to `batchSize` events from the queue.
2. If the request budget for the current minute is exhausted, it stops until
   the next minute (`http.lastError = "budget_exceeded"`).
3. It builds the batch, adds `Authorization` and/or `sig`, and POSTs it.
4. `2xx` → `http.sent += n`.
   Non-2xx or network error → `http.failed += n`; the batch goes back to the
   front of the queue if any event still has retries left.
5. After 3 consecutive failed requests, a 30-second circuit breaker opens: no
   requests are attempted until it closes. This protects both sides during an
   outage.
6. Events dropped after `maxRetries` increment `http.dropped` and produce an
   `http_drop` audit entry.

### Backpressure

The queue holds at most 10,000 events. When full, the oldest event is dropped
(`dropped++`) and the new one is appended. A growing queue is the signal that
your endpoint is too slow or the budget is too low.

## Session options

```lua
session = {
	ttlSec = 86400, -- 24 hours
}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ttlSec` | integer ≥ 1 | `86400` | how long an opt-in token stays valid |

Short TTLs are more privacy-friendly (a leaked token dies sooner); long TTLs
are friendlier to tools that poll a player’s events across a session. Tokens
are also revoked on opt-out and when the player leaves the game.

## Recipes

### 1. Console only (safest, zero infra)

```lua
ClientBridge.start({
	events = {
		biome = { scope = "server", data = { name = "string:24" } },
	},
})
```

### 2. HTTP with auth, console too

```lua
ClientBridge.start({
	endpoint = "https://api.example.com/clientbridge",
	secretName = "CB_TOKEN",
	events = {
		biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
		roll = { scope = "player", data = { aura = "string:32", rarity = "number" } },
	},
})
```

### 3. HTTP signed, low latency, no console noise

```lua
ClientBridge.start({
	endpoint = "https://api.example.com/clientbridge",
	signingKey = "<64 random bytes, server-side only>",
	transports = { console = false, http = true },
	http = { batchSize = 50, flushSec = 2, budgetPerMinute = 600 },
	events = {
		biome = { scope = "server", data = { name = "string:24" } },
	},
})
```

### 4. Strict privacy / lower limits

```lua
ClientBridge.start({
	endpoint = "https://api.example.com/clientbridge",
	secretName = "CB_TOKEN",
	allowClientOrigin = false,
	session = { ttlSec = 3600 },
	maxEventBytes = 2048,
	events = {
		roll = { scope = "player", data = { aura = "string:24" } },
	},
})
```

### 5. Local Studio testing of the HTTP flow

```lua
ClientBridge.start({
	endpoint = "http://127.0.0.1:8787/clientbridge",
	transports = { console = true, http = true },
	studioPolicyBypass = true, -- Studio only
	http = { flushSec = 1 },
	events = {
		biome = { scope = "server", data = { name = "string:24" } },
		roll = { scope = "player", data = { aura = "string:32" } },
	},
})
```

Run `api/start.bat` (or `python api/clientbridge_api.py`) first. See
[HTTP mode](07-http-mode.md).

## Tuning guide

| Goal | Change | Trade-off |
|---|---|---|
| Lower latency | smaller `flushSec` (1–2) and `batchSize` (10–50) | more HTTP requests, more CPU |
| Fewer requests | larger `batchSize` (100–250) and `flushSec` (10–30) | events arrive later |
| Stay under a provider limit | `budgetPerMinute` ≤ the provider’s limit | events buffer/drop during bursts |
| Survive an outage | keep `maxRetries` ≥ 3; monitor `http.queue` | memory grows while the endpoint is down |
| Privacy first | shorter `session.ttlSec`, `allowClientOrigin = false` | players must re-opt-in more often |
| Test without infra | console only, or Studio + localhost | no cross-machine tooling |

### Relationship with the backend limits

The module sends **at most** `budgetPerMinute` requests/min (default 400) and
each carries up to `batchSize` events. The reference Python API defaults to
6,000 write requests/min and 3,000 read requests/min, so one game is far below
its limits; several games fit comfortably in one instance. See
[Reference API — performance](08-python-api.md#performance-and-scaling).

### What happens when things go wrong

| Situation | Module behaviour |
|---|---|
| `HttpService.HttpEnabled` is false | HTTP does not start; `audit()` records `http_disabled`; console keeps working |
| Invalid endpoint | same as above |
| Endpoint returns `401`/`403` | batch retried, then dropped; check `secretName`/`signingKey` |
| Endpoint returns `429` | batch retried with the same backoff; API also sends `Retry-After` |
| Endpoint returns `5xx` | retried; circuit breaker after 3 consecutive failures |
| Endpoint is down | queue grows; oldest events drop at 10,000 |
| Server is at the request budget | flush pauses until the next minute |

Next: [Event schema](05-event-schema.md).

# 07 — HTTP mode (and real-time delivery)

The console channel is local. HTTP mode sends events to **your** server, so
tools can run anywhere, and the reference API can push them to connected tools
over WebSocket in real time.

This page is the operating guide. The wire format is in
[Protocol v1](06-protocol-v1.md); the backend is in
[Reference API](08-python-api.md); the socket contract is in
[Live streaming](18-streaming.md).

- [How the pieces fit](#how-the-pieces-fit)
- [Step 1 — enable HTTP requests](#step-1--enable-http-requests)
- [Step 2 — pick an endpoint](#step-2--pick-an-endpoint)
- [Step 3 — start the module](#step-3--start-the-module)
- [Step 4 — authenticate (optional)](#step-4--authenticate-optional)
- [Step 5 — the player opt-in](#step-5--the-player-opt-in)
- [Step 6 — run the backend locally](#step-6--run-the-backend-locally)
- [Step 7 — verify end to end](#step-7--verify-end-to-end)
- [Real-time delivery](#real-time-delivery)
- [Signing requests](#signing-requests)
- [Production checklist](#production-checklist)
- [Common pitfalls](#common-pitfalls)

## How the pieces fit

```
┌──────────────── Roblox server ────────────────┐
│ ClientBridge.emit / emitTo                    │
│   → queue (batch up to 100)                   │
│   → POST https://your-api/clientbridge        │
└───────────────────────────────────────────────┘
                     │ HTTP (Roblox cannot use WebSocket)
                     ▼
┌──────────────── your backend ─────────────────┐
│ validate → keep recent events in memory       │
│   ├── WebSocket /stream  → tools (live)       │
│   └── REST /sessions/... → tools (cursor)     │
└───────────────────────────────────────────────┘
```

Two independent switches decide what leaves the game:

1. **transport** — `transports.http = true` (or an `endpoint`, which enables it
   automatically);
2. **per-player consent** — player-scoped events are only queued for players
   who opted in. Server-scoped events do not require opt-in.

## Step 1 — enable HTTP requests

Roblox blocks outbound HTTP by default.

- **Studio:** Home → Game Settings → Security → **Allow HTTP Requests** → On.
- **Published experience:** Creator Hub → your experience → Settings → Security
  → Allow HTTP Requests. Changes apply to running servers on the next start.

Without this, `HttpService.HttpEnabled` is `false`, the module records
`http_disabled` in `audit()`, and only the console channel works.

## Step 2 — pick an endpoint

| Environment | Endpoint |
|---|---|
| Studio, local backend | `http://127.0.0.1:8787/clientbridge` |
| Studio, remote backend | `https://…` works too |
| Published game | **must** start with `https://` |

Rules enforced by the module:

- `https://` is always accepted;
- in Studio only, `http://127.0.0.1...` and `http://localhost...` are accepted
  for local testing (the host must be exact, not a lookalike);
- anything else disables HTTP, records `http_disabled`, and keeps the game
  running with console only.

Use the exact path your backend serves. The reference API listens on
`/clientbridge` (and `/v1/clientbridge`).

## Step 3 — start the module

```lua
local ClientBridge = require(game.ReplicatedStorage.ClientBridge)

ClientBridge.start({
	endpoint = "https://api.example.com/clientbridge",

	-- Optional:
	-- secretName = "CB_TOKEN",       -- Creator Hub secret, sent as Authorization
	-- signingKey = "…",             -- HMAC key, never sent
	-- http = { flushSec = 1 },      -- real-time-ish; default 5
	-- transports = { console = true, http = true },

	events = {
		biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
		roll  = { scope = "player", data = { aura = "string:32", rarity = "number" } },
	},
})
```

If you set `endpoint`, HTTP is enabled even without `transports.http = true`.
Setting `transports.http = false` explicitly wins and keeps HTTP off.

Verify what actually happened:

```lua
print(ClientBridge.metrics().http)
-- { configured = true, running = true, queue = 0, sent = 3, failed = 0, ... }
```

If `running = false`, read `audit()` — the reason is there
(`http_disabled`, `HttpService disabled or endpoint missing`).

## Step 4 — authenticate (optional)

There are two independent authentication mechanisms. Use one or both.

### Shared token (`secretName`)

1. Create a secret in **Creator Hub → your experience → Secrets**, e.g.
   `CB_TOKEN`, value `Bearer <random token>`.
2. Restrict it to the domain/place you expect (Creator Hub supports scope
   restrictions).
3. Reference it by name: `secretName = "CB_TOKEN"`.

At flush time the module calls `HttpService:GetSecret("CB_TOKEN")` and sends the
value **verbatim** as `Authorization`. Store exactly what your backend expects,
including the `Bearer ` prefix if it requires one.

If the secret is missing, the request goes out without the header (the backend
will usually answer `401`); the failure is visible in `metrics().http.failed`
and `lastError`.

### Signature (`signingKey`)

A shared HMAC key stored in the server Script. It is never transmitted; each
batch carries `sig`, and the backend recomputes it. This authenticates the
payload itself and survives proxies that strip headers. Setup is in
[Signing requests](#signing-requests).

> Keep the signing key out of client-visible code. Server Scripts are safe; do
> not put it in a ModuleScript under ReplicatedStorage.

## Step 5 — the player opt-in

Player-scoped events require consent **and** Roblox’s policy gate.

### Game side

```lua
-- ServerScriptService: wire your UI RemoteEvent or use the built-in flow
local function onOptInRequested(player)
	local token, expiresAt = ClientBridge.optIn(player)
	if token then
		-- send the token to the player's own UI (the module already does)
		print(player.Name, "opted in until", os.date("%c", expiresAt))
	else
		print(player.Name, "cannot opt in (policy or not started)")
	end
end

local function onOptOutRequested(player)
	ClientBridge.optOut(player)
end
```

### Player UI (LocalScript)

```lua
local ClientBridge = require(game.ReplicatedStorage.ClientBridge)

local function requestToken()
	ClientBridge.optIn()
	for _ = 1, 30 do
		task.wait(0.1)
		local token = ClientBridge.getSession()
		if token then
			showTokenUI(token) -- a copyable TextBox + "paste this into the tool"
			return
		end
	end
	showMessage("Opt-in not available on this account.")
end
```

Why polling? The token arrives asynchronously through the `Remote`; the module
sends it only to that player.

### The policy gate

`ClientBridge.optIn(player)` asks `PolicyService` for the player’s
`AllowedExternalLinkReferences`. If the list is empty, opt-in is refused:
`optIn` returns `nil, nil`, the client keeps `getSession() == nil`, and the
server records `optin_blocked`.

This is Roblox’s age/eligibility rule for external links, not something the
module can override. Show a friendly message rather than retrying.

**Studio testing:** test players report zero allowed links, so opt-in is always
refused. For local development only, start with:

```lua
studioPolicyBypass = true, -- ignored outside Studio
```

Then opt-in works in Play mode and you can test the whole flow.

## Step 6 — run the backend locally

```
cd api
python clientbridge_api.py
```

The banner prints the endpoint to use. With the reference API running on
`127.0.0.1:8787`:

```lua
ClientBridge.start({
	endpoint = "http://127.0.0.1:8787/clientbridge",
	transports = { console = true, http = true },
	studioPolicyBypass = true,
	http = { flushSec = 1 },
	events = { ... },
})
```

No secret is needed for localhost. The API is in-memory, so restarting it
clears the replay buffer — that is expected.

## Step 7 — verify end to end

Server-scoped event (no opt-in):

```lua
ClientBridge.emit("biome", { name = "Glitch", endsAt = os.time() + 300 })
```

Then:

```
python api/demo_stream.py               # should print the biome event live
python api/demo_tool.py --token cb_...  # reads a player's REST backlog
```

Player-scoped event (needs opt-in):

```lua
ClientBridge.optIn(player)              -- or via the player's UI
-- once the token exists:
ClientBridge.emitTo(player, "roll", { aura = "Chromatic", rarity = 75000000 })
```

Subscribe with the token:

```
python api/demo_stream.py --token cb_... --name ZeusBahiano
```

You should see the `roll` event only on that token’s stream, while everyone on
the public stream sees `biome`.

## Real-time delivery

Roblox cannot open WebSocket connections: `HttpService` speaks HTTP only.
The real-time path is therefore:

1. the game batches events and POSTs them (same as above);
2. the API stores each event in the in-memory replay buffer and immediately
   pushes it to WebSocket subscribers;
3. tools keep a WebSocket open and react in milliseconds.

Latency budget:

| Stage | Typical cost |
|---|---|
| `emit` → queue | microseconds |
| flush loop | up to `http.flushSec` (set `1` for real-time, default 5) |
| HTTP POST + validation | a few ms |
| WebSocket fan-out | sub-millisecond to a few ms |

So the dominant knob is `http.flushSec`. At `1`, a message is usually in a
tool’s socket within ~1–2 seconds. Lower values increase request count; watch
`http.budgetPerMinute` and the backend rate limit.

Full streaming contract: [Live streaming](18-streaming.md).

## Signing requests

1. Generate a long random key (≥ 32 bytes), e.g. from your password manager.
2. Put it in the server Script:

   ```lua
   signingKey = "…",
   ```

3. Configure the backend with the same value:

   ```
   python clientbridge_api.py --signing-key "…"
   ```

4. The backend rejects a batch whose `sig` does not match with
   `401 {"error":"unauthorized","detail":"bad_signature"}`.

Rotating the key requires deploying the backend and the game together. Since
signatures are verified per request, a rolling migration needs two accepted
keys; the reference API supports only one, so schedule a short restart window.

Verification details and the test vector live in
[Protocol v1 — Signature](06-protocol-v1.md#signature).

## Production checklist

- [ ] `Allow HTTP Requests` enabled for the published experience.
- [ ] Endpoint is `https://` with a valid certificate.
- [ ] HTTP endpoint behind a reverse proxy (Caddy/nginx) with request limits.
- [ ] `auth_token` and/or `signing_key` configured; secrets rotated.
- [ ] `studioPolicyBypass` is **false**.
- [ ] Player-scoped events require opt-in; UI explains refusal.
- [ ] `allowClientOrigin` is `false` if client-reported events are not wanted.
- [ ] `session.ttlSec` reviewed (24 h default; shorter is more private).
- [ ] Backend is sized for event rate: see
      [Reference API — performance](08-python-api.md#performance-and-scaling).
- [ ] Monitoring on `metrics().http.queue`, `failed`, `dropped`.
- [ ] A privacy notice explains what is sent and to whom.
- [ ] A support path for “stop sending my data” (`optOut`, `DELETE /sessions`).

## Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `http.running = false` | HTTP requests disabled | enable in Game Settings / Creator Hub |
| `http.configured = false` | endpoint invalid (not https, not localhost) | fix the URL |
| `http.failed` grows, `lastError = 401` | wrong/missing secret or signing key | fix Creator Hub secret / config |
| `http.queue` grows, `lastError = "budget_exceeded"` | too many requests per minute | raise `budgetPerMinute` or `flushSec` |
| events arrive in 5 s bursts | default flush | set `http.flushSec = 1` |
| opt-in returns nil in Studio | policy gate | `studioPolicyBypass = true` (Studio only) |
| opt-in returns nil in production | account not eligible | show a message; do not retry |
| no player events reach the API | player never opted in, or token expired | opt in first; check `metrics().sessions` |
| signature 401 | canonical JSON mismatch | port the encoder exactly; see test vector |

Next: [Reference API](08-python-api.md) to run the backend, or
[Live streaming](18-streaming.md) for the socket contract.

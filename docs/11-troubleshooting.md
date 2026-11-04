# 11 — Troubleshooting

Symptom → cause → fix. Start with the closest section.

- [Installation](#installation)
- [No console lines](#no-console-lines)
- [Schema and redaction rejection](#schema-and-redaction-rejection)
- [HTTP channel](#http-channel)
- [Opt-in](#opt-in)
- [Reference API](#reference-api)
- [WebSocket stream](#websocket-stream)
- [Studio-specific](#studio-specific)
- [Diagnostic snippets](#diagnostic-snippets)

## Installation

### `ClientBridge: missing ClientBridge_Server in ServerScriptService`

The facade in `ReplicatedStorage.ClientBridge` loads
`ServerScriptService.ClientBridge_Server.Server`. The folder or the `Server`
module is missing, renamed, or in the wrong service.

Fix: restore the tree from [Getting started](01-getting-started.md#option-b--manual-copy).
Rojo users: make sure the project synced without errors.

### `missing Remote`

`ReplicatedStorage.ClientBridge.Remote` (a **RemoteEvent**) does not exist.
The Rojo project creates it automatically; manual installs must create it.
Name it exactly `Remote`.

### `invalid spec: string<24> (biome.name)`

Use the colon form: `"string:24"`. The angle-bracket form is not valid in this
protocol.

### `events must be a table` / `spec for X must be a table`

`events` must be a table of `{ typeName = { scope = ..., data = { ... } } }`.

### Module loads but nothing happens

`start` was never called, or it returned `false`. Add:

```lua
local ok, err = ClientBridge.start({ ... })
print("ClientBridge start:", ok, err)
```

## No console lines

| Check | How |
|---|---|
| Is the console channel on? | `transports.console = true` (default) |
| Is the bootstrap LocalScript installed? | `StarterPlayerScripts` has `ClientBridge_Client` |
| Did `start` succeed? | print the return values |
| Is the event blocked? | `ClientBridge.metrics().stats.blocked` and `audit()` |
| Is the scope right? | `emitTo` sends only to that player; `emit` broadcasts |
| Is the Developer Console open? | F9 on the client window, not the server output |
| Is it a Live Server? | in Team Test/Play Solo, check the correct client |

The line is printed by a **LocalScript**, so it appears in the player log, not
the Studio Output window (the server does not print it).

### Lines appear but a tool can’t parse them

- parse after the exact prefix `[CLIENTBRIDGE:1] ` (one space);
- the rest is strict JSON (canonical, sorted keys) — use a JSON parser;
- lines can be split by the log layer in rare cases; ignore invalid JSON;
- de-duplicate by `id`.

## Schema and redaction rejection

Every rejection includes a reason in the second return value of `emit`:

```lua
local ok, reason = ClientBridge.emit("biome", { name = "Glitch", username = "x" })
-- ok = false, reason = "forbidden_field:username at $.username"
```

| Reason | Fix |
|---|---|
| `undeclared field: X` | add `X` to the schema or remove it from the payload |
| `expected string` / `expected finite number` / `expected integer` / `expected boolean` | send the declared type |
| `value not in enum` | use one of the enum keys |
| `string too long` | respect `string:N` |
| `text_too_long at $.x` | value over 64 chars; shorten it (redactor cap) |
| `forbidden_field:x at $.x` | rename/remove the field; it is on the PII list |
| `pii_email` / `pii_url` / `pii_ipv4` / `pii_cpf` / `pii_phone` / `pii_discord_tag` | remove the personal data |
| `event_too_large` | fewer/shorter fields (4 KB envelope) |
| `not_started` | call `start` first |
| `disabled` | `setEnabled(false)` is active |

Inspect what was blocked:

```lua
for _, entry in ipairs(ClientBridge.audit()) do
	if entry.kind == "blocked" then
		print(entry.at, entry.type, entry.reason)
	end
end
```

## HTTP channel

### `metrics().http.configured == false`

The endpoint failed validation:

- must start with `https://` outside Studio;
- `http://127.0.0.1…` / `http://localhost…` only in Studio;
- must be the full URL (including path).

`audit()` records `http_disabled` with the reason.

### `http.running == false` but `configured == true`

`HttpService.HttpEnabled` is false: enable **Allow HTTP Requests** in Game
Settings/Settings → Security, then restart the session.

### `http.sent` stays 0

Nothing was queued: check `transports.http`, `stats.http`, and that the game
actually emits events. For player events, a token is required.

### `http.failed` grows

Look at `http.lastError`:

| Error | Meaning | Fix |
|---|---|---|
| `401` | bad/missing secret or signature | match secretName/signingKey with the backend |
| `403` | backend denies the request | check backend config/reverse proxy |
| `404` | wrong path | use the exact endpoint path |
| `415` | wrong content type | you are not talking to the right server |
| `429` | rate limited | raise `budgetPerMinute`/backend limits |
| `request_error` | network/DNS/TLS | test the URL from the server environment |

### Events arrive only every 5 seconds

`http.flushSec` defaults to 5. For near-real-time set `http.flushSec = 1` (or
lower) and watch the request budget. Real-time tools should use the WebSocket
stream ([Live streaming](18-streaming.md)).

### Queue keeps growing

`metrics().http.queue` growing means the backend is slower than the game:

- endpoint latency high / failing;
- budget exhausted (`lastError = "budget_exceeded"`);
- rate limited by backend (`429`).

Fix the backend or raise the module’s budget; the queue caps at 10,000 events
and drops the oldest.

### Signature failures only on some payloads

Number/Unicode formatting in canonical JSON. Use the provided test vector and
the Python `canonical_json()` port; see
[Protocol v1 — Signature](06-protocol-v1.md#signature).

## Opt-in

### `optIn` returns `nil, nil`

| Cause | Check |
|---|---|
| module not started | `metrics().started` |
| policy refused | `audit()` has `optin_blocked`, reason `policy` |
| Studio test player | expected; use `studioPolicyBypass = true` |
| HTTP off | opt-in still creates a session, but no events are sent; check `transports.http` |

### Client `getSession()` stays `nil`

- the server refused opt-in (see above);
- the UI polled before the Remote message arrived (poll up to ~3 s);
- the player opted out or left the game.

### Token expired too fast

`session.ttlSec` (default 86400 = 24 h). Remember the client caches
`expiresAt`; the server enforces it.

### Player events do not reach the backend

- the player never opted in;
- `transports.http` is off;
- the event schema rejected the payload (check `stats.blocked`/audit);
- the token expired between opt-in and the event.

## Reference API

| Symptom | Cause | Fix |
|---|---|---|
| `WinError 10048` / `Address already in use` | another process on the port | `--port 8788` |
| exits with “refusing to bind a non-loopback interface” | `--host 0.0.0.0` without auth | add `--auth-token` or `--signing-key` |
| `401 unauthorized` | wrong `Authorization` | match the game secret exactly (including `Bearer `) |
| `400 invalid_json` | body not JSON | check the client/test payload |
| `400 invalid_event` | shape violation | read `detail`; remember events are flat primitives |
| `400 unsupported_version` | `v` != 1 | update module/backend |
| `413 payload_too_large` | body > `max_body_bytes` | raise the limit or reduce `batchSize` |
| `429 rate_limited` | too many requests | raise `--rate-limit` or slow the game |
| `404 unknown_session` | token expired/revoked | opt in again |
| `403 admin_disabled` | `/events` without admin token | set `--admin-token` |
| REST works, WS does not | using `http://` for WS | use `ws://` / `wss://` and a WebSocket client |
| WS `426 upgrade_required` | curl/browser GET without upgrade | use a WebSocket client |
| `replay_too_large` | `since` older than the buffer | page via REST, then connect from a recent cursor |
| restart lost everything | in-memory design | expected; persist on the consumer if needed |

## WebSocket stream

### Handshake `404`

The token is unknown/expired/revoked, or you connected without first creating a
session (a player event must have been ingested for that token). In Studio,
remember `studioPolicyBypass` for testing.

### Connected, but no events

- global stream sees only `scope = "server"` events;
- private events need `?token=` and an active session;
- the game may not be emitting/flushing yet (`flushSec`);
- check `curl /health` counters and `stream_subscribers`.

### Connected, then disconnected after ~15 s

The client is not answering WebSocket ping frames. Browsers do automatically;
custom clients must implement pong. Use the
[demo client](../api/demo_stream.py) as a reference.

### `replay_too_large`

The requested `since` is older than `max_replay_events`. Read the REST backlog
first, then connect with the last `next` cursor
([gap-free pattern](18-streaming.md#replay-and-gap-free-consumption)).

### Events arrive twice

Expected on a replay boundary: the REST page and the socket can overlap. Dedupe
by `id` (or `seq`).

### `name` does not control access

Correct. The name is a display label; the token is the credential. See
[Identity](18-streaming.md#identity-token-vs-username).

## Studio-specific

| Symptom | Cause | Fix |
|---|---|---|
| opt-in always refused | Studio players have no allowed external links | `studioPolicyBypass = true` |
| HTTP endpoint rejected | not localhost and not https | use `http://127.0.0.1:PORT/clientbridge` |
| `HttpService.HttpEnabled` false | setting off | Game Settings → Security → Allow HTTP Requests |
| Play mode sees old code | Studio caching | stop, re-sync/restart Play |
| Tests fail with wrong counters | another `start` already ran | run tests on a fresh Play session; `server_cycle` resets the server |

## Diagnostic snippets

### Server snapshot

```lua
local CB = require(game.ReplicatedStorage.ClientBridge)
print(game:GetService("HttpService"):JSONEncode(CB.metrics()))
for _, entry in ipairs(CB.audit()) do print(entry.at, entry.kind, entry.reason) end
```

### Force one player to opt in (testing)

```lua
local player = game:GetService("Players"):GetPlayers()[1]
local token, expiresAt = CB.optIn(player)
print(token, expiresAt)
```

### Check the endpoint from a tool

```bash
curl -s http://127.0.0.1:8787/health
curl -s 'http://127.0.0.1:8787/sessions/cb_.../events?since=0'
```

### Check the stream

```bash
python api/demo_stream.py --url http://127.0.0.1:8787
```

### Check the backend receives anything

Run the API without `--quiet` and watch for `POST /clientbridge -> 200` lines.

Next: [FAQ](12-faq.md).

# 18 — Live streaming (WebSocket)

The reference API can push events to connected tools in real time. This page is
the complete contract for that socket.

- [Why WebSocket (and why the game still uses HTTP)](#why-websocket-and-why-the-game-still-uses-http)
- [Connecting](#connecting)
- [Handshake errors](#handshake-errors)
- [Messages from the server](#messages-from-the-server)
- [Heartbeat](#heartbeat)
- [Delivery rules](#delivery-rules)
- [Replay and gap-free consumption](#replay-and-gap-free-consumption)
- [Identity: token vs username](#identity-token-vs-username)
- [Targeting a specific player](#targeting-a-specific-player)
- [Client examples](#client-examples)
- [Latency tuning](#latency-tuning)
- [Limits and failure modes](#limits-and-failure-modes)
- [Scaling](#scaling)
- [Security](#security)

## Why WebSocket (and why the game still uses HTTP)

Roblox’s `HttpService` can only make HTTP requests — it cannot open a WebSocket.
So the data flow is necessarily split:

```
game server ──HTTP POST (batches)──> API ──WebSocket push──> tools
```

The game does not need to know a socket exists. The API turns the batches into a
real-time stream:

| Path | Who uses it | Latency |
|---|---|---|
| Console line | local tools reading the player log | immediate |
| POST → WebSocket push | remote tools | ~`http.flushSec` + a few ms |
| POST → REST cursor | tools that prefer polling/history | ~`http.flushSec` + poll interval |

Everything here applies to the reference backend in [`api/`](../api/). A custom
backend may implement the same contract.

## Connecting

```text
ws://127.0.0.1:8787/stream                              public stream
wss://api.example.com/stream                            public stream, TLS
wss://api.example.com/stream?token=cb_<32 hex>          public + own private events
wss://api.example.com/stream?token=cb_...&name=Zeus     same, with a display label
wss://api.example.com/stream?since=1234                 replay from seq 1234, then live
```

### Query parameters

| Name | Required | Meaning |
|---|---|---|
| `token` | for private events | opt-in session token, `cb_` + 32 lowercase hex |
| `name` | no | display label, max 32 chars, `[A-Za-z0-9_.-]` after sanitising |
| `since` | no | replay events with `seq > since`, then continue live; capped by `max_replay_events` |

Rules:

- **No token** → global stream: only `scope = "server"` events.
- **Valid token** → the stream includes global events **plus** that session’s
  `scope = "player"` events. It never includes another session’s events.
- **No `since`** → live-only: the cursor starts at the current highest `seq`.
- **`since` present** → replay from that cursor, then live. Use `0` to ask for
  everything the in-memory buffer still has. If the cursor is older than the
  retained window, the server sends `replay_too_large` with a `cursor` you can
  resume from.
- The token is validated at handshake time against the API’s in-memory session
  registry. Expired/revoked/unknown tokens get `404` and never open a socket.

The server requires WebSocket version 13 (every current client) and upgrades at
`/stream`.

## Handshake errors

A failed handshake is a normal HTTP response with a JSON body — the socket is
never upgraded:

| Status | `error` | Cause |
|---|---|---|
| `426` | `upgrade_required` | `/stream` opened without WebSocket headers |
| `400` | `invalid_handshake` | missing `Sec-WebSocket-Key` or wrong version |
| `400` | `invalid_session` | token is not `cb_<32 lowercase hex>` |
| `404` | `unknown_session` | token unknown, expired or revoked |
| `400` | `invalid_query` | `since` is not a non-negative integer |

## Messages from the server

All application messages are JSON text frames.

### `hello`

Sent immediately after the upgrade, before any event.

```json
{
  "v": 1,
  "kind": "hello",
  "protocol": 1,
  "stream": "session",
  "name": "ZeusBahiano",
  "cursor": 1234,
  "heartbeatSeconds": 15
}
```

| Field | Meaning |
|---|---|
| `stream` | `"global"` or `"session"` |
| `name` | the sanitised display label, or `null` |
| `cursor` | the `since` value for a replay connection, or the current max `seq` for live-only |
| `heartbeatSeconds` | expected ping interval |

### `event`

One per stored event:

```json
{
  "v": 1,
  "kind": "event",
  "seq": 1235,
  "id": "4aa1af3c-7769-45fc-930e-fc378af8fa96",
  "type": "biome",
  "at": 1762000000,
  "scope": "server",
  "origin": "server",
  "data": { "name": "Glitch", "endsAt": 1762000300 }
}
```

- `seq` is a monotonic cursor assigned by the API; use it to reconnect.
- `session` is **not** included: the connection already defines whose stream it
  is. (The admin REST feed includes it.)
- `scope`/`origin` keep their protocol meanings.

### `error`

Sent right before the server closes a socket it cannot continue:

```json
{
  "v": 1,
  "kind": "error",
  "error": "replay_too_large",
  "detail": "history before the replay window was trimmed; reconnect with since=cursor to continue from the oldest retained event",
  "cursor": 50
}
```

`replay_too_large` means one of two things:

1. **history was trimmed** — `since` is older than the oldest retained event, so
   replaying would silently skip events. `cursor` is `oldest_seq - 1`: reconnect
   with it to continue from everything still available.
2. **the replay cap was reached with more data left** — more than
   `max_replay_events` (default 5000) events are pending. `cursor` is the last
   event sent; reconnect with it to fetch the next chunk.

Exactly `max_replay_events` events is **not** an error; the server replays all
of them and continues live. The error is only sent when there is at least one
more event to replay.

### `pong`

Only in response to an application-level `{"kind":"ping"}` sent by the client:

```json
{ "v": 1, "kind": "pong", "at": 1762000000 }
```

The stream is one-way: the server ignores any other client message. It never
executes commands.

## Heartbeat

Every ~15 seconds the server sends a **WebSocket ping frame** (opcode `0x9`).
Compliant clients (browsers, most libraries) reply with a pong automatically;
the reference demo client does it manually. If a client disappears, the write
fails and the connection is cleaned up.

An application-level ping is also available if your client cannot see protocol
frames:

```json
{ "kind": "ping" }
```

→ `{"kind":"pong","at":…}`

Clients may send a close frame or `{"kind":"close"}` to end the stream.

## Delivery rules

| Subscriber | Receives |
|---|---|
| no token (global) | `scope = "server"` events |
| token `cb_A` | `scope = "server"` events **and** `scope = "player"` events whose session is `cb_A` |
| token `cb_A` | **never** player events for `cb_B` |
| any | one event is delivered once per connection; duplicates are suppressed by event id at ingestion |

Player-scope events are published to the stream only when the game queued them
for HTTP and the player had an active token. There is no retroactive delivery
to a tool that connects later unless it replays from `since` within the buffer.

## Replay and gap-free consumption

The replay buffer is bounded (defaults: 20,000 events and 1 hour). A cursor
older than the retained window gets `replay_too_large`; the error’s `cursor`
tells you where to resume. The REST session endpoint uses the same buffer but
returns **private events only** — see [Pattern B](#pattern-b--no-gaps-recommended-for-tools).

### Pattern A — live only

```text
connect /stream
handle hello
process events as they arrive
```

Missing events between reconnects is acceptable.

### Pattern B — no gaps (recommended for tools)

The WebSocket stream is the only API that returns **global and private events
combined**, so use it as the cursor source:

```text
1. cursor = 0 (or the last seq you processed)
2. loop:
     open /stream?token=...&since={cursor}
     read hello
     process event messages, set cursor = event.seq
     if connection drops: reconnect with since=cursor
     if error "replay_too_large": use error.cursor, then reconnect with it
       (repeat if the cap error appears again while catching up)
3. keep cursor persisted between runs
```

The server replays everything still in the buffer after `cursor`, then switches
to live. There is no gap between replay and live delivery: events that arrive
during the replay are queued for the same connection.

If you also want REST, remember that `GET /sessions/{token}/events` is
**private-only**; it cannot be used alone to establish a combined cursor. Use it
for private history and replay the public history through
`/stream?since=…` (or the admin `/events` feed), or simply start the stream from
`0`.

Duplicates can happen on a replay/retry boundary; de-duplicate by `id` or
`seq`.

### Cursor semantics

- `seq` starts at 1 and increases for every stored event;
- REST `since` and WebSocket `since` are **exclusive** (`seq > since`);
- `hello.cursor` is where the stream starts;
- after a `replay_too_large` error, reconnect with the `cursor` from the error
  message, not with 0.

## Identity: token vs username

A common request: “use the Roblox username so players type something short”.
Be careful — usernames are **public**:

| Field | Secret? | Stable? | Safe as auth? |
|---|---|---|---|
| Roblox username | no | can change | no |
| Roblox `UserId` | public-ish | yes | no |
| Session token `cb_…` | yes (random) | per opt-in | yes |

If `?name=ZeusBahiano` were the credential, anyone could connect as Zeus and
read his private events. So:

- the **token is the credential**; it is random, revocable and expires;
- the **name is a display label**, chosen by the player/tool, used only in the
  `hello` message (and logs are redacted);
- the tool UX can still say “connect as ZeusBahiano” if it asks for both. The
  player copies the token once; the name is cosmetic.

The reference API never sends Roblox usernames from the game. If you want the
game to include a human-readable player name in the event payload, that is a
**game/privacy decision**; the module’s redactor will not stop a field named
`player`, but treating usernames as non-personal data is your call and should
be covered by your privacy notice.

## Targeting a specific player

The game already has the player object. Use it:

```lua
-- global message → every connected tool receives it
ClientBridge.emit("biome", { name = "Glitch", endsAt = os.time() + 300 })

-- targeted message → only that player's token stream receives it
local target = game:GetService("Players"):FindFirstChild("ZeusBahiano")
if target then
	ClientBridge.emitTo(target, "roll", { aura = "Chromatic", rarity = 75000000 })
end
```

- `emit` → `scope = "server"` → broadcast to global and session streams.
- `emitTo` → `scope = "player"` → delivered only to sockets opened with that
  player's token (and only if the HTTP copy was queued, i.e. they opted in).

“Send to the user specific” is therefore solved by *routing through the player
object*, not by putting usernames on the wire.

## Client examples

### Browser (no library, 10 lines)

```html
<script>
const socket = new WebSocket("ws://127.0.0.1:8787/stream");

socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.kind === "event") {
    console.log(message.seq, message.type, message.data);
  }
};
</script>
```

The browser responds to ping frames automatically. For private events, build
the URL with `?token=` from the player.

### Python (standard library)

[`api/demo_stream.py`](../api/demo_stream.py) is a complete client: handshake,
masked frames, ping/pong, reconnect, cursor. It also acts as documentation for
any language.

```bash
python api/demo_stream.py
python api/demo_stream.py --token cb_... --name ZeusBahiano
python api/demo_stream.py --since 0 --json
```

### Node.js (with the `ws` package)

```js
import WebSocket from "ws";
const ws = new WebSocket("wss://api.example.com/stream?token=cb_...");
ws.on("message", (raw) => {
  const message = JSON.parse(raw.toString());
  if (message.kind === "event") handle(message);
});
```

## Latency tuning

| Knob | Effect |
|---|---|
| `http.flushSec = 1` (module) | main latency source; lower = snappier, more POSTs |
| `http.batchSize` (module) | larger batches = fewer requests but bigger payloads |
| `http.budgetPerMinute` (module) | cap on outbound requests per minute |
| backend rate limits | must be above the module’s rate |
| network/TLS | usually a few ms |

Measured end-to-end on localhost with `flushSec = 1`: events reach a WebSocket
in roughly 1–1.5 seconds; the socket fan-out itself is sub-millisecond to a few
milliseconds.

## Limits and failure modes

| Limit | Default | Behaviour |
|---|---|---|
| replay per connection | `max_replay_events` 5000 | `replay_too_large` error, then close |
| subscriber queue | 5000 events | slow consumer’s oldest events are dropped to protect ingestion |
| frame size | 1 MiB | oversized client frame closes the connection |
| heartbeat | 15 s | write failure cleans up the socket |
| session lifetime | 24 h from opt-in | token stops working at handshake; a socket opened before expiry keeps receiving global events only |
| replay buffer | 20,000 events / 1 h | older events cannot be replayed or read |
| process restart | — | everything is lost; clients reconnect and continue live |

Common situations:

- **Tool restarts** → reconnect, replay from the last `seq` it processed.
- **`DELETE /sessions/{token}`** → the API closes every stream opened with that
  token; the client must reconnect with a new/invalid token and gets `404`.
- **Token expires by TTL while connected** → the socket is not force-closed, but
  the API rejects new events for that token, so only global events continue; a
  reconnect fails the handshake.
- **`optOut` in the game** → stops new events and drops queued ones, but does not
  close the backend stream; call `DELETE` for that.
- **Backend restart** → `hello.cursor` resets; tools should treat a lower cursor
  as a new stream and continue (history is gone by design).
- **Slow consumer** → it may miss events; it should reconnect with `since` and
  de-duplicate.

## Scaling

Measured with [`api/benchmark_stream.py`](../api/benchmark_stream.py):

| Scenario | Result |
|---|---|
| 50 subscribers, 200 events | 10,000 deliveries, ~7,250 deliveries/s, p95 fan-out 1.5 ms |

Notes:

- the reference uses **one thread per socket** (reader + writer). Hundreds of
  idle subscribers are fine; tens of thousands are not. For that, use an
  async server or a dedicated pub/sub.
- the event bus is **in-process**. Multiple API instances behind a load balancer
  require sticky routing or a shared bus (Redis pub/sub, NATS). Do not assume a
  tool gets all events when two instances are running.
- fan-out cost grows linearly with subscribers; a single global event to 1,000
  sockets means 1,000 queue writes (fast, but measurable).

## Security

- Use `wss://` in production. A proxy (Caddy/nginx) terminates TLS and forwards
  the upgrade.
- Treat the session token like a password: it appears in the URL by necessity
  (browsers cannot set headers on WebSocket). Redact it in proxy access logs;
  the reference API redacts it in its own logs.
- Prefer short `session.ttlSec` for sensitive games.
- The game’s `optOut` stops new events and purges its queue, but the backend
  copy is only erased by `DELETE /sessions/{token}` (which also closes streams)
  or by the fixed TTL. Surface that distinction in your privacy flow.
- The stream is read-only. There is no command channel by design.
- Rate-limit handshakes at the proxy if you expose the stream publicly; the
  built-in limits are per-process and aimed at accidental floods.

Next: [Security & privacy](09-security-privacy.md) for the broader model.

# 08 — Reference API (Python)

The optional backend for HTTP mode. One file, zero third-party dependencies,
no database: it receives batches from the game, keeps recent events in RAM and
delivers them to tools over WebSocket (live) or REST (cursor).

Source: [`api/`](../api/). This page is the operator guide; day-to-day usage is
also in [`api/README.md`](../api/README.md).

- [What it is (and is not)](#what-it-is-and-is-not)
- [How it holds data](#how-it-holds-data)
- [Run it](#run-it)
- [Configuration](#configuration)
- [Endpoints](#endpoints)
- [Identity and auth](#identity-and-auth)
- [Performance and scaling](#performance-and-scaling)
- [Deploying to a server](#deploying-to-a-server)
- [Operations](#operations)
- [Testing the backend](#testing-the-backend)
- [Extending it](#extending-it)
- [Troubleshooting](#troubleshooting)

## What it is (and is not)

| It is | It is not |
|---|---|
| A reference backend you can run as-is | a production SaaS |
| A live relay with a small replay window | a database/archive |
| Python standard library only | a framework app to install |
| A contract implementation (protocol v1) | the only possible backend |

Use it to:

- test the full HTTP + real-time flow locally without infrastructure;
- run a small community tool server;
- copy the design into your own stack.

## How it holds data

```
POST /clientbridge
   └── validate → append to ring buffer (RAM) → publish to WebSocket queues
                       │
                       ├── GET /sessions/{token}/events   (cursor reads)
                       ├── GET /events                     (admin feed)
                       └── GET /stream                     (live push)
```

- **Ring buffer:** the last `buffer_events` events (default 20,000), trimmed
  after `buffer_seconds` (default 3,600 s). Old events disappear; there is no
  disk.
- **Sessions:** token → first seen, last seen, universe/place, revoked flag.
  They expire `session_ttl_hours` after **first seen** (default 24 h); incoming
  batches refresh `last_seen` but do not extend the fixed lifetime.
- **Deduplication:** the last ~2× buffer size of event IDs is remembered, so a
  retried POST does not duplicate events.
- **Restart:** everything is gone. Tools reconnect and continue with new
  events; history must be persisted by the consumer if needed.
- **Memory:** roughly a few hundred bytes per event plus payload; 20k events is
  a few MB. Sessions are tiny.

This is deliberate: writing to disk on every batch adds latency and retention
obligations that a live bridge does not need.

## Run it

### Windows, no terminal knowledge

Double-click [`api/start.bat`](../api/start.bat). A console opens with:

```
  clientbridge-api 1.0.0  (protocol 1)
  ----------------------------------------------------------
  listening on       http://127.0.0.1:8787
  game endpoint      http://127.0.0.1:8787/clientbridge
  session reads      http://127.0.0.1:8787/sessions/<token>/events
  live stream        ws://127.0.0.1:8787/stream
  admin feed         http://127.0.0.1:8787/events
  health             http://127.0.0.1:8787/health
  storage            in memory (no disk)
  ...
```

Close the window or press Ctrl+C to stop. Restarting starts empty.

### Any platform

```
cd api
python clientbridge_api.py
```

Requirements: Python 3.9+ (3.10+ recommended), nothing else.

## Configuration

CLI flags, environment variables and a JSON file all work. Precedence:
**defaults < config file < environment < command line**.

| CLI flag | Env var | Default | Meaning |
|---|---|---|---|
| `--host` | `CLIENTBRIDGE_HOST` | `127.0.0.1` | bind address |
| `--port` | `CLIENTBRIDGE_PORT` | `8787` | TCP port |
| `--auth-token` | `CLIENTBRIDGE_AUTH_TOKEN` | empty | required `Authorization` for POST |
| `--admin-token` | `CLIENTBRIDGE_ADMIN_TOKEN` | empty | unlocks `/events` |
| `--signing-key` | `CLIENTBRIDGE_SIGNING_KEY` | empty | verify batch HMAC |
| `--buffer-events` | `CLIENTBRIDGE_BUFFER_EVENTS` | `20000` | ring size |
| `--buffer-seconds` | `CLIENTBRIDGE_BUFFER_SECONDS` | `3600` | max age kept |
| `--session-ttl-hours` | `CLIENTBRIDGE_SESSION_TTL_HOURS` | `24` | token lifetime |
| `--rate-limit` | `CLIENTBRIDGE_RATE_LIMIT` | `6000` | writes/min/IP |
| `--read-rate-limit` | `CLIENTBRIDGE_READ_RATE_LIMIT` | `3000` | reads/min |
| `--max-body-bytes` | `CLIENTBRIDGE_MAX_BODY_BYTES` | `1048576` | max POST size |
| `--max-batch-events` | — | `500` | events per POST |
| `--max-event-bytes` | — | `8192` | serialized event size |
| `--max-replay-events` | `CLIENTBRIDGE_MAX_REPLAY_EVENTS` | `5000` | replay per reconnect |
| `--strict-replay` | `CLIENTBRIDGE_STRICT_REPLAY` | off | 409 on exact replay |
| `--allow-insecure` | `CLIENTBRIDGE_ALLOW_INSECURE` | off | public bind without auth |
| `--quiet` | — | off | less logging |
| `--config` | — | none | JSON file |

Examples:

```bash
# Local, no auth, default port
python clientbridge_api.py

# Public behind a proxy, authenticated, signed
python clientbridge_api.py --host 0.0.0.0 --auth-token "s3cret" --signing-key "hmac-key"

# Tune for a busy game
python clientbridge_api.py --buffer-events 50000 --buffer-seconds 7200 --rate-limit 20000
```

> The server refuses to bind a non-loopback address without `auth_token` or
> `signing_key`. That is a safety rail; do not disable it casually.

## Endpoints

### `POST /clientbridge`

```bash
curl -s http://127.0.0.1:8787/clientbridge \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer s3cret' \
  -d '{"v":1,"universeId":1,"placeId":2,"sentAt":"2026-11-04T12:00:00Z",
       "events":[{"id":"evt_1","type":"biome","at":1762000000,
                  "scope":"server","origin":"server",
                  "data":{"name":"Glitch"}}]}'
```

```json
{"ok":true,"received":1,"stored":1,"duplicates":0}
```

`stored = 0, duplicates = 1` means the event ID was already known (a retry) and
nothing new was pushed to the stream.

### `GET /stream` (WebSocket)

```
ws://127.0.0.1:8787/stream                          public events
ws://127.0.0.1:8787/stream?token=cb_...&name=...    public + that session
ws://127.0.0.1:8787/stream?since=1234               replay from a cursor
```

The full contract is in [Live streaming](18-streaming.md).

### `GET /sessions/{token}/events`

```bash
curl -s 'http://127.0.0.1:8787/sessions/cb_.../events?since=0&limit=100'
```

Returns the player’s **private** buffered events with a `next` cursor. Public
(server-scope) events are not included; use the stream or the admin feed for
those. `404` when the token is unknown, expired or revoked.

### `DELETE /sessions/{token}`

Revokes the token, removes that session’s events from RAM and closes every live
stream opened with it. Response:

```json
{ "ok": true, "revoked": true, "deleted_events": 41, "closed_streams": 1 }
```

Used by tools that offer “forget my data”. The game’s `optOut` is not a remote
revoke: it only stops new events and purges its local queue.

### `GET /events` (admin)

Requires `--admin-token` and `Authorization`. Returns all buffered events with
`universe_id`, `place_id` and the `session` token. Disabled by default.

### `GET /health`

```json
{"ok":true,"service":"clientbridge-api","version":"1.0.0","protocol":1,
 "uptime_seconds":42,"events":17,"sessions":2,"stream_subscribers":3,
 "auth_required":false,"signing_required":false}
```

### CORS

Read endpoints answer `Access-Control-Allow-Origin: *` and support `OPTIONS`,
so a local dashboard can call them from a browser. WebSocket connections are
not restricted by CORS.

## Identity and auth

Three different things, often confused:

| Concept | What it is | Who chooses it |
|---|---|---|
| **Session token** (`cb_…`) | the credential for a player’s private events | the Roblox module, on opt-in |
| **Display name** (`?name=`) | a label shown to other consumers of that socket | the tool/player |
| **Authorization** (POST / admin) | shared secret between game/operator and API | you |

The API never invents identities and never trusts a display name for access.
A player’s Roblox username is public, so it cannot protect a private stream:
anyone could connect as `ZeusBahiano`. The token is unguessable and revocable,
so it is the key. The name is there so a UI can show “connected as …”.

If you want the game itself to target a player by username, do it inside
Roblox:

```lua
local target = game:GetService("Players"):FindFirstChild("ZeusBahiano")
if target then
	ClientBridge.emitTo(target, "roll", { aura = "Chromatic", rarity = 75000000 })
end
```

The event goes only to that player’s token stream. The API never needs the
username.

## Performance and scaling

Measured with the included benchmarks on a normal desktop (single process):

| Scenario | Throughput | p95 latency |
|---|---|---|
| POST batches of 10 events, 20 threads | ~2,750 req/s (~27,500 events/s) | ~13 ms |
| GET session events, 20 threads | ~8,500 req/s | ~4 ms |
| WebSocket fan-out, 50 subscribers × 200 events | ~7,250 deliveries/s | ~1.5 ms |

Run them yourself:

```
python benchmark.py --threads 20 --duration 10
python benchmark_stream.py --clients 50 --events 200
```

Why it is fast:

- no disk I/O on the hot path;
- a single ingest lock; reads are lock-light;
- events are appended to a deque and copied to subscriber queues (no SQL, no
  serialization on the write path beyond the JSON you already sent);
- WebSocket clients are served by lightweight per-connection threads, with one
  writer thread per socket.

### The multi-process caveat

The event bus is **in-process**. If you run two API instances behind a load
balancer, a tool connected to instance A will not receive events POSTed to
instance B. Options:

1. **One process** for the stream (this is fine up to the numbers above).
2. **Sticky routing:** the game and its tools talk to the same instance.
3. **Add a shared bus** (Redis pub/sub, NATS) between instances — out of scope
   for the reference API.

For most games, option 1 is the right trade-off and the reason the API has no
database.

### When you outgrow it

- Move ingestion to a queue (Redis Streams/Kafka) and fan out from workers.
- Persist events separately if you need history after restart.
- Use an async server (uvicorn + FastAPI/Starlette) for tens of thousands of
  idle sockets; the reference uses threads for simplicity.
- Shard by `universeId` across instances with sticky routing.

## Deploying to a server

### Behind Caddy (recommended, automatic TLS)

```
api.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

Run the API on loopback and let Caddy terminate TLS:

```bash
python clientbridge_api.py --auth-token "$CB_TOKEN" --signing-key "$CB_SIGNING_KEY"
```

Use `wss://api.example.com/stream` from tools; Caddy upgrades WebSockets
automatically.

### Docker

```bash
cd api
set CLIENTBRIDGE_AUTH_TOKEN=change-me
docker compose up --build
```

No volume: events are in RAM. The container refuses `0.0.0.0` without a token.

### Firewall and exposure

- Prefer loopback + reverse proxy; never expose the raw Python server to the
  internet without a proxy and auth.
- The game only needs the POST endpoint; tools need `wss://…/stream` and
  optionally the REST read endpoints.
- If you restrict by IP, remember Roblox game servers use a wide range of
  addresses; token/signature auth is more reliable.

## Operations

| Signal | Where | Healthy |
|---|---|---|
| `events` | `/health` | growing slowly with the buffer cap |
| `sessions` | `/health` | matches opt-in players |
| `stream_subscribers` | `/health` | matches connected tools |
| POST errors | access log | rare 4xx (bad game config) |
| `429` | access log | rate limit reached |
| process RSS | OS | bounded by `buffer_events` |

Logs are one line per request, with tokens redacted (`cb_***`). Event payloads
and Authorization headers are never logged. `--quiet` reduces logging.

Graceful stop: Ctrl+C (SIGINT) or SIGTERM. WebSocket connections are dropped;
tools reconnect with `?since=` and their last cursor.

There is **no backup** because there is no persistence. That is the design.

## Testing the backend

```
cd api
python -m unittest discover -s tests -v
```

64 tests cover: canonical JSON + Lua signature vector, validation, auth,
signature, replay (gap and cap), sessions, fixed TTL, pagination, admin feed,
rate limiting, CORS, oversized bodies, concurrency, atomic mixed batches,
WebSocket handshake, global/session delivery, revocation closing streams,
application ping/pong, unknown tokens and the in-memory store bounds.

See [Testing](15-testing.md) for the full suite and how to add cases.

## Extending it

The code is deliberately one file with clear seams:

| Want | Where to change |
|---|---|
| Persist events | add a sink called after `store_batch` |
| Different auth | `token_allowed` / `validate_batch` |
| More stream types | `EventBus.publish_many` filters |
| Shared bus | replace `EventBus` with Redis/NATS |
| Different limits | `DEFAULTS` and `validate_*` |
| Metrics | `/health` and `MemoryStore.counts` |

Keep the wire contract in [Protocol v1](06-protocol-v1.md) intact; extensions
should be additive.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `OSError: [WinError 10048]` | port in use | `--port 8788` |
| exits with "refusing to bind a non-loopback interface" | `0.0.0.0` without auth | add `--auth-token`/`--signing-key` |
| `401 unauthorized` on POST | wrong token/signature | match config with the game |
| `400 unsupported_version` | `v != 1` | update the module |
| `400 invalid_event` | shape/PII/size | read the `detail` field |
| `404 unknown_session` on stream | token expired/revoked/unknown | opt in again |
| `426 upgrade_required` | opened `/stream` with curl | use a WebSocket client |
| `replay_too_large` | `since` older than the buffer or beyond the replay cap | reconnect with the `cursor` from the error message |
| no events in stream | game HTTP disabled or endpoint wrong | check module `metrics().http` |
| tool sees public events but not private | token not given / wrong token | pass `?token=` |

More: [Troubleshooting](11-troubleshooting.md).

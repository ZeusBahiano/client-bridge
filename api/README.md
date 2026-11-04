# Reference API (HTTP + live WebSocket)

Zero-dependency Python backend for ClientBridge. It receives the batches the
Roblox experience POSTs, keeps the most recent events **in memory** and
delivers them instantly to connected tools.

```
Roblox server ──POST /clientbridge──> API ──WebSocket /stream──> global tools
                                        └────session token──────> that player's tool
```

There is **no database and no disk write**. This is a live relay, not a
storage service: the priority is that the client connected *now* receives the
event *now*.

## Run it in 30 seconds

1. Install Python 3.9+ from <https://www.python.org/downloads/> (on Windows,
   tick **Add python.exe to PATH**).
2. Double-click `start.bat` (Windows) or run `./start.sh` (Linux/macOS).
3. Read the banner. For local testing in Studio, the endpoint is:

   ```
   http://127.0.0.1:8787/clientbridge
   ```

No `pip install`. No database to install. No config file required.

## Configure the game

```lua
ClientBridge.start({
    endpoint = "http://127.0.0.1:8787/clientbridge", -- local Studio testing
    transports = { console = true, http = true },
    http = { flushSec = 1 }, -- real-time: flush every second
    events = {
        biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
        roll  = { scope = "player", data = { aura = "string:32", rarity = "number" } },
    },
})
```

The module batches events; the API pushes each stored event to WebSocket
subscribers immediately. End-to-end latency is therefore roughly
`http.flushSec` (default 5 s, set `1` for real-time).

For a published experience the endpoint must be **HTTPS** (Roblox blocks plain
HTTP outside Studio).

## See events live

No authentication needed for public (server-scope) events:

```
python demo_stream.py
```

For a player's private events, the player calls `ClientBridge.optIn()` in the
game, copies the `cb_...` token and runs:

```
python demo_stream.py --token cb_0123456789abcdef0123456789abcdef --name ZeusBahiano
```

The `--name` is a display label; the **token is the credential**. A Roblox
username is public and cannot protect a private stream, so it is never used as
the authorization key.

## REST fallback

```
GET    /sessions/{token}/events?since=0&limit=100
       -> {"ok":true,"events":[...],"next":42,"has_more":false}
```

`GET /events` (admin feed) and `DELETE /sessions/{token}` complete the
contract. See `../docs/06-protocol-v1.md`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/clientbridge` | receive a batch from the game |
| GET | `/stream` | live WebSocket feed (global, or session with `?token=`) |
| GET | `/sessions/{token}/events?since=&limit=` | read that player's events from the replay buffer |
| DELETE | `/sessions/{token}` | revoke the token and erase its events |
| GET | `/events?since=&limit=&universe_id=&type=` | admin feed (needs `admin_token`) |
| GET | `/health` | liveness, counters and connected subscribers |

## Command line

```
python clientbridge_api.py --help
```

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | bind address |
| `--port` | `8787` | TCP port |
| `--auth-token` | empty | required `Authorization` value for POST |
| `--admin-token` | empty | unlocks GET `/events` |
| `--signing-key` | empty | verify the batch HMAC signature |
| `--buffer-events` | `20000` | in-memory ring size (max replay history) |
| `--buffer-seconds` | `3600` | max age of an event kept for replay |
| `--session-ttl-hours` | `24` | opt-in token lifetime |
| `--rate-limit` | `6000` | write requests/minute/IP |
| `--read-rate-limit` | `3000` | read requests/minute |
| `--max-replay-events` | `5000` | max events replayed on a WebSocket reconnect |
| `--strict-replay` | off | answer 409 to an exact replay |
| `--config` | none | JSON config file (see `config.example.json`) |

Environment variables use the `CLIENTBRIDGE_` prefix, e.g.
`CLIENTBRIDGE_PORT=8787`. Precedence is **defaults < config file < environment <
command line**.

## Why no database?

- The HTTP channel is a relay: a connected tool needs the event now, not an
  archive.
- RAM removes a disk write per batch from the hot path. This backend handles
  ~2,700 POST/s (~27k events/s) and ~7,000 fan-out deliveries/s on a desktop.
- Privacy is simpler: nothing is persisted, so there is no retention policy to
  explain and no disk to leak.
- The ring buffer still supports reconnects with `?since=<seq>` and the REST
  read contract for a limited window (`buffer_events` / `buffer_seconds`).

If you need history after a restart, persist the stream on the consumer side
(or add a sink); the reference API intentionally does not.

## Security defaults

- binds to `127.0.0.1` unless told otherwise;
- refuses a public bind with neither `auth_token` nor `signing_key`
  (`--allow-insecure` overrides, for use behind a trusted proxy);
- event payloads, tokens and `Authorization` headers are never logged;
- known PII is already removed by the Roblox module before it sends anything;
- WebSocket sessions require a valid opt-in token for private events.

Read `../docs/09-security-privacy.md` before exposing it to the internet.

## Performance

Measured on a normal desktop with the included benchmarks:

| Scenario | Throughput | p95 latency |
|---|---|---|
| POST batches of 10 events, 20 threads | ~2,750 req/s (~27,500 events/s) | ~13 ms |
| GET session events, 20 threads | ~8,500 req/s | ~4 ms |
| WebSocket fan-out, 50 subscribers × 200 events | ~7,250 deliveries/s | ~1.5 ms |

Single Python process. For more, run several instances behind a reverse proxy
and keep each one close to its clients. See
`../docs/08-python-api.md#performance-and-scaling`.

## Test

```
python -m unittest discover -s tests -v
python benchmark.py --threads 20 --duration 10
python benchmark_stream.py --clients 50 --events 200
```

## Docker

```
set CLIENTBRIDGE_AUTH_TOKEN=change-me        # Windows
docker compose up --build
```

There is no volume: restarting means an empty replay buffer. The container
refuses to start on `0.0.0.0` without a token — that is intentional.

## License

MIT, same as the rest of the project.

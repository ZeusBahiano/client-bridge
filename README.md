# ClientBridge

A tiny, read-only bridge between a Roblox experience and external tools.

Games emit **events**; tools read them. No exploits, no memory reading, no
client injection, no inbound commands.

```
your game (server) ──emit──> ClientBridge ──console line──> player's log ──> local tools
                                        └──HTTP batches──> your API ──WebSocket──> live tools
```

## Why

AFK/RNG games live and die by their tools. Most of them scrape the client log,
or worse. ClientBridge gives developers a documented, versioned channel instead
— and gives tool authors a stable format instead of fragile string matching.

- **Console channel** — zero setup, always available: one line per event in the
  player’s log.
- **HTTP channel** — opt-in, origin-attributed, batched to *your* endpoint.
- **Live stream** — the included Python backend pushes events to WebSocket
  clients in real time.
- **Read-only by design** — tools can never send a command back.

## Quick start (console, 5 minutes)

Requires Roblox Studio and (optionally) [Rojo](https://rojo.space) 7.

1. Sync/copy the module ([install guide](docs/01-getting-started.md#install)).
2. Add a server Script:

```lua
local ClientBridge = require(game.ReplicatedStorage.ClientBridge)

ClientBridge.start({
	events = {
		biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
	},
})

ClientBridge.emit("biome", { name = "Glitch", endsAt = os.time() + 300 })
```

3. Press Play and open the client console (F9):

```text
[CLIENTBRIDGE:1] {"at":1762000000,"data":{"endsAt":1762000300,"name":"Glitch"},"id":"...","origin":"server","scope":"server","type":"biome"}
```

That is the whole console channel. Read it with
[examples/console_tail.py](examples/console_tail.py):

```bash
python examples/console_tail.py
```

## Quick start (HTTP + live WebSocket, 10 minutes)

1. Start the zero-dependency backend:

```bash
cd api
python clientbridge_api.py
```

2. Point the game at it (Studio only for plain HTTP):

```lua
ClientBridge.start({
	endpoint = "http://127.0.0.1:8787/clientbridge",
	transports = { console = true, http = true },
	http = { flushSec = 1 }, -- real-time-ish
	events = {
		biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
		roll  = { scope = "player", data = { aura = "string:32", rarity = "number" } },
	},
})
```

3. Watch events live:

```bash
python api/demo_stream.py
```

Player-scoped events need the player’s opt-in:

```lua
-- server, when the player clicks “connect” in your UI
ClientBridge.optIn(player)

-- later
ClientBridge.emitTo(player, "roll", { aura = "Chromatic", rarity = 75000000 })
```

```bash
python api/demo_stream.py --token cb_... --name ZeusBahiano
```

In Studio, set `studioPolicyBypass = true` to test opt-in locally. Full guide:
[HTTP mode](docs/07-http-mode.md) • [Live streaming](docs/18-streaming.md).

## What is included

| Path | What |
|---|---|
| [src/](src/) | the Luau module (facade, client, server, shared) |
| [api/](api/) | Python reference backend: HTTP ingest, WebSocket stream, REST reads |
| [examples/](examples/) | minimal server bootstrap and console-log reader |
| [tests/](tests/) | Luau test suite (21 checks) |
| [docs/](docs/) | the full documentation set (start at [docs/README.md](docs/README.md)) |

## API at a glance

**Server**

| Call | Purpose |
|---|---|
| `start(config)` | boot the module |
| `emit(type, data)` | server-scoped event |
| `emitTo(player, type, data)` | player-scoped event |
| `optIn(player)` / `optOut(player)` | manage the player’s HTTP session |
| `isOptedIn(player)` / `getSession(player)` | inspect it |
| `metrics()` / `audit()` | counters and recent metadata |
| `setEnabled(bool)` / `stop()` | kill switch / shutdown |

**Client**

| Call | Purpose |
|---|---|
| `emit(type, data)` | report a client-side fact (untrusted origin) |
| `onEvent(callback)` | receive envelopes in the game UI |
| `optIn()` / `optOut()` / `getSession()` | session handling for the UI |

Every function, parameter and error: [API reference](docs/03-api-reference.md).

**Reference backend endpoints**

| Method | Path | Purpose |
|---|---|---|
| POST | `/clientbridge` | receive a batch from the game |
| GET | `/stream` | live WebSocket stream (global or session) |
| GET | `/sessions/{token}/events` | read the player’s buffered events |
| DELETE | `/sessions/{token}` | revoke and erase the session |
| GET | `/events` | admin feed (disabled unless configured) |
| GET | `/health` | liveness and counters |

## Design defaults that keep you out of trouble

- only fields declared in `events` are sent;
- e-mails, phone numbers, IPs, URLs, CPF, Discord tags and long free text are
  dropped;
- `UserId` never leaves the server;
- player events require opt-in **and** Roblox’s external-link policy gate;
- HTTPS only (localhost allowed in Studio);
- batched, budgeted, retried, circuit-broken HTTP;
- 4 KB event cap, 64-char strings, 24-key generic limit;
- the reference backend writes nothing to disk and exposes no inbound path.

Read [Security & privacy](docs/09-security-privacy.md) and
[Roblox rules](docs/10-roblox-tos.md) before shipping.

## Performance (reference backend)

| Scenario | Throughput | p95 |
|---|---|---|
| POST batches of 10 events, 20 threads | ~2,750 req/s (~27,500 events/s) | ~13 ms |
| REST session reads, 20 threads | ~8,500 req/s | ~4 ms |
| WebSocket fan-out, 50 subscribers × 200 events | ~7,250 deliveries/s | ~1.5 ms |

Single process, standard library only. See
[performance & scaling](docs/08-python-api.md#performance-and-scaling).

## Install

**Rojo (recommended)**

```bash
rojo serve
```

`default.project.json` maps `src/ClientBridge` to
`ReplicatedStorage.ClientBridge` (including the `Remote` RemoteEvent),
`src/Server` to `ServerScriptService.ClientBridge_Server`, and the bootstrap to
StarterPlayerScripts. Tests are installed manually.

**Manual**

See the tree in [Getting started](docs/01-getting-started.md#option-b--manual-copy).
Do not rename the folders or the `Remote` event.

## Documentation

| Start here | What |
|---|---|
| [Getting started](docs/01-getting-started.md) | install and first event |
| [Concepts](docs/02-concepts.md) | event, scope, origin, channel, token |
| [API reference](docs/03-api-reference.md) | every function and error |
| [Configuration](docs/04-configuration.md) | every option and default |
| [Event schema](docs/05-event-schema.md) | validation, enums, limits |
| [Protocol v1](docs/06-protocol-v1.md) | exact wire format and signature |
| [HTTP mode](docs/07-http-mode.md) | opt-in, secrets, deployment |
| [Reference API](docs/08-python-api.md) | running the Python backend |
| [Live streaming](docs/18-streaming.md) | WebSocket contract |
| [Security](docs/09-security-privacy.md) / [rules](docs/10-roblox-tos.md) | trust and compliance |
| [Troubleshooting](docs/11-troubleshooting.md) / [FAQ](docs/12-faq.md) | when something breaks |
| [Architecture](docs/13-architecture.md) | internals and trade-offs |
| [Console consumers](docs/14-console-consumers.md) | writing tools that read the log |
| [Testing](docs/15-testing.md) / [Glossary](docs/16-glossary.md) / [Versioning](docs/17-versioning.md) | maintainers |

## Test

```bash
# backend (55 tests, no dependencies)
cd api && python -m unittest discover -s tests -v

# benchmarks
cd api && python benchmark.py --threads 20 --duration 10
cd api && python benchmark_stream.py --clients 50 --events 200
```

The Luau suite lives in [tests/Tests.luau](tests/Tests.luau) and runs inside
Studio; see [Testing](docs/15-testing.md).

## Notes

- Enable **Allow HTTP Requests** (Game Settings → Security) only if you use the
  HTTP channel.
- For a published experience the endpoint must be **HTTPS**; the hosted
  reference backend can sit behind any TLS reverse proxy.
- The reference backend stores events **in memory only** by design. If you need
  history across restarts, persist on the consumer side.
- Not affiliated with Roblox. Do not name your fork “Roblox …” — their naming
  rules are strict.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).

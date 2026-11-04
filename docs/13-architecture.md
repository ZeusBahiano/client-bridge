# 13 — Architecture

How the module and the reference backend are built, and why.

- [Repository map](#repository-map)
- [Module layers](#module-layers)
- [Server: the life of an event](#server-the-life-of-an-event)
- [HTTP queue](#http-queue)
- [Sessions and policy](#sessions-and-policy)
- [Console channel](#console-channel)
- [Crypto and canonical JSON](#crypto-and-canonical-json)
- [Reference backend](#reference-backend)
- [Design decisions and trade-offs](#design-decisions-and-trade-offs)
- [Failure modes](#failure-modes)
- [Extension points](#extension-points)

## Repository map

```
src/
├── client-bootstrap.client.luau   LocalScript: loads the facade, prints lines
├── ClientBridge/
│   ├── init.luau                  facade: server or client API
│   ├── Config.luau                defaults
│   ├── Client.luau                client side (Remote listener, emit/onEvent)
│   └── Shared/
│       ├── Json.luau              canonical JSON (sorted keys)
│       ├── Redactor.luau          PII / free-text blocking
│       └── Schema.luau            declarative validation
└── Server/
    ├── Server.luau                orchestration, lifecycle, dispatch
    ├── Http.luau                  queue, batches, budget, retries, circuit
    ├── Session.luau               token registry
    ├── Policy.luau                PolicyService gate (cached)
    ├── Console.luau               console line builder
    ├── Crypto.luau                SHA-256, HMAC-SHA256, base64url
    └── Audit.luau                 metadata ring buffer
api/
├── clientbridge_api.py            HTTP + WebSocket reference backend
├── demo_tool.py                   REST viewer
├── demo_stream.py                 WebSocket viewer (stdlib client)
├── benchmark.py                   HTTP load test
├── benchmark_stream.py            WebSocket fan-out test
└── tests/test_api.py              55 tests
tests/Tests.luau                   Luau test suite
examples/
├── bootstrap.server.luau          minimal server example
└── console_tail.py                console-channel reader
docs/                              this documentation
```

## Module layers

```
                ┌───────────── facade (init.luau) ─────────────┐
server API  ────┤ start stop emit emitTo optIn optOut …       │
client API  ────┤ emit onEvent optIn optOut getSession        │
                └───────────────────────┬──────────────────────┘
                                        │
                          ┌─────────────▼─────────────┐
                          │        Server.luau        │
                          │ validate → envelope →     │
                          │ dispatch → console/http   │
                          └───┬──────────┬────────────┘
                              │          │
                 ┌────────────▼──┐   ┌───▼──────────────┐
                 │ Schema        │   │ Console.luau     │
                 │ Redactor      │   │ (line + Remote)  │
                 │ Json          │   └──────────────────┘
                 └───────────────┘   ┌──────────────────┐
                                     │ Http.luau        │
                                     │ queue/batch/     │
                                     │ budget/retry     │
                                     └──────────────────┘
```

The facade does no work; it selects the server or client surface once, by
`RunService:IsServer()`. This is why the same `require` path works on both
sides.

## Server: the life of an event

1. **Call** — game code calls `emit(type, data)` or `emitTo(player, type, data)`.
2. **Lifecycle checks** — started? enabled? `data` is a table?
3. **Schema** — `Schema.validateEvent`: type name, declared fields, types,
   enums; rejects undeclared fields.
4. **Redaction** — `Redactor.check`: forbidden keys, 64-char strings, PII
   patterns.
5. **Envelope** — `{ id = GUID, type, at = os.time(), scope, origin, data }`.
6. **Size** — canonical JSON must be ≤ `maxEventBytes` (4096).
7. **Dispatch**
   - console: `Console.line` → `Remote:FireAllClients` (server scope) or
     `FireClient(player)` (player scope);
   - HTTP: `Http.enqueue(envelope, session?)` (server scope without a session;
     player scope only if a token exists).
8. **Accounting** — `stats.emitted/console/http`; blocked events go to `Audit`.

An event that fails any step returns `false, reason` and is **not** sent
anywhere.

## HTTP queue

```
emit → queue[≤10,000] ──flush loop (every flushSec)──► POST
                              │                         │
                              │                 2xx ──► sent
                              │                 else ──► retry (≤maxRetries)
                              │                         └─ 3 consecutive → breaker 30 s
                              └─ budget 400 req/min per server
```

Properties:

- **deferred** — `emit` never performs network I/O; latency spikes in
  `HttpService` do not affect gameplay;
- **batch** — up to `batchSize` (100) events per POST;
- **bounded** — 10,000 events; oldest dropped when full;
- **budgeted** — hard request cap per minute;
- **resilient** — retries, backoff via circuit breaker, drop-with-audit;
- **one-way** — responses are not read; `2xx` is the entire acceptance signal.

## Sessions and policy

`Session.luau` keeps two maps: token → `{player, expiresAt}` and player → token.
- token = `cb_` + GUID without dashes;
- `create` revokes the previous token for that player;
- `get` validates expiry lazily;
- `cleanup` runs every 30 s and on `PlayerRemoving`;
- the map never leaves the server (`UserId`/players are not serialised).

`Policy.luau` calls `PolicyService:GetPolicyInfoForPlayerAsync` and caches the
result per player for 5 minutes. The opt-in gate is
`#AllowedExternalLinkReferences > 0` — Roblox’s own external-link eligibility
rule. `studioPolicyBypass` skips it **only** when `RunService:IsStudio()` and the
skip is audited.

## Console channel

`Console.luau` builds one line: the fixed tag plus canonical JSON. The server
never prints it; it sends it over the `Remote` to the client, and the bootstrap
LocalScript prints it. Why:

- the line lands in the **player’s** log, where tools already read;
- a server-side `print` would go to the server output, not the player log;
- the client is also where `onEvent` callbacks run.

`FireAllClients` for server scope, `FireClient` for player scope — the Remote
message includes both the line and the decoded envelope.

## Crypto and canonical JSON

`Crypto.luau` implements SHA-256 and HMAC-SHA256 with `bit32`, plus base64url.
`Json.luau` implements a deterministic encoder (sorted keys, `%.14g` numbers,
minimal escaping). Signatures are computed over `Json.encode` output, so any
verifier must reproduce those bytes. Test vectors in `tests/Tests.luau` and in
the Python suite pin both implementations.

Why in Luau instead of Roblox APIs? Roblox exposes no HMAC/signing primitive to
server scripts. Implementing it locally keeps the protocol self-contained and
avoids an extra HTTP service.

## Reference backend

```
ThreadingHTTPServer
├── POST /clientbridge → validate → MemoryStore.store_batch
│                                   └→ EventBus.publish_many(new events)
├── GET /stream → WebSocket handshake → Subscriber queue → sender thread
├── GET/DELETE /sessions/... → MemoryStore (deque scan, O(buffer))
└── GET /events, /health
```

Key choices:

| Choice | Why |
|---|---|
| stdlib only | zero setup for non-programmers |
| in-memory deque | lowest latency; no disk/retention obligations |
| one ingest lock, lock-light reads | simple and predictable under load |
| one writer thread per WebSocket | simple backpressure; fine to hundreds of sockets |
| JSON text frames | debuggable with any client |
| no database | matches “the connected client matters” |
| bounded buffers | memory cannot grow without limit |

## Design decisions and trade-offs

| Decision | Benefit | Cost |
|---|---|---|
| Read-only, one-way | cannot be used to control the game | no round-trip queries |
| Opt-in for player data | privacy/compliance | friction before tools work |
| Console default on | zero setup | data sits in local logs |
| HTTP opt-in | remote tools | infrastructure + secrets |
| Redaction | fewer data leaks | false positives possible (e.g., a code that looks like a phone) |
| 64-char string cap | blocks free text | long names/URLs must be shortened |
| 4 KB envelope | bounded payloads | rich payloads need several events |
| Canonical JSON | signable, stable | verifiers must port the encoder |
| No persistence (API) | latency, simplicity, privacy | no history after restart |
| Per-event IDs | idempotent retries | 30 extra bytes |

## Failure modes

| Failure | Result | Recovery |
|---|---|---|
| Schema bug in game | events blocked, audited | fix schema; no crash |
| HTTP endpoint down | queue grows, oldest drops | endpoint back; queue drains |
| Backend slow | queue grows | raise budget/scale backend |
| Bad secret/key | repeated 401, retries then drops | fix config |
| Player not opted in | private HTTP copy skipped | player opts in |
| Client leaves | token revoked | new opt-in |
| API restart | replay buffer empty | tools reconnect, continue live |
| API process dies | tools reconnect | supervisor restarts; no history |
| Slow WebSocket consumer | oldest queued events dropped | reconnect with cursor |

## Extension points

Safe extensions:

- new client functions (additive);
- new schema spec kinds (additive, documented);
- new `Audit` kinds;
- new HTTP headers (ignored by old peers);
- new backend endpoints.

Breaking changes need a protocol bump: removing/renaming envelope fields,
changing canonical JSON, changing the console tag, changing delivery semantics.

Places to be careful:

- `Json.encode` — any change breaks signatures;
- `Redactor` patterns — tightening blocks previously valid events;
- default config — changing defaults changes behaviour silently;
- `EventBus` filtering — privacy depends on it.

Next: [Console consumers](14-console-consumers.md) or
[Testing](15-testing.md).

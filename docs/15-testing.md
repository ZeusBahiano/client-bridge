# 15 — Testing

Two test suites: the Luau module inside Roblox Studio, and the Python backend
on any machine.

- [Luau test suite](#luau-test-suite)
- [Python test suite](#python-test-suite)
- [End-to-end checklist](#end-to-end-checklist)
- [Benchmarks](#benchmarks)
- [Adding tests](#adding-tests)
- [Continuous integration](#continuous-integration)

## Luau test suite

Source: [`tests/Tests.luau`](../tests/Tests.luau).

### Install

The Rojo project does not ship tests. To run them:

1. Copy `tests/Tests.luau` into **ServerScriptService** as a ModuleScript named
   `ClientBridge_Tests`.
2. Make sure `ReplicatedStorage.ClientBridge` and
   `ServerScriptService.ClientBridge_Server` exist.

### Run

From the **Command Bar** while not playing, or from a server Script:

```lua
local T = require(game.ServerScriptService.ClientBridge_Tests)
for _, result in ipairs(T.run()) do
	print((result.ok and "PASS " or "FAIL ") .. result.name .. (result.detail and (" :: " .. result.detail) or ""))
end
```

For functions that need a running server (`Server.start`, `facade`), run in
**Play** mode (server context) or via a Studio MCP `execute_luau` call. The
`server_cycle` test calls `Server.stop()` first, so it is independent from
whatever else started the module.

### What it covers (21 checks)

| Group | Checks |
|---|---|
| JSON | canonical key order, arrays, strings, numbers, roundtrip, mixed-table error |
| Crypto | SHA-256 vectors (empty, `abc`, two blocks), HMAC vector, base64url |
| Redactor | clean payload, e-mail blocked, forbidden key, long text, URL/phone/CPF |
| Schema | compile + validate, undeclared field, enums, integer, string limit, old `string<24>` syntax rejected |
| Console | prefix, decodable JSON |
| Sessions | create/get/revoke, expiry |
| Audit | ring size and metadata |
| HTTP | configuration, queue, reject invalid endpoint |
| Server | start/emit/block/stop, metrics |
| Facade | constants and exposed functions |

Expected output: every line starts with `PASS`.

## Python test suite

Source: [`api/tests/test_api.py`](../api/tests/test_api.py).

### Run

```bash
cd api
python -m unittest discover -s tests -v
```

Requirements: Python 3.9+ and nothing else. The suite starts the real server as
a subprocess on a free loopback port; no network access is required.

### What it covers (64 tests)

| Area | Examples |
|---|---|
| Canonical JSON | Luau signature vector, escapes, non-finite rejection |
| Config | defaults, public-bind guard, insecure opt-in |
| HTTP ingest | valid batch, duplicates, invalid JSON, wrong version, bad types, player-without-session, server-with-session, content type, unknown endpoint, CORS, oversize |
| Auth | raw and `Bearer` token, wrong token, non-ASCII header is 401 |
| Signatures | valid Lua vector, tampered payload, tampered signature, missing, non-ASCII sig is 401 |
| Sessions | REST feed, cursor pagination, fixed TTL, delete, unknown/expired token, revoked-token rejection, mixed-batch atomicity |
| Admin | disabled by default, token required, all events |
| Rate limit | 429 + `Retry-After` |
| Concurrency | 12 threads × 8 batches |
| Memory store | ring bound, id dedupe, strict replay, revoke purge, stream filtering, dead-token guard |
| WebSocket | upgrade required, hello, global live events, session isolation, replay gap/cap cursors, unknown token, app ping/pong, DELETE closes streams, health subscriber count |

### How the fixture works

`ServerProcess` starts `python clientbridge_api.py --port <free> --quiet` with a
pipe for stderr, waits for `/health`, and terminates it on teardown. Each test
class gets its own process, so auth/signing/rate tests do not interfere.

## End-to-end checklist

Run this before a release:

1. **Backend**
   ```bash
   cd api
   python -m unittest discover -s tests
   python clientbridge_api.py
   ```
2. **Studio, module tests** — copy `tests/Tests.luau`, run `T.run()`, all `PASS`.
3. **Console channel** — Play, emit a server event, confirm the
   `[CLIENTBRIDGE:1]` line in the client console.
4. **HTTP server-scope** — start with
   `endpoint = "http://127.0.0.1:8787/clientbridge"`, `http.flushSec = 1`,
   emit `biome`, confirm `demo_stream.py` prints it.
5. **Opt-in** — `studioPolicyBypass = true`, opt a test player in, capture the
   token, call `emitTo(player, "roll", …)`, confirm only that token's stream
   receives it.
6. **Revocation (two layers)** — `optOut` stops new events and drops queued
   ones on the game side; it does *not* revoke the backend copy. Then call
   `DELETE /sessions/{token}` and confirm the live stream closes and the REST
   feed returns `404`.
7. **Signature** — start the API with `--signing-key`, configure the same
   `signingKey` in Studio, confirm deliveries (and a wrong key gives `401`).
8. **Shutdown** — Ctrl+C the API; the game keeps running (`http.queue` grows,
   then drains after restart, oldest events may drop).
9. **Git cleanliness** — no generated databases, no logs, no secrets, no test
   output in the repo.

## Benchmarks

Not pass/fail tests, but they catch performance regressions:

```bash
cd api
python benchmark.py --threads 20 --duration 10
python benchmark.py --mode get --token cb_... --threads 20 --duration 10
python benchmark_stream.py --clients 50 --events 200
```

Expected order of magnitude on a desktop (see
[Reference API](08-python-api.md#performance-and-scaling)):

| Benchmark | Expect |
|---|---|
| POST 10-event batches | thousands req/s, zero errors |
| GET session | thousands req/s |
| 50-subscriber fan-out | all complete, sub-ms p95 fan-out |

Compare against the numbers in the docs; a large drop means something got
slower (locking, allocations, accidental persistence).

## Adding tests

### Luau

Add a `check("name", function() ... end)` to `Tests.luau`:

```lua
check("my_feature", function()
	local ok, value = SomeModule.doThing()
	truthy(ok, "ok")
	eq(value, 42, "value")
end)
```

Use `eq`/`truthy` for readable failures. Keep tests independent: reset module
state at the start of the test (as `server_cycle` does with `Server.stop()`).

### Python

Add a method to an existing class or a new class; use `self.server.request`.
For store-only behavior, instantiate `api.MemoryStore` directly (fast, no
process):

```python
def test_my_case(self):
    store = api.MemoryStore(1000, 60, 1)
    stored, duplicates, events = store.store_batch(batch([event("e1")]), "h", False)
    self.assertEqual((stored, duplicates), (1, 0))
```

Keep the suite fast (< 30 s) and deterministic: no sleeps longer than needed, no
dependence on wall-clock order.

## Continuous integration

Any CI with Python 3.9+ can run the backend suite:

```yaml
- run: python -m unittest discover -s api/tests -v
  working-directory: .
```

The Luau suite needs Roblox Studio (or a Roblox place runner), so it is usually
run locally before release; the Python signature test vector is generated by the
Luau implementation and pins cross-language compatibility.

Next: [Glossary](16-glossary.md).

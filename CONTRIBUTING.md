# Contributing

Thanks for helping. This project is small on purpose: keep changes focused,
documented and tested.

## Before you start

- For bugs: open an issue with the module/protocol version, the channel
  (console/HTTP/stream), and a minimal reproduction.
- For security issues: **do not** open a public issue — see [SECURITY.md](SECURITY.md).
- For large features: open an issue first so the design can be discussed.

## Development setup

Requirements:

- Roblox Studio + [Rojo](https://rojo.space) 7 (for the Luau side)
- Python 3.9+ (for the reference backend and tooling)
- Git

```bash
git clone https://github.com/ZeusBahiano/client-bridge.git
cd client-bridge
rojo serve            # connect from Studio's Rojo plugin
python -m unittest discover -s api/tests -v
```

## Repository layout

```
src/            Luau module (protocol implementation)
tests/          Luau test suite
api/            Python reference backend + tests + benchmarks
examples/       runnable examples
docs/           documentation (start at docs/README.md)
```

## Coding guidelines

### Luau

- `--!strict` and explicit types in every module.
- Tabs for indentation (matches existing files).
- Server modules stay under `src/Server`; shared code under
  `src/ClientBridge/Shared`; no new top-level dependencies.
- Any wire-format change is a protocol change — see
  [Versioning](docs/17-versioning.md).
- Keep `emit` allocation-light: no network I/O on the call path.
- Never log or transmit player identifiers; extend `Redactor` if new PII
  patterns are discovered.

### Python

- Standard library only. The reference API must stay installable with nothing
  but `python`.
- 4-space indentation, type hints on public functions, comments that explain
  *why* (not *what*).
- Public behavior changes need a test in `api/tests/test_api.py`.
- Keep the canonical JSON encoder byte-identical to the Luau one; the test
  vector in `test_api.py` is the guard.
- No persistence: the reference API is deliberately in-memory.

### Docs

- English, plain language, examples that run unchanged.
- Link related pages instead of duplicating explanations.
- Update the table of contents when adding a section.
- If behavior changes, change the docs in the same commit.

## Tests

Run everything before pushing:

```bash
cd api
python -m unittest discover -s tests -v
python benchmark.py --threads 20 --duration 10
python benchmark_stream.py --clients 50 --events 200
```

Luau: copy `tests/Tests.luau` into `ServerScriptService.ClientBridge_Tests` and
run `T.run()` in Studio; every check must print `PASS`. See
[docs/15-testing.md](docs/15-testing.md).

## Pull requests

- One logical change per PR.
- Include tests for behavior changes.
- Update `CHANGELOG.md` under `Unreleased`.
- Describe what changed, why, and how you verified it (commands + output).
- Do not include secrets, logs, `.db` files, or unrelated formatting churn.

## Commit style

Short imperative subject, optional body:

```
api: add bounded replay for WebSocket reconnects

- ring buffer keeps last buffer_events events
- /stream?since= replays then goes live
- tests for filtering and reconnect cursor
```

## License

By contributing you agree that your changes are licensed under the MIT license
of this repository.

# ClientBridge documentation

Everything you need to understand ClientBridge — from the one-line console
event to the byte-level HTTP signature — is in this folder. The docs are
ordered so that each file only depends on the ones before it.

## Start where you are

| If you are… | Start here | Then |
|---|---|---|
| A game developer who just wants an event in the log | [Getting started](01-getting-started.md) | [Concepts](02-concepts.md) |
| Adding events to a real experience | [API reference](03-api-reference.md) | [Configuration](04-configuration.md), [Event schema](05-event-schema.md) |
| Building the HTTP channel | [HTTP mode](07-http-mode.md) | [Protocol v1](06-protocol-v1.md), [Reference API](08-python-api.md) |
| Writing a tool/macro that reads events | [Console consumers](14-console-consumers.md) | [Protocol v1](06-protocol-v1.md), [Live streaming](18-streaming.md) |
| Running the backend | [Reference API](08-python-api.md) | [Live streaming](18-streaming.md) |
| Worried about the Roblox rules | [Roblox rules](10-roblox-tos.md) | [Security & privacy](09-security-privacy.md) |
| Debugging something | [Troubleshooting](11-troubleshooting.md) | [FAQ](12-faq.md) |
| Maintaining or forking the project | [Architecture](13-architecture.md) | [Testing](15-testing.md), [Versioning](17-versioning.md) |

## Reading path (progressive)

**Part 1 — the basics**
- [01 — Getting started](01-getting-started.md): install and emit your first event.
- [02 — Concepts](02-concepts.md): the vocabulary: event, scope, origin, channel, token.

**Part 2 — using the module**
- [03 — API reference](03-api-reference.md): every function, parameter, return value and error.
- [04 — Configuration](04-configuration.md): every option and its default.
- [05 — Event schema](05-event-schema.md): declarative validation, enums and limits.

**Part 3 — the wire**
- [06 — Protocol v1](06-protocol-v1.md): the exact bytes, envelope, console line, HTTP batch, signature.
- [07 — HTTP mode](07-http-mode.md): enabling and operating the opt-in HTTP channel.
- [08 — Reference API](08-python-api.md): running the Python backend for HTTP mode.
- [18 — Live streaming](18-streaming.md): the WebSocket contract, identity and reconnects.

**Part 4 — trust**
- [09 — Security & privacy](09-security-privacy.md): threat model, redaction, secrets, retention.
- [10 — Roblox rules](10-roblox-tos.md): what is and is not allowed, and why this design is safe.

**Part 5 — operations and reference**
- [11 — Troubleshooting](11-troubleshooting.md): symptom → cause → fix.
- [12 — FAQ](12-faq.md): short answers to common questions.
- [13 — Architecture](13-architecture.md): how the module is built and why.
- [14 — Console consumers](14-console-consumers.md): writing external tools that read the log.
- [15 — Testing](15-testing.md): the test suite and how to add cases.
- [16 — Glossary](16-glossary.md): every term in one place.
- [17 — Versioning](17-versioning.md): protocol versions and breaking changes.

## Conventions

- `ClientBridge.start{...}` and `start({...})` mean the same thing (one table argument).
- `→` in signatures shows the return values, e.g. `emit(type, data) → (boolean, string?)`.
- `?:` means "or nil".
- Code blocks are Luau unless the heading says otherwise.
- “The module” means the Luau package in `src/`; “the API” means the optional
  Python backend in `api/`.
- “Player event” means `scope = "player"`; “server event” means
  `scope = "server"`.

## What is stable

| Surface | Stability |
|---|---|
| Console line prefix `[CLIENTBRIDGE:1]` | frozen for protocol 1 |
| Envelope fields | frozen for protocol 1 |
| HTTP batch shape | frozen for protocol 1 |
| Public Luau functions in [API reference](03-api-reference.md) | stable, additive changes only |
| Config keys in [Configuration](04-configuration.md) | stable, additive changes only |
| Python API endpoints | stable, additive changes only |
| Internal modules (`Server/Http.luau`, `api/clientbridge_api.py` internals) | free to change |

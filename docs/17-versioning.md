# 17 — Versioning and deprecation

Three version numbers exist. They are not the same.

| Version | Where | Changes when |
|---|---|---|
| **Protocol** (`PROTOCOL`, `"v"`, `:1`) | wire format | breaking change to envelope/console/HTTP |
| **Module** (`VERSION`) | Luau package | any release (semantic-ish) |
| **API** (`API_VERSION`) | Python backend | any backend release |

- Current protocol: **1**
- Current module: **1.1.0**
- Current reference API: **1.0.0**

## Protocol compatibility

A consumer written for protocol 1 must keep working as long as the module
reports protocol 1. That means:

- envelope fields are never removed or renamed;
- field meanings do not change;
- the console prefix stays `[CLIENTBRIDGE:1]`;
- the HTTP batch keeps `v`, `universeId`, `placeId`, `sentAt`, `events` and the
  optional `sig`;
- the signature algorithm and canonical JSON rules stay byte-identical;
- delivery semantics (scope, opt-in) do not silently change.

### Allowed without a bump (additive)

- new event types or schema spec kinds;
- new optional fields in **responses** (consumers must ignore unknown fields);
- new endpoints on the reference API;
- new audit kinds;
- new config keys (with defaults);
- performance changes.

### Requires a protocol bump

- adding a **required** envelope field or changing one’s type;
- removing a field;
- changing canonical JSON (signatures would break);
- changing the console tag;
- changing what `scope`/`origin` mean;
- changing opt-in guarantees;
- switching HTTP ingestion to a different content type.

When bumping:

1. increase `PROTOCOL` in `src/ClientBridge/init.luau`;
2. change the console tag to `[CLIENTBRIDGE:2]`;
3. change HTTP `"v": 2` and `X-ClientBridge-Version: 2`;
4. update [Protocol](06-protocol-v1.md) (copy it to `protocol-v2.md`, keep v1
   documented for older tools);
5. update the reference API to accept the versions it supports (optionally
   both during a transition);
6. document a migration window.

## Module release rules

The module follows a pragmatic semver:

| Change | Version |
|---|---|
| bug fix, docs only | patch (`1.0.1`) |
| new optional function/config/spec | minor (`1.1.0`) |
| behaviour change that could surprise | minor + release note |
| breaking Lua API (removing/renaming a function, changing returns) | major (`2.0.0`) |

Note: adding a *required* argument or changing a return shape is breaking for
Lua callers even if the wire format is unchanged — major bump.

## Reference API release rules

- adding an endpoint, config key or response field: minor;
- changing an endpoint shape or default that clients depend on: major;
- internal performance/refactors: patch;
- protocol support changes: follow the protocol bump and say which versions are
  accepted.

The API is a reference implementation: it may evolve faster than the protocol.
Its endpoints are documented in [Reference API](08-python-api.md) and
[Protocol](06-protocol-v1.md#read-contract).

## Deprecation

1. Announce the deprecation in `CHANGELOG.md` and the docs.
2. Keep the old behaviour working for at least one minor release.
3. Log/annotate warnings where practical (`audit()` kinds, API `Warning`
   headers are not required).
4. Remove only in the next major (or protocol) bump.
5. Update examples and docs in the same commit that removes it.

## Compatibility matrix (current)

| Module | Protocol | Reference API | Works |
|---|---|---|---|
| 1.0.x | 1 | 1.0.x | yes |
| 1.1.x | 1 | 1.0.x | yes |
| 1.0.x / 1.1.x | 1 | any custom backend implementing v1 | yes |
| future 2.x | 2 | 2.x | only with matching protocol |

Tools: match the **protocol**, not the module version. A tool that reads
`[CLIENTBRIDGE:1]` keeps working across all 1.x modules.

## What is *not* versioned

- The reference API’s in-memory buffer size and defaults (config, not contract).
- Logging format.
- Internal module structure.
- Benchmarks.

## Reporting a compatibility problem

If a module update breaks a protocol-1 consumer, that is a bug. Please include:

- module `VERSION` and `PROTOCOL`;
- the consumed channel (console/HTTP/stream);
- a minimal payload that reproduces it;
- the tool’s expected shape.

Open an issue following [CONTRIBUTING](../CONTRIBUTING.md).

# Changelog

All notable changes to this project are documented here.

## Unreleased

### Added

- **Live WebSocket streaming** in the reference API (`GET /stream`), with
  global and per-session channels, replay by `since` cursor, heartbeats and
  slow-consumer protection.
- Python API and stdlib WebSocket client (`demo_stream.py`), REST viewer
  (`demo_tool.py`), HTTP and fan-out benchmarks.
- Full documentation set (`docs/`), progressive from the first event to the
  protocol internals.
- `studioPolicyBypass` config: Studio-only opt-in testing without weakening
  production policy.
- `examples/console_tail.py`: reference console-channel reader.
- `default.project.json` now creates the `RemoteEvent`, so a fresh Rojo sync
  works without manual steps.
- 64 Python tests and additional Luau tests (enums, integer, redaction,
  numbers, server restart isolation).

### Changed

- Reference API is now **in-memory only** (no SQLite, no disk). The replay
  buffer is bounded by `buffer_events` / `buffer_seconds`; restarting starts
  empty. This removes disk I/O from the hot path.
- HTTP kill switch now also pauses the flush loop.
- Localhost endpoint validation is exact-host (no lookalike prefixes).
- Server `stop()` disconnects listeners, so `start/stop/start` is clean.

### Fixed

- Missing `Remote` in the Rojo project for fresh installs.
- Server restart leaving duplicate event handlers connected.
- Signature canonicalization documented and pinned with a Luau-generated test
  vector.
- `optOut`/player-leave now drop that session's queued HTTP events, so nothing
  private is POSTed after consent is withdrawn. Docs clarify that backend
  erasure needs `DELETE /sessions/{token}` or the fixed TTL (the one-way game
  cannot reach into a backend).
- Reference API session TTL is now fixed from `first_seen`, matching the
  module's token lifetime instead of an idle timeout.
- Batch ingestion is atomic: all session tokens are preflighted before any event
  is stored or published; a mixed batch with a dead token stores nothing.
- Publishing happens under the ingest lock, and `DELETE /sessions/{token}`
  closes that token's live streams, removing the revocation/publish race.
- WebSocket replay now reports a real gap when `since` is older than the
  retained buffer, includes a resumable cursor, and no longer errors when the
  replay is exactly `max_replay_events`.
- Non-ASCII `Authorization`/`sig` values return `401` instead of `500`;
  authentication happens before reading the request body; the WebSocket version
  header is required.
- `demo_stream.py` no longer crashes on a failed handshake; `console_tail.py`
  filters Player logs and handles truncation/rotation in auto-detect mode.
- `string:N` now rejects fractional/exponent limits; docs say strings are
  measured in UTF-8 bytes.
- Documentation corrections: opt-out/TTL semantics, retry pacing, combined
  stream cursor, bootstrap responsibilities and test coverage.

## 1.0.0 — 2026-11-02

- Initial release: read-only console + opt-in HTTP bridge, schema validation,
  redaction, sessions, policy gate, audit, metrics, Luau test suite, MIT
  license.

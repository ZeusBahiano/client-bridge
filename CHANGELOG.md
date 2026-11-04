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
- 55 Python tests and additional Luau tests (enums, integer, redaction,
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

## 1.0.0 — 2026-11-02

- Initial release: read-only console + opt-in HTTP bridge, schema validation,
  redaction, sessions, policy gate, audit, metrics, Luau test suite, MIT
  license.

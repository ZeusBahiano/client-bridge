# Protocol v1

Everything the module emits follows this contract.

## Event envelope

```json
{
  "id": "guid",
  "type": "biome",
  "at": 1762000000,
  "scope": "server",
  "origin": "server",
  "data": { "name": "Glitch", "endsAt": 1762000300 },
  "session": "cb_..."
}
```

- `id` — unique per event.
- `type` — lowercase, `[a-z0-9_.-]`, 1..32 chars.
- `at` — epoch seconds (server clock).
- `scope` — `server` (everyone) or `player` (one player).
- `origin` — `server` (trusted) or `client` (reported by the client, not trusted).
- `data` — flat table, strings <= 64 chars, up to 24 keys, event <= 4 KB.
- `session` — only on player events with opt-in; never present on console lines.

## Console line (local)

```
[CLIENTBRIDGE:1] {"at":1762000000,"data":{"name":"Glitch"},"id":"...","origin":"server","scope":"server","type":"biome"}
```

- keys in alphabetical order (canonical JSON)
- one line per event, fixed prefix
- printed by the **client**, so it lands in the player's local log

## HTTP POST (opt-in)

```
POST <your endpoint>
Authorization: Bearer <secret, optional>
X-ClientBridge-Version: 1
Content-Type: application/json

{
  "v": 1,
  "universeId": 123,
  "placeId": 456,
  "sentAt": "2026-11-01T23:10:00Z",
  "events": [ { "...event", "session": "cb_..." } ],
  "sig": "base64url(hmac-sha256)"
}
```

- `sig` only appears when `signingKey` is configured. It signs the canonical JSON of
  `{events, placeId, sentAt, universeId, v}`.
- Expected response: `2xx`. `429`/`5xx` triggers backoff.

## Suggested read contract

Not enforced, but useful so tools work across games:

```
GET    <endpoint>/session/{token}/events?since=<cursor>
DELETE <endpoint>/session/{token}
```

The player copies the token from the game into the tool; the tool exchanges it for
that session's events.

## Rules

- No inbound: the module exposes no endpoint and accepts no commands.
- `UserId` never appears in an envelope.
- Types not declared by the developer fall back to generic validation.
- Breaking changes get a new version (`:2` / `"v": 2`).

## Status codes

| Code | Meaning |
|---|---|
| 400 | invalid schema |
| 401 | auth |
| 409 | replay |
| 429 | back off |
| 5xx | retry |

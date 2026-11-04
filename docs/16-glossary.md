# 16 — Glossary

Every term used by the project, in one place.

**Admin feed** — `GET /events` on the reference API, listing all buffered
events; disabled unless `admin_token` is configured.

**`allowClientOrigin`** — config flag; when `false`, events emitted by a
`LocalScript` are ignored. Default `true`.

**Audit** — a 200-entry in-memory ring of metadata about lifecycle and blocked
events. `ClientBridge.audit()` returns the newest 100. Never stores field
values. See [API reference](03-api-reference.md#audit).

**Batch** — up to `http.batchSize` events sent in one HTTP POST.

**Bearer token** — a credential where possession is authorization. The session
token (`cb_…`) and the optional `Authorization` token are bearer tokens.

**Budget** — `http.budgetPerMinute`; the maximum number of outbound HTTP
requests per minute, per server instance.

**Canonical JSON** — the deterministic serialization used on the wire and for
signatures: sorted keys, no whitespace, `%.14g` numbers, minimal escaping. See
[Protocol v1](06-protocol-v1.md#canonical-json).

**Circuit breaker** — after 3 consecutive HTTP failures the module pauses
outbound requests for 30 s to let the backend recover.

**Client channel / console channel** — the transport where the client prints
`[CLIENTBRIDGE:1] {…}` lines into the player’s log.

**Console line** — one event printed by the client: fixed prefix plus canonical
JSON envelope.

**Cursor** — a position in the event sequence. `seq` is monotonic; `since` is
exclusive. Used by REST pages and WebSocket replay.

**Envelope** — the standard event wrapper: `id`, `type`, `at`, `scope`,
`origin`, `data` (+ `session` in HTTP for player events).

**Event** — one immutable fact published by the game (a biome change, an aura
roll). Not a command.

**Event bus** — the in-process fan-out in the reference API that copies stored
events to WebSocket subscriber queues.

**Fan-out** — delivering one event to many subscribers.

**Flush** — the periodic operation that takes queued events and POSTs them.

**Game ID / `universeId` / `placeId`** — Roblox identifiers included in HTTP
batches; `0` in Studio.

**Heartbeat** — the WebSocket ping sent every ~15 s; also the 30 s session
cleanup task in the module.

**HMAC-SHA256** — keyed hash used by `signingKey` to authenticate a batch. See
[signature](06-protocol-v1.md#signature).

**HTTP channel** — the transport where the server batches events and POSTs them
to an external endpoint.

**Idempotent** — repeating the operation has no additional effect. Event IDs
make ingestion idempotent.

**Kill switch** — `ClientBridge.setEnabled(false)`; stops new events (console
and HTTP) and pauses HTTP flushing.

**Metrics** — counters returned by `ClientBridge.metrics()`; see
[API reference](03-api-reference.md#metrics-and-audit-shapes).

**Opt-in** — the player’s explicit decision to create a session and share their
player-scoped HTTP events.

**Opt-out** — revoking that decision; the token dies immediately.

**Origin** — `"server"` (trusted) or `"client"` (reported by a client, not
trusted).

**PII** — personally identifiable information; the redactor blocks common
patterns and keys.

**PolicyService** — Roblox service used to decide whether a player may see
external links. The module’s opt-in gate uses
`AllowedExternalLinkReferences`.

**Protocol version** — the wire version (`:1` in the console tag, `"v": 1` in
HTTP). See [Versioning](17-versioning.md).

**Redaction** — removing or refusing data before transmission: forbidden keys,
long strings, PII patterns. See
[Security](09-security-privacy.md#what-is-removed-redaction).

**Replay buffer** — the reference API’s bounded in-memory history used for
`since` cursors. Default 20,000 events / 1 hour; gone on restart.

**Replay (signature)** — in `strict_replay` mode, an exact duplicate batch gets
`409` instead of idempotent success.

**Ring buffer** — a fixed-size queue that drops the oldest entry when full; used
by audit, the HTTP queue (10k) and the API replay buffer.

**Schema** — the per-event declaration of fields, types and scope. See
[Event schema](05-event-schema.md).

**Scope** — `"server"` (everyone) or `"player"` (one player).

**`secretName`** — name of a Creator Hub secret whose value becomes the
`Authorization` header.

**Sequence (`seq`)** — monotonic event number assigned by the reference API on
ingestion; used as a cursor.

**Session** — one opt-in lifetime: a token, its player, creation/last-seen and
expiry.

**Session token** — `cb_` + 32 lowercase hex; random bearer credential for one
player’s private events.

**`signingKey`** — HMAC key configured in the game server Script and the
backend; never transmitted.

**Spec** — one field declaration inside a schema (`"string:24"`, `"number"`,
`"integer"`, `"boolean"`, an enum table).

**`studioPolicyBypass`** — Studio-only flag that skips the policy gate so local
HTTP testing is possible. Ignored outside Studio.

**Subscriber** — one connected WebSocket client in the reference API.

**`transports`** — the `{ console, http }` switches in `start`.

**TTL (`session.ttlSec`)** — how long an opt-in token remains valid (default 24
hours).

**Universe** — a Roblox experience (`universeId`); a place (`placeId`) belongs to
it.

**WebSocket stream** — `/stream` on the reference API; live JSON events over a
persistent connection. See [Live streaming](18-streaming.md).

**WSS** — WebSocket over TLS; required in production.

**`X-ClientBridge-Version`** — HTTP request header carrying the protocol version
(`1`).

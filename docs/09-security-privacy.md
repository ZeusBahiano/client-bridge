# 09 — Security & privacy

ClientBridge handles player-adjacent data, so its defaults assume the data is
sensitive. This page explains the threat model, what the module removes, what
the backend does, and what remains **your** responsibility.

- [Design principles](#design-principles)
- [Threat model](#threat-model)
- [What is removed (redaction)](#what-is-removed-redaction)
- [Consent and the policy gate](#consent-and-the-policy-gate)
- [Tokens and secrets](#tokens-and-secrets)
- [The backend](#the-backend)
- [Player-facing privacy](#player-facing-privacy)
- [What game developers must not do](#what-game-developers-must-not-do)
- [Incident response](#incident-response)
- [Compliance notes](#compliance-notes)

## Design principles

1. **Read-only.** No inbound command channel, no remote control.
2. **Data minimisation.** Only declared fields leave; obvious PII is refused.
3. **Consent.** Player-scoped HTTP data requires an explicit opt-in, gated by
   Roblox policy.
4. **Least privilege.** Tokens are per-player, random, expiring and revocable.
5. **No persistence by default.** The reference backend keeps events in RAM
   only; history is a consumer concern.
6. **Fail closed on privacy, fail open on availability.** A redaction failure
   blocks the event; an HTTP outage never breaks the game.

## Threat model

| Actor | Wants | Mitigation |
|---|---|---|
| The player | to know and control what is shared | opt-in/out, token display, revocation |
| The tool author | to read a player's session | token possession; unguessable and revocable |
| A malicious tool | to read another player's data | tokens are per player; no username auth; no enumeration API |
| A modified client | to forge trusted events | client events are tagged `origin = "client"` and can be disabled |
| A network attacker | to read/modify batches | HTTPS; optional HMAC signature |
| The backend operator | to run a safe service | no disk, rate limits, redaction of logs, bind guard |
| A rogue game developer | to exfiltrate PII | redactor + policy rules + this documentation |

Out of scope: a compromised Roblox account, a compromised server Script, or a
backend operator who chooses to persist data. No client-side protocol can fix a
malicious server owner.

## What is removed (redaction)

Before an event is encoded, `Redactor` scans **every key and value**:

### Forbidden keys

At any nesting level, these keys are rejected:

```text
userid, user_id, username, displayname, display_name,
email, phone, cpf, address, password, token, auth, cookie,
chat, message, discord, ip, latitude, longitude
```

Example: `{ username = "zeus" }` → `forbidden_field:username at $.username`.

### Patterns in string values

| Pattern | Example | Rejection reason |
|---|---|---|
| e-mail | `a@b.com` | `pii_email` |
| URL | `https://...` | `pii_url` |
| Discord tag | `user#1234` | `pii_discord_tag` |
| IPv4 | `192.168.0.1` | `pii_ipv4` |
| CPF | `123.456.789-00` | `pii_cpf` |
| phone | `(11) 91234-5678` | `pii_phone` |
| international phone | `+55 11 91234-5678` | `pii_phone_intl` |

### Length

Any string longer than **64 characters** is rejected (`text_too_long`), even if
the schema allows more. This blocks free-text dumps and chat logs.

### Types

Nested tables/functions/userdata inside `data` are rejected by validation
before redaction; JSON primitives only.

### What is **not** automatically removed

- a player’s username if you put it in an allowed field name such as `player`
  (the redactor is name- and pattern-based, not a semantic classifier);
- game-specific identifiers you invent (account ids, trade ids, etc.);
- data in fields that merely *look* harmless but are personal to your game.

That is why the schema and this page exist: **you** decide what is in an event.
The redactor is a safety net, not a privacy officer.

## Consent and the policy gate

Player-scoped HTTP data requires two things:

1. **Explicit opt-in** by the player (`ClientBridge.optIn`).
2. **Roblox policy eligibility** — `PolicyService` must report at least one
   allowed external link reference for that player.

If either fails, no token exists and no player events are queued. There is no
hidden fallback that sends data anyway.

The opt-in is per session:

| Event | Effect |
|---|---|
| opt-in | new token (previous one revoked), 24 h default TTL |
| opt-out | token revoked instantly; client forgets it |
| player leaves | token revoked; policy cache cleared |
| expiry | server rejects it; the client also sees `expiresAt` |
| server restart | tokens are lost; players opt in again |

Tools cannot list tokens or enumerate sessions. A token is only obtainable from
the player who owns it.

## Tokens and secrets

Four different secrets exist; do not reuse them.

| Secret | Where it lives | Who has it | Rotation |
|---|---|---|---|
| Session token `cb_…` | server memory, player's UI | player + tools they choose | new opt-in / TTL |
| Creator Hub secret (`secretName`) | Creator Hub → Secrets | game server + backend | replace secret + backend |
| `signingKey` | server Script + backend config | game server + backend | coordinated deploy |
| backend `admin_token` | backend config | operator | restart with a new value |

Rules:

- never put any of these in a LocalScript, a public module, a log, or a
  screenshot;
- the session token is a bearer credential: anyone with it reads that session;
- `signingKey` authenticates payloads but does not encrypt them — HTTPS does
  that;
- rotate after a leak; revoke sessions with `optOut`/`DELETE`.

## The backend

The reference API applies these defaults (all configurable):

| Control | Behaviour |
|---|---|
| Bind address | `127.0.0.1`; public bind requires auth or signature |
| POST auth | optional shared token; `Authorization` compared in constant time |
| Signatures | optional HMAC-SHA256 over canonical JSON |
| Admin feed | disabled unless `admin_token` is set |
| Rate limits | writes 6,000/min/IP, reads 3,000/min |
| Body limits | 1 MiB total, 500 events/batch, 8 KiB/event |
| Logging | method, redacted path, status, timing; **no bodies, headers or tokens** |
| Storage | RAM only; no file, no database, no backup |
| CORS | read endpoints allow any origin (safe: no cookies/credentials) |
| Shutdown | SIGINT/SIGTERM closes the listener and sockets |

What the backend does **not** do:

- authenticate Roblox itself (use auth token/signature);
- validate schema semantics beyond protocol shape;
- store or forward anything after its buffer window;
- guarantee delivery to a disconnected tool.

### If you expose it publicly

- Put Caddy/nginx in front for TLS and body/rate limits.
- Use `wss://` for streams; `https://` for REST/POST.
- Keep `admin_token` long and separate.
- Monitor `429`, `413`, and WebSocket handshake failures.
- Redact `token` query parameters in proxy logs.
- Do not enable `--allow-insecure` in production.

## Player-facing privacy

A suggested notice (adapt to your game and jurisdiction):

> **What is shared, when you opt in.** If you choose to connect an external tool,
> this game sends a small set of game events (for example, biome changes and your
> aura rolls) to the tool server you selected. The data contains only the fields
> listed in the game's event list; it never contains your username, user ID,
> e-mail, chat messages, IP address or payment data. Your access code is random,
> can be revoked at any time from the game menu, and expires after 24 hours. The
> tool server is operated by [operator] and their privacy policy applies. If you
> do not opt in, no per-player data leaves the game.

Give the player:

- a clear button and explanation before opt-in;
- the token in a copyable field;
- a visible “disconnect / stop sharing” action (`optOut`);
- a way to ask the tool to delete data (`DELETE /sessions/{token}`).

## What game developers must not do

- Do not rebrand ClientBridge as “Roblox …” or imply endorsement.
- Do not use it to move chat logs, usernames, user IDs, inventory of real-money
  items, or any personal data.
- Do not use client-emitted events as authoritative; set
  `allowClientOrigin = false` if you do not need them.
- Do not opt players in automatically, on join, or through a hidden toggle.
- Do not block gameplay behind opt-in (consent must be free).
- Do not use the bridge to automate gameplay, inject input, read memory, or
  bypass anti-cheat — that is not what this project is, and it would violate
  Roblox’s rules.
- Do not persist player-scoped data longer than your privacy notice says.

## Incident response

If a token or key leaks:

| Leak | Immediate action |
|---|---|
| session token | `ClientBridge.optOut(player)` or `DELETE /sessions/{token}`; a new opt-in creates a new token |
| Creator Hub secret | rotate the secret in Creator Hub; the module reads it on every flush |
| `signingKey` | deploy a new key to the backend and the game together |
| backend admin token | restart the backend with a new value |
| whole API exposed | `ClientBridge.setEnabled(false)` on the game side; revoke tokens; take the API down; review logs |

The kill switch stops new outbound events immediately (including queued ones)
without stopping the game.

## Compliance notes

This is engineering guidance, not legal advice.

- **Consent:** opt-in is explicit; keep a record of how it was presented.
- **Minimisation:** declare the smallest schema that supports the feature.
- **Retention:** the reference backend has none by default. If you add a sink,
  define a retention period and delete on request.
- **Access/deletion:** `DELETE /sessions/{token}` erases the buffered session;
  a persistent consumer must implement its own deletion.
- **Children:** `PolicyService` is the gate Roblox provides; do not attempt to
  work around it.
- **Third parties:** the tool operator is a separate data controller. Tell the
  player who receives the data.

Next: [Roblox rules](10-roblox-tos.md).

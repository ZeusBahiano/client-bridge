# Security policy

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Use GitHub's private vulnerability reporting on this repository
(“Security” tab → “Report a vulnerability”), or contact the maintainer
[@ZeusBahiano](https://github.com/ZeusBahiano) privately.

Include:

- affected component (Luau module, reference API, docs/protocol);
- a minimal reproduction or proof of concept;
- impact assessment (what an attacker gains);
- suggested fix, if you have one.

You will get an acknowledgement as soon as possible. Please allow time for a
fix and a coordinated disclosure before publishing details.

## Scope

In scope:

- bypassing opt-in or the `PolicyService` gate;
- reading another player's session events with a token that is not theirs;
- PII leaking through the redactor or the HTTP/stream layers;
- signature bypass or canonical-JSON confusion;
- remote code execution / path traversal / injection in the reference API;
- denial of service in the module or the reference backend;
- secrets leaking into logs, errors or responses.

Out of scope:

- a compromised Roblox account or a malicious server Script;
- a backend operator who deliberately persists data;
- exploits against Roblox itself, Byfron/Hyperion, or other games;
- social engineering of the maintainer or players;
- issues only present in an outdated fork.

## Design guarantees

These are properties, not features — a report that shows otherwise is a
security bug:

- **No inbound channel.** Tools cannot send commands to the game.
- **Opt-in.** Player-scoped HTTP events are queued only for players with an
  active, policy-approved session.
- **No user identifiers.** `UserId` never leaves the server; `username`-keyed
  data is blocked by the redactor.
- **Signed payloads.** When `signingKey` is set, the reference API rejects
  bodies whose HMAC does not match.
- **No persistence.** The reference API stores events in RAM only; a restart
  erases them.
- **Token isolation.** A WebSocket opened with token A never receives player
  events for token B.

## Secrets

Never commit:

- Creator Hub secrets;
- `signingKey`;
- backend `auth_token` / `admin_token`;
- session tokens from real players.

Rotate anything that leaks; see
[docs/09-security-privacy.md](docs/09-security-privacy.md#incident-response).

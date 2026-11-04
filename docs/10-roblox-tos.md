# 10 — Roblox rules (ToS, Community Standards, HTTP)

> **Not legal advice.** Roblox updates its rules; read the current
> [Terms of Use](https://en.help.roblox.com/hc/en-us/articles/115004647846),
> [Community Standards](https://about.roblox.com/community-standards), the
> [Creator Third-Party App Policy](https://en.help.roblox.com/hc/en-us/articles/37924211313044)
> and the [HttpService docs](https://create.roblox.com/docs/cloud-services/http-service)
> before shipping. This page explains how ClientBridge maps to those rules and
> what remains the developer’s responsibility.

- [The short version](#the-short-version)
- [What ClientBridge does — and does not do](#what-clientbridge-does--and-does-not-do)
- [Macros, automation and “cheating”](#macros-automation-and-cheating)
- [External links and off-platform data](#external-links-and-off-platform-data)
- [HttpService rules](#httpservice-rules)
- [Third-party software rules](#third-party-software-rules)
- [Naming and branding](#naming-and-branding)
- [Data protection](#data-protection)
- [Compliant vs non-compliant usage](#compliant-vs-non-compliant-usage)
- [Checklist before publishing](#checklist-before-publishing)

## The short version

ClientBridge itself is designed to sit on the safe side of the rules:

- it only **reads facts the game chooses to publish**;
- it never injects code, reads memory, or automates input;
- player data requires **explicit opt-in** and Roblox’s **external-link policy
  gate**;
- obvious personal data is **removed before transmission**;
- the transport is Roblox’s own **HttpService**, used as documented.

What it cannot do is make an unsafe tool safe, or make auto-play allowed. A
macro that automates gameplay is a separate decision by the player/tool author,
and can violate Roblox rules and individual game rules regardless of how it
learns about the game state.

## What ClientBridge does — and does not do

| Does | Does not |
|---|---|
| lets server code publish validated events | read process memory or game files |
| prints lines in the player’s own log | inject DLLs, scripts or input |
| POSTs to a developer-configured HTTPS endpoint | bypass Byfron/Hyperion or any anti-cheat |
| delivers per-player data after opt-in | give tools a command channel into the game |
| removes PII-like data before sending | guarantee that every game-defined field is harmless |

The last point matters: a field named `account` containing a real account id is
still personal data even if the redactor does not recognise it. You own the
schema; treat it as a data-export decision.

## Macros, automation and “cheating”

Roblox prohibits exploiting, unauthorized modification, and software that gives
an unfair advantage. It also prohibits instructing others to violate the rules.
Game owners may additionally ban automation in their own experience.

Where a read-only bridge fits:

- **Publishing information** (biome changed, aura rolled) is ordinary game
  telemetry. Many games already print this to the console; ClientBridge gives it
  a stable, documented format.
- **Automating gameplay** (auto-clicking, auto-rolling, input injection, AFK
  farming) is not performed by ClientBridge and is not endorsed by it. Tools
  that do that are responsible for their own compliance.
- **Reading memory or injecting code** is an exploit; ClientBridge explicitly
  does not do it and should never be extended to do so.

The project’s position: give legitimate tools a sanctioned data channel so they
do not need exploits. That reduces the incentive to break the rules, but it does
not make every use of the data compliant.

### For game developers

- Decide whether tools/automation fit your game. You can expose only harmless
  data, rate-limit, or not enable HTTP at all.
- Do not publish data that gives a competitive advantage in ways your own rules
  prohibit.
- Do not use macros for your own benefit in ways that mislead players.
- State your policy and link it where players can see it (the privacy notice in
  [Security](09-security-privacy.md#player-facing-privacy) is a starting point).

### For tool authors

- Do not automate input unless the game allows it.
- Do not resell player data.
- Do not ask players to disable security features.
- Do not misrepresent the tool as “official Roblox”.

## External links and off-platform data

Roblox restricts how experiences direct players off-platform, especially minors.
ClientBridge’s HTTP channel is an external link in effect, so it uses Roblox’s
own gate:

```lua
PolicyService:GetPolicyInfoForPlayerAsync(player).AllowedExternalLinkReferences
```

If the list is empty, opt-in is refused. The module does not expose a bypass for
published games (`studioPolicyBypass` only works inside Studio).

Additionally:

- the player must actively choose to enable it; no automatic opt-in;
- no rewards may be conditioned on opting in (consent must be free);
- the tool’s destination must be disclosed (what it is, who runs it);
- do not use the channel to sell, trade, or move Robux/items off-platform;
- do not use it to contact minors outside Roblox.

## HttpService rules

Roblox’s documentation allows generic HTTP requests from the server, with
conditions that this project follows:

| Rule | How ClientBridge respects it |
|---|---|
| Only server-side requests | the module only calls `HttpService` from server code |
| HTTPS for production | the module rejects non-HTTPS endpoints outside Studio |
| No PII to third parties | redactor + opt-in + declared schema |
| Handle failures gracefully | queue, retries, circuit breaker, never break the game |
| Respect rate limits | 400 req/min budget by default + configurable |
| Secrets not exposed to clients | Creator Hub secrets / server-only config |
| Do not abuse external services | batching + retry on later flushes + circuit breaker |

Roblox requires games using HTTP to be able to handle failures; the module does
so by design (events drop after retries; the game keeps running).

## Third-party software rules

Roblox’s third-party app rules constrain what an external app may do with Roblox
data. In practice, for this project:

- use official interfaces (HTTP/WebSocket APIs you operate), not unofficial
  scraping of Roblox endpoints;
- do not collect Roblox account credentials or cookies;
- do not present the app as a Roblox product;
- comply with the Roblox branding guidelines if you mention Roblox at all;
- do not use the data to facilitate rule-breaking (e.g., scams).

ClientBridge never asks for Roblox credentials. The only credential it creates
is the session token.

## Naming and branding

- Do **not** name your product, server or fork “Roblox …”; Roblox is strict
  about its name and logo.
- Do not imply endorsement by Roblox or by the game.
- “ClientBridge” is the project name; forks should pick their own name.
- See Roblox’s
  [Name and Logo Community Usage Guidelines](https://en.help.roblox.com/hc/en-us/articles/115001708126).

## Data protection

Even where Roblox permits a transfer, privacy law may add requirements. At a
minimum:

- collect only what the feature needs (declare a narrow schema);
- tell the player what is sent and to whom;
- support deletion (`optOut`, `DELETE /sessions/{token}`, consumer-side delete);
- keep retention short (the reference backend keeps hours, not months);
- secure the backend (TLS, auth, no public admin feed);
- do not send children’s data to unknown third parties.

## Compliant vs non-compliant usage

| Usage | Assessment |
|---|---|
| Game publishes biome changes; players read them locally | low risk; local console only |
| Opted-in player connects a tracker that reads their own rolls | low risk; consent + policy gate |
| Global public stream of world events | low risk; no player data |
| Sending usernames/UserIds “for convenience” | high risk; avoid |
| Auto-opt-in on join | violates consent; do not |
| Using the bridge to auto-play/auto-click | tool-side ToS risk; not endorsed |
| Reading memory/injecting to obtain the same data | exploit; prohibited |
| Selling player data or directing minors off-platform | prohibited |

## Checklist before publishing

- [ ] HTTP is enabled only if the feature needs it.
- [ ] Endpoint is HTTPS and owned by you.
- [ ] Player flow is explicit opt-in with a visible explanation.
- [ ] `studioPolicyBypass` is `false`.
- [ ] Schema contains no usernames, UserIds, chat, e-mail, IPs, tokens.
- [ ] `allowClientOrigin` reflects your trust model.
- [ ] Privacy notice and a way to opt out/delete exist.
- [ ] Tools follow your game rules and Roblox rules.
- [ ] You have read the current Roblox policy pages (they change).

Next: [Troubleshooting](11-troubleshooting.md) or
[Security & privacy](09-security-privacy.md).

# 12 — FAQ

Short answers; each links to the detailed page.

## General

**What is ClientBridge?**
A read-only bridge that lets a Roblox experience publish typed events to the
player’s console and (opt-in) to an external API. See
[Concepts](02-concepts.md).

**Does it read memory, inject code, or bypass anti-cheat?**
No. It only publishes data the game itself chooses to emit. It cannot read
memory or inject anything, and it is not an exploit.

**Is it allowed by Roblox?**
The design follows Roblox’s rules: server-only `HttpService`, explicit opt-in,
the `PolicyService` external-link gate, no PII, no automation. Rules change and
macros are a separate topic — read [Roblox rules](10-roblox-tos.md).

**Does it work on mobile/console?**
The module is plain Luau and runs anywhere the game runs. The console channel
depends on where the client writes logs; the HTTP channel works regardless of
platform.

**Can a tool send commands to the game?**
No. There is no inbound channel, by design.

**Does it work in multiple games at once?**
Yes. Declare the same event names and scopes; consumers can tell games apart
with `universeId`/`placeId` (HTTP) or the log source (console).

**Why is the console line so verbose?**
It carries a unique `id`, `at`, `type`, `scope`, `origin` and `data` so a tool
needs no other context. It is still small (~200–400 bytes).

**How fast is it?**
Console: the next client frame. HTTP: `http.flushSec` (default 5 s; set 1 for
real-time) plus network. WebSocket fan-out: a few milliseconds. Benchmarks in
[Reference API](08-python-api.md#performance-and-scaling).

**Why implement SHA-256/HMAC in Luau?**
Because signatures must be produced inside the game server, and there is no
built-in `HttpService` signing API. It is tested against standard vectors in the
test suite.

## Installation

**Rojo or manual?**
Rojo is easier and creates the `RemoteEvent` automatically. Manual works too;
follow the tree exactly. See
[Getting started](01-getting-started.md#install).

**I get `missing Remote`.**
Create a **RemoteEvent** named `Remote` inside `ReplicatedStorage.ClientBridge`.

**Do I need a server Script?**
Yes: `ClientBridge.start` must run on the server. A LocalScript alone cannot
create sessions or send HTTP.

**Do I need the client bootstrap?**
For the console channel, yes. The bootstrap is what runs on the client and
requires the facade; the facade’s `Client.luau` registers the Remote listener
that prints the lines and delivers `onEvent` callbacks. Without it, server
events reach the client Remote but nothing prints.

## HTTP channel

**Why opt-in?**
Player-scoped data is personal; Roblox’s external-link rules require consent and
the policy gate. Server-scoped (public) events do not require opt-in.

**Why can’t I identify players by username?**
Usernames are public, so they cannot be a credential for a private stream. The
session token authorizes; `?name=` is a display label. See
[Identity](18-streaming.md#identity-token-vs-username).

**Can the game still target a specific player?**
Yes: `Players:FindFirstChild("Name")` → `ClientBridge.emitTo(player, …)`. The
API routes the event to that player’s token stream.

**Where does the player find the token?**
The game UI shows it after `optIn` (`ClientBridge.getSession()` on the client).
The module never prints it to the console by default.

**How long is a token valid?**
The game-side token lives 24 hours by default (`session.ttlSec`) from opt-in and
is revoked early by `optOut`, by leaving the game, or by a new opt-in. The
backend copy expires after its own `session_ttl_hours` (also 24 h, from
first-seen) or when `DELETE /sessions/{token}` is called; `optOut` alone does not
erase backend data.

**What if the player never opts in?**
Server-scope events still go to HTTP (no session) and to the console. Their
private events are console-only.

**Does HTTP replace the console channel?**
No, they are independent. You can run both (default when an endpoint is set),
console-only, or HTTP-only.

**Do I need a database?**
No. The reference API is in-memory. Persist on the consumer side if you need
history across restarts.

**What happens when the backend restarts?**
The replay buffer is empty; live subscribers keep working after reconnecting.
Tools should reconnect with a cursor and de-duplicate.

**Can I run several API instances?**
The reference event bus is in-process. Multiple instances need sticky routing or
a shared bus (Redis/NATS); see
[scaling](18-streaming.md#scaling).

**Is HTTP data encrypted?**
HTTPS/WSS protects it in transit. The optional HMAC signature authenticates the
payload but does not encrypt it.

## Tools and macros

**Where do tools get events?**
Two options: tail the player’s log (console channel,
[guide](14-console-consumers.md)) or connect to the API (`/stream` or REST).
The player supplies the token for private events.

**Do tools need to parse the game’s own prints?**
No — that is the point. They parse ClientBridge’s stable format or consume the
stream JSON.

**How do I avoid duplicate events?**
De-duplicate by `id` (all channels) or `seq` (stream/REST).

**How do I read history?**
`GET /sessions/{token}/events?since=…` while the event is still in the backend’s
replay buffer. Older than that is gone by design.

**Can I build a public tracker?**
Yes: subscribe to the global stream (no token). Never scrape private data.

**Is using a macro allowed?**
Automating gameplay is governed by Roblox and by each game’s rules; ClientBridge
neither performs nor endorses automation. See
[Roblox rules](10-roblox-tos.md#macros-automation-and-cheating).

## Troubleshooting

**Nothing appears.**
Check `start` return values, `metrics()`, `audit()`, the bootstrap script, and
whether you are looking at the client console. See
[Troubleshooting](11-troubleshooting.md).

**`optIn` returns nil.**
Not started, or the policy gate refused (normal in Studio unless
`studioPolicyBypass = true`).

**Signature errors only sometimes.**
Canonical JSON drift — numbers/Unicode. Use the test vector in
[Protocol v1](06-protocol-v1.md#signature).

**Why is my event blocked?**
The exact reason is returned; see
[rejection reasons](11-troubleshooting.md#schema-and-redaction-rejection).

## Project

**License?**
MIT.

**Can I fork it?**
Yes. Do not use Roblox’s name/branding; pick your own name for a fork.

**How do I contribute?**
See [CONTRIBUTING](../CONTRIBUTING.md).

**Where do I report a security issue?**
See [SECURITY](../SECURITY.md) — please do not open a public issue for
vulnerabilities.

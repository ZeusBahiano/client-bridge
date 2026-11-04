# 01 — Getting started

**Goal:** have the Roblox client print a real ClientBridge event in your
Developer Console in under ten minutes. No backend, no HTTP, no account.

## What ClientBridge is

A small read-only bridge between a Roblox experience and external tools:

```
your game (server)  ──emit──>  ClientBridge  ──console line──>  player's log  ──>  macro/tool
                                          └──HTTP batch (opt-in)──>  your API  ──>  tool
```

Games emit events. Tools read them. There is no inbound channel: external
programs can never send a command back.

## Requirements

| Thing | Version / note |
|---|---|
| Roblox Studio | any recent version |
| Rojo (recommended) | 7.x, only if you use the project file |
| A place you can edit | Play Solo is enough for the console channel |
| Python (optional) | 3.9+ — only for the HTTP backend in `api/` |

You do **not** need to enable HTTP requests for the console channel.

## Install

### Option A — Rojo (recommended)

The repository ships `default.project.json`. With Rojo 7:

```bash
rojo serve
```

Then connect from the Rojo plugin in Studio (Plugins → Rojo → Connect). You
get:

| Roblox location | Contents |
|---|---|
| `ReplicatedStorage.ClientBridge` | facade module + `Client`, `Config`, `Shared/*`, `Remote` |
| `ServerScriptService.ClientBridge_Server` | server-side modules |
| `StarterPlayer.StarterPlayerScripts.ClientBridge_Client` | client bootstrap |

Do not rename any of those four things; the module resolves them by name.

### Option B — manual copy

1. In **ReplicatedStorage**, create a **ModuleScript** named `ClientBridge` and
   paste the source of `src/ClientBridge/init.luau` into it.
2. Parent these **inside** that ModuleScript (a ModuleScript can hold children):
   - `Client.luau` → ModuleScript `Client`
   - `Config.luau` → ModuleScript `Config`
   - the `Shared` folder → Folder `Shared` with ModuleScripts `Json`,
     `Redactor`, `Schema`
   - a **RemoteEvent** named `Remote`
3. In **ServerScriptService**, create a Folder `ClientBridge_Server` and add
   ModuleScripts: `Server`, `Console`, `Http`, `Session`, `Crypto`, `Policy`,
   `Audit` (sources in `src/Server/`).
4. In **StarterPlayer → StarterPlayerScripts**, create a **LocalScript** (any
   name, e.g. `ClientBridge_Client`) containing
   [src/client-bootstrap.client.luau](../src/client-bootstrap.client.luau).

The final tree must look like:

```
ReplicatedStorage
└── ClientBridge                (ModuleScript, src/ClientBridge/init.luau)
    ├── Client                  (ModuleScript)
    ├── Config                  (ModuleScript)
    ├── Remote                  (RemoteEvent)
    └── Shared                  (Folder)
        ├── Json                (ModuleScript)
        ├── Redactor            (ModuleScript)
        └── Schema              (ModuleScript)
ServerScriptService
└── ClientBridge_Server         (Folder)
    ├── Server, Console, Http, Session, Crypto, Policy, Audit
StarterPlayer.StarterPlayerScripts
└── ClientBridge_Client         (LocalScript)
```

> Rojo users get `Remote` automatically from `default.project.json`. Manual
> installers must create it — forgetting it is the most common install error
> (`ClientBridge: missing ClientBridge_Server` or `missing Remote`).

## Your first event

Create a **Script** in `ServerScriptService` (for example `Main.server.luau`):

```lua
local ClientBridge = require(game.ReplicatedStorage.ClientBridge)

local ok, err = ClientBridge.start({
	events = {
		biome = {
			scope = "server",
			data = { name = "string:24", endsAt = "number" },
		},
	},
})

if not ok then
	warn("ClientBridge failed to start: " .. tostring(err))
	return
end

ClientBridge.emit("biome", {
	name = "Glitch",
	endsAt = os.time() + 300,
})
```

Press **Play** (F5), open the client's Developer Console (F9) and look for:

```
[CLIENTBRIDGE:1] {"at":1762000000,"data":{"endsAt":1762000300,"name":"Glitch"},"id":"...","origin":"server","scope":"server","type":"biome"}
```

The JSON keys are alphabetically sorted on purpose: it makes the line stable
and easy to parse. The value after `[CLIENTBRIDGE:1] ` is a complete
[event envelope](06-protocol-v1.md#event-envelope).

### Where did the line come from?

1. The server called `emit`, which validated the payload.
2. The module built an envelope `{id,type,at,scope,origin,data}`.
3. It sent the line through the `Remote` to every client (server scope).
4. The bootstrap LocalScript printed the line in the player's console/log.

That is the whole console channel. It needs nothing but Studio.

## Verify the installation

The repository includes a test module. In Studio's **Command Bar** (View →
Command Bar) while **not** playing:

```lua
local T = require(game.ServerScriptService.ClientBridge_Tests)
for _, r in ipairs(T.run()) do
    print((r.ok and "PASS " or "FAIL ") .. r.name .. (r.detail and (" :: " .. r.detail) or ""))
end
```

The test module is not installed by Rojo by default. Copy
[`tests/Tests.luau`](../tests/Tests.luau) into `ServerScriptService` as a
ModuleScript named `ClientBridge_Tests` first. Expected result: every line
starts with `PASS`. See [Testing](15-testing.md) for details.

## Next: player events

Player events are delivered only to the player they belong to:

```lua
ClientBridge.emitTo(player, "roll", {
	aura = "Chromatic",
	rarity = 75000000,
})
```

with a declared schema `roll = { scope = "player", data = { aura = "string:32", rarity = "number" } }`.

## Next: the HTTP channel (optional)

The console channel is local — a tool must read the player's log file. The
HTTP channel instead delivers events to **your** server, so tools can be
web-based or run anywhere. It is opt-in and requires:

- `Allow HTTP Requests` enabled in Game Settings → Security;
- an HTTPS endpoint (plain `http://127.0.0.1` is allowed **only in Studio**);
- a per-player opt-in, plus the Roblox age gate.

The repository includes a ready-to-run Python backend, so you can test the
whole flow locally:

```
cd api
python clientbridge_api.py
```

Then in the game:

```lua
ClientBridge.start({
	endpoint = "http://127.0.0.1:8787/clientbridge",
	transports = { console = true, http = true },
	events = {
		biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
	},
})
```

Full walkthrough: [HTTP mode](07-http-mode.md). Backend guide:
[Reference API](08-python-api.md).

## Common first-run problems

| Symptom | Likely cause | Fix |
|---|---|---|
| `ClientBridge: missing ClientBridge_Server` | folder not in ServerScriptService | see [manual copy](#option-b--manual-copy) |
| `missing Remote` | RemoteEvent not created | add `Remote` inside `ReplicatedStorage.ClientBridge` |
| No line in the console | bootstrap LocalScript missing, or console not opened | check StarterPlayerScripts, press F9 |
| `invalid spec` on start | typo in a schema, e.g. `"string<24>"` | use `"string:24"` |
| Event blocked with `forbidden_field` | key like `username` in the payload | remove it; see [redaction](09-security-privacy.md#what-is-removed-redaction) |
| Nothing happens in Studio with `optIn` | Studio players have no allowed external links | set `studioPolicyBypass = true` (Studio only) |

More: [Troubleshooting](11-troubleshooting.md).

## Where to go next

- Understand the words: [Concepts](02-concepts.md)
- Learn every call: [API reference](03-api-reference.md)
- Read the exact protocol: [Protocol v1](06-protocol-v1.md)

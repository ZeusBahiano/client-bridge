# Client Bridge

A tiny, read-only bridge between a Roblox experience and external tools.

Games emit events, tools read them. No exploits, no memory reading, no client injection.

## Why

AFK/RNG games live and die by their tools. Most of them scrape the client log, or worse.
This module gives them a documented, versioned channel instead.

## Two channels

**Console** — always on, zero setup. The client prints one line per event:

```
[CLIENTBRIDGE:1] {"at":1762000000,"data":{"name":"Glitch"},"id":"...","origin":"server","scope":"server","type":"biome"}
```

**HTTP** — opt-in. The server batches events and POSTs them to *your* endpoint.
The player opts in, gets a `cb_...` token, and pastes it into the tool.

Both are one-way. The module never accepts commands.

## Install

Rojo: use the included `default.project.json`.

Manual: copy `src/ClientBridge` to `ReplicatedStorage`, `src/Server` to
`ServerScriptService`, and `src/client-bootstrap.client.luau` to `StarterPlayerScripts`.

## Usage

```lua
local ClientBridge = require(game.ReplicatedStorage.ClientBridge)

ClientBridge.start({
    events = {
        biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
    },
})

ClientBridge.emit("biome", { name = "Glitch", endsAt = os.time() + 300 })
```

That is all for the console channel.

For HTTP, point it at your endpoint:

```lua
ClientBridge.start({
    endpoint = "https://api.example.com/clientbridge",
    secretName = "CB_TOKEN", -- Creator Hub -> Secrets (bound to a domain)
})
```

## API

| Call | What it does |
|---|---|
| `start(config)` | boots the module |
| `emit(type, data)` | server-wide event |
| `emitTo(player, type, data)` | per-player event |
| `optIn(player)` / `optOut(player)` | enables/disables HTTP for that player |
| `getSession(player)` | current token |
| `metrics()` | counters and queue size |
| `setEnabled(bool)` | kill switch |

Client side: `emit`, `onEvent`, `optIn`, `optOut`, `getSession`.

## Defaults that keep you out of trouble

- only the fields you declare in `events` are sent
- e-mails, phone numbers, IPs, URLs and long free text are dropped
- `UserId` never leaves the server
- player events only go out after opt-in
- HTTPS only (localhost allowed in Studio)
- 400 requests/min budget per server

## Notes

- Enable **Allow HTTP Requests** in Game Settings if you use the HTTP channel.
- Not affiliated with Roblox. Please don't name your fork "Roblox Client Bridge" —
  their naming rules are strict about it.

## License

MIT

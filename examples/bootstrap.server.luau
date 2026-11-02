--!strict
-- Copy this into ServerScriptService and adjust.
-- With no endpoint the module only prints to the client console. Zero infra.

local ClientBridge = require(game.ReplicatedStorage.ClientBridge)

local started, err = ClientBridge.start({
	-- endpoint = "https://api.example.com/clientbridge",
	-- secretName = "CB_TOKEN", -- Creator Hub -> Secrets
	events = {
		biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
		roll = { scope = "player", data = { aura = "string:32", rarity = "number" } },
	},
})

if not started then
	warn("ClientBridge did not start: " .. tostring(err))
	return
end

ClientBridge.emit("biome", { name = "Glitch", endsAt = os.time() + 300 })

-- Call this from your UI button:
-- ClientBridge.optIn(player)

-- Per-player events:
-- ClientBridge.emitTo(player, "roll", { aura = "Chromatic", rarity = 75000000 })

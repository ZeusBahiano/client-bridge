--!strict
-- Client side: sends events up, prints ready-made lines, exposes hooks for game UI.

local Client = {}

local remote = script.Parent:WaitForChild("Remote")
local listeners: { (any) -> () } = {}
local currentSession: string? = nil
local sessionExpiresAt: number? = nil

remote.OnClientEvent:Connect(function(message: any)
	if type(message) ~= "table" then
		return
	end

	if message.kind == "event" then
		if type(message.line) == "string" then
			print(message.line)
		end
		for _, callback in ipairs(listeners) do
			task.spawn(callback, message.envelope)
		end
	elseif message.kind == "session" then
		currentSession = message.token
		sessionExpiresAt = message.expiresAt
	end
end)

function Client.emit(typeName: string, data: any)
	remote:FireServer({ kind = "emit", type = typeName, data = data })
end

function Client.onEvent(callback: (any) -> ()): () -> ()
	table.insert(listeners, callback)
	return function()
		local index = table.find(listeners, callback)
		if index then
			table.remove(listeners, index)
		end
	end
end

function Client.optIn()
	remote:FireServer({ kind = "optin" })
end

function Client.optOut()
	remote:FireServer({ kind = "optout" })
end

function Client.getSession(): (string?, number?)
	return currentSession, sessionExpiresAt
end

return Client

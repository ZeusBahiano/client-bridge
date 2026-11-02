--!strict
-- Single entry point. The same require works on the server and on the client.

local RunService = game:GetService("RunService")

local ClientBridge = {
	VERSION = "1.0.0",
	PROTOCOL = 1,
}

if RunService:IsServer() then
	local ServerScriptService = game:GetService("ServerScriptService")
	local serverFolder = ServerScriptService:WaitForChild("ClientBridge_Server", 15)
	if not serverFolder then
		error("ClientBridge: missing ClientBridge_Server in ServerScriptService")
	end
	local Server = require(serverFolder:WaitForChild("Server"))

	ClientBridge.start = Server.start
	ClientBridge.stop = Server.stop
	ClientBridge.emit = Server.emit
	ClientBridge.emitTo = Server.emitTo
	ClientBridge.optIn = Server.optIn
	ClientBridge.optOut = Server.optOut
	ClientBridge.isOptedIn = Server.isOptedIn
	ClientBridge.getSession = Server.getSession
	ClientBridge.metrics = Server.metrics
	ClientBridge.audit = Server.audit
	ClientBridge.setEnabled = Server.setEnabled
else
	local Client = require(script.Client)

	ClientBridge.emit = Client.emit
	ClientBridge.onEvent = Client.onEvent
	ClientBridge.optIn = Client.optIn
	ClientBridge.optOut = Client.optOut
	ClientBridge.getSession = Client.getSession
end

return ClientBridge

--!strict
-- Orchestrates everything: validate, tag origin, fan out to console (client) and HTTP (opt-in).

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local HttpService = game:GetService("HttpService")
local RunService = game:GetService("RunService")

local Json = require(ReplicatedStorage.ClientBridge.Shared.Json)
local Schema = require(ReplicatedStorage.ClientBridge.Shared.Schema)
local Redactor = require(ReplicatedStorage.ClientBridge.Shared.Redactor)
local Defaults = require(ReplicatedStorage.ClientBridge.Config)

local Console = require(script.Parent.Console)
local Http = require(script.Parent.Http)
local Session = require(script.Parent.Session)
local Audit = require(script.Parent.Audit)
local Policy = require(script.Parent.Policy)

local Server = {}

local state = {
	started = false,
	enabled = true,
	config = nil :: any,
	specs = {} :: any,
	remote = nil :: any,
	heartbeat = nil :: thread?,
	remoteConnection = nil :: RBXScriptConnection?,
	playerRemovingConnection = nil :: RBXScriptConnection?,
	stats = { emitted = 0, blocked = 0, console = 0, http = 0 },
	lastError = nil :: string?,
}

local function mergeConfig(user: any): any
	local config = {}
	for key, value in pairs(Defaults) do
		config[key] = value
	end
	config.transports = { console = Defaults.transports.console, http = Defaults.transports.http }
	config.http = {}
	for key, value in pairs(Defaults.http) do
		config.http[key] = value
	end
	config.session = { ttlSec = Defaults.session.ttlSec }

	if type(user) == "table" then
		for key, value in pairs(user) do
			if key == "transports" and type(value) == "table" then
				for subKey, subValue in pairs(value) do
					config.transports[subKey] = subValue
				end
			elseif key == "http" and type(value) == "table" then
				for subKey, subValue in pairs(value) do
					config.http[subKey] = subValue
				end
			elseif key == "session" and type(value) == "table" then
				for subKey, subValue in pairs(value) do
					config.session[subKey] = subValue
				end
			else
				config[key] = value
			end
		end
	end

	local explicitHttp = type(user) == "table"
		and type(user.transports) == "table"
		and user.transports.http ~= nil
	if config.endpoint and not explicitHttp then
		config.transports.http = true
	end

	return config
end

local function buildEnvelope(typeName: string, data: any, scope: string, origin: string): any
	return {
		id = HttpService:GenerateGUID(false),
		type = typeName,
		at = os.time(),
		scope = scope,
		origin = origin,
		data = data,
	}
end

local function validatePayload(typeName: any, data: any): (boolean, string?)
	local ok, reason = Schema.validateEvent(typeName, data, state.specs)
	if not ok then
		return false, reason
	end
	local okRedactor, reasonRedactor = Redactor.check(data)
	if not okRedactor then
		return false, reasonRedactor
	end
	return true, nil
end

local function dispatch(envelope: any, player: Player?)
	if state.config.transports.console then
		local lineOk, line = Console.line(envelope)
		if lineOk and line then
			if envelope.scope == "server" then
				pcall(function()
					state.remote:FireAllClients({ kind = "event", line = line, envelope = envelope })
				end)
			elseif player then
				pcall(function()
					state.remote:FireClient(player, { kind = "event", line = line, envelope = envelope })
				end)
			end
			state.stats.console += 1
		end
	end

	if state.config.transports.http and Http.isConfigured() then
		if envelope.scope == "server" then
			Http.enqueue(envelope, nil)
			state.stats.http += 1
		elseif player then
			local token = Session.get(player)
			if token then
				Http.enqueue(envelope, token)
				state.stats.http += 1
			end
		end
	end
end

local function emitInternal(typeName: string, data: any, scope: string, origin: string, player: Player?): (boolean, string?)
	if not state.started then
		return false, "not_started"
	end
	if not state.enabled then
		return false, "disabled"
	end
	if type(data) ~= "table" then
		return false, "invalid_table"
	end

	local ok, reason = validatePayload(typeName, data)
	if not ok then
		state.stats.blocked += 1
		Audit.record("blocked", { type = typeName, reason = reason })
		return false, reason
	end

	local envelope = buildEnvelope(typeName, data, scope, origin)
	local encodedOk, encoded = pcall(Json.encode, envelope)
	if not encodedOk or type(encoded) ~= "string" then
		state.stats.blocked += 1
		return false, "encode_error"
	end
	if #encoded > state.config.maxEventBytes then
		state.stats.blocked += 1
		Audit.record("blocked", { type = typeName, reason = "event_too_large" })
		return false, "event_too_large"
	end

	state.stats.emitted += 1
	dispatch(envelope, player)
	return true, nil
end

function Server.start(userConfig: any): (boolean, string?)
	if state.started then
		return true
	end

	local okCompile, compiled, compileError = Schema.compile(
		type(userConfig) == "table" and userConfig.events or nil
	)
	if not okCompile then
		state.lastError = compileError
		Audit.record("start_fail", { error = compileError })
		return false, compileError
	end

	state.config = mergeConfig(userConfig)
	state.specs = compiled

	local remote = ReplicatedStorage.ClientBridge:WaitForChild("Remote", 15)
	if not remote then
		state.lastError = "missing Remote"
		return false, "missing Remote"
	end
	state.remote = remote

	Http.configure({
		endpoint = state.config.endpoint,
		secretName = state.config.secretName,
		signingKey = state.config.signingKey,
		batchSize = state.config.http.batchSize,
		flushSec = state.config.http.flushSec,
		budgetPerMinute = state.config.http.budgetPerMinute,
		maxRetries = state.config.http.maxRetries,
	})

	local httpEnabled = false
	if state.config.transports.http and state.config.endpoint then
		if not Http.isConfigured() then
			-- configure() stored the precise reason (for example an invalid
			-- scheme); Http.start() would only say "endpoint missing".
			local metrics = Http.metrics()
			Audit.record("http_disabled", { reason = metrics.lastError or "invalid_endpoint" })
		else
			local okStart, startReason = Http.start()
			httpEnabled = okStart
			if not okStart then
				Audit.record("http_disabled", { reason = startReason })
			end
		end
	end

	state.remoteConnection = remote.OnServerEvent:Connect(function(player: Player, payload: any)
		if type(payload) ~= "table" then
			return
		end
		if payload.kind == "emit" then
			if state.config.allowClientOrigin then
				emitInternal(payload.type, payload.data, "player", "client", player)
			end
		elseif payload.kind == "optin" then
			Server.optIn(player)
		elseif payload.kind == "optout" then
			Server.optOut(player)
		end
	end)

	state.playerRemovingConnection = Players.PlayerRemoving:Connect(function(player)
		local token = Session.get(player)
		if token then
			Http.dropSession(token)
		end
		Session.revoke(player)
		Policy.forget(player)
	end)

	state.started = true
	state.enabled = true
	state.heartbeat = task.spawn(function()
		while state.started do
			task.wait(30)
			Session.cleanup()
		end
	end)

	Audit.record("start", { http = httpEnabled })
	return true
end

function Server.stop()
	state.started = false
	Http.stop()
	if state.heartbeat then
		task.cancel(state.heartbeat)
		state.heartbeat = nil
	end
	if state.remoteConnection then
		state.remoteConnection:Disconnect()
		state.remoteConnection = nil
	end
	if state.playerRemovingConnection then
		state.playerRemovingConnection:Disconnect()
		state.playerRemovingConnection = nil
	end
	Audit.record("stop")
end

function Server.emit(typeName: string, data: any): (boolean, string?)
	return emitInternal(typeName, data, Schema.scopeOf(typeName, state.specs), "server", nil)
end

function Server.emitTo(player: Player, typeName: string, data: any): (boolean, string?)
	return emitInternal(typeName, data, "player", "server", player)
end

function Server.optIn(player: Player): (string?, number?)
	if not state.started then
		return nil, nil
	end
	-- A token is a bearer credential: only hand it to the player themselves.
	-- The PolicyService gate is what makes this opt-in compliant with Roblox
	-- external-link rules for accounts that are allowed to see external links.
	local allowed = Policy.canUseExternalTools(player)
	if not allowed and state.config.studioPolicyBypass and RunService:IsStudio() then
		-- Local development only: Studio test players report no allowed
		-- external links, which would otherwise make opt-in impossible to test.
		allowed = true
		Audit.record("optin_policy_bypass", { studio = true })
	end
	if not allowed then
		Audit.record("optin_blocked", { reason = "policy" })
		return nil, nil
	end
	local token, expiresAt = Session.create(player, state.config.session.ttlSec)
	if state.remote then
		state.remote:FireClient(player, { kind = "session", token = token, expiresAt = expiresAt })
	end
	Audit.record("optin")
	return token, expiresAt
end

function Server.optOut(player: Player)
	local token = Session.get(player)
	Session.revoke(player)
	local dropped = Http.dropSession(token)
	if state.remote then
		state.remote:FireClient(player, { kind = "session", token = nil })
	end
	Audit.record("optout", { dropped = dropped })
end

function Server.isOptedIn(player: Player): boolean
	return Session.get(player) ~= nil
end

function Server.getSession(player: Player): (string?, number?)
	return Session.get(player)
end

function Server.setEnabled(value: boolean)
	state.enabled = value
	Http.setEnabled(value)
end

function Server.metrics(): any
	return {
		version = 1,
		started = state.started,
		enabled = state.enabled,
		stats = state.stats,
		http = Http.metrics(),
		sessions = Session.count(),
		audits = Audit.total(),
		lastError = state.lastError,
	}
end

function Server.audit(): { any }
	return Audit.list(100)
end

function Server.isStarted(): boolean
	return state.started
end

return Server

--!strict
-- Queue + batch + budget (400/min) + retry + circuit breaker.
-- Retries happen on the next flush; there is no separate delayed backoff.
-- Auth comes from the Creator Hub secrets store.

local HttpService = game:GetService("HttpService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Json = require(ReplicatedStorage.ClientBridge.Shared.Json)
local Crypto = require(script.Parent.Crypto)
local Audit = require(script.Parent.Audit)

local Http = {}

local function isEndpointAllowed(url: any): boolean
	if type(url) ~= "string" then
		return false
	end
	if string.sub(url, 1, 8) == "https://" then
		return true
	end
	-- Local testing inside Studio only. Match the host exactly so that
	-- "http://127.0.0.1.evil.example" is not mistaken for loopback.
	if RunService:IsStudio() then
		local allowedHosts = { "http://127.0.0.1", "http://localhost" }
		for _, base in ipairs(allowedHosts) do
			if url == base then
				return true
			end
			local prefix = base .. ":"
			if string.sub(url, 1, #prefix) == prefix then
				return true
			end
			prefix = base .. "/"
			if string.sub(url, 1, #prefix) == prefix then
				return true
			end
		end
	end
	return false
end

local state = {
	configured = false,
	endpoint = nil :: string?,
	secretName = nil :: string?,
	signingKey = nil :: string?,
	batchSize = 100,
	flushSec = 5,
	budgetPerMinute = 400,
	maxRetries = 4,
	queue = {} :: { any },
	usedThisMinute = 0,
	minuteStart = os.time(),
	sent = 0,
	failed = 0,
	dropped = 0,
	lastError = nil :: string?,
	running = false,
	loop = nil :: thread?,
	circuitUntil = 0,
	consecutiveFailures = 0,
	enabled = true,
}

function Http.configure(config: any)
	if config.endpoint and not isEndpointAllowed(config.endpoint) then
		state.endpoint = nil
		state.configured = false
		state.lastError = "endpoint must be https (http only for localhost/Studio)"
		return
	end
	state.endpoint = config.endpoint
	state.secretName = config.secretName
	state.signingKey = config.signingKey
	state.batchSize = config.batchSize or 100
	state.flushSec = config.flushSec or 5
	state.budgetPerMinute = config.budgetPerMinute or 400
	state.maxRetries = config.maxRetries or 4
	state.configured = state.endpoint ~= nil
end

function Http.isConfigured(): boolean
	return state.configured
end

function Http.isAvailable(): boolean
	if not state.enabled or not state.configured then
		return false
	end
	local ok, enabled = pcall(function()
		return HttpService.HttpEnabled
	end)
	return ok and enabled == true
end

function Http.setEnabled(value: boolean)
	state.enabled = value
end

local function budgetAllows(): boolean
	local current = os.time()
	if current - state.minuteStart >= 60 then
		state.minuteStart = current
		state.usedThisMinute = 0
	end
	return state.usedThisMinute < state.budgetPerMinute
end

local function buildEvents(batch: { any }): { any }
	local events = {}
	for _, item in ipairs(batch) do
		local copy = {}
		for key, value in pairs(item.envelope) do
			copy[key] = value
		end
		if item.session then
			copy.session = item.session
		end
		table.insert(events, copy)
	end
	return events
end

function Http.enqueue(envelope: any, session: string?)
	if not state.configured then
		return false
	end
	table.insert(state.queue, { envelope = envelope, session = session, attempts = 0 })
	if #state.queue > 10000 then
		table.remove(state.queue, 1)
		state.dropped += 1
	end
	return true
end

function Http.flush()
	if not state.endpoint then
		return
	end
	-- Kill switch: Server.setEnabled(false) must stop outbound traffic even
	-- though the flush loop is still alive.
	if not state.enabled then
		return
	end
	if os.clock() < state.circuitUntil then
		return
	end

	while #state.queue > 0 do
		if not budgetAllows() then
			state.lastError = "budget_exceeded"
			return
		end

		local batch = {}
		local count = math.min(#state.queue, state.batchSize)
		for index = 1, count do
			batch[index] = table.remove(state.queue, 1)
		end

		local body = {
			v = 1,
			universeId = game.GameId,
			placeId = game.PlaceId,
			sentAt = os.date("!%Y-%m-%dT%H:%M:%SZ"),
			events = buildEvents(batch),
		}

		if state.signingKey then
			local canonical = Json.encode({
				v = body.v,
				universeId = body.universeId,
				placeId = body.placeId,
				sentAt = body.sentAt,
				events = body.events,
			})
			body.sig = Crypto.base64url(Crypto.hmacSha256(state.signingKey :: string, canonical))
		end

		local headers: any = {
			["Content-Type"] = "application/json",
			["X-ClientBridge-Version"] = "1",
		}

		if state.secretName then
			local ok, secret = pcall(function()
				return HttpService:GetSecret(state.secretName :: string)
			end)
			if ok and secret then
				headers.Authorization = secret
			end
		end

		local ok, response = pcall(function()
			return HttpService:RequestAsync({
				Url = state.endpoint :: string,
				Method = "POST",
				Headers = headers,
				Body = Json.encode(body),
			})
		end)

		state.usedThisMinute += 1

		if ok and response and response.Success then
			state.sent += #batch
			state.consecutiveFailures = 0
		else
			state.failed += #batch
			state.consecutiveFailures += 1
			if ok and response then
				state.lastError = tostring(response.StatusCode) .. " " .. tostring(response.StatusMessage)
			else
				state.lastError = "request_error"
			end

			local canRetry = true
			for _, item in ipairs(batch) do
				item.attempts += 1
				if item.attempts > state.maxRetries then
					canRetry = false
				end
			end

			if canRetry then
				for index = #batch, 1, -1 do
					table.insert(state.queue, 1, batch[index])
				end
			else
				state.dropped += #batch
				Audit.record("http_drop", { count = #batch, error = state.lastError })
			end

			if state.consecutiveFailures >= 3 then
				state.circuitUntil = os.clock() + 30
			end
			return
		end
	end
end

function Http.flushAndWait(timeoutSec: number?)
	local deadline = os.clock() + (timeoutSec or 2)
	while #state.queue > 0 and os.clock() < deadline do
		Http.flush()
		task.wait()
	end
end

function Http.start(): (boolean, string?)
	if state.running then
		return true
	end
	if not Http.isAvailable() then
		state.lastError = "HttpService disabled or endpoint missing"
		return false, state.lastError
	end
	state.running = true
	state.loop = task.spawn(function()
		while state.running do
			task.wait(state.flushSec)
			Http.flush()
		end
	end)
	return true
end

function Http.stop()
	state.running = false
	Http.flushAndWait(2)
end

function Http.metrics(): any
	return {
		running = state.running,
		configured = state.configured,
		queue = #state.queue,
		sent = state.sent,
		failed = state.failed,
		dropped = state.dropped,
		lastError = state.lastError,
		usedThisMinute = state.usedThisMinute,
	}
end

function Http.clearQueue()
	state.queue = {}
end

-- Removes queued events belonging to one session token. Called when a player
-- opts out or leaves so nothing private is POSTed after consent is withdrawn.
function Http.dropSession(token: string?): number
	if type(token) ~= "string" or token == "" then
		return 0
	end
	local kept = {}
	local removed = 0
	for _, item in ipairs(state.queue) do
		if item.session == token then
			removed += 1
		else
			table.insert(kept, item)
		end
	end
	state.queue = kept
	return removed
end

return Http

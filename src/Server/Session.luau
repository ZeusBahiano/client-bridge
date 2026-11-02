--!strict
-- Short-lived opt-in token. The token <-> player map never leaves this module.

local HttpService = game:GetService("HttpService")

local Session = {}

local byToken: { [string]: { player: Player, expiresAt: number } } = {}
local byPlayer: { [Player]: string } = {}
local DEFAULT_TTL = 86400

local function now(): number
	return os.time()
end

function Session.create(player: Player, ttlSec: number?): (string, number)
	local ttl = ttlSec or DEFAULT_TTL
	Session.revoke(player)
	local token = "cb_" .. string.gsub(HttpService:GenerateGUID(false), "%-", "")
	local expiresAt = now() + ttl
	byToken[token] = { player = player, expiresAt = expiresAt }
	byPlayer[player] = token
	return token, expiresAt
end

function Session.get(player: Player): (string?, number?)
	local token = byPlayer[player]
	if not token then
		return nil, nil
	end
	local entry = byToken[token]
	if not entry or entry.expiresAt <= now() then
		byToken[token] = nil
		byPlayer[player] = nil
		return nil, nil
	end
	return token, entry.expiresAt
end

function Session.resolve(token: string): (Player?, number?)
	if type(token) ~= "string" then
		return nil, nil
	end
	local entry = byToken[token]
	if not entry then
		return nil, nil
	end
	if entry.expiresAt <= now() then
		byToken[token] = nil
		if byPlayer[entry.player] == token then
			byPlayer[entry.player] = nil
		end
		return nil, nil
	end
	return entry.player, entry.expiresAt
end

function Session.revoke(player: Player)
	local token = byPlayer[player]
	if token then
		byToken[token] = nil
		byPlayer[player] = nil
	end
end

function Session.cleanup()
	local current = now()
	for token, entry in pairs(byToken) do
		if entry.expiresAt <= current then
			if byPlayer[entry.player] == token then
				byPlayer[entry.player] = nil
			end
			byToken[token] = nil
		end
	end
end

function Session.count(): number
	local n = 0
	for _ in pairs(byToken) do
		n += 1
	end
	return n
end

return Session

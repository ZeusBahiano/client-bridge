--!strict
-- Uses PolicyService to decide whether a player may see external links/tools.
-- Roblox has no public age API; the allowed external links list is the official gate.

local PolicyService = game:GetService("PolicyService")

local Policy = {}

local cache: { [Player]: { at: number, allowed: boolean } } = {}
local CACHE_SECONDS = 300

function Policy.info(player: Player): any?
	local ok, info = pcall(function()
		return PolicyService:GetPolicyInfoForPlayerAsync(player)
	end)
	if ok then
		return info
	end
	return nil
end

function Policy.canShowExternalLink(player: Player): boolean
	local cached = cache[player]
	if cached and (os.time() - cached.at) < CACHE_SECONDS then
		return cached.allowed
	end

	local info = Policy.info(player)
	local allowed = false
	if info and type(info.AllowedExternalLinkReferences) == "table" then
		allowed = #info.AllowedExternalLinkReferences > 0
	end

	cache[player] = { at = os.time(), allowed = allowed }
	return allowed
end

function Policy.canUseExternalTools(player: Player): boolean
	return Policy.canShowExternalLink(player)
end

function Policy.forget(player: Player)
	cache[player] = nil
end

return Policy

--!strict
-- Blocks personal data and free text before anything is sent.

local Redactor = {}

local MAX_STRING = 64

local FORBIDDEN_KEYS = {
	userid = true,
	user_id = true,
	username = true,
	displayname = true,
	display_name = true,
	email = true,
	phone = true,
	cpf = true,
	address = true,
	password = true,
	token = true,
	auth = true,
	cookie = true,
	chat = true,
	message = true,
	discord = true,
	ip = true,
	latitude = true,
	longitude = true,
}

local PATTERNS = {
	{ name = "email", pattern = "[%w%.%-_]+@[%w%.%-_]+%.%a%a%a?" },
	{ name = "url", pattern = "https?://" },
	{ name = "discord_tag", pattern = "[%w_%.]+#%d%d%d%d" },
	{ name = "ipv4", pattern = "%d%d?%d?%.%d%d?%d?%.%d%d?%d?%.%d%d?%d?" },
	{ name = "cpf", pattern = "%d%d%d%.%d%d%d%.%d%d%d%-%d%d" },
	{ name = "phone", pattern = "%(%d%d%)%s*%d%d%d%d%d?%-?%d%d%d%d" },
	{ name = "phone_intl", pattern = "%+%d%d%d?%s?%d%d%d%d%d%d%d%d" },
}

local function scanString(s: string): (boolean, string?)
	if #s > MAX_STRING then
		return false, "text_too_long"
	end
	for _, entry in ipairs(PATTERNS) do
		if string.find(s, entry.pattern) then
			return false, "pii_" .. entry.name
		end
	end
	return true, nil
end

function Redactor.scanValue(value: any, path: string?): (boolean, string?)
	local p = path or "$"
	local tv = type(value)

	if tv == "string" then
		local ok, reason = scanString(value)
		if not ok then
			return false, (reason or "pii") .. " at " .. p
		end
		return true, nil
	elseif tv == "number" then
		if value ~= value or value == math.huge or value == -math.huge then
			return false, "invalid_number at " .. p
		end
		return true, nil
	elseif tv == "boolean" then
		return true, nil
	elseif tv == "table" then
		for k, v in pairs(value) do
			if type(k) ~= "string" then
				return false, "non_string_key at " .. p
			end
			if FORBIDDEN_KEYS[string.lower(k)] then
				return false, "forbidden_field:" .. k .. " at " .. p
			end
			local ok, reason = Redactor.scanValue(v, p .. "." .. k)
			if not ok then
				return false, reason
			end
		end
		return true, nil
	end

	return false, "unsupported_type:" .. tv .. " at " .. p
end

function Redactor.check(envelope: any): (boolean, string?)
	return Redactor.scanValue(envelope, "$")
end

function Redactor.maxStringLength(): number
	return MAX_STRING
end

return Redactor

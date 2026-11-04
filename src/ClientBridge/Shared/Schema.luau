--!strict
-- Generic validation plus an optional declarative schema per event type.

local Schema = {}

local TYPE_NAME_PATTERN = "^[a-z0-9_%.%-]+$"
local MAX_TYPE_LENGTH = 32
local MAX_KEYS = 24
local MAX_EVENT_BYTES = 4096

local function trim(s: string): string
	return (string.gsub(s, "^%s*(.-)%s*$", "%1"))
end

local function parseFieldSpec(spec: any): (boolean, any)
	if type(spec) == "string" then
		local normalized = trim(spec)
		if normalized == "string" then
			return true, { kind = "string", max = 64 }
		end
		if normalized == "number" then
			return true, { kind = "number" }
		end
		if normalized == "integer" then
			return true, { kind = "integer" }
		end
		if normalized == "boolean" then
			return true, { kind = "boolean" }
		end
		-- "string:N" form (e.g. string:24); N must be a plain positive integer
		if string.sub(normalized, 1, 7) == "string:" then
			local limitText = trim(string.sub(normalized, 8))
			if string.match(limitText, "^%d+$") then
				local limit = tonumber(limitText)
				if limit and limit >= 1 and limit <= 4096 then
					return true, { kind = "string", max = limit }
				end
			end
		end
		return false, "invalid spec: " .. normalized
	elseif type(spec) == "table" then
		local allowed = {}
		local count = 0
		for key, value in pairs(spec) do
			if value ~= true then
				return false, "enum values must be true"
			end
			allowed[key] = true
			count += 1
		end
		if count == 0 then
			return false, "empty enum"
		end
		return true, { kind = "enum", allowed = allowed }
	end
	return false, "invalid spec"
end

function Schema.compile(events: any): (boolean, any, string?)
	if events == nil then
		return true, {}, nil
	end
	if type(events) ~= "table" then
		return false, nil, "events must be a table"
	end

	local compiled = {}
	for typeName, spec in pairs(events) do
		if
			type(typeName) ~= "string"
			or #typeName == 0
			or #typeName > MAX_TYPE_LENGTH
			or not string.match(typeName, TYPE_NAME_PATTERN)
		then
			return false, nil, "invalid type: " .. tostring(typeName)
		end
		if type(spec) ~= "table" then
			return false, nil, "spec for " .. typeName .. " must be a table"
		end

		local scope = spec.scope or "server"
		if scope ~= "server" and scope ~= "player" then
			return false, nil, "invalid scope on " .. typeName
		end

		local fields = {}
		local fieldCount = 0
		for fieldName, fieldSpec in pairs(spec.data or spec.fields or {}) do
			if type(fieldName) ~= "string" then
				return false, nil, "invalid field on " .. typeName
			end
			local ok, parsed = parseFieldSpec(fieldSpec)
			if not ok then
				return false, nil, tostring(parsed) .. " (" .. typeName .. "." .. fieldName .. ")"
			end
			fields[fieldName] = parsed
			fieldCount += 1
		end

		compiled[typeName] = { scope = scope, fields = fields, fieldCount = fieldCount }
	end

	return true, compiled, nil
end

local function validateField(value: any, field: any): (boolean, string?)
	local kind = field.kind
	if kind == "string" then
		if type(value) ~= "string" then
			return false, "expected string"
		end
		if #value > field.max then
			return false, "string too long"
		end
		return true, nil
	elseif kind == "number" then
		if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then
			return false, "expected finite number"
		end
		return true, nil
	elseif kind == "integer" then
		if type(value) ~= "number" or math.floor(value) ~= value then
			return false, "expected integer"
		end
		return true, nil
	elseif kind == "boolean" then
		if type(value) ~= "boolean" then
			return false, "expected boolean"
		end
		return true, nil
	elseif kind == "enum" then
		if not field.allowed[value] then
			return false, "value not in enum"
		end
		return true, nil
	end
	return false, "unknown kind"
end

function Schema.validateEvent(typeName: any, data: any, compiled: any): (boolean, string?)
	if
		type(typeName) ~= "string"
		or #typeName == 0
		or #typeName > MAX_TYPE_LENGTH
		or not string.match(typeName, TYPE_NAME_PATTERN)
	then
		return false, "invalid type"
	end
	if type(data) ~= "table" then
		return false, "data must be a table"
	end

	local spec = compiled[typeName]
	local keyCount = 0

	for key, value in pairs(data) do
		if type(key) ~= "string" then
			return false, "keys must be strings"
		end
		keyCount += 1

		if spec then
			local field = spec.fields[key]
			if not field then
				return false, "undeclared field: " .. key
			end
			local ok, reason = validateField(value, field)
			if not ok then
				return false, tostring(reason) .. " in " .. key
			end
		else
			if keyCount > MAX_KEYS then
				return false, "too many keys"
			end
			local valueType = type(value)
			if valueType == "string" then
				if #value > 64 then
					return false, "string too long in " .. key
				end
			elseif valueType == "number" then
				if value ~= value or value == math.huge or value == -math.huge then
					return false, "invalid number in " .. key
				end
			elseif valueType ~= "boolean" then
				return false, "unsupported type in " .. key
			end
		end
	end

	return true, nil
end

function Schema.scopeOf(typeName: string, compiled: any): string
	local spec = compiled[typeName]
	if spec and spec.scope then
		return spec.scope
	end
	return "server"
end

function Schema.maxEventBytes(): number
	return MAX_EVENT_BYTES
end

return Schema

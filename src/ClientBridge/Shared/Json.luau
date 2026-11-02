--!strict
-- Deterministic JSON (sorted keys). Used for the wire format and for signing.

local Json = {}

local function escapeString(s: string): string
	local out = {}
	for i = 1, #s do
		local b = string.byte(s, i)
		if b == 34 then
			out[#out + 1] = '\\"'
		elseif b == 92 then
			out[#out + 1] = "\\\\"
		elseif b == 8 then
			out[#out + 1] = "\\b"
		elseif b == 12 then
			out[#out + 1] = "\\f"
		elseif b == 10 then
			out[#out + 1] = "\\n"
		elseif b == 13 then
			out[#out + 1] = "\\r"
		elseif b == 9 then
			out[#out + 1] = "\\t"
		elseif b < 32 then
			out[#out + 1] = string.format("\\u%04x", b)
		else
			out[#out + 1] = string.char(b)
		end
	end
	return table.concat(out)
end

local function isArray(t: { [any]: any }): boolean
	local n = 0
	for k in pairs(t) do
		if type(k) ~= "number" then
			return false
		end
		n += 1
	end
	if n == 0 then
		return false
	end
	for i = 1, n do
		if t[i] == nil then
			return false
		end
	end
	return true
end

local encodeValue

local function encodeTable(t: { [any]: any }, out: { string })
	if isArray(t) then
		out[#out + 1] = "["
		for i = 1, #t do
			if i > 1 then
				out[#out + 1] = ","
			end
			encodeValue(t[i], out)
		end
		out[#out + 1] = "]"
		return
	end

	local keys = {}
	for k in pairs(t) do
		if type(k) ~= "string" then
			error("ClientBridge.Json: object keys must be strings")
		end
		keys[#keys + 1] = k
	end
	table.sort(keys)

	out[#out + 1] = "{"
	for i, k in ipairs(keys) do
		if i > 1 then
			out[#out + 1] = ","
		end
		out[#out + 1] = '"'
		out[#out + 1] = escapeString(k)
		out[#out + 1] = '":'
		encodeValue(t[k], out)
	end
	out[#out + 1] = "}"
end

function encodeValue(v: any, out: { string })
	local tv = type(v)
	if v == nil then
		out[#out + 1] = "null"
	elseif tv == "boolean" then
		out[#out + 1] = if v then "true" else "false"
	elseif tv == "number" then
		if v ~= v or v == math.huge or v == -math.huge then
			out[#out + 1] = "null"
		else
			out[#out + 1] = string.format("%.14g", v)
		end
	elseif tv == "string" then
		out[#out + 1] = '"'
		out[#out + 1] = escapeString(v)
		out[#out + 1] = '"'
	elseif tv == "table" then
		encodeTable(v, out)
	else
		error("ClientBridge.Json: unsupported type " .. tv)
	end
end

function Json.encode(value: any): string
	local out = {}
	encodeValue(value, out)
	return table.concat(out)
end

function Json.decode(text: string): any
	return game:GetService("HttpService"):JSONDecode(text)
end

function Json.isArray(t: any): boolean
	return type(t) == "table" and isArray(t)
end

return Json

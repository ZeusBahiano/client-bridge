--!strict
-- Small ring buffer. Stores metadata only, never field values.

local Audit = {}

local MAX_ENTRIES = 200
local entries: { any } = {}
local head = 0
local total = 0

function Audit.record(kind: string, detail: any?)
	total += 1
	head = (head % MAX_ENTRIES) + 1
	local entry = { at = os.time(), kind = kind }
	if type(detail) == "table" then
		for key, value in pairs(detail) do
			if type(key) == "string" then
				entry[key] = value
			end
		end
	elseif detail ~= nil then
		entry.detail = tostring(detail)
	end
	entries[head] = entry
end

function Audit.list(limit: number?): { any }
	local out = {}
	local max = limit or MAX_ENTRIES
	local count = math.min(#entries, MAX_ENTRIES)
	for index = 1, math.min(max, count) do
		local position = ((head - index) % MAX_ENTRIES) + 1
		if entries[position] then
			table.insert(out, entries[position])
		end
	end
	return out
end

function Audit.total(): number
	return total
end

function Audit.clear()
	entries = {}
	head = 0
	total = 0
end

return Audit

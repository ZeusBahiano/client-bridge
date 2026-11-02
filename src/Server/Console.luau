--!strict
-- Builds the T1 line: "[CLIENTBRIDGE:1] {json}". The CLIENT prints it.

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Json = require(ReplicatedStorage.ClientBridge.Shared.Json)

local Console = {}

local TAG = "[CLIENTBRIDGE:1] "
local MAX_LINE = 8192

function Console.line(envelope: any): (boolean, string?)
	local ok, encoded = pcall(Json.encode, envelope)
	if not ok or type(encoded) ~= "string" then
		return false, "encode_error"
	end
	local line = TAG .. encoded
	if #line > MAX_LINE then
		return false, "line_too_large"
	end
	return true, line
end

function Console.tag(): string
	return TAG
end

return Console

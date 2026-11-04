--!strict
-- No external dependencies. Drop this into ServerScriptService and run:
--   local T = require(game.ServerScriptService.Tests)
--   local results = T.run()

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerScriptService = game:GetService("ServerScriptService")

local Json = require(ReplicatedStorage.ClientBridge.Shared.Json)
local Redactor = require(ReplicatedStorage.ClientBridge.Shared.Redactor)
local Schema = require(ReplicatedStorage.ClientBridge.Shared.Schema)

local Crypto = require(ServerScriptService.ClientBridge_Server.Crypto)
local Console = require(ServerScriptService.ClientBridge_Server.Console)
local Session = require(ServerScriptService.ClientBridge_Server.Session)
local Audit = require(ServerScriptService.ClientBridge_Server.Audit)
local Http = require(ServerScriptService.ClientBridge_Server.Http)
local Server = require(ServerScriptService.ClientBridge_Server.Server)

local Tests = {}

local function run(): { any }
	local results = {}

	local function check(name: string, fn: () -> ())
		local ok, err = pcall(fn)
		table.insert(results, {
			name = name,
			ok = ok,
			detail = (not ok) and tostring(err) or nil,
		})
	end

	local function eq(actual: any, expected: any, label: string?)
		if actual ~= expected then
			error((label or "eq") .. ": expected [" .. tostring(expected) .. "] got [" .. tostring(actual) .. "]", 2)
		end
	end

	local function truthy(value: any, label: string?)
		if not value then
			error((label or "truthy") .. " failed", 2)
		end
	end

	check("json_canonical", function()
		eq(Json.encode({ b = 1, a = 2 }), '{"a":2,"b":1}', "key order")
		eq(Json.encode({ 1, 2, 3 }), "[1,2,3]", "array")
		eq(Json.encode("x"), '"x"', "string")
	end)

	check("json_roundtrip", function()
		local decoded = Json.decode(Json.encode({ a = 1, b = { 2, 3 } }))
		eq(decoded.a, 1, "a")
		eq(decoded.b[2], 3, "b2")
	end)

	check("sha256_vectors", function()
		eq(Crypto.hex(Crypto.sha256("abc")), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "abc")
		eq(
			Crypto.hex(Crypto.sha256("")),
			"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
			"empty"
		)
		eq(
			Crypto.hex(Crypto.sha256("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")),
			"248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
			"two blocks"
		)
	end)

	check("hmac_vector", function()
		eq(
			Crypto.hex(Crypto.hmacSha256("key", "The quick brown fox jumps over the lazy dog")),
			"f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8",
			"hmac"
		)
	end)

	check("base64url", function()
		eq(Crypto.base64url("hello world"), "aGVsbG8gd29ybGQ", "b64")
	end)

	check("redactor_clean", function()
		truthy(Redactor.check({ name = "Glitch", endsAt = 123 }), "clean payload")
	end)

	check("redactor_email", function()
		truthy(not Redactor.check({ note = "mail me at a@b.com" }), "email should be blocked")
	end)

	check("redactor_forbidden_field", function()
		truthy(not Redactor.check({ username = "zeus" }), "username should be blocked")
	end)

	check("redactor_long_text", function()
		truthy(not Redactor.check({ msg = string.rep("a", 80) }), "long text should be blocked")
	end)

	check("schema_compile_validate", function()
		local ok, compiled, err = Schema.compile({
			biome = { scope = "server", data = { name = "string:24", endsAt = "number" } },
		})
		truthy(ok, "compile: " .. tostring(err))
		truthy(Schema.validateEvent("biome", { name = "Glitch", endsAt = 1 }, compiled), "valid event")
		truthy(not Schema.validateEvent("biome", { name = "Glitch", extra = true }, compiled), "extra field should fail")
		truthy(not Schema.validateEvent("weird type!", {}, compiled), "bad type name should fail")
		truthy(Schema.validateEvent("freeform", { a = 1, b = "x" }, compiled), "generic should pass")
		eq(Schema.scopeOf("biome", compiled), "server", "scope")
	end)

	check("console_line", function()
		local ok, line = Console.line({
			id = "x",
			type = "biome",
			at = 1,
			scope = "server",
			origin = "server",
			data = { name = "Glitch" },
		})
		truthy(ok, "line")
		truthy(line, "line string")
		local tag = "[CLIENTBRIDGE:1] "
		eq(string.sub(line, 1, #tag), tag, "tag")
		eq(Json.decode(string.sub(line, #tag + 1)).type, "biome", "type")
	end)

	check("session_cycle", function()
		local fakePlayer: any = { Name = "TestPlayer" }
		local token, expiresAt = Session.create(fakePlayer, 60)
		truthy(string.sub(token, 1, 3) == "cb_", "prefix")
		truthy(expiresAt > os.time(), "expiry")
		eq(Session.get(fakePlayer), token, "get")
		Session.revoke(fakePlayer)
		eq(Session.get(fakePlayer), nil, "revoked")
	end)

	check("session_expiry", function()
		local fakePlayer: any = { Name = "Expired" }
		Session.create(fakePlayer, -5)
		eq(Session.get(fakePlayer), nil, "expired")
	end)

	check("audit_ring", function()
		Audit.clear()
		Audit.record("test", { count = 1 })
		eq(Audit.total(), 1, "total")
		local list = Audit.list(10)
		eq(#list, 1, "list")
		eq(list[1].kind, "test", "kind")
	end)

	check("http_config", function()
		Http.configure({ endpoint = nil })
		truthy(not Http.isConfigured(), "no endpoint")
		Http.configure({ endpoint = "https://example.com/clientbridge", batchSize = 2 })
		truthy(Http.isConfigured(), "with endpoint")
		Http.configure({ endpoint = "ftp://example.com/clientbridge" })
		truthy(not Http.isConfigured(), "invalid scheme must be rejected")
		Http.configure({ endpoint = "http://127.0.0.1.evil.example/cb" })
		truthy(not Http.isConfigured(), "lookalike localhost must be rejected")
		Http.configure({ endpoint = "https://example.com/clientbridge" })
		Http.clearQueue()
		Http.enqueue({ id = "1", type = "x" }, "cb_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
		Http.enqueue({ id = "2", type = "x" }, "cb_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
		eq(Http.metrics().queue, 2, "queue")
		eq(Http.dropSession("cb_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), 1, "drop one session")
		eq(Http.metrics().queue, 1, "queue after drop")
		Http.clearQueue()
		eq(Http.metrics().queue, 0, "queue cleared")
	end)

	check("schema_enum_and_integer", function()
		local ok, compiled, err = Schema.compile({
			roll = {
				scope = "player",
				data = { aura = "string:32", rarity = "integer", tier = { Common = true, Rare = true } },
			},
		})
		truthy(ok, "compile: " .. tostring(err))
		truthy(Schema.validateEvent("roll", { aura = "Cosmic", rarity = 5, tier = "Rare" }, compiled), "valid")
		truthy(not Schema.validateEvent("roll", { aura = "Cosmic", rarity = 5.5, tier = "Rare" }, compiled), "float integer")
		truthy(not Schema.validateEvent("roll", { aura = "Cosmic", rarity = 5, tier = "Mythic" }, compiled), "enum")
		truthy(not Schema.validateEvent("roll", { aura = string.rep("a", 33), rarity = 5, tier = "Rare" }, compiled), "string limit")
	end)

	check("schema_errors", function()
		local ok, _, err = Schema.compile({ ["Bad Type!"] = { data = {} } })
		truthy(not ok, "bad type name")
		truthy(err ~= nil, "error message")
		ok = Schema.compile({ ["ok"] = { data = { name = "string<24>" } } })
		truthy(not ok, "old angle-bracket syntax must be rejected")
		ok = Schema.compile({ ["ok"] = { data = { name = "string:2.5" } } })
		truthy(not ok, "fractional string limit must be rejected")
		ok = Schema.compile({ ["ok"] = { data = { name = "string:1e2" } } })
		truthy(not ok, "exponent string limit must be rejected")
	end)

	check("redactor_url_and_phone", function()
		truthy(not Redactor.check({ link = "join https://evil.example now" }), "url")
		truthy(not Redactor.check({ contact = "+55 98765432" }), "phone")
		truthy(not Redactor.check({ id = "123.456.789-00" }), "cpf")
	end)

	check("json_numbers", function()
		eq(Json.encode({ n = 1.5 }), '{"n":1.5}', "float")
		eq(Json.encode({ n = 300 }), '{"n":300}', "int")
		truthy(not pcall(Json.encode, { [1] = "x", a = 2 }), "mixed table must error")
	end)

	check("server_cycle", function()
		Server.stop() -- make the test independent from whatever started earlier
		Http.clearQueue()
		Audit.clear()
		local ok, err = Server.start({
			events = {
				biome = { scope = "server", data = { name = "string:24" } },
			},
		})
		truthy(ok, "start: " .. tostring(err))
		truthy(Server.isStarted(), "started")

		local emitted, emitErr = Server.emit("biome", { name = "Glitch" })
		truthy(emitted, "emit: " .. tostring(emitErr))

		local blocked, blockReason = Server.emit("biome", { name = "Glitch", username = "x" })
		truthy(not blocked, "should block")
		truthy(blockReason ~= nil, "reason")

		local metrics = Server.metrics()
		truthy(metrics.started, "metric started")
		eq(metrics.stats.emitted, 1, "emitted")
		eq(metrics.stats.blocked, 1, "blocked")

		Server.stop()
		truthy(not Server.isStarted(), "stopped")
	end)

	check("facade", function()
		local ClientBridge = require(ReplicatedStorage.ClientBridge)
		truthy(ClientBridge.VERSION ~= nil, "version")
		truthy(type(ClientBridge.start) == "function", "start")
		truthy(type(ClientBridge.emit) == "function", "emit")
		truthy(type(ClientBridge.metrics) == "function", "metrics")
	end)

	return results
end

Tests.run = run

return Tests

--!strict
-- SHA-256 + HMAC-SHA256 in plain Luau (bit32). Test vectors live in the test suite.

local Crypto = {}

local K = {
	0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
	0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
	0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
	0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
	0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
	0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
	0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
	0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
}

local MASK = 0xFFFFFFFF

local function rrot(x: number, n: number): number
	return bit32.bor(bit32.rshift(x, n), bit32.lshift(x, 32 - n))
end

local function toBytes(s: string): { number }
	local out = table.create(#s)
	for i = 1, #s do
		out[i] = string.byte(s, i)
	end
	return out
end

local function padMessage(bytes: { number }): { number }
	local bitLen = #bytes * 8
	bytes[#bytes + 1] = 0x80
	while (#bytes % 64) ~= 56 do
		bytes[#bytes + 1] = 0
	end
	local high = math.floor(bitLen / 4294967296)
	local low = bitLen % 4294967296
	for shift = 24, 0, -8 do
		bytes[#bytes + 1] = bit32.band(bit32.rshift(high, shift), 0xFF)
	end
	for shift = 24, 0, -8 do
		bytes[#bytes + 1] = bit32.band(bit32.rshift(low, shift), 0xFF)
	end
	return bytes
end

local function processBlock(block: { number }, h0: number, h1: number, h2: number, h3: number, h4: number, h5: number, h6: number, h7: number)
	local w = table.create(64, 0)
	for i = 0, 15 do
		local j = i * 4 + 1
		w[i + 1] = bit32.bor(
			bit32.lshift(block[j], 24),
			bit32.lshift(block[j + 1], 16),
			bit32.lshift(block[j + 2], 8),
			block[j + 3]
		)
	end
	for i = 17, 64 do
		local x = w[i - 15]
		local y = w[i - 2]
		local s0 = bit32.bxor(rrot(x, 7), rrot(x, 18), bit32.rshift(x, 3))
		local s1 = bit32.bxor(rrot(y, 17), rrot(y, 19), bit32.rshift(y, 10))
		w[i] = bit32.band(w[i - 16] + s0 + w[i - 7] + s1, MASK)
	end

	local a, b, c, d, e, f, g, h = h0, h1, h2, h3, h4, h5, h6, h7

	for i = 1, 64 do
		local s1 = bit32.bxor(rrot(e, 6), rrot(e, 11), rrot(e, 25))
		local ch = bit32.bxor(bit32.band(e, f), bit32.band(bit32.bnot(e), g))
		local t1 = bit32.band(h + s1 + ch + K[i] + w[i], MASK)
		local s0 = bit32.bxor(rrot(a, 2), rrot(a, 13), rrot(a, 22))
		local maj = bit32.bxor(bit32.band(a, b), bit32.band(a, c), bit32.band(b, c))
		local t2 = bit32.band(s0 + maj, MASK)

		h = g
		g = f
		f = e
		e = bit32.band(d + t1, MASK)
		d = c
		c = b
		b = a
		a = bit32.band(t1 + t2, MASK)
	end

	return bit32.band(h0 + a, MASK),
		bit32.band(h1 + b, MASK),
		bit32.band(h2 + c, MASK),
		bit32.band(h3 + d, MASK),
		bit32.band(h4 + e, MASK),
		bit32.band(h5 + f, MASK),
		bit32.band(h6 + g, MASK),
		bit32.band(h7 + h, MASK)
end

function Crypto.sha256(message: string): string
	local bytes = padMessage(toBytes(message))
	local h0, h1, h2, h3 = 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a
	local h4, h5, h6, h7 = 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19

	local block = table.create(64, 0)
	for offset = 1, #bytes, 64 do
		for i = 1, 64 do
			block[i] = bytes[offset + i - 1]
		end
		h0, h1, h2, h3, h4, h5, h6, h7 = processBlock(block, h0, h1, h2, h3, h4, h5, h6, h7)
	end

	local parts = { h0, h1, h2, h3, h4, h5, h6, h7 }
	local out = table.create(32, 0)
	local index = 1
	for _, word in ipairs(parts) do
		for shift = 24, 0, -8 do
			out[index] = bit32.band(bit32.rshift(word, shift), 0xFF)
			index += 1
		end
	end
	return string.char(table.unpack(out))
end

local BLOCK_SIZE = 64

local function xorBytes(a: string, b: number): string
	local out = {}
	for i = 1, #a do
		out[i] = string.char(bit32.bxor(string.byte(a, i), b))
	end
	return table.concat(out)
end

function Crypto.hmacSha256(key: string, message: string): string
	local k = key
	if #k > BLOCK_SIZE then
		k = Crypto.sha256(k)
	end
	if #k < BLOCK_SIZE then
		k = k .. string.rep("\0", BLOCK_SIZE - #k)
	end
	local ipad = xorBytes(k, 0x36)
	local opad = xorBytes(k, 0x5C)
	return Crypto.sha256(opad .. Crypto.sha256(ipad .. message))
end

function Crypto.hex(raw: string): string
	return (string.gsub(raw, ".", function(c)
		return string.format("%02x", string.byte(c))
	end))
end

local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

function Crypto.base64url(raw: string): string
	local out = {}
	local i = 1
	while i <= #raw do
		local b1 = string.byte(raw, i)
		local b2 = string.byte(raw, i + 1)
		local b3 = string.byte(raw, i + 2)
		local n = bit32.bor(bit32.lshift(b1, 16), bit32.lshift(b2 or 0, 8), b3 or 0)
		local c1 = bit32.band(bit32.rshift(n, 18), 63) + 1
		local c2 = bit32.band(bit32.rshift(n, 12), 63) + 1
		local c3 = bit32.band(bit32.rshift(n, 6), 63) + 1
		local c4 = bit32.band(n, 63) + 1
		out[#out + 1] = string.sub(B64, c1, c1)
		out[#out + 1] = string.sub(B64, c2, c2)
		if b2 then
			out[#out + 1] = string.sub(B64, c3, c3)
		end
		if b3 then
			out[#out + 1] = string.sub(B64, c4, c4)
		end
		i += 3
	end
	local encoded = table.concat(out)
	encoded = string.gsub(encoded, "%+", "-")
	encoded = string.gsub(encoded, "/", "_")
	return encoded
end

return Crypto

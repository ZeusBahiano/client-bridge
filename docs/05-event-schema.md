# 05 — Event schema

The schema is the contract between the game and everyone reading its events.
It is optional, but every production integration should use it.

- [Why declare a schema](#why-declare-a-schema)
- [Where it lives](#where-it-lives)
- [Event types and scopes](#event-types-and-scopes)
- [Field specs](#field-specs)
- [Enums](#enums)
- [Undeclared types](#undeclared-types)
- [Limits that always apply](#limits-that-always-apply)
- [Worked examples](#worked-examples)
- [Common mistakes](#common-mistakes)
- [Best practices](#best-practices)

## Why declare a schema

```lua
ClientBridge.start({
	events = {
		biome = {
			scope = "server",
			data = {
				name = "string:24",
				endsAt = "number",
			},
		},
	},
})
```

Declaring gives you four things:

| Benefit | What it means |
|---|---|
| **Rejection of drift** | an undeclared field is an error, not silently shipped |
| **Type safety** | `rarity = "lots"` is rejected instead of corrupting a tool’s parser |
| **Size control** | over-long strings and event bloat are caught before sending |
| **Documentation** | the schema *is* the machine-readable description of each event |

Without a schema the generic rules apply (see
[Undeclared types](#undeclared-types)), which are looser and easier to get
wrong.

## Where it lives

`events` is a table passed to `ClientBridge.start`. The keys are event types;
the values are specs:

```lua
events = {
	<typeName> = {
		scope = "server" | "player",   -- optional, default "server"
		data  = { <fieldName> = <fieldSpec>, ... },  -- or `fields`
	},
}
```

- `data` and `fields` are interchangeable aliases; use one per event.
- The schema is compiled once at start. A bad schema makes `start` return
  `false, reason` and nothing runs.
- Fields are compared **exactly**: declaring `name` and `endsAt` means an event
  with `name` and `ends_at` is rejected (`undeclared field: ends_at`).

## Event types and scopes

### Type names

- 1 to 32 characters;
- only lowercase ASCII letters, digits, `.`, `_`, `-`;
- recommended style: short nouns (`biome`, `roll`, `trade`) or namespaced
  (`shop.purchase`, `aura.equipped`).

These are rejected at compile time:

```text
Biome        (uppercase)
biome change (space)
biome!       (punctuation)
""           (empty)
<33 chars>   (too long)
```

### Scope

| Scope | Meaning |
|---|---|
| `"server"` (default) | the event belongs to everyone |
| `"player"` | the event belongs to one player |

`ClientBridge.emit` uses the declared scope. `ClientBridge.emitTo` always sends
as `player`, even when the declared scope says `server` — per-player delivery
can never accidentally become a broadcast.

Client-emitted events (`ClientBridge.emit` in a LocalScript) are always
`player` scope and `client` origin, whatever the schema says.

## Field specs

Each field maps to one of these spec forms:

| Spec | Accepts | Notes |
|---|---|---|
| `"string"` | string, ≤ 64 bytes | effective maximum; see [limits](#limits-that-always-apply) |
| `"string:N"` | string, ≤ N characters | N from 1 to 4096 |
| `"number"` | finite number | integers and decimals; no NaN/∞ |
| `"integer"` | whole number | `math.floor(v) == v` |
| `"boolean"` | `true` / `false` | |
| `{ A = true, B = true }` | one of the listed values | [enum](#enums) |

### `string:N`

```lua
name = "string:24"  -- "Glitch" (6) ok, 25 bytes rejected
```

Rules:

- the **colon** form is the only accepted one; `"string<24>"` is rejected with
  `invalid spec: string<24> (biome.name)`;
- `N` must be a whole number from 1 to 4096 (`"string:2.5"` and
  `"string:1e2"` are rejected);
- whitespace around the spec is trimmed (`" string:24 "` is fine);
- strings are measured in **UTF-8 bytes** (`#s` in Luau), so non-ASCII text
  consumes more than one unit per visible character and also consumes more of
  the 4 KB envelope budget.

### Why the redactor caps at 64 anyway

`Redactor` (privacy) rejects **any** string longer than 64 bytes, even if the
schema allows `string:200`. The effective maximum for a field is therefore
`min(N, 64)` bytes. Use `string:64` or less unless you are prepared for the
redactor to reject longer values.

### Numbers

- `"number"` accepts integers and floats, but not `NaN`, `math.huge` or
  `-math.huge`.
- `"integer"` additionally requires a whole value. Counts, levels and IDs that
  must be whole should use `"integer"`.
- JSON output uses `%.14g`, so keep magnitudes sane; something above 2^53 loses
  precision (same as any JSON number).

### Booleans

`"boolean"` accepts only `true`/`false`. It is useful for flags
(`isShiny`, `isNew`).

## Enums

```lua
events = {
	roll = {
		scope = "player",
		data = {
			aura = "string:32",
			tier = { Common = true, Rare = true, Legendary = true },
		},
	},
}
```

The enum is a table whose **keys are the allowed values** and whose values must
be `true`:

```lua
ClientBridge.emitTo(player, "roll", { aura = "Nova", tier = "Rare" }) -- ok
ClientBridge.emitTo(player, "roll", { aura = "Nova", tier = "Mythic" }) -- rejected
-- reason: value not in enum in tier
```

Rules:

- at least one entry; an empty enum fails at compile time;
- every entry must be `true` (`{ Common = 1 }` is rejected);
- values are compared directly, so strings are the natural choice;
- changing an enum later is a breaking change for consumers — announce it.

## Undeclared types

If you emit a type that is not in `events`, generic validation applies:

| Rule | Limit |
|---|---|
| payload | must be a table |
| keys | strings only |
| key count | at most 24 |
| string values | at most 64 bytes |
| number values | finite |
| boolean values | allowed |
| anything else | rejected (`unsupported type in X`) |

The redactor still runs, so forbidden keys and PII patterns are still blocked.

Generic validation is a safety net, not a contract. Tools cannot rely on the
shape of an undeclared event; declare it as soon as it is public.

## Limits that always apply

Regardless of schema:

| Limit | Value | Enforced by |
|---|---|---|
| string length | 64 bytes | Redactor |
| declared field count | no hard limit (keep it small) | sanity |
| undeclared key count | 24 | Schema |
| serialized envelope | 4096 bytes (`maxEventBytes`) | Server |
| event type length | 32 characters | Schema |
| console line | 8192 bytes | Console |

An event is rejected with `event_too_large` **after** the schema passes if the
canonical JSON of the whole envelope exceeds 4096 bytes. Because of the 64-char
string cap, you would need many fields to reach that.

## Worked examples

### 1. Global biome timer

```lua
events = {
	biome = {
		scope = "server",
		data = {
			name = "string:24",   -- current biome
			endsAt = "number",    -- epoch seconds
		},
	},
}

ClientBridge.emit("biome", { name = "Glitch", endsAt = os.time() + 300 })
```

### 2. Per-player aura roll

```lua
events = {
	roll = {
		scope = "player",
		data = {
			aura = "string:32",
			rarity = "number",
			shiny = "boolean",
		},
	},
}

ClientBridge.emitTo(player, "roll", { aura = "Chromatic", rarity = 75000000, shiny = true })
```

### 3. Purchase with an enum

```lua
events = {
	shop_purchase = {
		scope = "player",
		data = {
			item = "string:32",
			currency = { Coins = true, Robux = true, Gems = true },
			price = "integer",
		},
	},
}

ClientBridge.emitTo(player, "shop_purchase", {
	item = "Lucky Potion",
	currency = "Coins",
	price = 2500,
})
```

### 4. Timer using `integer` and `boolean`

```lua
events = {
	boost = {
		scope = "server",
		data = {
			kind = { Luck = true, Speed = true },
			multiplier = "number",
			endsAt = "integer",
			stackable = "boolean",
		},
	},
}
```

### 5. Client-reported hint

```lua
events = {
	screen = {
		scope = "player",
		data = {
			name = { Inventory = true, Shop = true, Trade = true },
		},
	},
}
-- LocalScript
ClientBridge.emit("screen", { name = "Inventory" })
```

Remember: origin is `client`, so treat it as a hint.

## Common mistakes

| Mistake | Error | Fix |
|---|---|---|
| `"string<24>"` | `invalid spec: string<24> (...)` | use `"string:24"` |
| forgot to declare an extra field | `undeclared field: X` | add it to the schema |
| used `string` for a number | `expected string` | use `"number"` / `"integer"` |
| float where integer is required | `expected integer` | round or use `"number"` |
| enum written as an array | `invalid spec` | `{ Common = true }`, not `{ "Common" }` |
| too-long text | `text_too_long at $.field` | shorten it; no PII dumps |
| PII-looking value | `pii_email at $.x` | do not send it |
| uppercase type | `invalid type` | lowercase names |
| schema is not a table | `spec for X must be a table` | wrap it in `{ ... }` |

## Best practices

1. **One fact per event type.** `biome` and `roll` are separate; do not bundle
   unrelated data into `update`.
2. **Declare every field you send.** Undeclared fields are errors, and errors
   are visible to players as missing events.
3. **Prefer narrow specs.** `string:16` is better than `string:64`; `integer`
   is better than `number` for counts.
4. **Use enums for closed sets.** They make tool code trivial and catch typos.
5. **Never put personal data in `data`.** The redactor will block obvious cases,
   but do not try to smuggle usernames, chat, e-mails or IDs.
6. **Treat the schema as an API.** Removing or renaming a field breaks tools;
   add new fields/types and let old ones live.
7. **Keep the payload small.** A tool may receive thousands per minute; a
   compact payload is a gift to every consumer.
8. **Version semantics with new types.** If `roll` starts meaning something
   else, prefer `roll_v2` over silently changing behaviour.

Next: [Protocol v1](06-protocol-v1.md).

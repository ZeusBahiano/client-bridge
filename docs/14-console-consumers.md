# 14 — Console consumers (tool/macro authors)

The console channel needs no server: the Roblox client prints one line per
event, and a tool reads the player’s local log. This page is for people building
those tools.

- [What you get](#what-you-get)
- [Where the log is](#where-the-log-is)
- [Reading the log](#reading-the-log)
- [Parsing an event](#parsing-an-event)
- [Delivery rules](#delivery-rules)
- [Ordering and duplicates](#ordering-and-duplicates)
- [Reference reader](#reference-reader)
- [Tool etiquette](#tool-etiquette)
- [Limits and pitfalls](#limits-and-pitfalls)

## What you get

When the game enables the console channel (the default), the player’s client
receives and prints:

```text
[CLIENTBRIDGE:1] {"at":1762000000,"data":{"endsAt":1762000300,"name":"Glitch"},"id":"4aa1af3c-...","origin":"server","scope":"server","type":"biome"}
```

One line = one event. The prefix is fixed; the rest is canonical JSON (see
[Protocol v1](06-protocol-v1.md#canonical-json)).

Advantages:

- works with any game using the module, with zero infrastructure;
- no token, no opt-in, no backend;
- local: the tool can run on the player’s machine.

Trade-offs:

- only available while the game is running on that machine;
- tools must locate and tail a log file;
- the log may rotate or be truncated;
- server operators cannot see what tools do.

## Where the log is

The Roblox **client** writes its log to a per-platform folder. Typical paths:

| Platform | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\Roblox\logs` |
| macOS | `~/Library/Logs/Roblox` |
| Linux (Sober) | `~/.var/app/org.vinegarhq.Sober/data/sober/logs` |
| Other launchers | look for the folder the launcher prints/uses; the client’s console output is the same |

Files look like:

```text
0.609.0.123456_20261104T120000Z_Player_ab12c_...log
```

Rules of thumb:

- pick the **newest** `*.log` whose name contains `Player`;
- open it with shared read access;
- follow from the end for new events;
- the file is created when the client starts; an empty folder means the game is
  not running.

## Reading the log

Polling a file is enough:

```python
import time
from pathlib import Path

path = Path(pattern)  # newest player log
position = path.stat().st_size
while True:
    size = path.stat().st_size
    if size < position:          # rotated/truncated
        position = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(position)
        for line in handle:
            handle_line(line)
        position = handle.tell()
    time.sleep(0.25)
```

Better: the included reader does exactly this, with dedupe and auto-detection:

```bash
python examples/console_tail.py                     # newest log, follow
python examples/console_tail.py --log path/to.log   # specific file
python examples/console_tail.py --from-start --json # replay backlog
```

## Parsing an event

```python
import json, re
LINE = re.compile(r"\[CLIENTBRIDGE:1\]\s*(\{.*\})\s*$")

match = LINE.search(line)
if match:
    event = json.loads(match.group(1))
    print(event["type"], event["scope"], event["origin"], event["data"])
```

Rules:

1. Match the **exact** prefix `[CLIENTBRIDGE:1] `.
2. Parse the remainder as JSON; ignore a line that fails (partial write).
3. Use `id` for de-duplication and `at` for ordering.
4. Treat unknown fields as additions (forward compatibility).
5. `origin == "client"` is untrusted; `origin == "server"` comes from server
   code.

JavaScript:

```js
const LINE = /\[CLIENTBRIDGE:1\]\s*(\{.*\})\s*$/;
const match = line.match(LINE);
if (match) {
  const event = JSON.parse(match[1]);
  handle(event);
}
```

## Delivery rules

| Event scope | Who sees it in the console |
|---|---|
| `server` | every player’s client (all logs) |
| `player` | only that player’s client |

A tool running on the player’s machine therefore sees:

- all public events;
- the player’s own private events;
- **not** other players’ private events.

That is the console channel’s privacy model. If the tool needs other players’
data, that is a server-side/API decision, not a log-reading one.

## Ordering and duplicates

- Lines are appended in the order the client received them.
- `at` is the **server** timestamp (not the client clock), so use it for
  ordering across players.
- A single event has one `id`; if you re-read a file from the start, dedupe by
  `id` to avoid repeats.
- Tailing a rotated log can produce a gap; re-read from the beginning if gaps
  are unacceptable (and dedupe).

## Reference reader

[`examples/console_tail.py`](../examples/console_tail.py) is the reference:

- finds the newest log (preferring names containing `Player`);
- follows it without loading the whole file;
- re-reads from the beginning if the file is truncated;
- when run without `--log`, switches to the newest log after rotation (a fixed
  `--log` path is followed, so restart the reader if that exact file is
  replaced);
- parses and prints `[HH:MM:SS] type (origin) key=value …`;
- `--json` prints the raw envelope;
- `--from-start` replays.

Use it as-is for quick scripts, or copy the parsing logic into your tool.

## Tool etiquette

- **Do not poll faster than needed.** 200–500 ms tail polling is plenty; the
  client writes lines as they arrive.
- **Do not modify the log.** Open read-only.
- **Do not upload a player’s full log.** Extract only ClientBridge lines; the
  log may contain unrelated Roblox output.
- **Respect the game’s rules and Roblox’s.** Reading a line is not the same as
  automating gameplay; see [Roblox rules](10-roblox-tos.md).
- **Handle absence gracefully.** The game may not use ClientBridge, the channel
  may be disabled, or the player may be on a different platform.

## Limits and pitfalls

| Situation | What happens | Handling |
|---|---|---|
| Very long event | server blocks `line_too_large` (>8192 bytes) | should not happen after redaction |
| Log rotation | the file path changes | re-detect the newest `*.log` |
| Client crash | last line may be partial | ignore unparsable lines |
| Two game clients | separate log files | let the user pick the right one |
| Non-ASCII text | UTF-8 in the log | decode as UTF-8 with replacement |
| Logs disabled by launcher | no output | instruct the user; fall back to HTTP channel |
| The tool reads another player's log | privacy violation | never do this |

Next: [Testing](15-testing.md) if you contribute, or back to
[the documentation index](README.md).

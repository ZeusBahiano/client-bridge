#!/usr/bin/env python3
"""
Console-channel reader for ClientBridge.

The console channel needs no server: the Roblox client prints one line per
event and this script tails the local log file, so you can build tools before
touching HTTP.

    python console_tail.py                     # newest log in the default folder
    python console_tail.py --log path/to.log   # a specific file
    python console_tail.py --from-start        # replay everything
    python console_tail.py --json              # raw event JSON

Default log folder: %LOCALAPPDATA%\\Roblox\\logs (Windows).
On macOS: ~/Library/Logs/Roblox
On Linux (Sober): ~/.var/app/org.vinegarhq.Sober/data/sober/logs or the
log path printed by your launcher.

Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

LINE_RE = re.compile(r"\[CLIENTBRIDGE:1\]\s*(\{.*\})\s*$")


def default_log_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Roblox" / "logs"
    mac = Path.home() / "Library" / "Logs" / "Roblox"
    if mac.is_dir():
        return mac
    return Path.home() / ".var" / "app" / "org.vinegarhq.Sober" / "data" / "sober" / "logs"


def newest_log(folder: Path) -> Optional[Path]:
    if not folder.is_dir():
        return None
    files = [path for path in folder.glob("*.log") if path.is_file()]
    if not files:
        return None
    # Roblox client logs contain "_Player_"; prefer them over unrelated logs.
    players = [path for path in files if "Player" in path.name]
    candidates = players or files
    return max(candidates, key=lambda path: path.stat().st_mtime)


def format_event(event: dict) -> str:
    stamp = time.strftime("%H:%M:%S", time.localtime(int(event.get("at", 0))))
    data = event.get("data") or {}
    fields = "  ".join(f"{key}={value!r}" for key, value in data.items())
    return f"[{stamp}] {event.get('type', '?'):<12} ({event.get('origin', '?')}) {fields}"


def _handle_line(line: str, seen: set, raw_json: bool) -> None:
    match = LINE_RE.search(line.rstrip("\r\n"))
    if not match:
        return
    try:
        event = json.loads(match.group(1))
    except json.JSONDecodeError:
        return
    event_id = str(event.get("id", ""))
    if event_id and event_id in seen:
        return
    if event_id:
        seen.add(event_id)
    print(json.dumps(event, ensure_ascii=False) if raw_json else format_event(event), flush=True)


def follow(path: Path, from_start: bool, raw_json: bool, fixed: bool) -> int:
    seen: set[str] = set()
    handle = None
    current: Optional[Path] = None
    opened_before = False
    try:
        while True:
            candidate = path if fixed else newest_log(path.parent)
            if candidate is None:
                time.sleep(0.25)
                continue
            if handle is None or candidate != current:
                if handle is not None:
                    handle.close()
                current = candidate
                try:
                    handle = current.open("r", encoding="utf-8", errors="replace")
                except OSError:
                    handle = None
                    time.sleep(0.25)
                    continue
                # First open honours --from-start; a rotated file is read from
                # the beginning so no new events are lost.
                if from_start or opened_before:
                    handle.seek(0)
                else:
                    handle.seek(0, os.SEEK_END)
                opened_before = True
            try:
                size = current.stat().st_size
                if size < handle.tell():
                    handle.seek(0)  # file was truncated; re-read and dedupe
            except OSError:
                handle.close()
                handle = None
                current = None
                continue

            line = handle.readline()
            if not line:
                time.sleep(0.25)
                continue
            _handle_line(line, seen, raw_json)
    finally:
        if handle is not None:
            handle.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Tail [CLIENTBRIDGE:1] lines from a Roblox client log.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--log", help="log file (default: newest in the Roblox folder)")
    parser.add_argument("--from-start", action="store_true", help="parse existing content")
    parser.add_argument("--json", action="store_true", help="print raw event JSON")
    args = parser.parse_args(argv)

    path = Path(args.log) if args.log else newest_log(default_log_dir())
    if not path or not path.is_file():
        print(f"log file not found. Pass --log, or start the game first.", file=sys.stderr)
        print(f"looked in: {default_log_dir()}", file=sys.stderr)
        return 1

    print(f"tailing {path}  (Ctrl+C to stop)", file=sys.stderr)
    try:
        return follow(path, args.from_start, args.json, fixed=bool(args.log))
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())

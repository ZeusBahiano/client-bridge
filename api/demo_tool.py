#!/usr/bin/env python3
"""
Small viewer for the ClientBridge reference API.

It polls one opt-in session token and prints the events as they arrive, which
is exactly what a macro/tool would do. No dependencies, only the standard
library.

    python demo_tool.py --token cb_0123456789abcdef0123456789abcdef

Press Ctrl+C to stop. Use --once to fetch the current backlog and exit.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict

DEFAULT_URL = "http://127.0.0.1:8787"


def fetch(url: str, token: str, since: int, limit: int) -> Dict[str, Any]:
    endpoint = f"{url.rstrip('/')}/sessions/{token}/events?since={since}&limit={limit}"
    request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def format_event(event: Dict[str, Any]) -> str:
    stamp = time.strftime("%H:%M:%S", time.localtime(int(event.get("at", 0))))
    data = event.get("data") or {}
    if isinstance(data, dict):
        fields = "  ".join(f"{key}={value!r}" for key, value in data.items())
    else:
        fields = repr(data)
    origin = event.get("origin", "?")
    return f"[{stamp}] {event.get('type', '?'):<12} ({origin}) {fields}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Read ClientBridge events for one session token.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--token", required=True, help="session token (cb_...)")
    parser.add_argument("--url", default=DEFAULT_URL, help="API base URL")
    parser.add_argument("--interval", type=float, default=2.0, help="poll interval")
    parser.add_argument("--since", type=int, default=0, help="start cursor")
    parser.add_argument("--limit", type=int, default=200, help="events per request")
    parser.add_argument("--once", action="store_true", help="fetch once and exit")
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    args = parser.parse_args(argv)

    if not args.token.startswith("cb_") or len(args.token) != 35:
        print("warning: token does not look like cb_<32 hex>", file=sys.stderr)

    cursor = args.since
    print(f"reading {args.url}  token={args.token[:6]}...  (Ctrl+C to stop)")
    try:
        while True:
            try:
                page = fetch(args.url, args.token, cursor, args.limit)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    print("this token is unknown, expired or was revoked.", file=sys.stderr)
                    return 2
                if exc.code == 429:
                    retry = int(exc.headers.get("Retry-After", "5"))
                    print(f"rate limited, retrying in {retry}s...", file=sys.stderr)
                    time.sleep(retry)
                    continue
                print(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
                return 1
            except urllib.error.URLError as exc:
                print(f"cannot reach the API: {exc.reason}", file=sys.stderr)
                return 1

            for event in page.get("events", []):
                if args.json:
                    print(json.dumps(event, ensure_ascii=False))
                else:
                    print(format_event(event))
            cursor = int(page.get("next", cursor))
            if args.once:
                return 0
            time.sleep(max(args.interval, 0.2))
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

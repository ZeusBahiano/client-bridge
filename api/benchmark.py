#!/usr/bin/env python3
"""
Load test for the ClientBridge reference API.

Reports request throughput and latency under concurrent load using only the
Python standard library. It speaks the same protocol as the Luau module and
uses HTTP keep-alive, so the numbers reflect the server rather than the
client's connection setup.

Examples
--------
    # 20 workers POSTing 10-event batches for 15 seconds
    python benchmark.py --duration 15 --threads 20

    # read throughput for one session token (seed it first)
    python benchmark.py --mode get --token cb_<32 hex> --threads 20

    # machine-readable result
    python benchmark.py --json
"""

from __future__ import annotations

import argparse
import http.client
import json
import statistics
import sys
import threading
import time
import uuid
from typing import List


def percentile(values: List[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


class Worker(threading.Thread):
    def __init__(self, args, deadline: float):
        super().__init__(daemon=True)
        self.args = args
        self.deadline = deadline
        self.latencies: List[float] = []
        self.count = 0
        self.errors = 0
        self.statuses: set[int] = set()

    def run(self) -> None:
        conn = http.client.HTTPConnection(self.args.host, self.args.port, timeout=15)
        path = "/clientbridge"
        if self.args.mode == "get":
            path = f"/sessions/{self.args.token}/events?since=0&limit={self.args.limit}"
        while time.monotonic() < self.deadline:
            started = time.perf_counter()
            try:
                if self.args.mode == "post":
                    body = json.dumps(self._batch()).encode()
                    conn.request("POST", path, body, self._headers())
                else:
                    conn.request("GET", path, headers=self._headers())
                response = conn.getresponse()
                response.read()
                self.statuses.add(response.status)
                if response.status >= 400:
                    self.errors += 1
            except Exception:  # noqa: BLE001 - count as a failed request
                self.errors += 1
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = http.client.HTTPConnection(
                    self.args.host, self.args.port, timeout=15
                )
            self.latencies.append((time.perf_counter() - started) * 1000.0)
            self.count += 1
        conn.close()

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.args.token and self.args.mode == "post":
            headers["Authorization"] = f"Bearer {self.args.token}"
        return headers

    def _batch(self) -> dict:
        events = []
        for _ in range(self.args.batch_size):
            events.append(
                {
                    "id": uuid.uuid4().hex,
                    "type": "biome",
                    "at": int(time.time()),
                    "scope": "server",
                    "origin": "server",
                    "data": {
                        "name": "Glitch",
                        "endsAt": int(time.time()) + 300,
                        "index": self.count,
                    },
                }
            )
        return {
            "v": 1,
            "universeId": self.args.universe,
            "placeId": self.args.place,
            "sentAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "events": events,
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Load-test a running ClientBridge API instance.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--mode", choices=("post", "get"), default="post")
    parser.add_argument("--threads", type=int, default=10)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--token", default="", help="session token for --mode get")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--universe", type=int, default=10774290674)
    parser.add_argument("--place", type=int, default=78552789773801)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.mode == "get" and not args.token:
        parser.error("--mode get requires --token")
    if args.threads < 1 or args.duration <= 0:
        parser.error("--threads and --duration must be positive")

    url = args.url
    if "://" in url:
        scheme, rest = url.split("://", 1)
        if scheme != "http":
            parser.error("only http:// URLs are supported by this benchmark")
        url = rest
    host, _, port_text = url.partition(":")
    args.host = host
    args.port = int(port_text or "80")

    deadline = time.monotonic() + args.duration
    workers = [Worker(args, deadline) for _ in range(args.threads)]
    started = time.perf_counter()
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    elapsed = time.perf_counter() - started

    latencies = [value for worker in workers for value in worker.latencies]
    requests = sum(worker.count for worker in workers)
    errors = sum(worker.errors for worker in workers)
    statuses = sorted({status for worker in workers for status in worker.statuses})
    result = {
        "mode": args.mode,
        "threads": args.threads,
        "duration_s": round(elapsed, 3),
        "requests": requests,
        "errors": errors,
        "requests_per_second": round(requests / elapsed, 1) if elapsed else 0,
        "events_per_second": round(
            requests * args.batch_size / elapsed, 1
        ) if elapsed and args.mode == "post" else None,
        "latency_ms": {
            "avg": round(statistics.mean(latencies), 2) if latencies else 0,
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
            "max": round(max(latencies), 2) if latencies else 0,
        },
        "statuses": statuses,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print()
        print(f"  mode        {result['mode']}  ({args.threads} threads, {args.duration}s)")
        print(f"  requests    {result['requests']}  ({result['requests_per_second']}/s)")
        if result["events_per_second"]:
            print(f"  events      {result['events_per_second']}/s")
        lat = result["latency_ms"]
        print(f"  latency     avg {lat['avg']} ms | p50 {lat['p50']} | p95 {lat['p95']} | p99 {lat['p99']} | max {lat['max']}")
        print(f"  errors      {result['errors']}  (statuses: {result['statuses']})")
        print()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

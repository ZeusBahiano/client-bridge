#!/usr/bin/env python3
"""
Fan-out benchmark for the ClientBridge WebSocket stream.

Connects N WebSocket subscribers, POSTs M events through the normal HTTP
ingestion endpoint and measures how fast every subscriber receives every
event.

    python benchmark_stream.py --clients 50 --events 200

The reported "deliveries/s" is events × clients ÷ elapsed wall time; the
latency is the time between the POST completing and the last subscriber
receiving that event.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.request
from typing import Dict, List

from demo_stream import WebSocketClient


def percentile(values: List[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


class Subscriber(threading.Thread):
    def __init__(self, url: str, token: str, expected: int, barrier: threading.Barrier):
        super().__init__(daemon=True)
        self.url = url
        self.token = token
        self.expected = expected
        self.barrier = barrier
        self.received: Dict[str, float] = {}
        self.error: str | None = None

    def run(self) -> None:
        try:
            client = WebSocketClient(self.url, token=self.token)
            hello = client.receive()
            if not hello or hello.get("kind") != "hello":
                raise RuntimeError("no hello")
            # Wait until every subscriber is connected so nobody misses the
            # events because of a slow handshake.
            self.barrier.wait(timeout=30)
            while len(self.received) < self.expected:
                message = client.receive()
                if message is None:
                    raise RuntimeError("stream closed")
                if message.get("kind") == "event":
                    self.received[message["id"]] = time.time()
            client.close()
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            try:
                self.barrier.abort()
            except Exception:  # noqa: BLE001
                pass


def post_event(url: str, event_id: str) -> None:
    payload = {
        "v": 1,
        "universeId": 10774290674,
        "placeId": 78552789773801,
        "sentAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events": [
            {
                "id": event_id,
                "type": "biome",
                "at": int(time.time()),
                "scope": "server",
                "origin": "server",
                "data": {"name": "Glitch", "endsAt": int(time.time()) + 300},
            }
        ],
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/clientbridge",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure ClientBridge WebSocket fan-out throughput.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--clients", type=int, default=50)
    parser.add_argument("--events", type=int, default=200)
    parser.add_argument("--token", default="", help="connect as this session (optional)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.clients < 1 or args.events < 1:
        parser.error("--clients and --events must be positive")

    barrier = threading.Barrier(args.clients + 1)
    subscribers = [
        Subscriber(args.url, args.token, args.events, barrier) for _ in range(args.clients)
    ]
    for subscriber in subscribers:
        subscriber.start()
    try:
        barrier.wait(timeout=40)
    except threading.BrokenBarrierError:
        print("subscribers did not become ready", file=sys.stderr)
        return 1

    ids = [f"bench_{int(time.time())}_{index}" for index in range(args.events)]
    post_times: Dict[str, float] = {}
    started = time.perf_counter()
    try:
        for event_id in ids:
            post_event(args.url, event_id)
            post_times[event_id] = time.time()
    except Exception as exc:  # noqa: BLE001
        print(f"POST failed: {exc}", file=sys.stderr)
        return 1

    deadline = time.time() + 60
    while time.time() < deadline:
        if all(len(sub.received) >= args.events for sub in subscribers):
            break
        time.sleep(0.05)
    elapsed = time.perf_counter() - started

    errors = [sub.error for sub in subscribers if sub.error]
    received_counts = [len(sub.received) for sub in subscribers]
    complete = sum(1 for count in received_counts if count >= args.events)
    latencies = []
    for event_id in ids:
        times = [sub.received[event_id] for sub in subscribers if event_id in sub.received]
        if len(times) == len(subscribers) and event_id in post_times:
            latencies.append((max(times) - post_times[event_id]) * 1000.0)

    deliveries = sum(received_counts)
    result = {
        "clients": args.clients,
        "events": args.events,
        "elapsed_s": round(elapsed, 3),
        "deliveries": deliveries,
        "deliveries_per_second": round(deliveries / elapsed, 1) if elapsed else 0,
        "complete_clients": complete,
        "errors": errors[:3],
        "latency_ms": {
            "avg": round(statistics.mean(latencies), 2) if latencies else 0,
            "p95": round(percentile(latencies, 0.95), 2),
            "max": round(max(latencies), 2) if latencies else 0,
        },
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print()
        print(f"  clients     {result['clients']}   events {result['events']}")
        print(f"  complete    {complete}/{args.clients} subscribers got every event")
        print(f"  deliveries  {deliveries}  ({result['deliveries_per_second']}/s)")
        lat = result["latency_ms"]
        print(f"  fanout lat  avg {lat['avg']} ms | p95 {lat['p95']} | max {lat['max']}")
        if errors:
            print(f"  errors      {errors[:1]}")
        print()
    return 0 if complete == args.clients and not errors else 1


if __name__ == "__main__":
    sys.exit(main())

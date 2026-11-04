#!/usr/bin/env python3
"""
Real-time ClientBridge viewer.

Connects to the reference API over WebSocket and prints messages as they are
stored. It is also a compact reference for tool authors who do not want a
third-party WebSocket library: the whole client is ~150 lines of stdlib.

    python demo_stream.py                                   # public/global stream
    python demo_stream.py --token cb_... --name ZeusBahiano # your session + global
    python demo_stream.py --since 0                         # replay then go live

Press Ctrl+C to stop. Reconnects automatically, resuming from the last seq.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
from typing import Optional
from urllib.parse import quote, urlparse

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketClient:
    def __init__(self, url: str, token: str = "", name: str = "", since: Optional[int] = None):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "ws"):
            raise ValueError("use http:// or ws://")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = "/stream"
        params = []
        if token:
            params.append("token=" + quote(token))
        if name:
            params.append("name=" + quote(name))
        if since is not None:
            params.append("since=" + str(int(since)))
        if params:
            path += "?" + "&".join(params)

        self.sock = socket.create_connection((host, port), timeout=15)
        # The server sends a ping every 15 s; give it two intervals before
        # treating the connection as dead.
        self.sock.settimeout(30)
        self.closed = False
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        self.file = self.sock.makefile("rb")
        status_line = self.file.readline().decode("latin-1").strip()
        headers = {}
        while True:
            line = self.file.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            name_, _, value = line.decode("latin-1").partition(":")
            headers[name_.strip().lower()] = value.strip()
        if "101" not in status_line:
            try:
                length = int(headers.get("content-length", "0") or "0")
                body = self.file.read(length).decode("utf-8", "replace") if length else ""
            except (OSError, ValueError):
                body = ""
            self.close()
            raise ConnectionError(f"handshake failed: {status_line} {body}")
        expected = base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise ConnectionError("bad Sec-WebSocket-Accept")
        self.closed = False

    def send_frame(self, opcode: int, payload: bytes = b"") -> None:
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def receive(self) -> Optional[dict]:
        """Return the next JSON message, or None when the server closed."""
        while True:
            header = self.file.read(2)
            if not header or len(header) < 2:
                return None
            first, second = header[0], header[1]
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self.file.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.file.read(8))[0]
            payload = self.file.read(length) or b""
            if opcode == 0x8:  # close
                return None
            if opcode == 0x9:  # ping -> pong
                self.send_frame(0xA, payload)
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode == 0x1:
                try:
                    return json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

    def send_json(self, payload: dict) -> None:
        self.send_frame(0x1, json.dumps(payload).encode("utf-8"))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.send_frame(0x8, struct.pack("!H", 1000))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass


def format_event(message: dict) -> str:
    stamp = time.strftime("%H:%M:%S", time.localtime(int(message.get("at", 0))))
    data = message.get("data") or {}
    fields = "  ".join(f"{key}={value!r}" for key, value in data.items())
    return f"[{stamp}] {message.get('type', '?'):<12} ({message.get('origin', '?')}) {fields}"


def run(args) -> int:
    cursor = args.since
    while True:
        try:
            client = WebSocketClient(args.url, args.token, args.name, cursor)
        except (OSError, ConnectionError) as exc:
            print(f"connection failed: {exc}. retrying in 3s...", file=sys.stderr)
            time.sleep(3)
            continue

        print(f"connected to {args.url} (Ctrl+C to stop)", file=sys.stderr)
        try:
            while True:
                try:
                    message = client.receive()
                except TimeoutError:
                    print("stream timed out (no heartbeat). reconnecting...", file=sys.stderr)
                    break
                if message is None:
                    print("stream closed by server. reconnecting...", file=sys.stderr)
                    break
                kind = message.get("kind")
                if kind == "hello":
                    print(
                        f"hello: stream={message.get('stream')} name={message.get('name')} "
                        f"cursor={message.get('cursor')}",
                        file=sys.stderr,
                    )
                elif kind == "event":
                    cursor = message.get("seq", cursor)
                    print(json.dumps(message, ensure_ascii=False) if args.json else format_event(message), flush=True)
                elif kind == "error":
                    print(f"server error: {message.get('error')} {message.get('detail', '')}", file=sys.stderr)
                elif kind == "pong":
                    continue
        except KeyboardInterrupt:
            client.close()
            return 0
        finally:
            client.close()

        if args.once:
            return 0
        time.sleep(2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Print ClientBridge events in real time (stdlib WebSocket client).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default="http://127.0.0.1:8787", help="API base URL")
    parser.add_argument("--token", default="", help="session token for private events")
    parser.add_argument("--name", default="", help="display name shown in hello messages")
    parser.add_argument("--since", type=int, default=None, help="replay from this seq before going live")
    parser.add_argument("--json", action="store_true", help="print raw JSON messages")
    parser.add_argument("--once", action="store_true", help="stop after one connection closes")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Tests for the ClientBridge reference API.

Run with:

    python -m unittest discover -s tests -v

They start the real server as a subprocess on a free TCP port with an in-memory
store, so nothing is mocked and no network access is required.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clientbridge_api as api  # noqa: E402

API_SCRIPT = str(Path(__file__).resolve().parents[1] / "clientbridge_api.py")

# Generated inside Roblox Studio with the Luau module (Crypto + Json) so this
# vector catches any drift between the Luau canonical encoder and the Python
# one used to verify signatures.
LUA_SIGNING_KEY = "test-signing-key"
LUA_CANONICAL = (
    '{"events":[{"at":1762000000,"data":{"endsAt":1762000300,"name":"Glitch"},'
    '"id":"evt_1","origin":"server","scope":"server","type":"biome"}],'
    '"placeId":78552789773801,"sentAt":"2026-11-04T12:00:00Z",'
    '"universeId":10774290674,"v":1}'
)
LUA_SIGNATURE = "_450CPheIz4xCH_X7GogCcij5wxeo9NeAMyR-xTH9I8"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ServerProcess:
    def __init__(self, *extra_args: str):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [
                sys.executable,
                API_SCRIPT,
                "--port",
                str(self.port),
                "--quiet",
                *extra_args,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.base = f"http://127.0.0.1:{self.port}"
        self._wait_ready()

    def _wait_ready(self) -> None:
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                err = self.proc.stderr.read().decode("utf-8", "replace")
                raise RuntimeError(f"server exited early: {err}")
            try:
                with urllib.request.urlopen(self.base + "/health", timeout=1) as res:
                    if res.status == 200:
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.05)
        raise RuntimeError("server did not become ready")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.proc.stderr:
            self.proc.stderr.close()

    def request(
        self,
        method: str,
        path: str,
        payload=None,
        raw: bytes | None = None,
        headers: dict | None = None,
        content_type: str = "application/json",
    ):
        data = raw
        if data is None and payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as res:
                body = res.read()
                return res.status, json.loads(body) if body else None, dict(res.headers)
        except urllib.error.HTTPError as exc:
            try:
                try:
                    body = exc.read()
                except (ConnectionError, OSError):
                    body = b""
                try:
                    parsed = json.loads(body) if body else None
                except json.JSONDecodeError:
                    parsed = body.decode("utf-8", "replace")
                return exc.code, parsed, dict(exc.headers)
            finally:
                exc.close()


def event(event_id: str = "evt_1", **overrides):
    base = {
        "id": event_id,
        "type": "biome",
        "at": 1762000000,
        "scope": "server",
        "origin": "server",
        "data": {"name": "Glitch", "endsAt": 1762000300},
    }
    base.update(overrides)
    return base


def batch(events, universe_id: int = 10774290674, place_id: int = 78552789773801, sent_at: str = "2026-11-04T12:00:00Z"):
    return {
        "v": 1,
        "universeId": universe_id,
        "placeId": place_id,
        "sentAt": sent_at,
        "events": events,
    }


def sign(batch_payload, key: str = LUA_SIGNING_KEY) -> str:
    canonical = api.canonical_json(batch_payload)
    digest = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class CanonicalJsonTests(unittest.TestCase):
    def test_matches_luau_test_vectors(self):
        self.assertEqual(api.canonical_json({"b": 1, "a": 2}), '{"a":2,"b":1}')
        self.assertEqual(api.canonical_json([1, 2, 3]), "[1,2,3]")
        self.assertEqual(api.canonical_json("x"), '"x"')
        self.assertEqual(api.canonical_json(1.5), "1.5")
        self.assertEqual(api.canonical_json(300), "300")

    def test_matches_luau_signature_vector(self):
        payload = {
            "v": 1,
            "universeId": 10774290674,
            "placeId": 78552789773801,
            "sentAt": "2026-11-04T12:00:00Z",
            "events": [
                {
                    "id": "evt_1",
                    "type": "biome",
                    "at": 1762000000,
                    "scope": "server",
                    "origin": "server",
                    "data": {"name": "Glitch", "endsAt": 1762000300},
                }
            ],
        }
        self.assertEqual(api.canonical_json(payload), LUA_CANONICAL)
        self.assertEqual(sign(payload), LUA_SIGNATURE)

    def test_escapes_control_characters(self):
        self.assertEqual(api.canonical_json({"s": "a\nb\tc"}), '{"s":"a\\nb\\tc"}')

    def test_rejects_non_finite_numbers(self):
        with self.assertRaises(ValueError):
            api.canonical_json({"n": float("nan")})


class ConfigTests(unittest.TestCase):
    def test_defaults_are_loopback(self):
        config = api.load_config([])
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["port"], 8787)
        self.assertEqual(config["buffer_events"], 20000)
        self.assertEqual(config["buffer_seconds"], 3600)

    def test_refuses_public_bind_without_auth(self):
        with self.assertRaises(ValueError):
            api.load_config(["--host", "0.0.0.0"])

    def test_public_bind_allowed_with_auth(self):
        config = api.load_config(["--host", "0.0.0.0", "--auth-token", "secret"])
        self.assertEqual(config["auth_token"], "secret")

    def test_public_bind_allowed_insecure_explicitly(self):
        config = api.load_config(["--host", "0.0.0.0", "--allow-insecure"])
        self.assertTrue(config["allow_insecure"])


class PublicEndpointTests(unittest.TestCase):
    def setUp(self):
        self.server = ServerProcess()

    def tearDown(self):
        self.server.close()

    def test_health(self):
        status, body, _ = self.server.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["protocol"], 1)
        self.assertFalse(body["auth_required"])

    def test_index(self):
        status, body, _ = self.server.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("post_batch", body["endpoints"])

    def test_post_batch(self):
        status, body, _ = self.server.request("POST", "/clientbridge", batch([event()]))
        self.assertEqual(status, 200)
        self.assertEqual(body["stored"], 1)
        self.assertEqual(body["duplicates"], 0)

    def test_duplicate_events_are_idempotent(self):
        self.server.request("POST", "/clientbridge", batch([event()]))
        status, body, _ = self.server.request("POST", "/clientbridge", batch([event()]))
        self.assertEqual(status, 200)
        self.assertEqual(body["stored"], 0)
        self.assertEqual(body["duplicates"], 1)

    def test_batch_replay_is_idempotent_by_default(self):
        payload = batch([event()])
        first = self.server.request("POST", "/clientbridge", payload)
        second = self.server.request("POST", "/clientbridge", payload)
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 200)
        self.assertEqual(second[1]["duplicates"], 1)

    def test_console_style_server_event_has_no_session(self):
        status, body, _ = self.server.request("POST", "/clientbridge", batch([event()]))
        self.assertEqual(status, 200)
        self.assertEqual(body["received"], 1)

    def test_invalid_json(self):
        status, body, _ = self.server.request(
            "POST", "/clientbridge", raw=b"{not json"
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_json")

    def test_unsupported_version(self):
        payload = batch([event()])
        payload["v"] = 2
        status, body, _ = self.server.request("POST", "/clientbridge", payload)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "unsupported_version")

    def test_bad_type_name(self):
        status, body, _ = self.server.request(
            "POST", "/clientbridge", batch([event(type="Bad Type!")])
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_event")

    def test_player_event_requires_session(self):
        status, body, _ = self.server.request(
            "POST", "/clientbridge", batch([event(scope="player")])
        )
        self.assertEqual(status, 400)
        self.assertIn("session", body["detail"])

    def test_server_event_rejects_session(self):
        status, body, _ = self.server.request(
            "POST",
            "/clientbridge",
            batch([event(session="cb_" + "0" * 32)]),
        )
        self.assertEqual(status, 400)

    def test_wrong_content_type(self):
        status, body, _ = self.server.request(
            "POST",
            "/clientbridge",
            batch([event()]),
            content_type="text/plain",
        )
        self.assertEqual(status, 415)
        self.assertEqual(body["error"], "unsupported_media_type")

    def test_unknown_endpoint(self):
        status, body, _ = self.server.request("GET", "/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_cors_preflight(self):
        status, _, headers = self.server.request("OPTIONS", "/clientbridge")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("POST", headers.get("Access-Control-Allow-Methods", ""))

    def test_session_feed_and_delete(self):
        token = "cb_" + "a" * 32
        payload = batch([event("evt_player", scope="player", session=token)])
        status, _, _ = self.server.request("POST", "/clientbridge", payload)
        self.assertEqual(status, 200)

        status, body, _ = self.server.request("GET", f"/sessions/{token}/events")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["events"]), 1)
        self.assertEqual(body["events"][0]["id"], "evt_player")
        self.assertNotIn("session", body["events"][0])
        self.assertEqual(body["next"], body["events"][0]["seq"])

        # Cursor is exclusive.
        status, body, _ = self.server.request(
            "GET", f"/sessions/{token}/events?since={body['next']}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["events"], [])

        status, body, _ = self.server.request("DELETE", f"/sessions/{token}")
        self.assertEqual(status, 200)
        self.assertEqual(body["deleted_events"], 1)

        status, _, _ = self.server.request("GET", f"/sessions/{token}/events")
        self.assertEqual(status, 404)

    def test_revoked_session_rejects_new_events(self):
        token = "cb_" + "9" * 32
        status, _, _ = self.server.request(
            "POST",
            "/clientbridge",
            batch([event("evt_rev_1", scope="player", session=token)]),
        )
        self.assertEqual(status, 200)
        status, _, _ = self.server.request("DELETE", f"/sessions/{token}")
        self.assertEqual(status, 200)
        status, body, _ = self.server.request(
            "POST",
            "/clientbridge",
            batch([event("evt_rev_2", scope="player", session=token)]),
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_event")

    def test_session_pagination(self):
        token = "cb_" + "b" * 32
        events = [
            event(f"evt_{index}", scope="player", session=token) for index in range(5)
        ]
        self.server.request("POST", "/clientbridge", batch(events))

        seen = []
        cursor = 0
        while True:
            status, body, _ = self.server.request(
                "GET", f"/sessions/{token}/events?since={cursor}&limit=2"
            )
            self.assertEqual(status, 200)
            seen.extend(item["id"] for item in body["events"])
            cursor = body["next"]
            if not body["has_more"]:
                break
        self.assertEqual(seen, [f"evt_{index}" for index in range(5)])

    def test_unknown_session(self):
        status, body, _ = self.server.request(
            "GET", "/sessions/cb_" + "c" * 32 + "/events"
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "unknown_session")

    def test_invalid_session_token(self):
        status, body, _ = self.server.request("GET", "/sessions/not-a-token/events")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_session")

    def test_admin_feed_disabled_by_default(self):
        status, body, _ = self.server.request("GET", "/events")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "admin_disabled")

    def test_payload_too_large(self):
        big = batch([event("evt_big")])
        status, body, _ = self.server.request(
            "POST",
            "/clientbridge",
            raw=json.dumps(big).encode() + b" " * (2 * 1024 * 1024),
        )
        # Sending a huge body with a valid Content-Length still trips the limit.
        self.assertEqual(status, 413)


class AuthServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ServerProcess("--auth-token", "s3cret")

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_missing_auth(self):
        status, body, _ = self.server.request("POST", "/clientbridge", batch([event()]))
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")

    def test_raw_secret(self):
        status, _, _ = self.server.request(
            "POST",
            "/clientbridge",
            batch([event()]),
            headers={"Authorization": "s3cret"},
        )
        self.assertEqual(status, 200)

    def test_bearer_secret(self):
        status, _, _ = self.server.request(
            "POST",
            "/clientbridge",
            batch([event("evt_2")]),
            headers={"Authorization": "Bearer s3cret"},
        )
        self.assertEqual(status, 200)

    def test_wrong_secret(self):
        status, _, _ = self.server.request(
            "POST",
            "/clientbridge",
            batch([event()]),
            headers={"Authorization": "nope"},
        )
        self.assertEqual(status, 401)

    def test_non_ascii_secret_is_401_not_500(self):
        status, body, _ = self.server.request(
            "POST",
            "/clientbridge",
            batch([event()]),
            headers={"Authorization": "ünïcode"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")


class SignatureServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ServerProcess("--signing-key", LUA_SIGNING_KEY)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_missing_signature(self):
        status, body, _ = self.server.request("POST", "/clientbridge", batch([event()]))
        self.assertEqual(status, 401)
        self.assertIn("signature", body["detail"])

    def test_valid_signature(self):
        payload = batch([event()])
        payload["sig"] = sign(payload)
        status, body, _ = self.server.request("POST", "/clientbridge", payload)
        self.assertEqual(status, 200)
        self.assertEqual(body["stored"], 1)

    def test_tampered_payload(self):
        payload = batch([event()])
        payload["sig"] = sign(payload)
        payload["events"][0]["data"]["name"] = "Tampered"
        status, body, _ = self.server.request("POST", "/clientbridge", payload)
        self.assertEqual(status, 401)

    def test_tampered_signature(self):
        payload = batch([event()])
        payload["sig"] = sign(payload)[:-1] + ("A" if sign(payload)[-1] != "A" else "B")
        status, _, _ = self.server.request("POST", "/clientbridge", payload)
        self.assertEqual(status, 401)

    def test_non_ascii_signature_is_401_not_500(self):
        payload = batch([event()])
        payload["sig"] = "não-é-base64url"
        status, body, _ = self.server.request("POST", "/clientbridge", payload)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")


class AdminServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ServerProcess("--admin-token", "admin123")

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_admin_requires_token(self):
        status, body, _ = self.server.request("GET", "/events")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")

    def test_admin_reads_all_events(self):
        token = "cb_" + "d" * 32
        self.server.request(
            "POST",
            "/clientbridge",
            batch(
                [
                    event("evt_server"),
                    event("evt_player", scope="player", session=token),
                ]
            ),
        )
        status, body, _ = self.server.request(
            "GET", "/events", headers={"Authorization": "Bearer admin123"}
        )
        self.assertEqual(status, 200)
        ids = [item["id"] for item in body["events"]]
        self.assertIn("evt_server", ids)
        self.assertIn("evt_player", ids)
        player = next(item for item in body["events"] if item["id"] == "evt_player")
        self.assertEqual(player["session"], token)


class RateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ServerProcess("--rate-limit", "3")

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_rate_limit(self):
        statuses = []
        for index in range(5):
            status, body, headers = self.server.request(
                "POST", "/clientbridge", batch([event(f"evt_rl_{index}")])
            )
            statuses.append(status)
            if status == 429:
                self.assertIn("Retry-After", headers)
                self.assertEqual(body["error"], "rate_limited")
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertEqual(statuses[3], 429)


class WebSocketClient:
    """Minimal RFC 6455 client, just enough to test the stream endpoint."""

    def __init__(self, base_url: str, token: str = "", since: int | None = None):
        parsed = base_url.replace("http://", "").split(":")
        host = parsed[0]
        port = int(parsed[1]) if len(parsed) > 1 else 80
        path = "/stream"
        params = []
        if token:
            params.append("token=" + quote(token))
        if since is not None:
            params.append("since=" + str(since))
        if params:
            path += "?" + "&".join(params)

        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.settimeout(10)
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
        self.headers = {}
        while True:
            line = self.file.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.decode("latin-1").partition(":")
            self.headers[name.strip().lower()] = value.strip()
        if "101" not in status_line:
            try:
                length_text = self.headers.get("content-length")
                if length_text is not None:
                    body = self.file.read(int(length_text)).decode("utf-8", "replace")
                else:
                    body = b""
            except (OSError, ValueError):
                body = ""
            self.close()
            raise ConnectionError(f"{status_line} {body}")
        expected = base64.b64encode(
            hashlib.sha1((key + api.WS_MAGIC).encode()).digest()
        ).decode()
        if self.headers.get("sec-websocket-accept") != expected:
            self.close()
            raise ConnectionError("invalid Sec-WebSocket-Accept")

    def send_json(self, payload: dict) -> None:
        payload_bytes = json.dumps(payload).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(payload_bytes)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload_bytes))
        self.sock.sendall(bytes(header) + mask + masked)

    def recv(self, timeout: float = 5.0) -> dict:
        self.sock.settimeout(timeout)
        while True:
            try:
                header = self.file.read(2)
            except socket.timeout as exc:
                raise TimeoutError("no message received") from exc
            if not header or len(header) < 2:
                raise ConnectionError("server closed the stream")
            first, second = header[0], header[1]
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self.file.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.file.read(8))[0]
            payload = self.file.read(length) or b""
            if opcode == 0x8:
                raise ConnectionError("server sent close")
            if opcode == 0x9:
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class MemoryStoreTests(unittest.TestCase):
    def make_store(self, buffer_events: int = 100) -> api.MemoryStore:
        return api.MemoryStore(buffer_events, 3600, 1.0)

    def test_ring_buffer_is_bounded(self):
        store = self.make_store(buffer_events=100)
        events = [event(f"evt_{index}") for index in range(150)]
        stored, duplicates, _ = store.store_batch(batch(events), "hash1", False)
        self.assertEqual(stored, 150)
        self.assertEqual(duplicates, 0)
        self.assertEqual(store.counts()["events"], 100)

    def test_duplicate_ids_are_ignored(self):
        store = self.make_store()
        payload = batch([event("evt_same")])
        store.store_batch(payload, "hash2", False)
        stored, duplicates, new_events = store.store_batch(payload, "hash3", False)
        self.assertEqual(stored, 0)
        self.assertEqual(duplicates, 1)
        self.assertEqual(new_events, [])

    def test_strict_replay(self):
        store = self.make_store()
        payload = batch([event("evt_replay")])
        store.store_batch(payload, "same-hash", True)
        with self.assertRaises(api.ApiError) as context:
            store.store_batch(payload, "same-hash", True)
        self.assertEqual(context.exception.status, 409)

    def test_revoke_purges_session_events(self):
        store = self.make_store()
        token = "cb_" + "e" * 32
        store.store_batch(
            batch([event("evt_private", scope="player", session=token)]), "hash4", False
        )
        self.assertEqual(len(store.read_stream(0, token, 10)), 1)
        result = store.revoke_session(token)
        self.assertEqual(result["deleted_events"], 1)
        self.assertEqual(store.counts()["events"], 0)

    def test_revoked_token_rejects_new_events(self):
        store = self.make_store()
        token = "cb_" + "3" * 32
        store.store_batch(
            batch([event("evt_once", scope="player", session=token)]), "h6", False
        )
        store.revoke_session(token)
        with self.assertRaises(api.ApiError) as context:
            store.store_batch(
                batch([event("evt_after", scope="player", session=token)]), "h7", False
            )
        self.assertEqual(context.exception.status, 400)

    def test_expired_token_rejects_new_events(self):
        store = self.make_store()
        token = "cb_" + "4" * 32
        store.store_batch(
            batch([event("evt_old", scope="player", session=token)]), "h8", False
        )
        store._sessions[token]["first_seen"] -= store.session_ttl_seconds + 10
        with self.assertRaises(api.ApiError):
            store.store_batch(
                batch([event("evt_new", scope="player", session=token)]), "h9", False
            )
        self.assertFalse(store.session_valid(token))

    def test_ttl_is_fixed_from_first_seen(self):
        store = self.make_store()
        token = "cb_" + "5" * 32
        store.store_batch(
            batch([event("evt_ttl", scope="player", session=token)]), "h10", False
        )
        # A batch refreshes last_seen, but must not extend the token lifetime.
        store._sessions[token]["last_seen"] += 10
        store._sessions[token]["first_seen"] -= store.session_ttl_seconds + 1
        self.assertFalse(store.session_valid(token))

    def test_mixed_batch_with_dead_token_is_atomic(self):
        store = self.make_store()
        token = "cb_" + "6" * 32
        store.store_batch(
            batch([event("evt_before", scope="player", session=token)]), "h11", False
        )
        store.revoke_session(token)
        payload = batch(
            [
                event("evt_server_should_not_store"),
                event("evt_dead", scope="player", session=token),
                event("evt_server_after"),
            ]
        )
        with self.assertRaises(api.ApiError) as context:
            store.store_batch(payload, "h12", False)
        self.assertEqual(context.exception.status, 400)
        # Nothing from the rejected batch may be stored.
        self.assertEqual(store.counts()["events"], 0)
        self.assertEqual(store.read_all_events(0, 100)["events"], [])

    def test_stream_filtering(self):
        store = self.make_store()
        token_a = "cb_" + "1" * 32
        token_b = "cb_" + "2" * 32
        store.store_batch(
            batch(
                [
                    event("evt_public"),
                    event("evt_a", scope="player", session=token_a),
                    event("evt_b", scope="player", session=token_b),
                ]
            ),
            "hash5",
            False,
        )
        global_ids = [item["id"] for item in store.read_stream(0, None, 10)]
        self.assertEqual(global_ids, ["evt_public"])
        a_ids = [item["id"] for item in store.read_stream(0, token_a, 10)]
        self.assertEqual(a_ids, ["evt_public", "evt_a"])


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.server = ServerProcess()

    def tearDown(self):
        self.server.close()

    def post(self, events):
        status, body, _ = self.server.request("POST", "/clientbridge", batch(events))
        self.assertEqual(status, 200, body)
        return body

    def test_stream_requires_upgrade(self):
        status, body, _ = self.server.request("GET", "/stream")
        self.assertEqual(status, 426)
        self.assertEqual(body["error"], "upgrade_required")

    def test_global_stream_receives_server_events(self):
        client = WebSocketClient(self.server.base)
        try:
            hello = client.recv()
            self.assertEqual(hello["kind"], "hello")
            self.assertEqual(hello["stream"], "global")
            self.post([event("evt_live")])
            message = client.recv()
            self.assertEqual(message["kind"], "event")
            self.assertEqual(message["id"], "evt_live")
            self.assertEqual(message["scope"], "server")
            self.assertEqual(message["data"]["name"], "Glitch")
        finally:
            client.close()

    def test_session_stream_gets_public_and_own_private_events(self):
        token_a = "cb_" + "a1" * 16
        token_b = "cb_" + "b2" * 16
        # Seed session A so the handshake accepts the token; these events
        # happened before the connection, so a live stream does not replay them.
        self.post([event("evt_seed", scope="player", session=token_a)])
        client = WebSocketClient(self.server.base, token=token_a)
        try:
            hello = client.recv()
            self.assertEqual(hello["stream"], "session")
            self.post(
                [
                    event("evt_public"),
                    event("evt_a", scope="player", session=token_a),
                    event("evt_b", scope="player", session=token_b),
                ]
            )
            first = client.recv()
            second = client.recv()
            self.assertEqual(first["id"], "evt_public")
            self.assertEqual(second["id"], "evt_a")
            with self.assertRaises(TimeoutError):
                client.recv(timeout=0.5)
        finally:
            client.close()

    def test_replay_then_live(self):
        self.post([event("evt_r1"), event("evt_r2")])
        client = WebSocketClient(self.server.base, since=0)
        try:
            hello = client.recv()
            self.assertEqual(hello["kind"], "hello")
            self.assertEqual(hello["cursor"], 0)
            self.assertEqual(client.recv()["id"], "evt_r1")
            self.assertEqual(client.recv()["id"], "evt_r2")
            self.post([event("evt_r3")])
            self.assertEqual(client.recv()["id"], "evt_r3")
        finally:
            client.close()

    def test_unknown_token_is_rejected(self):
        with self.assertRaises(ConnectionError) as context:
            WebSocketClient(self.server.base, token="cb_" + "f" * 32)
        self.assertIn("404", str(context.exception))

    def test_version_header_is_required(self):
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=5)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall(
                (
                    "GET /stream HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.server.port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            status_line = sock.recv(512).decode("latin-1").split("\r\n")[0]
            self.assertIn("400", status_line)
        finally:
            sock.close()

    def test_application_ping(self):
        client = WebSocketClient(self.server.base)
        try:
            client.recv()  # hello
            client.send_json({"kind": "ping"})
            message = client.recv()
            self.assertEqual(message["kind"], "pong")
        finally:
            client.close()

    def test_health_reports_subscribers(self):
        client = WebSocketClient(self.server.base)
        try:
            client.recv()  # hello
            status, body, _ = self.server.request("GET", "/health")
            self.assertEqual(status, 200)
            self.assertGreaterEqual(body["stream_subscribers"], 1)
        finally:
            client.close()

    def test_delete_closes_live_stream(self):
        token = "cb_" + "7" * 32
        self.post([event("evt_close", scope="player", session=token)])
        client = WebSocketClient(self.server.base, token=token)
        try:
            self.assertEqual(client.recv()["kind"], "hello")
            status, body, _ = self.server.request("DELETE", f"/sessions/{token}")
            self.assertEqual(status, 200)
            self.assertEqual(body["closed_streams"], 1)
            with self.assertRaises(ConnectionError):
                client.recv(timeout=3)
        finally:
            client.close()


class StreamBufferTests(unittest.TestCase):
    """Small buffer/max-replay instance to test cursor edge cases."""

    def setUp(self):
        self.server = ServerProcess(
            "--buffer-events", "100", "--max-replay-events", "3"
        )

    def tearDown(self):
        self.server.close()

    def post(self, events):
        status, body, _ = self.server.request("POST", "/clientbridge", batch(events))
        self.assertEqual(status, 200, body)

    def test_gap_is_reported_with_resume_cursor(self):
        self.post([event(f"evt_{index}") for index in range(150)])
        client = WebSocketClient(self.server.base, since=10)
        try:
            self.assertEqual(client.recv()["kind"], "hello")
            message = client.recv()
            self.assertEqual(message["kind"], "error")
            self.assertEqual(message["error"], "replay_too_large")
            # The buffer keeps 51..150, so the resumable cursor is 50.
            self.assertEqual(message["cursor"], 50)
        finally:
            client.close()

    def test_exact_cap_does_not_error(self):
        events = [event(f"evt_cap_{index}") for index in range(3)]
        self.post(events)
        client = WebSocketClient(self.server.base, since=0)
        try:
            self.assertEqual(client.recv()["kind"], "hello")
            ids = [client.recv()["id"] for _ in range(3)]
            self.assertEqual(ids, [item["id"] for item in events])
            self.post([event("evt_cap_live")])
            self.assertEqual(client.recv()["id"], "evt_cap_live")
        finally:
            client.close()

    def test_cap_exceeded_can_resume_from_error_cursor(self):
        events = [event(f"evt_more_{index}") for index in range(5)]
        self.post(events)
        client = WebSocketClient(self.server.base, since=0)
        try:
            self.assertEqual(client.recv()["kind"], "hello")
            for index in range(3):
                self.assertEqual(client.recv()["id"], events[index]["id"])
            message = client.recv()
            self.assertEqual(message["kind"], "error")
            self.assertEqual(message["error"], "replay_too_large")
            cursor = message["cursor"]
        finally:
            client.close()
        resumed = WebSocketClient(self.server.base, since=cursor)
        try:
            self.assertEqual(resumed.recv()["kind"], "hello")
            remaining = [resumed.recv()["id"] for _ in range(2)]
            self.assertEqual(remaining, [events[3]["id"], events[4]["id"]])
        finally:
            resumed.close()


class ConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ServerProcess()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_concurrent_batches(self):
        import threading

        total = 12
        per_thread = 8
        failures: list = []

        def worker(worker_id: int) -> None:
            for index in range(per_thread):
                payload = batch(
                    [event(f"evt_c_{worker_id}_{index}", data={"name": "Glitch"})]
                )
                status, body, _ = self.server.request("POST", "/clientbridge", payload)
                if status != 200 or body.get("stored") != 1:
                    failures.append((status, body))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(failures, [])
        status, body, _ = self.server.request(
            "GET", "/health", headers={"Cache-Control": "no-cache"}
        )
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)

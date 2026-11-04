#!/usr/bin/env python3
"""
ClientBridge reference API (HTTP transport, protocol v1).

This is the smallest useful backend for the HTTP channel of ClientBridge:
it receives the batches a Roblox experience POSTs, keeps the most recent
events in memory and delivers them to tools either as a live WebSocket stream
(lowest latency) or through the REST read contract (history within the replay
buffer).

It has ZERO third-party dependencies and NO database: only the Python standard
library and RAM. That is intentional — ClientBridge is a live bridge, not a
storage service. See ../docs/08-python-api.md for the full guide and
../docs/06-protocol-v1.md for the wire protocol.

Quick start (Windows):

    python clientbridge_api.py

Then use this endpoint in the game:

    http://127.0.0.1:8787/clientbridge    (Studio / local testing only)

Endpoints
---------
POST   /clientbridge                     receive a batch from the game
GET    /stream                           live WebSocket feed (global or session)
GET    /sessions/{token}/events          read a player's opted-in events
DELETE /sessions/{token}                 revoke the token and erase its events
GET    /events                           admin feed (all in-memory events)
GET    /health                           liveness / counters
GET    /                                  short service description

Security defaults
-----------------
* Binds to 127.0.0.1 only.
* Refuses to listen on a public interface without an auth token, a signing
  key, or an explicit --allow-insecure.
* Never logs event payloads, tokens or Authorization headers.
* HMAC-SHA256 signature verification is available and checked with the exact
  canonical JSON the Luau module produces.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import hmac
import json
import logging
import math
import os
import queue
import re
import signal
import struct
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

API_NAME = "clientbridge-api"
API_VERSION = "1.0.0"
PROTOCOL_VERSION = 1
PROJECT_URL = "https://github.com/ZeusBahiano/client-bridge"

LOG = logging.getLogger("clientbridge_api")

TYPE_NAME_RE = re.compile(r"^[a-z0-9_.-]{1,32}$")
SESSION_RE = re.compile(r"^cb_[0-9a-f]{32}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

DEFAULTS: Dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8787,
    "auth_token": "",       # shared secret used by POST (optional)
    "admin_token": "",      # unlocks GET /events (optional)
    "signing_key": "",      # HMAC key used to verify batch signatures (optional)
    # Everything is in memory: this is a live relay, not a database.
    "buffer_events": 20000,  # ring buffer size (memory bound)
    "buffer_seconds": 3600,  # max age of an event kept for replay
    "session_ttl_hours": 24.0,
    # The Luau module caps itself at 400 HTTP requests/minute per server
    # (each carrying up to 100 events). The defaults below are deliberately
    # higher so one API instance can serve several busy games at once.
    "rate_limit": 6000,      # write requests per minute per client IP (~100/s)
    "read_rate_limit": 3000, # read requests per minute per client IP/token (~50/s)
    "max_body_bytes": 1024 * 1024,
    "max_batch_events": 500,
    "max_event_bytes": 8192,
    "max_replay_events": 5000,
    "strict_replay": False,
    "allow_insecure": False,
    "quiet": False,
}

ENV_KEYS = {
    "CLIENTBRIDGE_HOST": "host",
    "CLIENTBRIDGE_PORT": "port",
    "CLIENTBRIDGE_AUTH_TOKEN": "auth_token",
    "CLIENTBRIDGE_ADMIN_TOKEN": "admin_token",
    "CLIENTBRIDGE_SIGNING_KEY": "signing_key",
    "CLIENTBRIDGE_BUFFER_EVENTS": "buffer_events",
    "CLIENTBRIDGE_BUFFER_SECONDS": "buffer_seconds",
    "CLIENTBRIDGE_SESSION_TTL_HOURS": "session_ttl_hours",
    "CLIENTBRIDGE_RATE_LIMIT": "rate_limit",
    "CLIENTBRIDGE_READ_RATE_LIMIT": "read_rate_limit",
    "CLIENTBRIDGE_MAX_BODY_BYTES": "max_body_bytes",
    "CLIENTBRIDGE_MAX_REPLAY_EVENTS": "max_replay_events",
    "CLIENTBRIDGE_STRICT_REPLAY": "strict_replay",
    "CLIENTBRIDGE_ALLOW_INSECURE": "allow_insecure",
}


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class ApiError(Exception):
    """An error that becomes a JSON response instead of a stack trace."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str = "",
        headers: Optional[Dict[str, str]] = None,
    ):
        super().__init__(detail or code)
        self.status = int(status)
        self.code = code
        self.detail = detail
        self.headers = headers or {}


# ---------------------------------------------------------------------------
# canonical JSON (must match src/ClientBridge/Shared/Json.luau byte for byte)
# ---------------------------------------------------------------------------
_LUA_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _lua_number(value: float) -> str:
    """Format a number the way Luau's string.format("%.14g", v) does."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return format(number, ".14g")


def _lua_string(value: str) -> str:
    out: List[str] = ['"']
    for char in value:
        escaped = _LUA_ESCAPES.get(char)
        if escaped is not None:
            out.append(escaped)
        elif ord(char) < 0x20:
            out.append("\\u%04x" % ord(char))
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _lua_encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _lua_number(value)
    if isinstance(value, str):
        return _lua_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_lua_encode(item) for item in value) + "]"
    if isinstance(value, dict):
        # Luau sorts keys with table.sort(), i.e. by UTF-8 byte order.
        keys = sorted(value.keys(), key=lambda k: str(k).encode("utf-8"))
        return "{" + ",".join(
            f"{_lua_encode(str(key))}:{_lua_encode(value[key])}" for key in keys
        ) + "}"
    raise ValueError(f"cannot encode {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Deterministic JSON identical to the Luau encoder used for signing."""
    return _lua_encode(value)


def now_iso(epoch: Optional[float] = None) -> str:
    stamp = _dt.datetime.fromtimestamp(
        time.time() if epoch is None else epoch, tz=_dt.timezone.utc
    )
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> Optional[float]:
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------
def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _check_json_value(value: Any, path: str, max_string: int) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > max_string:
            raise ApiError(400, "invalid_event", f"{path}: string longer than {max_string} characters")
        return
    if _is_number(value):
        return
    raise ApiError(
        400,
        "invalid_event",
        f"{path}: data must be flat (string, number, boolean or null)",
    )


EVENT_KEYS = {"id", "type", "at", "scope", "origin", "data", "session"}


def validate_event(raw: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(400, "invalid_event", "every event must be a JSON object")
    unknown = set(raw) - EVENT_KEYS
    if unknown:
        raise ApiError(400, "invalid_event", f"unknown event field(s): {', '.join(sorted(unknown))}")

    event_id = raw.get("id")
    if not isinstance(event_id, str) or not ID_RE.match(event_id):
        raise ApiError(400, "invalid_event", "id must be 1..64 chars of [A-Za-z0-9_-]")

    type_name = raw.get("type")
    if not isinstance(type_name, str) or not TYPE_NAME_RE.match(type_name):
        raise ApiError(
            400,
            "invalid_event",
            "type must match ^[a-z0-9_.-]{1,32}$ (lowercase)",
        )

    at = raw.get("at")
    if not _is_int(at) or at < 0:
        raise ApiError(400, "invalid_event", "at must be a positive integer (epoch seconds)")

    scope = raw.get("scope")
    if scope not in ("server", "player"):
        raise ApiError(400, "invalid_event", "scope must be 'server' or 'player'")

    origin = raw.get("origin")
    if origin not in ("server", "client"):
        raise ApiError(400, "invalid_event", "origin must be 'server' or 'client'")

    data = raw.get("data")
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_event", "data must be a JSON object")
    if len(data) > 64:
        raise ApiError(400, "invalid_event", "data has more than 64 keys")
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            raise ApiError(400, "invalid_event", "data keys must be non-empty strings")
        if len(key) > 64:
            raise ApiError(400, "invalid_event", "data key longer than 64 characters")
        _check_json_value(value, f"data.{key}", 1024)

    session = raw.get("session")
    if scope == "player":
        if not isinstance(session, str) or not SESSION_RE.match(session):
            raise ApiError(
                400,
                "invalid_event",
                "player events require a session token shaped like cb_<32 lowercase hex>",
            )
    elif session is not None:
        raise ApiError(400, "invalid_event", "server events must not carry a session token")

    event = {
        "id": event_id,
        "type": type_name,
        "at": int(at),
        "scope": scope,
        "origin": origin,
        "data": data,
    }
    if session is not None:
        event["session"] = session

    size = len(canonical_json(event).encode("utf-8"))
    if size > int(config["max_event_bytes"]):
        raise ApiError(
            400,
            "invalid_event",
            f"event {event_id} is {size} bytes (limit {config['max_event_bytes']})",
        )
    return event


def validate_batch(payload: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_payload", "body must be a JSON object")

    version = payload.get("v")
    if not _is_int(version) or version != PROTOCOL_VERSION:
        raise ApiError(400, "unsupported_version", f"expected v={PROTOCOL_VERSION}")

    universe_id = payload.get("universeId")
    place_id = payload.get("placeId")
    if not _is_int(universe_id) or universe_id < 0:
        raise ApiError(400, "invalid_payload", "universeId must be a non-negative integer")
    if not _is_int(place_id) or place_id < 0:
        raise ApiError(400, "invalid_payload", "placeId must be a non-negative integer")

    sent_at = payload.get("sentAt")
    if parse_iso(sent_at) is None:
        raise ApiError(400, "invalid_payload", "sentAt must be an ISO-8601 UTC timestamp")

    events_raw = payload.get("events")
    if not isinstance(events_raw, list) or not events_raw:
        raise ApiError(400, "invalid_payload", "events must be a non-empty array")
    if len(events_raw) > int(config["max_batch_events"]):
        raise ApiError(
            400,
            "invalid_payload",
            f"too many events in one batch (limit {config['max_batch_events']})",
        )

    events = [validate_event(item, config) for item in events_raw]
    return {
        "v": version,
        "universeId": int(universe_id),
        "placeId": int(place_id),
        "sentAt": sent_at,
        "events": events,
        "sig": payload.get("sig"),
    }


def verify_signature(batch: Dict[str, Any], signing_key: str) -> Tuple[bool, str]:
    """Return (ok, reason) for the batch HMAC."""
    signature = batch.get("sig")
    if not isinstance(signature, str) or not signature:
        return False, "missing_signature"
    signed = {
        "v": batch["v"],
        "universeId": batch["universeId"],
        "placeId": batch["placeId"],
        "sentAt": batch["sentAt"],
        "events": batch["events"],
    }
    try:
        digest = hmac.new(
            signing_key.encode("utf-8"),
            canonical_json(signed).encode("utf-8"),
            hashlib.sha256,
        ).digest()
    except (ValueError, UnicodeError):
        return False, "signature_error"
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    if not hmac.compare_digest(signature, expected):
        return False, "bad_signature"
    return True, ""


def token_allowed(header_value: Optional[str], expected: str) -> bool:
    """Compare an Authorization header against a configured token.

    Accepts both the exact value the game stores as a Creator Hub secret and
    the conventional "Bearer <token>" form, so the module (which sends the
    secret verbatim) and normal HTTP clients both work.
    """
    if not expected:
        return True
    if not header_value:
        return False
    expected_plain = expected[7:] if expected.lower().startswith("bearer ") else expected
    candidates = [header_value]
    if header_value.lower().startswith("bearer "):
        candidates.append(header_value[7:])
    return any(hmac.compare_digest(candidate, expected_plain) for candidate in candidates)


# ---------------------------------------------------------------------------
# storage (in-memory by design)
# ---------------------------------------------------------------------------
class MemoryStore:
    """Bounded in-memory relay for events and opt-in sessions.

    ClientBridge is a live bridge, not a database: the priority is delivering
    events to connected clients with the lowest possible latency. Events are
    kept in a ring buffer only long enough to serve:

    * live WebSocket subscribers,
    * reconnects with a cursor,
    * the optional REST read contract.

    Nothing is written to disk. A restart starts empty, which is intentional:
    if a tool needs history, it should consume the stream or persist it on its
    own side.
    """

    def __init__(self, buffer_events: int, buffer_seconds: int, session_ttl_hours: float):
        self.buffer_events = max(int(buffer_events), 100)
        self.buffer_seconds = int(buffer_seconds)
        self.session_ttl_seconds = int(float(session_ttl_hours) * 3600)
        self._lock = threading.RLock()
        self._seq = 0
        self._events: Deque[Dict[str, Any]] = deque(maxlen=self.buffer_events)
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._session_events: Dict[str, Deque[Dict[str, Any]]] = {}
        self._seen_ids: Set[str] = set()
        self._seen_order: Deque[str] = deque()
        self._seen_limit = self.buffer_events * 2
        self._dead_tokens: Set[str] = set()
        self._dead_order: Deque[str] = deque()
        self._dead_limit = self.buffer_events * 2
        self._batches: Dict[str, int] = {}

    # -- internals ----------------------------------------------------------
    def _mark_dead(self, token: str) -> None:
        if token in self._dead_tokens:
            return
        self._dead_tokens.add(token)
        self._dead_order.append(token)
        while len(self._dead_order) > self._dead_limit:
            self._dead_tokens.discard(self._dead_order.popleft())

    def _assert_token_usable(self, token: str, now: int) -> None:
        """Refuse new events for revoked or expired sessions.

        The game should stop sending as soon as the token is revoked, but a
        retried/stale batch must never resurrect a dead session or push a
        private event to a stream that is supposed to be over.
        """
        if token in self._dead_tokens:
            raise ApiError(
                400, "invalid_event", "session token was revoked or expired"
            )
        info = self._sessions.get(token)
        if info is None:
            return  # first time this session is seen: register it
        if info["revoked"]:
            self._mark_dead(token)
            raise ApiError(400, "invalid_event", "session token was revoked")
        if now - int(info["last_seen"]) > self.session_ttl_seconds:
            self._mark_dead(token)
            self._sessions.pop(token, None)
            self._session_events.pop(token, None)
            raise ApiError(400, "invalid_event", "session token expired")

    def _remember_id(self, event_id: str) -> bool:
        """Return False when the id was already seen (an idempotent retry)."""
        if event_id in self._seen_ids:
            return False
        self._seen_ids.add(event_id)
        self._seen_order.append(event_id)
        while len(self._seen_order) > self._seen_limit:
            old = self._seen_order.popleft()
            self._seen_ids.discard(old)
        return True

    def _require_session(self, token: str) -> Dict[str, Any]:
        row = self._sessions.get(token)
        if row is None:
            raise ApiError(404, "unknown_session", "session token not found")
        if row["revoked"]:
            raise ApiError(404, "unknown_session", "session token was revoked")
        if int(time.time()) - int(row["last_seen"]) > self.session_ttl_seconds:
            self._mark_dead(token)
            self._sessions.pop(token, None)
            self._session_events.pop(token, None)
            raise ApiError(404, "unknown_session", "session token expired")
        return row

    @staticmethod
    def _view(event: Dict[str, Any], admin: bool = False) -> Dict[str, Any]:
        view = {
            "seq": event["seq"],
            "id": event["id"],
            "type": event["type"],
            "at": event["at"],
            "scope": event["scope"],
            "origin": event["origin"],
            "data": event["data"],
            "received_at": now_iso(event["received_at"]),
        }
        if admin:
            view["universe_id"] = event["universe_id"]
            view["place_id"] = event["place_id"]
            view["session"] = event["session"]
        return view

    def _page(
        self,
        items: List[Dict[str, Any]],
        since: int,
        limit: int,
        session_info: Optional[Dict[str, Any]],
        admin: bool = False,
    ) -> Dict[str, Any]:
        has_more = len(items) > limit
        page = items[:limit]
        events = [self._view(event, admin=admin) for event in page]
        next_cursor = events[-1]["seq"] if events else since
        result: Dict[str, Any] = {
            "ok": True,
            "events": events,
            "next": next_cursor,
            "has_more": has_more,
        }
        if session_info is not None:
            result["session"] = session_info
        return result

    # -- writes -------------------------------------------------------------
    def store_batch(
        self, batch: Dict[str, Any], body_hash: str, strict_replay: bool
    ) -> Tuple[int, int, List[Dict[str, Any]]]:
        now = int(time.time())
        stored = 0
        duplicates = 0
        new_events: List[Dict[str, Any]] = []
        touched_tokens: Set[str] = set()

        with self._lock:
            if strict_replay:
                seen_at = self._batches.get(body_hash)
                if seen_at is not None and now - int(seen_at) < 600:
                    raise ApiError(409, "replay", "this exact batch was already accepted")
                self._batches[body_hash] = now

            for raw in batch["events"]:
                token = raw.get("session")
                if token:
                    self._assert_token_usable(token, now)
                    touched_tokens.add(token)
                if not self._remember_id(raw["id"]):
                    duplicates += 1
                    continue

                self._seq += 1
                event = {
                    "seq": self._seq,
                    "id": raw["id"],
                    "type": raw["type"],
                    "at": raw["at"],
                    "scope": raw["scope"],
                    "origin": raw["origin"],
                    "session": token,
                    "data": raw["data"],
                    "universe_id": batch["universeId"],
                    "place_id": batch["placeId"],
                    "received_at": now,
                }
                self._events.append(event)
                if token:
                    bucket = self._session_events.get(token)
                    if bucket is None:
                        bucket = deque(maxlen=self.buffer_events)
                        self._session_events[token] = bucket
                    bucket.append(event)
                new_events.append(event)
                stored += 1

            for token in touched_tokens:
                info = self._sessions.get(token)
                if info is None:
                    self._sessions[token] = {
                        "first_seen": now,
                        "last_seen": now,
                        "universe_id": batch["universeId"],
                        "place_id": batch["placeId"],
                        "revoked": False,
                    }
                else:
                    info["last_seen"] = now
                    info["universe_id"] = batch["universeId"]
                    info["place_id"] = batch["placeId"]

        return stored, duplicates, new_events

    def revoke_session(self, token: str) -> Dict[str, Any]:
        with self._lock:
            self._require_session(token)
            self._sessions[token]["revoked"] = True
            self._mark_dead(token)
            removed = len(self._session_events.pop(token, ()))
            self._events = deque(
                (event for event in self._events if event.get("session") != token),
                maxlen=self.buffer_events,
            )
        return {"deleted_events": removed, "revoked": True}

    # -- reads --------------------------------------------------------------
    def session_valid(self, token: str) -> bool:
        with self._lock:
            try:
                self._require_session(token)
            except ApiError:
                return False
            return True

    def max_seq(self) -> int:
        with self._lock:
            return self._seq

    def read_session_events(self, token: str, since: int, limit: int) -> Dict[str, Any]:
        with self._lock:
            row = self._require_session(token)
            items = [
                event
                for event in self._session_events.get(token, ())
                if event["seq"] > since
            ][: limit + 1]
            expires_at = int(row["last_seen"]) + self.session_ttl_seconds
            session_info = {
                "first_seen": now_iso(row["first_seen"]),
                "last_seen": now_iso(row["last_seen"]),
                "expires_at": now_iso(expires_at),
                "universe_id": row["universe_id"],
                "place_id": row["place_id"],
            }
        return self._page(items, since, limit, session_info)

    def read_stream(
        self, since: int, token: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        with self._lock:
            out: List[Dict[str, Any]] = []
            for event in self._events:
                if event["seq"] <= since:
                    continue
                if event["scope"] != "server" and (
                    not token or event.get("session") != token
                ):
                    continue
                out.append(event)
                if len(out) >= limit:
                    break
        return out

    def read_all_events(
        self,
        since: int,
        limit: int,
        universe_id: Optional[int] = None,
        type_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            items: List[Dict[str, Any]] = []
            for event in self._events:
                if event["seq"] <= since:
                    continue
                if universe_id is not None and event["universe_id"] != universe_id:
                    continue
                if type_name is not None and event["type"] != type_name:
                    continue
                items.append(event)
                if len(items) >= limit + 1:
                    break
        return self._page(items, since, limit, None, admin=True)

    def counts(self) -> Dict[str, int]:
        not_before = int(time.time()) - self.session_ttl_seconds
        with self._lock:
            active = sum(
                1
                for row in self._sessions.values()
                if not row["revoked"] and int(row["last_seen"]) >= not_before
            )
            return {"events": len(self._events), "sessions": active}

    def cleanup(self) -> Dict[str, int]:
        now = int(time.time())
        removed_events = 0
        removed_sessions = 0
        with self._lock:
            if self.buffer_seconds > 0:
                cutoff = now - self.buffer_seconds
                while self._events and self._events[0]["received_at"] < cutoff:
                    self._events.popleft()
                    removed_events += 1

            expired = [
                token
                for token, row in self._sessions.items()
                if int(row["last_seen"]) < now - self.session_ttl_seconds
            ]
            for token in expired:
                self._mark_dead(token)
                self._sessions.pop(token, None)
                self._session_events.pop(token, None)
                removed_sessions += 1

            if self.buffer_seconds > 0:
                for token, bucket in list(self._session_events.items()):
                    while bucket and bucket[0]["received_at"] < now - self.buffer_seconds:
                        bucket.popleft()

            stale_hashes = [
                key for key, seen_at in self._batches.items() if now - int(seen_at) >= 600
            ]
            for key in stale_hashes:
                self._batches.pop(key, None)

        return {"events": removed_events, "sessions": removed_sessions}


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, limit_per_minute: int):
        self.limit = max(int(limit_per_minute), 1)
        self._lock = threading.Lock()
        self._windows: Dict[str, Tuple[int, int]] = {}

    def allow(self, key: str) -> Tuple[bool, int]:
        window = int(time.monotonic() // 60)
        with self._lock:
            current_window, count = self._windows.get(key, (window, 0))
            if current_window != window:
                current_window, count = window, 0
            if count >= self.limit:
                retry_after = max(1, 60 - int(time.monotonic() % 60))
                self._windows[key] = (current_window, count)
                return False, retry_after
            self._windows[key] = (current_window, count + 1)
            if len(self._windows) > 10000:
                self._windows = {
                    k: v for k, v in self._windows.items() if v[0] == window
                }
            return True, 0


# ---------------------------------------------------------------------------
# real-time fan-out (WebSocket)
# ---------------------------------------------------------------------------
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_MAX_FRAME = 1024 * 1024
WS_HEARTBEAT_SECONDS = 15
NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


class WebSocketProtocolError(Exception):
    pass


def ws_accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + WS_MAGIC).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def ws_frame(opcode: int, payload: bytes = b"") -> bytes:
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)
    return bytes(header) + payload


def ws_read_message(rfile) -> Optional[Tuple[int, bytes]]:  # noqa: ANN001
    """Read one complete client message, handling fragmentation.

    Returns (opcode, payload) for text/binary frames or a (frame_opcode,
    payload) pair for close/ping/pong. Returns None when the peer closed the
    TCP connection.
    """
    fragments = bytearray()
    message_opcode: Optional[int] = None
    while True:
        header = rfile.read(2)
        if not header or len(header) < 2:
            return None
        first, second = header[0], header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            extended = rfile.read(2)
            if len(extended) < 2:
                return None
            length = struct.unpack("!H", extended)[0]
        elif length == 127:
            extended = rfile.read(8)
            if len(extended) < 8:
                return None
            length = struct.unpack("!Q", extended)[0]
        if length > WS_MAX_FRAME:
            raise WebSocketProtocolError("frame too large")
        mask = rfile.read(4) if masked else None
        if masked and (mask is None or len(mask) < 4):
            return None
        payload = rfile.read(length)
        if payload is None or len(payload) < length:
            return None
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

        if opcode == 0x8:  # close
            return 0x8, payload
        if opcode == 0x9:  # ping
            return 0x9, payload
        if opcode == 0xA:  # pong
            return 0xA, payload
        if opcode in (0x1, 0x2):
            if fin:
                return opcode, payload
            message_opcode = opcode
            fragments = bytearray(payload)
            continue
        if opcode == 0x0:
            if message_opcode is None:
                raise WebSocketProtocolError("unexpected continuation")
            fragments += payload
            if len(fragments) > WS_MAX_FRAME:
                raise WebSocketProtocolError("message too large")
            if fin:
                return message_opcode, bytes(fragments)
            continue
        raise WebSocketProtocolError(f"unsupported opcode {opcode}")


class Subscriber:
    def __init__(self, token: Optional[str], name: str):
        self.token = token
        self.name = name
        self.queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=5000)
        self.closed = False
        self.dropped = 0

    def close(self) -> None:
        self.closed = True
        try:
            self.queue.put_nowait(None)  # wake the sender
        except queue.Full:
            pass


class EventBus:
    """Fan-out of freshly stored events to live WebSocket subscribers.

    Delivery rules:
    * everyone receives `scope = "server"` events;
    * a subscriber connected with a session token also receives that token's
      `scope = "player"` events, and nobody else's.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: List[Subscriber] = []

    def subscribe(self, token: Optional[str], name: str) -> Subscriber:
        subscriber = Subscriber(token, name)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)
        subscriber.close()

    def count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish_many(self, events: Iterable[Dict[str, Any]]) -> None:
        if not events:
            return
        with self._lock:
            subscribers = list(self._subscribers)
        for event in events:
            scope = event.get("scope")
            token = event.get("session")
            for subscriber in subscribers:
                if subscriber.closed:
                    continue
                if scope != "server" and (not subscriber.token or subscriber.token != token):
                    continue
                try:
                    subscriber.queue.put_nowait(event)
                except queue.Full:
                    # Slow consumer: drop its oldest event rather than
                    # blocking the ingest path.
                    try:
                        subscriber.queue.get_nowait()
                    except queue.Empty:
                        pass
                    subscriber.dropped += 1
                    try:
                        subscriber.queue.put_nowait(event)
                    except queue.Full:
                        pass


def stream_event_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "kind": "event",
        "seq": event["seq"],
        "id": event["id"],
        "type": event["type"],
        "at": event["at"],
        "scope": event["scope"],
        "origin": event["origin"],
        "data": event["data"],
    }


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class AppContext:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.store = MemoryStore(
            int(config["buffer_events"]),
            int(config["buffer_seconds"]),
            float(config["session_ttl_hours"]),
        )
        self.bus = EventBus()
        self.started_at = time.time()
        self.write_limiter = RateLimiter(int(config["rate_limit"]))
        self.read_limiter = RateLimiter(int(config["read_rate_limit"]))
        self.shutdown_event = threading.Event()


def _redact_path(path: str) -> str:
    return SESSION_RE.sub("cb_***", path)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"{API_NAME}/{API_VERSION}"

    # -- plumbing -----------------------------------------------------------
    @property
    def ctx(self) -> AppContext:
        return self.server.ctx  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Silence the default stderr access log; _run() writes one safe line.
        return

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._common_headers()
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._run("GET", self._handle_get)

    def do_POST(self) -> None:  # noqa: N802
        self._run("POST", self._handle_post)

    def do_DELETE(self) -> None:  # noqa: N802
        self._run("DELETE", self._handle_delete)

    def _run(self, method: str, handler) -> None:
        start = time.time()
        status = 500
        try:
            status = handler()
        except ApiError as exc:
            status = self._send_json(
                exc.status,
                {"ok": False, "error": exc.code, "detail": exc.detail},
                extra=exc.headers,
            )
        except (BrokenPipeError, ConnectionResetError):
            status = 499
        except Exception:  # noqa: BLE001 - never leak internals to a client
            LOG.exception("unhandled error on %s %s", method, _redact_path(self.path))
            status = self._send_json(
                500,
                {
                    "ok": False,
                    "error": "internal_error",
                    "detail": "see the server log",
                },
            )
        finally:
            elapsed_ms = (time.time() - start) * 1000.0
            LOG.info(
                "%s %s -> %d (%.1f ms)",
                method,
                _redact_path(self.path),
                status,
                elapsed_ms,
            )

    # -- responses ----------------------------------------------------------
    def _common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-ClientBridge-Version",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-ClientBridge-Version", str(PROTOCOL_VERSION))

    def _send_json(
        self, status: int, payload: Dict[str, Any], extra: Optional[Dict[str, str]] = None
    ) -> int:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(int(status))
        self._common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if extra:
            for name, value in extra.items():
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        return int(status)

    # -- routing ------------------------------------------------------------
    def _handle_get(self) -> int:
        parsed = urlparse(self.path)
        parts = [segment for segment in parsed.path.split("/") if segment]
        query = parse_qs(parsed.query)

        if parsed.path in ("/", ""):
            return self._send_json(
                200,
                {
                    "ok": True,
                    "service": API_NAME,
                    "version": API_VERSION,
                    "protocol": PROTOCOL_VERSION,
                    "project": PROJECT_URL,
                    "endpoints": {
                        "post_batch": "/clientbridge",
                        "stream": "/stream",
                        "session_events": "/sessions/{token}/events",
                        "delete_session": "/sessions/{token}",
                        "admin_events": "/events",
                        "health": "/health",
                    },
                },
            )

        if parts == ["health"]:
            counts = self.ctx.store.counts()
            return self._send_json(
                200,
                {
                    "ok": True,
                    "service": API_NAME,
                    "version": API_VERSION,
                    "protocol": PROTOCOL_VERSION,
                    "uptime_seconds": int(time.time() - self.ctx.started_at),
                    "events": counts["events"],
                    "sessions": counts["sessions"],
                    "stream_subscribers": self.ctx.bus.count(),
                    "auth_required": bool(self.ctx.config["auth_token"]),
                    "signing_required": bool(self.ctx.config["signing_key"]),
                },
            )

        if parts == ["stream"]:
            return self._handle_stream(parsed, query)

        if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "events":
            return self._handle_session_events(parts[1], query)

        if parts == ["events"]:
            return self._handle_admin_events(query)

        raise ApiError(404, "not_found", "no such endpoint")

    def _handle_post(self) -> int:
        parsed = urlparse(self.path)
        if parsed.path not in ("/clientbridge", "/v1/clientbridge"):
            raise ApiError(404, "not_found", "POST to /clientbridge")

        allowed, retry_after = self.ctx.write_limiter.allow(self._client_ip())
        if not allowed:
            raise ApiError(
                429,
                "rate_limited",
                f"try again in {retry_after}s",
                headers={"Retry-After": str(retry_after)},
            )

        content_type = (self.headers.get("Content-Type") or "").lower()
        if not content_type.startswith("application/json"):
            raise ApiError(
                415,
                "unsupported_media_type",
                "Content-Type must be application/json",
            )

        raw = self._read_body()
        if not token_allowed(
            self.headers.get("Authorization"), str(self.ctx.config["auth_token"])
        ):
            raise ApiError(401, "unauthorized", "invalid or missing Authorization header")

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "invalid_json", "body is not valid UTF-8 JSON")

        batch = validate_batch(payload, self.ctx.config)

        signing_key = str(self.ctx.config["signing_key"])
        if signing_key:
            ok, reason = verify_signature(batch, signing_key)
            if not ok:
                raise ApiError(401, "unauthorized", reason)

        body_hash = hashlib.sha256(raw).hexdigest()
        stored, duplicates, new_events = self.ctx.store.store_batch(
            batch, body_hash, bool(self.ctx.config["strict_replay"])
        )
        # Push to live WebSocket subscribers only after the commit succeeded.
        self.ctx.bus.publish_many(new_events)
        return self._send_json(
            200,
            {
                "ok": True,
                "received": len(batch["events"]),
                "stored": stored,
                "duplicates": duplicates,
            },
        )

    def _handle_delete(self) -> int:
        parts = [segment for segment in urlparse(self.path).path.split("/") if segment]
        if len(parts) != 2 or parts[0] != "sessions":
            raise ApiError(404, "not_found", "DELETE /sessions/{token}")
        allowed, retry_after = self.ctx.write_limiter.allow(self._client_ip())
        if not allowed:
            raise ApiError(
                429,
                "rate_limited",
                f"try again in {retry_after}s",
                headers={"Retry-After": str(retry_after)},
            )
        result = self.ctx.store.revoke_session(self._validate_token(parts[1]))
        return self._send_json(200, {"ok": True, **result})

    def _handle_session_events(self, token: str, query: Dict[str, List[str]]) -> int:
        token = self._validate_token(token)
        allowed, retry_after = self.ctx.read_limiter.allow("token:" + token)
        if not allowed:
            raise ApiError(
                429,
                "rate_limited",
                f"try again in {retry_after}s",
                headers={"Retry-After": str(retry_after)},
            )
        since = self._int_param(query, "since", 0, minimum=0)
        limit = self._int_param(query, "limit", 100, minimum=1, maximum=500)
        page = self.ctx.store.read_session_events(token, since, limit)
        return self._send_json(200, page)

    def _handle_admin_events(self, query: Dict[str, List[str]]) -> int:
        admin_token = str(self.ctx.config["admin_token"])
        if not admin_token:
            raise ApiError(
                403,
                "admin_disabled",
                "set admin_token to enable the /events feed",
            )
        if not token_allowed(self.headers.get("Authorization"), admin_token):
            raise ApiError(401, "unauthorized", "admin token required")
        allowed, retry_after = self.ctx.read_limiter.allow("admin:" + self._client_ip())
        if not allowed:
            raise ApiError(
                429,
                "rate_limited",
                f"try again in {retry_after}s",
                headers={"Retry-After": str(retry_after)},
            )
        since = self._int_param(query, "since", 0, minimum=0)
        limit = self._int_param(query, "limit", 100, minimum=1, maximum=500)
        universe_id = self._optional_int_param(query, "universe_id")
        type_name = (query.get("type") or [None])[0]
        if type_name is not None and not TYPE_NAME_RE.match(type_name):
            raise ApiError(400, "invalid_query", "type must match ^[a-z0-9_.-]{1,32}$")
        page = self.ctx.store.read_all_events(since, limit, universe_id, type_name)
        return self._send_json(200, page)

    # -- WebSocket stream ---------------------------------------------------
    def _handle_stream(self, parsed, query: Dict[str, List[str]]) -> int:  # noqa: ANN001
        if (self.headers.get("Upgrade") or "").lower() != "websocket":
            raise ApiError(
                426,
                "upgrade_required",
                "connect with a WebSocket client (Upgrade: websocket)",
                headers={"Upgrade": "websocket"},
            )
        client_key = self.headers.get("Sec-WebSocket-Key")
        if not client_key:
            raise ApiError(400, "invalid_handshake", "Sec-WebSocket-Key is required")
        if (self.headers.get("Sec-WebSocket-Version") or "13") != "13":
            raise ApiError(400, "invalid_handshake", "only WebSocket version 13 is supported")

        token_values = query.get("token")
        token: Optional[str] = None
        if token_values:
            token = self._validate_token(token_values[0])
            if not self.ctx.store.session_valid(token):
                raise ApiError(404, "unknown_session", "session token not found or expired")

        if "since" in query:
            since = self._int_param(query, "since", 0, minimum=0)
            replay = True
        else:
            since = self.ctx.store.max_seq()
            replay = False

        name = self._sanitize_name((query.get("name") or [""])[0])
        subscriber = self.ctx.bus.subscribe(token, name)
        write_lock = threading.Lock()
        cursor = since
        try:
            self._ws_accept(client_key)
            self._ws_send_json(
                write_lock,
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "hello",
                    "protocol": PROTOCOL_VERSION,
                    "stream": "session" if token else "global",
                    "name": name or None,
                    "cursor": since,
                    "heartbeatSeconds": WS_HEARTBEAT_SECONDS,
                },
            )

            if replay:
                max_replay = int(self.ctx.config["max_replay_events"])
                sent_replay = 0
                while sent_replay < max_replay:
                    rows = self.ctx.store.read_stream(cursor, token, 500)
                    if not rows:
                        break
                    for event in rows:
                        self._ws_send_text(write_lock, json.dumps(stream_event_payload(event)))
                        cursor = event["seq"]
                        sent_replay += 1
                    if len(rows) < 500:
                        break
                if sent_replay >= max_replay:
                    self._ws_send_json(
                        write_lock,
                        {
                            "v": PROTOCOL_VERSION,
                            "kind": "error",
                            "error": "replay_too_large",
                            "detail": "use GET /sessions/{token}/events to page through history",
                            "cursor": cursor,
                        },
                    )
                    self._ws_close(write_lock, 1013)
                    return 101

            sender = threading.Thread(
                target=self._stream_sender,
                args=(subscriber, write_lock, cursor),
                name="clientbridge-stream",
                daemon=True,
            )
            sender.start()

            self._ws_read_loop(write_lock)
            return 101
        except (WebSocketProtocolError, OSError, ValueError, BrokenPipeError, ConnectionResetError):
            return 101
        except Exception:  # noqa: BLE001
            LOG.exception("stream error")
            return 101
        finally:
            self.ctx.bus.unsubscribe(subscriber)
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass

    def _ws_accept(self, client_key: str) -> None:
        accept = ws_accept_key(client_key)
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        self.wfile.write(response.encode("ascii"))
        self.wfile.flush()
        self.close_connection = True

    def _ws_send_frame(self, write_lock: threading.Lock, opcode: int, payload: bytes = b"") -> None:
        with write_lock:
            self.wfile.write(ws_frame(opcode, payload))
            self.wfile.flush()

    def _ws_send_text(self, write_lock: threading.Lock, text: str) -> None:
        self._ws_send_frame(write_lock, 0x1, text.encode("utf-8"))

    def _ws_send_json(self, write_lock: threading.Lock, payload: Dict[str, Any]) -> None:
        self._ws_send_text(write_lock, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    def _ws_close(self, write_lock: threading.Lock, code: int = 1000) -> None:
        try:
            self._ws_send_frame(write_lock, 0x8, struct.pack("!H", code))
        except OSError:
            pass

    def _ws_read_loop(self, write_lock: threading.Lock) -> None:
        while True:
            message = ws_read_message(self.rfile)
            if message is None:
                return
            opcode, payload = message
            if opcode == 0x8:
                self._ws_close(write_lock, 1000)
                return
            if opcode == 0x9:
                self._ws_send_frame(write_lock, 0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                # The stream is one-way. Accept an application-level close or
                # ping; ignore everything else instead of inventing commands.
                try:
                    data = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                kind = data.get("kind") if isinstance(data, dict) else None
                if kind == "close":
                    self._ws_close(write_lock, 1000)
                    return
                if kind == "ping":
                    self._ws_send_json(
                        write_lock, {"v": PROTOCOL_VERSION, "kind": "pong", "at": int(time.time())}
                    )

    def _stream_sender(
        self, subscriber: Subscriber, write_lock: threading.Lock, initial_cursor: int
    ) -> None:
        cursor = initial_cursor
        while not subscriber.closed:
            try:
                event = subscriber.queue.get(timeout=WS_HEARTBEAT_SECONDS)
            except queue.Empty:
                try:
                    self._ws_send_frame(write_lock, 0x9, b"")
                except (OSError, ValueError):
                    break
                continue
            if event is None:
                break
            if event["seq"] <= cursor:
                continue
            cursor = event["seq"]
            try:
                self._ws_send_text(
                    write_lock, json.dumps(stream_event_payload(event), separators=(",", ":"), ensure_ascii=False)
                )
            except (OSError, ValueError):
                break
        subscriber.closed = True
        try:
            self.connection.close()
        except OSError:
            pass

    def _sanitize_name(self, value: str) -> str:
        cleaned = NAME_RE.sub("", value.strip())[:32]
        return cleaned

    # -- request helpers ----------------------------------------------------
    def _client_ip(self) -> str:
        # Only used as a rate-limit bucket key. Do not trust X-Forwarded-For.
        return str(self.client_address[0]) if self.client_address else "unknown"

    def _read_body(self) -> bytes:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise ApiError(411, "length_required", "Content-Length is required")
        try:
            length = int(length_header)
        except ValueError:
            raise ApiError(400, "invalid_payload", "invalid Content-Length")
        limit = int(self.ctx.config["max_body_bytes"])
        if length < 0 or length > limit:
            # Do not read a body larger than the limit: answer and drop the
            # connection so keep-alive framing stays correct.
            self.close_connection = True
            raise ApiError(
                413,
                "payload_too_large",
                f"body limit is {limit} bytes",
                headers={"Connection": "close"},
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ApiError(400, "invalid_payload", "incomplete body")
        return raw

    def _validate_token(self, token: str) -> str:
        if not SESSION_RE.match(token):
            raise ApiError(400, "invalid_session", "token must be cb_<32 lowercase hex>")
        return token

    def _int_param(
        self,
        query: Dict[str, List[str]],
        name: str,
        default: int,
        minimum: int,
        maximum: int = 2**63 - 1,
    ) -> int:
        values = query.get(name)
        if not values:
            return default
        try:
            value = int(values[0])
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_query", f"{name} must be an integer")
        if value < minimum or value > maximum:
            raise ApiError(
                400, "invalid_query", f"{name} must be between {minimum} and {maximum}"
            )
        return value

    def _optional_int_param(
        self, query: Dict[str, List[str]], name: str
    ) -> Optional[int]:
        values = query.get(name)
        if not values:
            return None
        try:
            return int(values[0])
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_query", f"{name} must be an integer")


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], ctx: AppContext):
        self.ctx = ctx
        super().__init__(address, Handler)

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        LOG.exception("connection error from %s", client_address)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def _coerce(key: str, value: Any) -> Any:
    if key in ("port", "rate_limit", "read_rate_limit", "max_body_bytes",
               "max_batch_events", "max_event_bytes", "max_replay_events",
               "buffer_events", "buffer_seconds"):
        return int(value)
    if key in ("session_ttl_hours",):
        return float(value)
    if key in ("strict_replay", "allow_insecure", "quiet"):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    return "" if value is None else str(value)


def _apply(config: Dict[str, Any], source: Dict[str, Any]) -> None:
    for key, value in source.items():
        if value is None:
            continue
        if key not in DEFAULTS:
            raise ValueError(f"unknown configuration key: {key}")
        config[key] = _coerce(key, value)


def load_config(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    parser = argparse.ArgumentParser(
        prog="clientbridge_api.py",
        description="ClientBridge reference API (protocol v1, zero dependencies).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", help="path to a JSON config file")
    parser.add_argument("--host", help="interface to bind")
    parser.add_argument("--port", type=int, help="TCP port")
    parser.add_argument("--auth-token", help="shared secret required for POST")
    parser.add_argument("--admin-token", help="secret required for GET /events")
    parser.add_argument("--signing-key", help="HMAC key used to verify batch sig")
    parser.add_argument("--buffer-events", type=int, help="in-memory event ring size")
    parser.add_argument("--buffer-seconds", type=int, help="max age kept for replay")
    parser.add_argument("--session-ttl-hours", type=float, help="session lifetime")
    parser.add_argument("--rate-limit", type=int, help="writes/minute/IP")
    parser.add_argument("--read-rate-limit", type=int, help="reads/minute")
    parser.add_argument("--max-body-bytes", type=int, help="max POST body size")
    parser.add_argument("--max-batch-events", type=int, help="max events per batch")
    parser.add_argument("--max-event-bytes", type=int, help="max serialized event size")
    parser.add_argument(
        "--max-replay-events",
        type=int,
        help="max events replayed to a WebSocket that reconnects with ?since=",
    )
    parser.add_argument(
        "--strict-replay",
        action="store_const",
        const=True,
        default=None,
        help="respond 409 to an exact replay of a recent batch",
    )
    parser.add_argument(
        "--allow-insecure",
        action="store_const",
        const=True,
        default=None,
        help="allow binding to a public interface with no auth/signing",
    )
    parser.add_argument(
        "--quiet",
        action="store_const",
        const=True,
        default=None,
        help="less logging",
    )
    args = parser.parse_args(argv)

    config = dict(DEFAULTS)

    if args.config:
        with open(args.config, "r", encoding="utf-8") as handle:
            file_config = json.load(handle)
        if not isinstance(file_config, dict):
            raise ValueError("config file must contain a JSON object")
        _apply(config, file_config)

    env_config = {}
    for env_name, key in ENV_KEYS.items():
        if env_name in os.environ:
            env_config[key] = os.environ[env_name]
    _apply(config, env_config)

    cli = {
        "host": args.host,
        "port": args.port,
        "auth_token": args.auth_token,
        "admin_token": args.admin_token,
        "signing_key": args.signing_key,
        "buffer_events": args.buffer_events,
        "buffer_seconds": args.buffer_seconds,
        "session_ttl_hours": args.session_ttl_hours,
        "rate_limit": args.rate_limit,
        "read_rate_limit": args.read_rate_limit,
        "max_body_bytes": args.max_body_bytes,
        "max_batch_events": args.max_batch_events,
        "max_event_bytes": args.max_event_bytes,
        "max_replay_events": args.max_replay_events,
        "strict_replay": args.strict_replay,
        "allow_insecure": args.allow_insecure,
        "quiet": args.quiet,
    }
    _apply(config, cli)

    if not (0 < int(config["port"]) < 65536):
        raise ValueError("port must be between 1 and 65535")
    if int(config["buffer_events"]) < 100:
        raise ValueError("buffer_events must be >= 100")
    if int(config["buffer_seconds"]) < 0:
        raise ValueError("buffer_seconds must be >= 0 (0 disables age trimming)")
    if float(config["session_ttl_hours"]) <= 0:
        raise ValueError("session_ttl_hours must be > 0")
    if int(config["rate_limit"]) < 1 or int(config["read_rate_limit"]) < 1:
        raise ValueError("rate limits must be >= 1")
    if int(config["max_body_bytes"]) < 1024:
        raise ValueError("max_body_bytes must be >= 1024")
    if int(config["max_batch_events"]) < 1:
        raise ValueError("max_batch_events must be >= 1")
    if int(config["max_replay_events"]) < 1:
        raise ValueError("max_replay_events must be >= 1")

    loopback = config["host"] in ("127.0.0.1", "::1", "localhost")
    if not loopback and not config["auth_token"] and not config["signing_key"]:
        if not config["allow_insecure"]:
            raise ValueError(
                "refusing to bind a non-loopback interface without --auth-token "
                "or --signing-key (use --allow-insecure only behind a trusted proxy)"
            )
    return config


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _cleanup_loop(ctx: AppContext) -> None:
    while not ctx.shutdown_event.wait(600):
        try:
            removed = ctx.store.cleanup()
            if removed["events"] or removed["sessions"]:
                LOG.info(
                    "cleanup removed %d events and %d sessions",
                    removed["events"],
                    removed["sessions"],
                )
        except Exception:  # noqa: BLE001
            LOG.exception("cleanup failed")


def _print_banner(config: Dict[str, Any], ctx: AppContext) -> None:
    host = config["host"]
    port = config["port"]
    base = f"http://{host}:{port}"
    print()
    print(f"  {API_NAME} {API_VERSION}  (protocol {PROTOCOL_VERSION})")
    print("  " + "-" * 58)
    print(f"  listening on       {base}")
    print(f"  game endpoint      {base}/clientbridge")
    print(f"  session reads      {base}/sessions/<token>/events")
    print(f"  live stream        ws://{host}:{port}/stream")
    print(f"  admin feed         {base}/events")
    print(f"  health             {base}/health")
    print(f"  storage            in memory (no disk)")
    print(
        "  post auth          "
        + ("shared token" if config["auth_token"] else "open (loopback only)")
    )
    print(
        "  signatures         "
        + ("required (HMAC-SHA256)" if config["signing_key"] else "optional")
    )
    print(
        f"  replay buffer      {config['buffer_events']} events / "
        f"{config['buffer_seconds']}s max age"
    )
    print(f"  docs               {PROJECT_URL}")
    print()
    print("  Press Ctrl+C to stop.")
    print()
    counts = ctx.store.counts()
    LOG.info(
        "started on %s:%s (events=%d sessions=%d)",
        host,
        port,
        counts["events"],
        counts["sessions"],
    )


def serve(config: Dict[str, Any]) -> int:
    level = logging.WARNING if config["quiet"] else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    ctx = AppContext(config)
    ctx.store.cleanup()
    cleanup_thread = threading.Thread(
        target=_cleanup_loop, args=(ctx,), name="clientbridge-cleanup", daemon=True
    )
    cleanup_thread.start()

    try:
        server = ApiServer((config["host"], int(config["port"])), ctx)
    except OSError as exc:
        LOG.error("could not bind %s:%s: %s", config["host"], config["port"], exc)
        ctx.shutdown_event.set()
        return 1

    _print_banner(config, ctx)

    def stop(_signum: int, _frame: Any) -> None:
        LOG.info("stopping")
        ctx.shutdown_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
    except ValueError:
        pass  # not the main thread (tests import serve in a thread)

    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        ctx.shutdown_event.set()
        server.server_close()
        LOG.info("stopped")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    try:
        config = load_config(argv)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        return serve(config)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

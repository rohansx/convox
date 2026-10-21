"""FastAPI dependency providers, the call registry Protocol and its in-memory default.

The registry is what the console reads for the calls list and for the per-call snapshot
on page load. It lives in process memory (ADR 0009); the session owns the real one.
"""

from __future__ import annotations

import base64
import binascii
import copy
import dataclasses
import hashlib
import hmac
import math
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import HTTPException, Request, status
from starlette.requests import HTTPConnection

from dubhash.config.pairs import PairConfig
from dubhash.config.settings import Settings
from dubhash.events import EventBus
from dubhash.types import CallEvent

ACTIVE_STATUSES = frozenset({"ringing", "active", "in_progress"})
MARKER_KINDS = frozenset({"dtmf", "barge_in", "bypass", "gap", "provider_error"})


@runtime_checkable
class CallRegistry(Protocol):
    def list_calls(self) -> list[dict[str, Any]]: ...

    def get_call(self, call_id: str) -> dict[str, Any] | None: ...


def _as_dict(obj: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return dict(obj)


class InMemoryCallRegistry:
    """Calls keyed by id. Active calls first, then the most recently ended `max_ended`.
    `apply_event` keeps state, queue depth, turns and markers current from the EventBus,
    so a page reload mid-call shows the history so far."""

    def __init__(self, *, max_ended: int = 20, max_turns: int = 500) -> None:
        self._calls: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_ended = max_ended
        self._max_turns = max_turns

    def put(self, call: dict[str, Any]) -> None:
        call_id = call["call_id"]
        row = {
            "status": "ringing",
            "state": "IDLE",
            "queue_depth": 0,
            "escape_used": False,
            "turns": [],
            "markers": [],
            "legs": {},
            **self._calls.get(call_id, {}),
            **call,
        }
        self._calls[call_id] = row
        self._calls.move_to_end(call_id)
        self._evict()

    def update(self, call_id: str, **fields: Any) -> None:
        if call_id in self._calls:
            self._calls[call_id].update(fields)
            self._evict()

    def remove(self, call_id: str) -> None:
        self._calls.pop(call_id, None)

    def list_calls(self) -> list[dict[str, Any]]:
        calls = list(reversed(self._calls.values()))
        active = [c for c in calls if c["status"] in ACTIVE_STATUSES]
        ended = [c for c in calls if c["status"] not in ACTIVE_STATUSES]
        return [self._summary(c) for c in active + ended]

    def get_call(self, call_id: str) -> dict[str, Any] | None:
        call = self._calls.get(call_id)
        return copy.deepcopy(call) if call is not None else None

    def apply_event(self, ev: CallEvent) -> None:
        call = self._calls.get(ev.call_id)
        if call is None:
            return
        p = ev.payload
        if ev.kind == "state":
            call["state"] = p.get("to", p.get("state", call["state"]))
            call["queue_depth"] = p.get("queue_depth", call["queue_depth"])
        elif ev.kind == "turn":
            turn = _as_dict(p.get("turn", p))
            turns = call["turns"]
            for i, t in enumerate(turns):
                if t.get("seq") == turn.get("seq"):
                    turns[i] = {**t, **turn}
                    break
            else:
                turns.append(turn)
                del turns[: -self._max_turns]
        elif ev.kind == "call":
            fields = {k: v for k, v in p.items() if k != "call_id"}
            if "turns" in fields:  # end-of-call summary count; keep the turn list intact
                fields["turns_total"] = fields.pop("turns")
            call.update(fields)
            self._evict()
        elif ev.kind == "leg":
            call["legs"][p.get("side", "?")] = p.get("status")
        if ev.kind == "bypass":
            call["escape_used"] = True
        if ev.kind in MARKER_KINDS:
            call["markers"].append({"kind": ev.kind, "ts_ms": ev.ts_ms, **p})
            del call["markers"][: -self._max_turns]

    def _summary(self, call: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in call.items() if k not in ("turns", "markers")}
        spoken = [t for t in call["turns"] if not t.get("backchannel")]
        out["n_turns"] = len(spoken)
        if out.get("p50_first_audio_ms") is None:
            fa = sorted(
                t["t_play_start"] - t["t_speech_end"]
                for t in spoken
                if t.get("t_play_start") is not None and t.get("t_speech_end") is not None
            )
            if fa:
                out["p50_first_audio_ms"] = fa[max(1, math.ceil(len(fa) / 2)) - 1]
        return out

    def _evict(self) -> None:
        ended = [k for k, c in self._calls.items() if c["status"] not in ACTIVE_STATUSES]
        for k in ended[: max(0, len(ended) - self._max_ended)]:
            del self._calls[k]


# --- dependency providers -------------------------------------------------------------


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


def get_registry(request: Request) -> CallRegistry:
    return request.app.state.registry


def get_writer(request: Request) -> Any:
    return request.app.state.writer


DEFAULT_PASSWORDS = frozenset({"", "change-me"})


COOKIE = "dubhash_console"
COOKIE_TTL_S = 12 * 3600


def console_cookie(password: str, now: float | None = None) -> str:
    """`<expiry>.<hmac>` keyed on the console password, so rotating it logs everyone out."""
    exp = int((now if now is not None else time.time()) + COOKIE_TTL_S)
    sig = hmac.new(password.encode(), f"console-v1:{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def _cookie_ok(value: str | None, password: str) -> bool:
    if not value or "." not in value:
        return False
    exp, _, sig = value.partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    want = hmac.new(password.encode(), f"console-v1:{exp}".encode(), hashlib.sha256).hexdigest()
    return secrets.compare_digest(sig, want)


def _basic_ok(request: Request, password: str) -> bool:
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        _, _, got = base64.b64decode(encoded).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return secrets.compare_digest(got.encode(), password.encode())


def require_console_auth(request: Request) -> None:
    """Login cookie (from /login) or HTTP Basic, password = CONSOLE_PASSWORD. Off in
    env=test. In prod a default/empty password locks the console instead of exposing live
    transcripts. Browsers asking for a page are redirected to /login (some never show the
    Basic popup); API clients get the Basic challenge."""
    settings: Settings = request.app.state.settings
    if settings.dubhash_env == "test":
        return
    expected = settings.console_password
    if settings.dubhash_env == "prod" and expected in DEFAULT_PASSWORDS:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "console password not set")
    if _cookie_ok(request.cookies.get(COOKIE), expected) or _basic_ok(request, expected):
        return
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise HTTPException(
            status.HTTP_303_SEE_OTHER, headers={"Location": f"/login?next={target}"}
        )
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "authentication required",
        headers={"WWW-Authenticate": 'Basic realm="dubhash console"'},
    )


# --- sessions (contract: dubhash/session/{call_session,registry,providers}.py) ----------
# Imported lazily so the API imports cleanly while the session package is in flight, and
# so tests can override these two providers with a FakeSession double.


def get_session_registry(conn: HTTPConnection) -> Any:
    """The process-wide SessionRegistry (create/get/remove/list of live CallSessions)."""
    registry = getattr(conn.app.state, "sessions", None)
    if registry is None:
        from dubhash.session.registry import SessionRegistry

        registry = conn.app.state.sessions = SessionRegistry()
    return registry


def get_session_factory(conn: HTTPConnection) -> Callable[..., Any]:
    """factory(call_id, pair, *, mode, escape_dtmf, record, co_located, opening_line[, auto]).
    auto (session.language.AutoLang) is passed only for calls with lang "auto" sides."""
    state = conn.app.state

    def make(
        call_id: str,
        pair: PairConfig,
        *,
        mode: Literal["interpret", "bridge"] = "interpret",
        escape_dtmf: str = "0",
        record: bool = False,
        co_located: bool = False,
        opening_line: Literal["short", "full", "off"] = "full",
        auto: Any = None,
    ) -> Any:
        from dubhash.session.call_session import CallSession
        from dubhash.session.providers import build_providers

        session = CallSession(
            call_id,
            pair,
            build_providers(pair, state.settings, lid=auto is not None),
            bus=state.bus,
            writer=state.writer,
            mode=mode,
            escape_dtmf=escape_dtmf,
            record=record,
            co_located=co_located,
            opening_line=opening_line,
            auto=auto,
        )
        session.start_prewarm()  # STT/TTS/MT sockets open while the phones join (L7)
        return session

    return make

"""FastAPI dependency providers, the call registry Protocol and its in-memory default.

The registry is what the console reads for the calls list and for the per-call snapshot
on page load. It lives in process memory (ADR 0009); the session owns the real one.
"""

from __future__ import annotations

import base64
import binascii
import copy
import dataclasses
import secrets
from collections import OrderedDict
from typing import Any, Protocol, runtime_checkable

from fastapi import HTTPException, Request, status

from dubhash.config.settings import Settings
from dubhash.events import EventBus
from dubhash.types import CallEvent

ACTIVE_STATUSES = frozenset({"ringing", "in_progress"})
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
            call.update({k: v for k, v in p.items() if k != "call_id"})
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


def require_console_auth(request: Request) -> None:
    """HTTP Basic, any username, password = CONSOLE_PASSWORD. Off in env=test. In prod a
    default/empty password locks the console instead of exposing live transcripts."""
    settings: Settings = request.app.state.settings
    if settings.dubhash_env == "test":
        return
    expected = settings.console_password
    if settings.dubhash_env == "prod" and expected in DEFAULT_PASSWORDS:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "console password not set")
    challenge = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "authentication required",
        headers={"WWW-Authenticate": 'Basic realm="dubhash console"'},
    )
    header = request.headers.get("authorization", "")
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic":
        raise challenge
    try:
        _, _, password = base64.b64decode(encoded).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        raise challenge from None
    if not secrets.compare_digest(password.encode(), expected.encode()):
        raise challenge

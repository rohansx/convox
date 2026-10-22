"""Platform API: POST /v1/calls, GET /v1/calls/{id}, GET /v1/calls/{id}/turns,
POST /v1/calls/{id}/hangup (docs/tech-spec/api.md).

The `CallBook` is the API's own per-call bookkeeping next to the SessionRegistry: the
provider call sids, per-leg status from webhooks, which sides have attached a websocket,
why the call ended, and the supervisor task that enforces the no-answer rule and removes
the session from the registry `linger_s` after it ends (so GET still works for a while).

Decisions (not fixed by api.md):
  - Both dials happen before the 201, in parallel, so a provider rejection can be the
    documented 502 `provider_error`. The session is hung up with reason
    "provider_error" and the call reads as `failed`.
  - The no-answer timer counts until both legs have attached their websocket.
  - Session status maps to api.md: ringing -> ringing, active -> in_progress,
    ended -> failed (no answer, dial or leg failure) or completed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.requests import HTTPConnection

from convox.api.auth import require_platform_key
from convox.api.deps import get_session_factory, get_session_registry
from convox.api.schemas import (
    ApiError,
    CreateCallRequest,
    CreateCallResponse,
    HangupResponse,
    validation_error,
)
from convox.config.pairs import (
    AUTO,
    PairConfig,
    auto_pair,
    find_pair,
    language_sides,
    load_pairs,
)
from convox.config.settings import Settings
from convox.interp.phrases import DEFAULT_VERTICAL
from convox.session.language import AutoLang, ResolverConfig
from convox.telephony import Dialer, DialError, ProviderUnavailable, build_dialer, leg_ws_url
from convox.types import Side

log = logging.getLogger(__name__)

NO_ANSWER_S = 25.0
LINGER_S = 300.0
FAILED_REASONS = frozenset(
    {"no_answer", "provider_error", "busy", "failed", "no-answer", "timeout", "canceled"}
)
SIDES: tuple[Side, Side] = ("a", "b")


def new_call_id() -> str:
    """ "c_" + 10 lowercase base32 characters (50 bits) from `secrets`."""
    return "c_" + base64.b32encode(secrets.token_bytes(10)).decode().lower()[:10]


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(slots=True)
class LegState:
    lang: str
    provider_call_sid: str | None = None
    status: str = "dialing"
    attached: bool = False
    stream_sid: str | None = None
    sample_rate: int | None = None
    answered_at: str | None = None
    closed_at: str | None = None
    close_reason: str | None = None


@dataclass(slots=True)
class CallHandle:
    call_id: str
    session: Any
    provider: str
    mode: str
    record: bool
    legs: dict[Side, LegState]
    webhook: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    ended_at: str | None = None
    end_reason: str | None = None
    both_attached: asyncio.Event = field(default_factory=asyncio.Event)
    seen_statuses: set[tuple[str, str]] = field(default_factory=set)
    _t0: float = field(default_factory=time.monotonic)

    def now_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def end(self, reason: str) -> None:
        if self.end_reason is None:
            self.end_reason = reason

    def api_status(self) -> str:
        status = getattr(self.session, "status", "ringing")
        if status == "ringing":
            return "ringing"
        if status == "active":
            return "in_progress"
        return "failed" if self.end_reason in FAILED_REASONS else "completed"


class CallBook:
    def __init__(self, *, no_answer_s: float | None = NO_ANSWER_S, linger_s: float = LINGER_S):
        self.no_answer_s = no_answer_s
        self.linger_s = linger_s
        self._calls: dict[str, CallHandle] = {}
        self._sids: dict[str, tuple[str, Side]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    def add(self, handle: CallHandle) -> None:
        self._calls[handle.call_id] = handle

    def get(self, call_id: str) -> CallHandle | None:
        return self._calls.get(call_id)

    def pop(self, call_id: str) -> None:
        handle = self._calls.pop(call_id, None)
        if handle is not None:
            for leg in handle.legs.values():
                if leg.provider_call_sid:
                    self._sids.pop(leg.provider_call_sid, None)

    def record_sid(self, call_id: str, side: Side, sid: str) -> None:
        handle = self._calls.get(call_id)
        if handle is not None:
            handle.legs[side].provider_call_sid = sid
            self._sids[sid] = (call_id, side)

    def find_sid(self, sid: str) -> tuple[str, Side] | None:
        return self._sids.get(sid)

    def leg_attached(self, call_id: str, side: Side) -> bool:
        """Note a websocket for (call, side). False if that side already has one."""
        handle = self._calls.get(call_id)
        if handle is None:
            return True  # session not created through this API; the session decides
        leg = handle.legs[side]
        if leg.attached:
            return False
        leg.attached = True
        leg.status = "in-progress"
        leg.answered_at = leg.answered_at or utcnow_iso()
        if all(handle.legs[s].attached for s in SIDES):
            handle.both_attached.set()
        return True

    def leg_closed(self, call_id: str, side: Side, reason: str | None) -> None:
        handle = self._calls.get(call_id)
        if handle is not None:
            leg = handle.legs[side]
            leg.closed_at = leg.closed_at or utcnow_iso()
            leg.close_reason = leg.close_reason or reason

    def spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def supervise(self, handle: CallHandle, registry: Any, *, no_answer: bool) -> None:
        session = handle.session
        try:
            if no_answer and self.no_answer_s is not None:
                closed = asyncio.ensure_future(session.wait_closed())
                both = asyncio.ensure_future(handle.both_attached.wait())
                try:
                    done, _ = await asyncio.wait(
                        {closed, both}, timeout=self.no_answer_s, return_when="FIRST_COMPLETED"
                    )
                finally:
                    both.cancel()
                    closed.cancel()
                if not done:
                    log.info("call.no_answer", extra={"call_id": handle.call_id})
                    handle.end("no_answer")
                    await session.hangup("no_answer")
            await session.wait_closed()
            handle.ended_at = handle.ended_at or utcnow_iso()
            await asyncio.sleep(self.linger_s)
        finally:
            registry.remove(handle.call_id)
            self.pop(handle.call_id)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


# --- dependency providers -------------------------------------------------------------


def get_call_book(conn: HTTPConnection) -> CallBook:
    book = getattr(conn.app.state, "call_book", None)
    if book is None:
        book = conn.app.state.call_book = CallBook()
    return book


def get_pairs(conn: HTTPConnection) -> dict[str, PairConfig]:
    pairs = getattr(conn.app.state, "pairs", None)
    if pairs is None:
        pairs = conn.app.state.pairs = load_pairs(conn.app.state.settings.pairs_file)
    return pairs


def get_dialer_factory(conn: HTTPConnection) -> Callable[[str], Dialer]:
    """provider name -> Dialer (cached per app, sharing one httpx client)."""
    state = conn.app.state
    settings: Settings = state.settings
    if getattr(state, "dialers", None) is None:
        state.dialers = {}

    def get(provider: str) -> Dialer:
        if provider not in state.dialers:
            if getattr(state, "http", None) is None:
                state.http = httpx.AsyncClient(timeout=10.0)
            state.dialers[provider] = build_dialer(provider, settings, client=state.http)
        return state.dialers[provider]

    return get


def _settings(conn: HTTPConnection) -> Settings:
    return conn.app.state.settings


SettingsDep = Annotated[Settings, Depends(_settings)]
Book = Annotated[CallBook, Depends(get_call_book)]
Sessions = Annotated[Any, Depends(get_session_registry)]
Factory = Annotated[Callable[..., Any], Depends(get_session_factory)]
Pairs = Annotated[dict[str, PairConfig], Depends(get_pairs)]
Dialers = Annotated[Callable[[str], Dialer], Depends(get_dialer_factory)]

router = APIRouter(prefix="/v1/calls", dependencies=[Depends(require_platform_key)])


def unsupported_pair(lang_a: str, lang_b: str) -> ApiError:
    return ApiError(422, "unsupported_pair", f"no config for pair {lang_a[:2]}-{lang_b[:2]}")


def call_pair(
    pairs: dict[str, PairConfig],
    settings: Settings,
    langs: dict[Side, str],
    hints: dict[Side, str | None],
) -> tuple[PairConfig, AutoLang | None]:
    """The call's pair, and its AutoLang when a side's lang is "auto" (then the pair is
    the starting one, config.pairs.auto_pair). Raises the 422 unsupported_pair."""
    if AUTO not in langs.values():
        pair = find_pair(pairs, langs["a"], langs["b"])
        if pair is None:
            raise unsupported_pair(langs["a"], langs["b"])
        return pair, None
    try:
        pair = auto_pair(pairs, langs, hints)
    except ValueError:
        raise unsupported_pair(langs["a"], langs["b"]) from None
    auto = AutoLang(
        hints={s: hints.get(s) for s in SIDES if langs[s] == AUTO},
        sides=language_sides(pairs),
        config=ResolverConfig(min_speech_ms=settings.lang_min_speech_ms),
        stt_open=settings.auto_stt_open,
    )
    return pair, auto


def auto_kw(auto: AutoLang | None) -> dict[str, Any]:
    """Session-factory kwargs for an AutoLang (none for fixed pairs)."""
    return {"auto": auto} if auto is not None else {}


def note_console(conn: HTTPConnection, row: dict[str, Any]) -> None:
    """Show the new call on the console's calls list (InMemoryCallRegistry)."""
    put = getattr(conn.app.state.registry, "put", None)
    if put is not None:
        put(row)


@router.post("", status_code=201, response_model=CreateCallResponse)
async def create_call(
    request: Request,
    settings: SettingsDep,
    book: Book,
    sessions: Sessions,
    factory: Factory,
    pairs: Pairs,
    dialers: Dialers,
) -> JSONResponse:
    try:
        raw = await request.json()
    except ValueError:
        raise ApiError(400, "invalid_body", "body is not valid JSON") from None
    try:
        req = CreateCallRequest.model_validate(raw)
    except ValidationError as e:
        raise validation_error(e) from None
    pair, auto = call_pair(
        pairs,
        settings,
        {"a": req.a.lang, "b": req.b.lang},
        {"a": req.a.lang_hint, "b": req.b.lang_hint},
    )
    try:
        dialer = dialers(req.provider)
    except ProviderUnavailable as e:
        raise ApiError(503, "provider_unavailable", str(e)) from None

    call_id = new_call_id()
    session = factory(
        call_id,
        pair,
        mode=req.mode,
        escape_dtmf=req.escape_dtmf,
        record=req.record,
        opening_line=settings.pstn_opening_line,
        **auto_kw(auto),
        vertical=req.vertical or DEFAULT_VERTICAL,
    )
    sessions.create(call_id, session)
    handle = CallHandle(
        call_id=call_id,
        session=session,
        provider=req.provider,
        mode=req.mode,
        record=req.record,
        legs={"a": LegState(req.a.lang), "b": LegState(req.b.lang)},
        webhook=str(req.webhook) if req.webhook else None,
    )
    book.add(handle)
    note_console(
        request,
        {
            "call_id": call_id,
            "a_lang": req.a.lang,
            "b_lang": req.b.lang,
            "a_number": req.a.number,
            "b_number": req.b.number,
            "provider": req.provider,
            "mode": req.mode,
            "status": "ringing",
        },
    )
    if handle.webhook:
        from convox.api.webhooks import start_notifier

        start_notifier(request, handle)

    numbers = {"a": req.a.number, "b": req.b.number}
    results = await asyncio.gather(
        *(
            dialer.dial(call_id, s, numbers[s], req.caller_id, leg_ws_url(settings, call_id, s))
            for s in SIDES
        ),
        return_exceptions=True,
    )
    errors: list[str] = []
    for side, res in zip(SIDES, results, strict=True):
        if isinstance(res, BaseException):
            handle.legs[side].status = "failed"
            errors.append(f"leg {side}: {res}")
            if not isinstance(res, DialError):
                log.error("call.dial_crash", extra={"call_id": call_id, "error": repr(res)})
        else:
            book.record_sid(call_id, side, res)
    book.spawn(book.supervise(handle, sessions, no_answer=not errors))
    if errors:
        handle.end("provider_error")
        await session.hangup("provider_error")
        raise ApiError(502, "provider_error", "; ".join(errors), call_id=call_id)
    return JSONResponse(CreateCallResponse(call_id=call_id).model_dump(), status_code=201)


def _live(call_id: str, sessions: Any) -> Any:
    session = sessions.get(call_id)
    if session is None:
        raise ApiError(404, "call_not_found", f"no call {call_id}", call_id=call_id)
    return session


@router.get("/{call_id}")
async def get_call(call_id: str, sessions: Sessions, book: Book) -> dict[str, Any]:
    session = _live(call_id, sessions)
    summary: dict[str, Any] = dict(session.summary())
    handle = book.get(call_id)
    if handle is not None:
        status = handle.api_status()
        legs = {s: dataclasses.asdict(leg) for s, leg in handle.legs.items()}
        for leg in legs.values():
            leg.pop("attached", None)
            leg.pop("status", None)
        provider, created_at, ended_at = handle.provider, handle.created_at, handle.ended_at
        for s, detected in (summary.get("langs") or {}).items():
            if s in legs:
                legs[s]["detected"] = detected  # lang "auto": what the call resolved
    else:
        raw = summary.get("status")
        status = {"active": "in_progress", "ended": "completed"}.get(raw, raw or "ringing")
        pair = getattr(session, "pair", None)
        legs = {s: {"lang": pair.side(s).lang} for s in SIDES} if pair is not None else {}
        provider, created_at, ended_at = None, None, None
    out: dict[str, Any] = {
        "call_id": call_id,
        "status": status,
        "provider": provider,
        "mode": summary.get("mode", getattr(session, "mode", None)),
        "pair": summary.get("pair"),
        "created_at": created_at,
        "ended_at": ended_at,
        "legs": legs,
        "escape_used": bool(summary.get("escape_used", False)),
        "latency": {
            "turns": summary.get("turns", 0),
            "p50_first_audio_ms": summary.get("p50_first_audio_ms"),
            "p95_first_audio_ms": summary.get("p95_first_audio_ms"),
        },
        "duration_s": summary.get("duration_s"),
    }
    floor = getattr(session, "floor", None)
    if status == "in_progress" and floor is not None and hasattr(floor, "state"):
        state = floor.state
        out["floor"] = {
            "state": getattr(state, "name", str(state)),
            "queue_depth": getattr(floor, "queue_depth", None),
        }
    return out


def session_turns(session: Any) -> list[Any]:
    """The live session's turn log. Contract gap: CallSession does not name it yet; we
    accept `turn_log` (a list of Turn, or an object with `.turns`) or a `turns` list."""
    log_ = getattr(session, "turn_log", None)
    if log_ is None:
        log_ = getattr(session, "turns", None)
    if log_ is None:
        return []
    if not isinstance(log_, list | tuple) and hasattr(log_, "turns"):
        log_ = log_.turns
    try:
        return list(log_)
    except TypeError:
        return []


def turn_json(turn: Any, retain_text: bool, flags: dict[str, int] | None = None) -> dict[str, Any]:
    t = dataclasses.asdict(turn) if dataclasses.is_dataclass(turn) else dict(turn)
    if flags:
        t["flags"] = dict(flags)  # interpreter flags beside the row (L10)
    t.pop("call_id", None)
    if not retain_text:
        t["src_text"] = None
        t["tgt_text"] = None
    start, end = t.get("t_play_start"), t.get("t_speech_end")
    t["first_audio_ms"] = start - end if start is not None and end is not None else None
    return t


@router.get("/{call_id}/turns")
async def get_turns(call_id: str, sessions: Sessions, book: Book) -> dict[str, Any]:
    session = sessions.get(call_id)
    if session is None:
        # TODO(store): read ended calls from the `turns` table via store.db.
        raise ApiError(404, "call_not_found", f"no live call {call_id}", call_id=call_id)
    handle = book.get(call_id)
    retain = handle.record if handle is not None else bool(getattr(session, "record", False))
    flags = getattr(getattr(session, "turn_log", None), "flags", None) or {}
    turns = sorted(
        (turn_json(t, retain, flags.get(getattr(t, "seq", None))) for t in session_turns(session)),
        key=lambda t: t.get("seq") or 0,
    )
    return {"call_id": call_id, "text_retained": retain, "turns": turns}


@router.post("/{call_id}/hangup", response_model=HangupResponse)
async def hangup_call(call_id: str, sessions: Sessions, book: Book) -> dict[str, Any]:
    session = _live(call_id, sessions)
    if getattr(session, "status", None) == "ended":
        raise ApiError(409, "call_ended", f"call {call_id} already ended", call_id=call_id)
    handle = book.get(call_id)
    if handle is not None:
        handle.end("api_hangup")
    await session.hangup("api_hangup")
    status = handle.api_status() if handle is not None else "completed"
    return {"call_id": call_id, "status": status}

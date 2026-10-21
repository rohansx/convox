"""Provider-facing HTTP and outbound platform webhooks.

Inbound (provider -> us):
  POST /webhooks/exotel          Calls/connect StatusCallback (form: CallSid, Status,
                                 EventType = answered | terminal)
  POST /webhooks/plivo           Call hangup_url (form: CallUUID, CallStatus, HangupCause)
  GET|POST /telephony/exotel/stream   Voicebot applet dynamic URL -> {"url": wss...}
  GET|POST /telephony/plivo/answer    answer_url -> <Stream> XML
Each status callback publishes a "leg" CallEvent {side, status, provider}. A failed, busy,
unanswered or completed leg hangs up the session (in the background; we answer 200 fast).
Callbacks are deduped on (call, side, status). Our dial URLs carry ?call=&side=; without
them we fall back to the CallBook's provider-sid map. Provider signature checks are TODO
(api.md: "provider signature check if offered, otherwise IP allowlist").

Outbound (us -> platform): `PlatformNotifier` subscribes to the EventBus for one call and
POSTs call.started / turn / call.ended to the call's `webhook` URL, signed
`X-Dubhash-Signature: sha256=<hex HMAC-SHA256(body, WEBHOOK_SIGNING_SECRET)>`, 5 s timeout,
up to 3 retries with exponential backoff on network errors, 429 and 5xx. It runs in its
own task, never on the hot path.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse

from dubhash.api.calls import CallBook, CallHandle, get_call_book, utcnow_iso
from dubhash.api.deps import get_session_registry
from dubhash.config.settings import Settings
from dubhash.events import EventBus
from dubhash.telephony.base import leg_ws_url
from dubhash.telephony.exotel_api import parse_custom_field
from dubhash.telephony.plivo_api import stream_xml
from dubhash.types import CallEvent, Side

log = logging.getLogger(__name__)

router = APIRouter()
Book = Annotated[CallBook, Depends(get_call_book)]
Sessions = Annotated[Any, Depends(get_session_registry)]

# provider status -> (event status, hangup reason or None)
_STATUS = {
    "ringing": ("ringing", None),
    "answered": ("answered", None),
    "in-progress": ("answered", None),
    "completed": ("completed", "completed"),
    "failed": ("failed", "failed"),
    "busy": ("busy", "busy"),
    "no-answer": ("no_answer", "no_answer"),
    "timeout": ("no_answer", "no_answer"),
    "cancel": ("canceled", "canceled"),
    "canceled": ("canceled", "canceled"),
}


async def read_params(request: Request) -> dict[str, str]:
    """Query params merged with a urlencoded or JSON body (no python-multipart needed)."""
    params = dict(request.query_params)
    body = await request.body()
    if not body:
        return params
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict):
            params.update({k: str(v) for k, v in data.items() if v is not None})
    else:
        params.update(parse_qsl(body.decode(errors="replace"), keep_blank_values=True))
    return params


def _target(
    params: dict[str, str], book: CallBook, sid_keys: tuple[str, ...]
) -> tuple[str, Side] | None:
    call, side = params.get("call"), params.get("side")
    if call and side in ("a", "b"):
        return call, side  # type: ignore[return-value]
    for key in sid_keys:
        if params.get(key) and (hit := book.find_sid(params[key])):
            return hit
    return None


async def _status_callback(
    provider: str,
    raw_status: str,
    target: tuple[str, Side] | None,
    book: CallBook,
    sessions: Any,
    bus: EventBus,
    background: BackgroundTasks,
) -> Response:
    session = sessions.get(target[0]) if target else None
    if target is None or session is None:
        log.warning(
            "webhook.unknown_call",
            extra={"provider": provider, "target": target, "status": raw_status},
        )
        return Response(status_code=200)
    call_id, side = target
    status, reason = _STATUS.get(raw_status.lower(), (raw_status.lower() or "unknown", None))
    handle = book.get(call_id)
    if handle is not None:
        key = (side, status)
        if key in handle.seen_statuses:
            return Response(status_code=200)
        handle.seen_statuses.add(key)
        leg = handle.legs[side]
        leg.status = status
        if status == "answered":
            leg.answered_at = leg.answered_at or utcnow_iso()
        if reason is not None:
            leg.closed_at = leg.closed_at or utcnow_iso()
            leg.close_reason = leg.close_reason or reason
    ts = handle.now_ms() if handle is not None else 0
    bus.publish(
        CallEvent(call_id, ts, "leg", {"side": side, "status": status, "provider": provider})
    )
    if reason is not None and getattr(session, "status", None) != "ended":
        if handle is not None:
            handle.end(reason)
        background.add_task(session.hangup, reason)
    return Response(status_code=200)


@router.post("/webhooks/exotel")
async def exotel_status(
    request: Request, background: BackgroundTasks, book: Book, sessions: Sessions
) -> Response:
    params = await read_params(request)
    raw = "answered" if params.get("EventType") == "answered" else params.get("Status", "")
    target = _target(params, book, ("CallSid",))
    if target is None:
        target = parse_custom_field(params.get("CustomField"))
    return await _status_callback(
        "exotel", raw, target, book, sessions, request.app.state.bus, background
    )


@router.post("/webhooks/plivo")
async def plivo_status(
    request: Request, background: BackgroundTasks, book: Book, sessions: Sessions
) -> Response:
    params = await read_params(request)
    target = _target(params, book, ("RequestUUID", "CallUUID"))
    return await _status_callback(
        "plivo",
        params.get("CallStatus", ""),
        target,
        book,
        sessions,
        request.app.state.bus,
        background,
    )


@router.api_route("/telephony/plivo/answer", methods=["GET", "POST"])
async def plivo_answer(request: Request, sessions: Sessions) -> Response:
    settings: Settings = request.app.state.settings
    params = await read_params(request)
    call_id, side = params.get("call", ""), params.get("side")
    if side not in ("a", "b") or sessions.get(call_id) is None:
        log.warning("plivo.answer_unknown_call", extra={"call_id": call_id, "side": side})
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n<Response><Hangup/></Response>'
        return Response(xml, media_type="application/xml")
    xml = stream_xml(leg_ws_url(settings, call_id, side), settings.plivo_stream_content_type)
    return Response(xml, media_type="application/xml")


def _mask(value: str) -> str:
    """Phone-number-like values keep their last 4 digits only (conventions: never log numbers)."""
    digits = sum(ch.isdigit() for ch in value)
    return "*" * (len(value) - 4) + value[-4:] if digits >= 7 and len(value) > 4 else value


@router.api_route("/telephony/exotel/stream", methods=["GET", "POST"])
async def exotel_stream_url(request: Request, book: Book, sessions: Sessions) -> JSONResponse:
    """Dynamic Voicebot applet URL (see dubhash/telephony/exotel_api.py docstring)."""
    settings: Settings = request.app.state.settings
    params = await read_params(request)
    target = _target(params, book, ())
    target = target or parse_custom_field(params.get("CustomField"))
    if target is None and params.get("CallSid"):
        target = book.find_sid(params["CallSid"])
    # Exotel does not document what it sends here (open question): log the shape.
    log.info(
        "exotel.stream_url_request",
        extra={
            "method": request.method,
            "fields": {k: _mask(str(v)) for k, v in params.items()},
            "resolved": f"{target[0]}:{target[1]}" if target else None,
        },
    )
    if target is None or sessions.get(target[0]) is None:
        log.warning("exotel.stream_url_unresolved", extra={"call_sid": params.get("CallSid")})
        return JSONResponse({"error": "unknown call"}, status_code=404)
    return JSONResponse({"url": leg_ws_url(settings, *target)})


# --- outbound platform webhooks -------------------------------------------------------


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _turn_dict(obj: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return dict(obj) if isinstance(obj, dict) else {}


STARTED_STATUSES = frozenset({"active", "in_progress"})
ENDED_STATUSES = frozenset({"ended", "completed", "failed"})


class PlatformNotifier:
    def __init__(
        self,
        handle: CallHandle,
        bus: EventBus,
        *,
        secret: str,
        client: httpx.AsyncClient,
        retries: int = 3,
        backoff_s: float = 0.5,
        timeout_s: float = 5.0,
        grace_s: float = 0.5,
    ) -> None:
        assert handle.webhook, "PlatformNotifier needs a webhook url"
        self.handle = handle
        self.url = handle.webhook
        self.bus = bus
        self.secret = secret
        self.client = client
        self.retries = retries
        self.backoff_s = backoff_s
        self.timeout_s = timeout_s
        self.grace_s = grace_s
        self._started = False
        self._ended = False
        self._turn_seqs: set[Any] = set()
        self._transcript: list[dict[str, Any]] = []
        # Subscribe now (construct inside the loop): the bus registers our queue when the
        # first anext() runs, one loop turn from here, before any leg can attach.
        self._events: AsyncIterator[CallEvent] = bus.subscribe(handle.call_id)
        self._first: asyncio.Future[CallEvent] | None = asyncio.ensure_future(anext(self._events))

    async def run(self) -> None:
        events = self._events
        closed = asyncio.ensure_future(self.handle.session.wait_closed())
        nxt, self._first = self._first, None
        try:
            while not self._ended:
                if nxt is None:
                    nxt = asyncio.ensure_future(anext(events))
                if closed.done():
                    # session is gone: give in-flight events a moment, then wrap up
                    done, _ = await asyncio.wait({nxt}, timeout=self.grace_s)
                    if not done:
                        break
                else:
                    await asyncio.wait({nxt, closed}, return_when=asyncio.FIRST_COMPLETED)
                    if not nxt.done():
                        continue
                ev, nxt = nxt.result(), None
                await self.on_event(ev)
            if not self._ended:
                await self.send_ended()
        except StopAsyncIteration:
            pass
        finally:
            closed.cancel()
            if nxt is not None:
                nxt.cancel()
            with contextlib.suppress(Exception):
                await events.aclose()  # type: ignore[attr-defined]

    async def on_event(self, ev: CallEvent) -> None:
        if ev.kind == "call":
            status = ev.payload.get("status")
            if status in STARTED_STATUSES and not self._started:
                self._started = True
                await self.deliver("call.started", self._started_data())
            elif status in ENDED_STATUSES:
                await self.send_ended()
        elif ev.kind == "turn":
            turn = _turn_dict(ev.payload.get("turn", ev.payload))
            seq = turn.get("seq")
            if turn.get("backchannel") or seq in self._turn_seqs:
                return
            self._turn_seqs.add(seq)
            if self.handle.record:
                self._transcript.append(
                    {k: turn.get(k) for k in ("seq", "side", "src_text", "tgt_text")}
                )
            await self.deliver("turn", self._turn_data(turn))

    async def send_ended(self) -> None:
        if self._ended:
            return
        self._ended = True
        await self.deliver("call.ended", self._ended_data())

    def _started_data(self) -> dict[str, Any]:
        h = self.handle
        return {
            "mode": h.mode,
            "a": {"lang": h.legs["a"].lang, "lang_source": "profile"},
            "b": {"lang": h.legs["b"].lang, "lang_source": "profile"},
        }

    @staticmethod
    def _turn_data(turn: dict[str, Any]) -> dict[str, Any]:
        start, end = turn.get("t_play_start"), turn.get("t_speech_end")
        clipped_ms = turn.get("clipped_ms")
        return {
            "seq": turn.get("seq"),
            "side": turn.get("side"),
            "src_text": turn.get("src_text"),
            "tgt_text": turn.get("tgt_text"),
            "first_audio_ms": start - end if start is not None and end is not None else None,
            "clipped": bool(clipped_ms),
            "clipped_ms": clipped_ms,
        }

    def _ended_data(self) -> dict[str, Any]:
        h = self.handle
        try:
            summary = dict(h.session.summary())
        except Exception:  # noqa: BLE001 - never lose call.ended over a summary bug
            summary = {}
        duration = summary.get("duration_s")
        return {
            "status": h.api_status(),
            "duration_s": duration,
            "interpreted_s": duration if h.mode == "interpret" else 0,
            "turns": summary.get("turns"),
            "p50_first_audio_ms": summary.get("p50_first_audio_ms"),
            "p95_first_audio_ms": summary.get("p95_first_audio_ms"),
            "escape_used": bool(summary.get("escape_used", False)),
            "legs": {s: {"close_reason": leg.close_reason} for s, leg in h.legs.items()},
            "transcript": self._transcript if h.record else None,
        }

    async def deliver(self, event: str, data: dict[str, Any]) -> bool:
        envelope = {
            "event": event,
            "call_id": self.handle.call_id,
            "ts": utcnow_iso(),
            "data": data,
        }
        body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
        headers = {
            "content-type": "application/json",
            "x-dubhash-signature": sign(self.secret, body),
            "x-dubhash-event": event,
        }
        for attempt in range(self.retries + 1):
            if attempt:
                await asyncio.sleep(self.backoff_s * 2 ** (attempt - 1))
            try:
                resp = await self.client.post(
                    self.url, content=body, headers=headers, timeout=self.timeout_s
                )
            except httpx.HTTPError as e:
                log.warning("webhook.retry", extra={"event": event, "error": repr(e)})
                continue
            if resp.status_code < 300:
                return True
            if resp.status_code != 429 and resp.status_code < 500:
                break
            log.warning("webhook.retry", extra={"event": event, "status": resp.status_code})
        log.error("webhook.failed", extra={"call_id": self.handle.call_id, "event": event})
        return False


def start_notifier(request: Request, handle: CallHandle) -> None:
    """Spawn a PlatformNotifier for a new call (called by POST /v1/calls)."""
    state = request.app.state
    client = getattr(state, "webhook_client", None)
    if client is None:
        client = state.webhook_client = httpx.AsyncClient()
    notifier = PlatformNotifier(
        handle, state.bus, secret=state.settings.webhook_signing_secret, client=client
    )
    get_call_book(request).spawn(notifier.run())

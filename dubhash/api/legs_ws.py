"""WS /ws/leg?call=&side=: where the provider's media websocket lands (api.md).

The provider is chosen by sniffing the first message (dubhash/legs/{exotel,plivo}.py):
  Exotel voicebot: {"event": "connected"}, then {"event": "start", "start": {"stream_sid"..}}
  Plivo stream:    {"event": "start", "start": {"streamId": ..}} (no `connected`)
The sniffed message is replayed to the leg, which then owns the socket via `leg.run()`.

Close codes (proposed, 4xxx app range): 4400 bad side / unrecognised first message /
provider mismatch, 4401 Exotel auth failed, 4404 unknown call, 4408 no first message
within FIRST_MESSAGE_TIMEOUT_S, 4409 that side already has a socket or the session
refused the leg.

Auth: when EXOTEL_WS_BASIC_USER or EXOTEL_ALLOWED_IPS is set, an Exotel socket must carry
matching Basic credentials or come from an allowlisted IP (security-privacy.md). Plivo
socket auth is an open question (leg-transports.md); until then a Plivo socket is accepted
only for a call whose CallBook provider is plivo (or one not created via /v1/calls), on
the unguessable call id. TODO: validate X-Plivo-Signature-V3 on the upgrade.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ipaddress
import json
import logging
import secrets
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket

from dubhash.api.calls import CallBook, get_call_book
from dubhash.api.deps import get_session_registry
from dubhash.config.settings import Settings
from dubhash.legs.exotel import ExotelLeg
from dubhash.legs.plivo import PlivoLeg
from dubhash.legs.recording import RecordingLeg
from dubhash.store.recorder import RecorderBook
from dubhash.types import Side

log = logging.getLogger(__name__)

FIRST_MESSAGE_TIMEOUT_S = 10.0
CLOSE_BAD_REQUEST = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_UNKNOWN_CALL = 4404
CLOSE_TIMEOUT = 4408
CLOSE_CONFLICT = 4409

router = APIRouter()

Book = Annotated[CallBook, Depends(get_call_book)]
Sessions = Annotated[Any, Depends(get_session_registry)]


class ReplayWS:
    """Wraps a websocket so already-read text messages are received again first."""

    def __init__(self, ws: Any, replay: list[str]) -> None:
        self.ws = ws
        self._replay = list(replay)

    async def receive_text(self) -> str:
        if self._replay:
            return self._replay.pop(0)
        return await self.ws.receive_text()

    async def receive_bytes(self) -> bytes:
        return await self.ws.receive_bytes()

    async def receive(self) -> Mapping[str, Any]:
        if self._replay:
            return {"type": "websocket.receive", "text": self._replay.pop(0)}
        return await self.ws.receive()

    async def send_text(self, data: str) -> None:
        await self.ws.send_text(data)

    async def send_bytes(self, data: bytes) -> None:
        await self.ws.send_bytes(data)

    async def close(self, code: int = 1000) -> None:
        await self.ws.close(code)


def sniff_provider(msg: Any) -> str | None:
    if not isinstance(msg, dict):
        return None
    event = msg.get("event")
    if event == "connected":
        return "exotel"
    if event == "start":
        start = msg.get("start") if isinstance(msg.get("start"), dict) else {}
        if "stream_sid" in start or "stream_sid" in msg:
            return "exotel"
        if "streamId" in start or "streamId" in msg:
            return "plivo"
    return None


def _is_proxy(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def client_ip(ws: WebSocket) -> str:
    """Peer address; behind a local proxy (Caddy) the last X-Forwarded-For hop."""
    peer = ws.client.host if ws.client else ""
    xff = ws.headers.get("x-forwarded-for")
    if xff and _is_proxy(peer):
        return xff.split(",")[-1].strip()
    return peer


def _basic_ok(header: str | None, user: str, basic_pass: str) -> bool:
    scheme, _, encoded = (header or "").partition(" ")
    if scheme.lower() != "basic" or not user:
        return False
    try:
        got_user, _, got_pass = base64.b64decode(encoded).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    user_ok = secrets.compare_digest(got_user.encode(), user.encode())
    pass_ok = secrets.compare_digest(got_pass.encode(), basic_pass.encode())
    return user_ok and pass_ok


def exotel_authorized(ws: WebSocket, settings: Settings) -> bool:
    allowed = {ip.strip() for ip in settings.exotel_allowed_ips.split(",") if ip.strip()}
    user, basic_pass = settings.exotel_ws_basic_user, settings.exotel_ws_basic_pass
    if not allowed and not user:
        return True
    if user and _basic_ok(ws.headers.get("authorization"), user, basic_pass):
        return True
    return bool(allowed) and client_ip(ws) in allowed


async def close_ws(ws: Any, code: int) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code)


async def recording_leg(ws: Any, session: Any, side: Side, leg: Any) -> Any:
    """`leg` wrapped in a RecordingLeg when settings.call_recording is on (dev/test only,
    store/recorder.py), else `leg`. A recorder failure never stops the call."""
    try:
        state = ws.app.state
        if not state.settings.call_recording or getattr(session, "clock", None) is None:
            return leg
        book = getattr(state, "recorders", None)
        if book is None:
            book = state.recorders = RecorderBook(state.settings.call_recording_dir, state.bus)
        return RecordingLeg(leg, await book.recorder_for(session), side)
    except Exception:
        log.exception("recorder.attach_failed", extra={"side": side})
        return leg


async def serve_leg(
    ws: Any, session: Any, side: Side, leg: Any, book: CallBook, call_id: str
) -> None:
    """Pump the leg while attaching it to the session; record leg facts in the CallBook."""
    run = asyncio.create_task(leg.run())
    try:
        await session.attach_leg(side, await recording_leg(ws, session, side, leg))
    except Exception as e:
        log.warning("leg.rejected", extra={"call_id": call_id, "side": side, "error": repr(e)})
        leg.close_reason = "rejected"
        await close_ws(ws, CLOSE_CONFLICT)
        await leg.close()
    started = getattr(leg, "started", None)
    if isinstance(started, asyncio.Event):
        waiter = asyncio.ensure_future(started.wait())
        await asyncio.wait({run, waiter}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        handle = book.get(call_id)
        if handle is not None and started.is_set():
            handle.legs[side].stream_sid = getattr(leg, "stream_sid", None)
            handle.legs[side].sample_rate = getattr(leg, "sample_rate", None)
    try:
        await run
    finally:
        book.leg_closed(call_id, side, getattr(leg, "close_reason", None))


@router.websocket("/ws/leg")
async def leg_socket(ws: WebSocket, sessions: Sessions, book: Book) -> None:
    settings: Settings = ws.app.state.settings
    call_id = ws.query_params.get("call") or ""
    side = ws.query_params.get("side")
    await ws.accept()
    if side not in ("a", "b"):
        return await close_ws(ws, CLOSE_BAD_REQUEST)
    session = sessions.get(call_id) if call_id else None
    if session is None:
        log.warning("leg.unknown_call", extra={"call_id": call_id, "side": side})
        return await close_ws(ws, CLOSE_UNKNOWN_CALL)
    try:
        first = await asyncio.wait_for(ws.receive_text(), FIRST_MESSAGE_TIMEOUT_S)
    except TimeoutError:
        return await close_ws(ws, CLOSE_TIMEOUT)
    except Exception:  # disconnect or binary frame
        return await close_ws(ws, CLOSE_BAD_REQUEST)
    try:
        provider = sniff_provider(json.loads(first))
    except ValueError:
        provider = None
    handle = book.get(call_id)
    expected = handle.provider if handle is not None else None
    if provider is None or (expected in ("exotel", "plivo") and provider != expected):
        log.warning(
            "leg.bad_first_message",
            extra={"call_id": call_id, "side": side, "provider": provider, "expected": expected},
        )
        return await close_ws(ws, CLOSE_BAD_REQUEST)
    if provider == "exotel" and not exotel_authorized(ws, settings):
        log.warning("leg.unauthorized", extra={"call_id": call_id, "side": side})
        return await close_ws(ws, CLOSE_UNAUTHORIZED)
    if not book.leg_attached(call_id, side):
        return await close_ws(ws, CLOSE_CONFLICT)
    wrapped = ReplayWS(ws, [first])
    leg = ExotelLeg(side, wrapped) if provider == "exotel" else PlivoLeg(side, wrapped)
    await serve_leg(ws, session, side, leg, book, call_id)

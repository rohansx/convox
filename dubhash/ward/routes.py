"""Ward mode routes: the page and its push-to-talk websocket.

WS /ws/ward protocol (JSON text messages plus binary PCM16 @16 kHz):

    client -> {"type": "config", "a": "mr-IN", "b": "hi-IN"}
    server <- {"type": "ready", "session", "a", "b"}
    client -> {"type": "start", "side": "a"}, binary frames..., {"type": "end"}
    server <- {"type": "turn", side, src_text, tgt_text, warning, numbers, timings}
              binary TTS chunks..., {"type": "audio_end", "timings"}
           or {"type": "empty", "side"} when nothing was heard
    client -> {"type": "cancel"}   drop the current press (too short, slipped finger)
    client -> {"type": "reset"}    new patient: empty transcript, same languages (-> ready)
    client -> {"type": "export"}
    server <- {"type": "transcript", "json": {...}, "text": "..."}
    server <- {"type": "error", "message"} for a bad message or a provider failure;
              the socket stays open.

Auth: GET /ward sits behind the console's Basic auth and embeds a signed, expiring
token; the websocket checks it (browsers do not reliably send Basic credentials on a
websocket handshake). Without a token the socket is accepted only in env=test. The
transcript lives in the socket's memory only and is gone when the page closes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dubhash.api.console import TEMPLATES_DIR
from dubhash.api.deps import require_console_auth
from dubhash.config.pairs import PairConfig, load_pairs
from dubhash.config.settings import Settings
from dubhash.ward.service import (
    DEFAULT_LANGS,
    LANGS,
    PRESETS,
    WardProviders,
    WardSession,
    WardUtterance,
    build_ward_providers,
    lang_label,
    ward_pair,
)

log = logging.getLogger(__name__)

TOKEN_TTL_S = 12 * 3600  # one ward shift
POLICY_VIOLATION = 1008

ProvidersFactory = Callable[[PairConfig], WardProviders]

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter()


# --- auth ----------------------------------------------------------------------------


def _sign(settings: Settings, payload: str) -> str:
    key = f"ward-ws:{settings.console_password}".encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_ws_token(settings: Settings, now: float | None = None) -> str:
    exp = str(int((time.time() if now is None else now) + TOKEN_TTL_S))
    return f"{exp}.{_sign(settings, exp)}"


def verify_ws_token(settings: Settings, token: str, now: float | None = None) -> bool:
    exp, _, sig = token.partition(".")
    if not exp.isdigit() or not sig:
        return False
    if int(exp) < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(sig, _sign(settings, exp))


# --- dependencies --------------------------------------------------------------------


def ward_providers_factory(websocket: WebSocket) -> ProvidersFactory:
    settings: Settings = websocket.app.state.settings
    return lambda pair: build_ward_providers(pair, settings)


# --- page ----------------------------------------------------------------------------


@router.get("/ward", response_class=HTMLResponse, dependencies=[Depends(require_console_auth)])
async def ward_page(request: Request) -> HTMLResponse:
    settings: Settings = request.app.state.settings
    langs = [{"code": c, "name": n, "native": native} for c, (n, native) in LANGS.items()]
    return templates.TemplateResponse(
        request,
        "ward.html",
        {
            "langs": langs,
            "default_a": DEFAULT_LANGS[0],
            "default_b": DEFAULT_LANGS[1],
            "presets": [
                {"a": a, "b": b, "label": f"{lang_label(a)} ⇄ {lang_label(b)}"} for a, b in PRESETS
            ],
            "ws_token": make_ws_token(settings),
        },
    )


# --- websocket -----------------------------------------------------------------------


class _Protocol(Exception):
    """A client message we can't act on; reported, the socket stays open."""


class WardSocket:
    def __init__(self, ws: WebSocket, settings: Settings, factory: ProvidersFactory) -> None:
        self.ws = ws
        self.settings = settings
        self.factory = factory
        self.session: WardSession | None = None
        self.utt: WardUtterance | None = None

    async def run(self) -> None:
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                try:
                    if msg.get("bytes") is not None:
                        if self.utt is not None:
                            await self.utt.push(msg["bytes"])
                    elif msg.get("text") is not None:
                        await self.on_text(msg["text"])
                except _Protocol as e:
                    await self.send({"type": "error", "message": str(e)})
                except WebSocketDisconnect:
                    raise
                except Exception as e:  # provider failure: report it, keep the page alive
                    log.warning("provider.error", extra={"stage": "ward", "error": repr(e)})
                    await self._drop_utt()
                    await self.send({"type": "error", "message": f"translation failed: {e}"})
        except WebSocketDisconnect:
            return
        finally:
            await self._drop_utt()

    async def send(self, obj: dict[str, Any]) -> None:
        await self.ws.send_text(json.dumps(obj, ensure_ascii=False))

    async def _drop_utt(self) -> None:
        utt, self.utt = self.utt, None
        if utt is not None:
            try:
                await utt.cancel()
            except Exception as e:  # noqa: BLE001 - closing a dead stream is best effort
                log.debug("ward.cancel_error", extra={"error": repr(e)})

    async def on_text(self, text: str) -> None:
        try:
            m = json.loads(text)
        except json.JSONDecodeError:
            raise _Protocol("not JSON") from None
        if not isinstance(m, dict):
            raise _Protocol("expected an object")
        kind = m.get("type")
        if kind == "config":
            await self.on_config(m)
        elif kind == "start":
            await self.on_start(m)
        elif kind == "end":
            await self.on_end()
        elif kind == "cancel":
            await self._drop_utt()
        elif kind == "reset":
            await self.on_reset()
        elif kind == "export":
            s = self._session()
            await self.send({"type": "transcript", "json": s.to_dict(), "text": s.to_text()})
        else:
            raise _Protocol(f"unknown message type {kind!r}")

    def _session(self) -> WardSession:
        if self.session is None:
            raise _Protocol("send config first")
        return self.session

    async def on_config(self, m: dict[str, Any]) -> None:
        a, b = str(m.get("a", "")), str(m.get("b", ""))
        try:
            pair = ward_pair(a, b, load_pairs(self.settings.pairs_file))
        except ValueError as e:
            raise _Protocol(str(e)) from None
        await self._drop_utt()
        previous = self.session
        self.session = WardSession(pair, self.factory(pair))
        if previous is not None:  # a language change keeps the conversation's transcript
            self.session.transcript = previous.transcript
            self.session.session_id = previous.session_id
            self.session.started_at = previous.started_at
        await self.send({"type": "ready", "session": self.session.session_id, "a": a, "b": b})

    async def on_reset(self) -> None:
        """New patient: same languages, empty transcript, new session id."""
        old = self._session()
        await self._drop_utt()
        self.session = WardSession(old.pair, old.providers)
        a, b = old.pair.a.lang, old.pair.b.lang
        await self.send({"type": "ready", "session": self.session.session_id, "a": a, "b": b})

    async def on_start(self, m: dict[str, Any]) -> None:
        session = self._session()
        side = m.get("side")
        if side not in ("a", "b"):
            raise _Protocol("side must be 'a' or 'b'")
        await self._drop_utt()
        self.utt = await session.start(side)

    async def on_end(self) -> None:
        utt, self.utt = self.utt, None
        if utt is None:
            raise _Protocol("end without start")
        turn, audio = await utt.finish_streaming()
        if not turn.src_text:
            await self.send({"type": "empty", "side": turn.side})
            return
        await self.send(
            {
                "type": "turn",
                "seq": turn.seq,
                "side": turn.side,
                "src_lang": turn.src_lang,
                "tgt_lang": turn.tgt_lang,
                "src_text": turn.src_text,
                "tgt_text": turn.tgt_text,
                "warning": turn.warning,
                "numbers": turn.numbers,
                "timings": dict(turn.timings),
            }
        )
        async for chunk in audio:
            await self.ws.send_bytes(chunk)
        await self.send({"type": "audio_end", "seq": turn.seq, "timings": dict(turn.timings)})


@router.websocket("/ws/ward")
async def ward_ws(
    websocket: WebSocket, factory: Annotated[ProvidersFactory, Depends(ward_providers_factory)]
) -> None:
    settings: Settings = websocket.app.state.settings
    token = websocket.query_params.get("token")
    allowed = verify_ws_token(settings, token) if token else settings.dubhash_env == "test"
    if not allowed:
        await websocket.close(code=POLICY_VIOLATION)
        return
    await websocket.accept()
    await WardSocket(websocket, settings, factory).run()

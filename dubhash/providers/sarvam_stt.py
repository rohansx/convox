"""Sarvam realtime streaming STT (saaras:v3-realtime) over websocket.

https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming
One socket carries many utterances. Default endpointing is "manual": we send speech_start
when a stream opens and speech_end on end(), so finals line up 1:1 with our own (Silero)
utterances. With endpointing="vad" Sarvam's own VAD delimits turns, and vad.speech_end
sets `eou_signal` so the Endpointer can force EOU early (open question Q2).

Sockets are kept warm per language in a small pool, pinged while idle (code 1008 is
Sarvam's inactivity close), and reconnected on failure with the utterance's audio replayed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Literal
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from dubhash.audio.frames import SAMPLE_RATE
from dubhash.providers.registry import ProviderError

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "wss://api.sarvam.ai"
DEFAULT_MODEL = "saaras:v3-realtime"
# The realtime socket uses or-IN for Odia; REST endpoints use od-IN.
_LANG_ALIASES = {"od-IN": "or-IN"}
_MAX_RECOVERIES = 2

Endpointing = Literal["manual", "vad"]


class _Conn:
    """One live socket plus its reader task. Messages go to whichever stream owns it."""

    def __init__(self, ws: ClientConnection, lang: str) -> None:
        self.ws = ws
        self.lang = lang
        self.sink: SarvamSTTStream | None = None
        self.dead = asyncio.Event()
        self._pinger: asyncio.Task[None] | None = None
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if self.sink is not None:
                    self.sink._on_message(msg)
        except ConnectionClosed:
            pass
        finally:
            self.dead.set()

    async def send(self, msg: dict[str, Any]) -> None:
        if self.dead.is_set():
            raise ConnectionClosed(None, None)
        await self.ws.send(json.dumps(msg))

    def start_keepalive(self, interval_s: float) -> None:
        self.stop_keepalive()
        self._pinger = asyncio.create_task(self._ping_loop(interval_s))

    def stop_keepalive(self) -> None:
        if self._pinger is not None:
            self._pinger.cancel()
            self._pinger = None

    async def _ping_loop(self, interval_s: float) -> None:
        with contextlib.suppress(ConnectionClosed):
            while not self.dead.is_set():
                await asyncio.sleep(interval_s)
                await self.send({"event": "ping"})

    async def close(self) -> None:
        self.stop_keepalive()
        self.sink = None
        await self.ws.close()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reader


class SarvamSTTStream:
    """One utterance on a (possibly warm) socket."""

    def __init__(self, stt: SarvamSTT, lang: str, endpointing: Endpointing) -> None:
        self._stt = stt
        self._lang = lang
        self._manual = endpointing == "manual"
        self._conn: _Conn | None = None
        self._audio: list[bytes] = []
        self._finals: list[str] = []
        self._pending = 0  # utterances started on the server but not finalised
        self._settled = asyncio.Event()
        self._partials: asyncio.Queue[str | None] = asyncio.Queue()
        self._ended = False
        self._closed = False
        self._recoveries = 0
        self.eou_signal = asyncio.Event()  # set on Sarvam vad.speech_end (vad mode only)

    async def _bind(self, conn: _Conn) -> None:
        conn.stop_keepalive()
        conn.sink = self
        self._conn = conn
        self._finals.clear()
        if self._manual:
            self._pending = 1
            self._settled.clear()
            await conn.send({"event": "speech_start"})
        else:
            self._pending = 0
            self._settled.set()

    async def push(self, frame: bytes) -> None:
        if self._ended or self._closed:
            return
        self._audio.append(frame)
        await self._send({"event": "audio_input", "audio": base64.b64encode(frame).decode()})

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._conn is None:
            return  # broken: recovery already failed
        try:
            await self._conn.send(msg)
        except ConnectionClosed:
            await self._recover()

    async def _recover(self) -> bool:
        """Reconnect and replay this utterance's audio. False if we gave up."""
        old, self._conn = self._conn, None
        if old is not None:
            await old.close()
        while self._recoveries < _MAX_RECOVERIES:
            self._recoveries += 1
            log.warning(
                "provider.error",
                extra={"provider": "sarvam", "stage": "stt", "reconnecting": True},
            )
            try:
                conn = await self._stt._connect(self._lang)
                await self._bind(conn)
                for frame in self._audio:
                    await conn.send(
                        {"event": "audio_input", "audio": base64.b64encode(frame).decode()}
                    )
                if self._ended and self._manual:
                    await conn.send({"event": "speech_end"})
                return True
            except (ProviderError, ConnectionClosed):
                if self._conn is not None:
                    await self._conn.close()
                    self._conn = None
        log.error(
            "provider.error",
            extra={"provider": "sarvam", "stage": "stt", "reconnecting": False},
        )
        return False

    async def end(self) -> str:
        if self._closed:
            return ""
        if self._ended:
            return " ".join(self._finals)
        self._ended = True
        t0 = time.monotonic()
        if self._manual:
            await self._send({"event": "speech_end"})
        deadline = t0 + self._stt.final_timeout_s
        ok = False
        while self._conn is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            settled = asyncio.ensure_future(self._settled.wait())
            dead = asyncio.ensure_future(self._conn.dead.wait())
            try:
                await asyncio.wait(
                    {settled, dead}, timeout=remaining, return_when="FIRST_COMPLETED"
                )
            finally:
                settled.cancel()
                dead.cancel()
            if self._settled.is_set():
                ok = True
                break
            if self._conn.dead.is_set():
                if not await self._recover():
                    break
                continue
        text = " ".join(self._finals)
        if ok:
            log.info(
                "stt.final",
                extra={"provider": "sarvam", "latency_ms": int((time.monotonic() - t0) * 1000)},
            )
            conn, self._conn = self._conn, None
            if conn is not None:
                self._stt._release(conn)
        else:
            log.warning("stt.final_timeout after %.1fs", self._stt.final_timeout_s)
            await self._drop()
        self._partials.put_nowait(None)
        return text

    def partials(self) -> AsyncIterator[str]:
        return self._iter_partials()

    async def _iter_partials(self) -> AsyncIterator[str]:
        while True:
            p = await self._partials.get()
            if p is None:
                self._partials.put_nowait(None)  # let later iterators finish too
                return
            yield p

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._drop()
        self._partials.put_nowait(None)

    async def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()

    def _on_message(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if event == "transcript.partial":
            self._partials.put_nowait(str(msg.get("text", "")))
        elif event == "transcript.final":
            text = str(msg.get("text", "")).strip()
            if text:
                self._finals.append(text)
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self._settled.set()
        elif event == "vad.speech_start":
            self._pending += 1
            self._settled.clear()
        elif event == "vad.speech_end":
            self.eou_signal.set()
        elif event == "error":
            log.warning(
                "provider.error",
                extra={
                    "provider": "sarvam",
                    "stage": "stt",
                    "error": msg.get("code"),
                    "fatal": msg.get("is_fatal"),
                },
            )


class SarvamSTT:
    name = "sarvam"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        endpointing: Endpointing = "manual",
        stream_type: str = "fast",
        final_timeout_s: float = 3.0,
        connect_timeout_s: float = 5.0,
        keepalive_s: float = 10.0,
        pool_size: int = 2,
        vad_silence_ms: int | None = None,
    ) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._model = model
        self._endpointing: Endpointing = endpointing
        self._stream_type = stream_type
        self.final_timeout_s = final_timeout_s
        self._connect_timeout = connect_timeout_s
        self._keepalive = keepalive_s
        self._pool_size = pool_size
        self._vad_silence_ms = vad_silence_ms
        self._idle: dict[str, list[_Conn]] = {}

    async def open(self, lang: str) -> SarvamSTTStream:
        lang = _LANG_ALIASES.get(lang, lang)
        stream = SarvamSTTStream(self, lang, self._endpointing)
        conn = self._take_idle(lang) or await self._connect(lang)
        try:
            await stream._bind(conn)
        except ConnectionClosed:
            await conn.close()
            await stream._bind(await self._connect(lang))
        return stream

    async def prewarm(self, lang: str) -> None:
        """Open a socket ahead of speech so open() skips the 150-300 ms handshake."""
        lang = _LANG_ALIASES.get(lang, lang)
        if not self._idle.get(lang):
            self._release(await self._connect(lang))

    def _take_idle(self, lang: str) -> _Conn | None:
        pool = self._idle.get(lang, [])
        while pool:
            conn = pool.pop()
            if not conn.dead.is_set():
                return conn
            asyncio.ensure_future(conn.close())
        return None

    def _query(self, lang: str) -> str:
        q: dict[str, Any] = {
            "language_code": lang,
            "model": self._model,
            "stream_type": self._stream_type,
            "endpointing": self._endpointing,
            "encoding": "linear16",
            "sample_rate": SAMPLE_RATE,
        }
        if self._endpointing == "vad" and self._vad_silence_ms is not None:
            q["silence_duration_ms"] = self._vad_silence_ms
        return urlencode(q)

    async def _connect(self, lang: str) -> _Conn:
        url = f"{self._base}/speech-to-text-realtime/ws?{self._query(lang)}"
        try:
            ws = await connect(
                url,
                additional_headers={"api-subscription-key": self._key},
                open_timeout=self._connect_timeout,
            )
        except (OSError, InvalidHandshake, TimeoutError) as e:
            raise ProviderError(f"sarvam stt connect failed: {type(e).__name__}") from e
        return _Conn(ws, lang)

    def _release(self, conn: _Conn) -> None:
        conn.sink = None
        pool = self._idle.setdefault(conn.lang, [])
        if conn.dead.is_set() or len(pool) >= self._pool_size:
            asyncio.ensure_future(conn.close())
            return
        conn.start_keepalive(self._keepalive)
        pool.append(conn)

    async def aclose(self) -> None:
        for pool in self._idle.values():
            for conn in pool:
                await conn.close()
        self._idle.clear()

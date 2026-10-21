"""Sarvam realtime streaming STT (saaras:v3-realtime) over websocket.

https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming
One socket carries many utterances. Default endpointing is "manual": we send speech_start
when a stream opens and speech_end on end(), so finals line up 1:1 with our own (Silero)
utterances. With endpointing="vad" Sarvam's own VAD delimits turns, and vad.speech_end
sets `eou_signal` so the Endpointer can force EOU early (open question Q2).

Sockets are kept warm per language in a small pool, pinged while idle (code 1008 is
Sarvam's inactivity close), and reconnected on failure with the utterance's audio replayed.
The warm-socket machinery (WarmSocket, WarmSTTStream, WarmSTTPool) is shared with
deepgram_stt.py.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol
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
WireMsg = dict[str, Any] | bytes


class MessageSink(Protocol):
    def _on_message(self, msg: dict[str, Any]) -> None: ...


class WarmSocket:
    """One live vendor socket plus its reader task. Parsed JSON messages go to whichever
    stream currently owns it (`sink`)."""

    def __init__(self, ws: ClientConnection, lang: str, ping: dict[str, Any]) -> None:
        self.ws = ws
        self.lang = lang
        self.sink: MessageSink | None = None
        self.dead = asyncio.Event()
        self._ping = ping
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
                if self.sink is not None and isinstance(msg, dict):
                    self.sink._on_message(msg)
        except ConnectionClosed:
            pass
        finally:
            self.dead.set()

    async def send(self, msg: WireMsg) -> None:
        if self.dead.is_set():
            raise ConnectionClosed(None, None)
        await self.ws.send(msg if isinstance(msg, bytes) else json.dumps(msg))

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
                await self.send(self._ping)

    async def close(self) -> None:
        self.stop_keepalive()
        self.sink = None
        await self.ws.close()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reader


class WarmSTTStream:
    """One utterance on a (possibly warm) socket. Subclasses define the wire messages.

    `_pending` counts utterances the server has started but not finalised; end() waits
    until it reaches zero (or the timeout), reconnecting and replaying the utterance's
    audio if the socket dies on the way.
    """

    provider = "?"

    def __init__(self, stt: WarmSTTPool, lang: str) -> None:
        self._stt = stt
        self._lang = lang
        self._conn: WarmSocket | None = None
        self._audio: list[bytes] = []
        self._finals: list[str] = []
        self._pending = 0
        self._settled = asyncio.Event()
        self._partials: asyncio.Queue[str | None] = asyncio.Queue()
        self._ended = False
        self._closed = False
        self._recoveries = 0
        self.eou_signal = asyncio.Event()  # vendor end-of-utterance hint, when it has one

    # -- wire hooks -----------------------------------------------------------------
    def _final_per_stream(self) -> bool:
        """True if exactly one settling final follows our end message."""
        return True

    def _start_msgs(self) -> list[WireMsg]:
        return []

    def _audio_msg(self, frame: bytes) -> WireMsg:
        raise NotImplementedError

    def _end_msgs(self) -> list[WireMsg]:
        return []

    def _on_message(self, msg: dict[str, Any]) -> None:
        raise NotImplementedError

    # -- shared machinery -----------------------------------------------------------
    def _add_final(self, text: str, *, settles: bool = True) -> None:
        text = text.strip()
        if text:
            self._finals.append(text)
        if settles:
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self._settled.set()

    def _speech_started(self) -> None:
        self._pending += 1
        self._settled.clear()

    async def _bind(self, conn: WarmSocket) -> None:
        conn.stop_keepalive()
        conn.sink = self
        self._conn = conn
        self._finals.clear()
        self._pending = 1 if self._final_per_stream() else 0
        if self._pending:
            self._settled.clear()
        else:
            self._settled.set()
        for m in self._start_msgs():
            await conn.send(m)

    async def push(self, frame: bytes) -> None:
        if self._ended or self._closed:
            return
        self._audio.append(frame)
        await self._send(self._audio_msg(frame))

    async def _send(self, msg: WireMsg) -> None:
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
                extra={"provider": self.provider, "stage": "stt", "reconnecting": True},
            )
            try:
                conn = await self._stt._connect(self._lang)
                await self._bind(conn)
                for frame in self._audio:
                    await conn.send(self._audio_msg(frame))
                if self._ended:
                    for m in self._end_msgs():
                        await conn.send(m)
                return True
            except (ProviderError, ConnectionClosed):
                await self._drop()
        log.error(
            "provider.error",
            extra={"provider": self.provider, "stage": "stt", "reconnecting": False},
        )
        return False

    async def end(self) -> str:
        if self._closed:
            return ""
        if self._ended:
            return " ".join(self._finals)
        self._ended = True
        t0 = time.monotonic()
        for m in self._end_msgs():
            await self._send(m)
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
                    {settled, dead}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                settled.cancel()
                dead.cancel()
            if self._settled.is_set():
                ok = True
                break
            if self._conn.dead.is_set() and not await self._recover():
                break
        text = " ".join(self._finals)
        if ok:
            log.info(
                "stt.final",
                extra={
                    "provider": self.provider,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                },
            )
            conn, self._conn = self._conn, None
            if conn is not None:
                self._stt._release(conn)
        else:
            log.warning(
                "stt.final_timeout after %.1fs (%s)", self._stt.final_timeout_s, self.provider
            )
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


class WarmSTTPool:
    """Idle warm sockets per language, pinged while idle. Subclasses build the URL."""

    name = "?"
    _ping: dict[str, Any] = {}

    def __init__(
        self,
        *,
        headers: dict[str, str],
        final_timeout_s: float,
        connect_timeout_s: float,
        keepalive_s: float,
        pool_size: int,
    ) -> None:
        self._headers = headers
        self.final_timeout_s = final_timeout_s
        self._connect_timeout = connect_timeout_s
        self._keepalive = keepalive_s
        self._pool_size = pool_size
        self._idle: dict[str, list[WarmSocket]] = {}

    def _lang(self, lang: str) -> str:
        return lang

    def _url(self, lang: str) -> str:
        raise NotImplementedError

    def _new_stream(self, lang: str) -> WarmSTTStream:
        raise NotImplementedError

    async def _open(self, lang: str) -> WarmSTTStream:
        lang = self._lang(lang)
        stream = self._new_stream(lang)
        conn = self._take_idle(lang) or await self._connect(lang)
        try:
            await stream._bind(conn)
        except ConnectionClosed:
            await conn.close()
            await stream._bind(await self._connect(lang))
        return stream

    async def prewarm(self, lang: str) -> None:
        """Open a socket ahead of speech so open() skips the 150-300 ms handshake."""
        lang = self._lang(lang)
        if not self._idle.get(lang):
            self._release(await self._connect(lang))

    def _take_idle(self, lang: str) -> WarmSocket | None:
        pool = self._idle.get(lang, [])
        while pool:
            conn = pool.pop()
            if not conn.dead.is_set():
                return conn
            asyncio.ensure_future(conn.close())
        return None

    async def _connect(self, lang: str) -> WarmSocket:
        try:
            ws = await connect(
                self._url(lang),
                additional_headers=self._headers,
                open_timeout=self._connect_timeout,
            )
        except (OSError, InvalidHandshake, TimeoutError) as e:
            raise ProviderError(f"{self.name} stt connect failed: {type(e).__name__}") from e
        return WarmSocket(ws, lang, self._ping)

    def _release(self, conn: WarmSocket) -> None:
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


class SarvamSTTStream(WarmSTTStream):
    provider = "sarvam"

    def __init__(self, stt: WarmSTTPool, lang: str, endpointing: Endpointing) -> None:
        super().__init__(stt, lang)
        self._manual = endpointing == "manual"

    def _final_per_stream(self) -> bool:
        return self._manual

    def _start_msgs(self) -> list[WireMsg]:
        return [{"event": "speech_start"}] if self._manual else []

    def _audio_msg(self, frame: bytes) -> WireMsg:
        return {"event": "audio_input", "audio": base64.b64encode(frame).decode()}

    def _end_msgs(self) -> list[WireMsg]:
        return [{"event": "speech_end"}] if self._manual else []

    def _on_message(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if event == "transcript.partial":
            self._partials.put_nowait(str(msg.get("text", "")))
        elif event == "transcript.final":
            self._add_final(str(msg.get("text", "")))
        elif event == "vad.speech_start":
            self._speech_started()
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


class SarvamSTT(WarmSTTPool):
    name = "sarvam"
    _ping = {"event": "ping"}

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
        super().__init__(
            headers={"api-subscription-key": api_key},
            final_timeout_s=final_timeout_s,
            connect_timeout_s=connect_timeout_s,
            keepalive_s=keepalive_s,
            pool_size=pool_size,
        )
        self._base = base_url.rstrip("/")
        self._model = model
        self._endpointing: Endpointing = endpointing
        self._stream_type = stream_type
        self._vad_silence_ms = vad_silence_ms

    async def open(self, lang: str) -> SarvamSTTStream:
        stream = await self._open(lang)
        assert isinstance(stream, SarvamSTTStream)
        return stream

    def _lang(self, lang: str) -> str:
        return _LANG_ALIASES.get(lang, lang)

    def _new_stream(self, lang: str) -> SarvamSTTStream:
        return SarvamSTTStream(self, lang, self._endpointing)

    def _url(self, lang: str) -> str:
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
        return f"{self._base}/speech-to-text-realtime/ws?{urlencode(q)}"

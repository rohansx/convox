"""Sarvam Bulbul streaming TTS over websocket, PCM16 @16k out.

https://docs.sarvam.ai/api-reference/text-to-speech/stream
Per synth on one socket: config -> text -> flush; the server streams base64 audio messages
and then an `event` with event_type "final" (send_completion_event=true). Sockets are kept
warm in a small idle pool per model and reused for the next synth.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
from collections.abc import AsyncIterator
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.protocol import State

from dubhash.audio.frames import SAMPLE_RATE
from dubhash.audio.resample import Resampler
from dubhash.providers.errors import ProviderError

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "wss://api.sarvam.ai"
DEFAULT_MODEL = "bulbul:v3"
V4_MODEL = "bulbul:v4-flash"

# Placeholder ids from the spec -> Bulbul v3 speakers. Sarvam's voices page names shubh
# (male) and ishita (female) as top-rated across all 11 languages. Needs the listening test.
_GENDER_DEFAULT = {"male": "shubh", "female": "ishita"}
_LANG_ALIASES = {"or-IN": "od-IN"}


def vendor_voice(voice: str) -> tuple[str, str]:
    """Map "bulbul:<speaker or alias>" to (model, speaker).

    v4-flash persona ids look like name_lang_style; plain names are v3 speakers.
    "bulbul:kn-male-1"-style placeholders map to the v3 gender default.
    """
    vid = voice.split(":", 1)[1] if voice.startswith("bulbul:") else voice
    parts = vid.split("-")
    if len(parts) == 3 and parts[1] in _GENDER_DEFAULT:
        return DEFAULT_MODEL, _GENDER_DEFAULT[parts[1]]
    if "_" in vid:
        return V4_MODEL, vid
    return DEFAULT_MODEL, vid


def decode_audio(content_type: str, raw: bytes) -> tuple[bytes, int | None]:
    """Return (pcm16 mono, sample rate if the payload states one).

    The docs list linear16/wav codecs but also say "currently supports MP3 only"; we ask
    for linear16 and accept wav-wrapped chunks too. MP3 would need a decoder dependency.
    """
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return _parse_wav(raw)
    if "mp3" in content_type or "mpeg" in content_type:
        raise ProviderError(f"bulbul returned {content_type}; expected linear16")
    return raw, None


def _parse_wav(raw: bytes) -> tuple[bytes, int | None]:
    rate: int | None = None
    i = 12
    while i + 8 <= len(raw):
        cid, size = raw[i : i + 4], struct.unpack("<I", raw[i + 4 : i + 8])[0]
        body = raw[i + 8 : i + 8 + size]
        if cid == b"fmt ":
            rate = struct.unpack("<I", body[4:8])[0]
        elif cid == b"data":
            # streaming wavs may carry a placeholder size; take everything after the header
            return raw[i + 8 :], rate
        i += 8 + size + (size & 1)
    return b"", rate


class BulbulTTS:
    name = "bulbul"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        connect_timeout_s: float = 5.0,
        chunk_timeout_s: float = 5.0,
        max_idle_s: float = 30.0,
        pool_size: int = 2,
        min_buffer_size: int = 30,
    ) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._connect_timeout = connect_timeout_s
        self._chunk_timeout = chunk_timeout_s
        self._max_idle = max_idle_s
        self._pool_size = pool_size
        self._min_buffer = min_buffer_size
        self._idle: dict[str, list[tuple[ClientConnection, float]]] = {}

    def synth(self, text: str, voice: str, lang: str) -> AsyncIterator[bytes]:
        return self._synth(text, voice, lang)

    async def _synth(self, text: str, voice: str, lang: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        model, speaker = vendor_voice(voice)
        lang = _LANG_ALIASES.get(lang, lang)
        t0 = time.monotonic()
        for attempt in range(2):
            ws, reused = await self._acquire(model)
            done = False
            yielded = False
            try:
                async for pcm in self._run(ws, text, speaker, lang):
                    if not yielded:
                        yielded = True
                        log.info(
                            "tts.ttfb",
                            extra={
                                "provider": self.name,
                                "voice": voice,
                                "latency_ms": int((time.monotonic() - t0) * 1000),
                            },
                        )
                    yield pcm
                done = True
                return
            except ConnectionClosed as e:
                if reused and not yielded and attempt == 0:
                    log.info("tts.reconnect", extra={"provider": self.name})
                    continue
                raise ProviderError(f"bulbul socket closed: {e}") from e
            finally:
                if done:
                    self._release(model, ws)
                else:
                    await ws.close()

    async def _run(
        self, ws: ClientConnection, text: str, speaker: str, lang: str
    ) -> AsyncIterator[bytes]:
        config: dict[str, Any] = {
            "language_code": lang,
            "speaker": speaker,
            "speech_sample_rate": str(SAMPLE_RATE),
            "output_audio_codec": "linear16",
            "min_buffer_size": self._min_buffer,
        }
        await ws.send(json.dumps({"type": "config", "data": config}))
        await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
        await ws.send(json.dumps({"type": "flush"}))
        resampler: Resampler | None = None
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), self._chunk_timeout)
            except TimeoutError as e:
                raise ProviderError("bulbul: no audio within timeout") from e
            msg = json.loads(raw)
            kind = msg.get("type")
            data = msg.get("data") or {}
            if kind == "audio":
                pcm, rate = decode_audio(
                    data.get("content_type", ""), base64.b64decode(data["audio"])
                )
                if rate and rate != SAMPLE_RATE:
                    resampler = resampler or Resampler(rate, SAMPLE_RATE)
                if resampler is not None:
                    pcm = resampler.process(pcm)
                if pcm:
                    yield pcm
            elif kind == "event" and data.get("event_type") == "final":
                if resampler is not None and (tail := resampler.flush()):
                    yield tail
                return
            elif kind == "error":
                raise ProviderError(f"bulbul error {data.get('code')}: {data.get('message')}")

    async def _acquire(self, model: str) -> tuple[ClientConnection, bool]:
        pool = self._idle.get(model, [])
        now = time.monotonic()
        while pool:
            ws, since = pool.pop()
            if ws.state is State.OPEN and now - since < self._max_idle:
                return ws, True
            await ws.close()
        url = f"{self._base}/text-to-speech/ws?model={model}&send_completion_event=true"
        try:
            ws = await connect(
                url,
                additional_headers={"api-subscription-key": self._key},
                open_timeout=self._connect_timeout,
            )
        except (OSError, InvalidHandshake, TimeoutError) as e:
            raise ProviderError(f"bulbul connect failed: {type(e).__name__}") from e
        return ws, False

    def _release(self, model: str, ws: ClientConnection) -> None:
        pool = self._idle.setdefault(model, [])
        if ws.state is State.OPEN and len(pool) < self._pool_size:
            pool.append((ws, time.monotonic()))
        else:
            asyncio.ensure_future(ws.close())

    async def prewarm(self, voice: str) -> None:
        model, _ = vendor_voice(voice)
        ws, _ = await self._acquire(model)
        self._release(model, ws)

    async def aclose(self) -> None:
        for pool in self._idle.values():
            for ws, _ in pool:
                await ws.close()
        self._idle.clear()

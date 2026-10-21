"""Deterministic, network-free implementations of the pipeline interfaces. Used by tests,
the bench harness and the all-fakes pipeline. They run faster than real time unless a
delay is configured."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import AsyncIterator

import numpy as np

from dubhash.audio.frames import SAMPLE_RATE, SAMPLE_WIDTH, bytes_to_ms, ms_to_bytes

MS_PER_WORD = 300
MIN_HEARD_MS = 100


def _word_count(ms: int) -> int:
    return 0 if ms < MIN_HEARD_MS else max(1, ms // MS_PER_WORD)


class FakeSTTStream:
    def __init__(self, owner: FakeSTT, lang: str) -> None:
        self._owner = owner
        self.lang = lang
        self._bytes = 0
        self._words = 0
        self._partials: asyncio.Queue[str | None] = asyncio.Queue()
        self._closed = False
        self.pushed_frames = 0

    def _text(self, n_words: int) -> str:
        scripted = self._owner._peek()
        if scripted is not None:
            return " ".join(scripted.split()[:n_words])
        return " ".join(f"w{i}" for i in range(1, n_words + 1))

    async def push(self, frame: bytes) -> None:
        if self._closed:
            return
        self._bytes += len(frame)
        self.pushed_frames += 1
        n = _word_count(bytes_to_ms(self._bytes))
        if n > self._words:
            self._words = n
            text = self._text(n)
            if text:
                self._partials.put_nowait(text)

    async def end(self) -> str:
        if self._owner.final_delay_ms > 0:
            await asyncio.sleep(self._owner.final_delay_ms / 1000)
        ms = bytes_to_ms(self._bytes)
        if ms < MIN_HEARD_MS:
            final = ""
        else:
            scripted = self._owner._pop()
            final = scripted if scripted is not None else self._text(_word_count(ms))
        self._finish()
        return final

    async def partials(self) -> AsyncIterator[str]:
        while True:
            p = await self._partials.get()
            if p is None:
                return
            yield p

    def _finish(self) -> None:
        if not self._closed:
            self._closed = True
            self._partials.put_nowait(None)

    async def aclose(self) -> None:
        self._finish()


class FakeSTT:
    """Each end() returns the next scripted transcript, else one word ("w1 w2 ...") per
    300 ms of audio pushed; "" under 100 ms of audio (a scripted entry is not consumed
    then)."""

    name = "fake"

    def __init__(self, script: list[str] | None = None, final_delay_ms: int = 0) -> None:
        self._script: deque[str] = deque(script or [])
        self.final_delay_ms = final_delay_ms
        self.opened = 0
        self.langs: list[str] = []

    def _peek(self) -> str | None:
        return self._script[0] if self._script else None

    def _pop(self) -> str | None:
        return self._script.popleft() if self._script else None

    async def open(self, lang: str) -> FakeSTTStream:
        self.opened += 1
        self.langs.append(lang)
        return FakeSTTStream(self, lang)


class FakeMT:
    """translate() returns f"[{tgt}] {text}", or the dictionary entry for text."""

    name = "fake"

    def __init__(
        self, prefix: str = "[tgt] ", dictionary: dict[str, str] | None = None, delay_ms: int = 0
    ) -> None:
        # `prefix` is kept for signature compatibility; the target code replaces "tgt".
        self.prefix = prefix
        self.dictionary = dict(dictionary or {})
        self.delay_ms = delay_ms
        self.calls = 0

    async def translate(self, text: str, src: str, tgt: str) -> str:
        self.calls += 1
        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000)
        if text in self.dictionary:
            return self.dictionary[text]
        return self.prefix.replace("tgt", tgt, 1) + text


class FakeTTS:
    """Yields a quiet tone of len(text) * ms_per_char ms as PCM16@16k in chunk_ms chunks.
    ttfb_ms > 0 sleeps before the first chunk; realtime sleeps chunk_ms between chunks."""

    name = "fake"
    TONE_HZ = 330.0
    AMPLITUDE = 2000

    def __init__(
        self, ms_per_char: int = 60, ttfb_ms: int = 0, chunk_ms: int = 100, realtime: bool = False
    ) -> None:
        self.ms_per_char = ms_per_char
        self.ttfb_ms = ttfb_ms
        self.chunk_ms = chunk_ms
        self.realtime = realtime
        self.requests: list[tuple[str, str, str]] = []

    def _pcm(self, ms: int) -> bytes:
        t = np.arange(SAMPLE_RATE * ms // 1000) / SAMPLE_RATE
        return (np.sin(2 * math.pi * self.TONE_HZ * t) * self.AMPLITUDE).astype(np.int16).tobytes()

    async def synth(self, text: str, voice: str, lang: str) -> AsyncIterator[bytes]:
        self.requests.append((text, voice, lang))
        pcm = self._pcm(len(text) * self.ms_per_char)
        step = ms_to_bytes(self.chunk_ms)
        if self.ttfb_ms > 0:
            await asyncio.sleep(self.ttfb_ms / 1000)
        for i in range(0, len(pcm), step):
            if i and self.realtime:
                await asyncio.sleep(self.chunk_ms / 1000)
            yield pcm[i : i + step]


class EnergyVAD:
    """RMS-energy VAD. prob is 0.5 at threshold_dbfs and moves linearly by 0.05/dB, so
    anything 10 dB above the threshold is 1.0. Stateless; a fallback for Silero."""

    def __init__(self, threshold_dbfs: float = -40.0) -> None:
        self.threshold_dbfs = threshold_dbfs

    def speech_prob(self, frame: bytes) -> float:
        n = len(frame) // SAMPLE_WIDTH
        if n == 0:
            return 0.0
        x = np.frombuffer(frame[: n * SAMPLE_WIDTH], dtype=np.int16).astype(np.float64)
        rms = math.sqrt(float(np.mean(x * x)))
        if rms <= 0:
            return 0.0
        dbfs = 20 * math.log10(rms / 32768.0)
        return min(1.0, max(0.0, 0.5 + (dbfs - self.threshold_dbfs) * 0.05))

    def reset(self) -> None:
        pass

"""Deterministic, network-free implementations of the pipeline interfaces. Used by tests,
the bench harness and the all-fakes pipeline. They run faster than real time unless a
delay is configured."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Any

import numpy as np

from dubhash.audio.frames import SAMPLE_RATE, SAMPLE_WIDTH, bytes_to_ms, ms_to_bytes
from dubhash.interp.langid import LangGuess

MS_PER_WORD = 300
MIN_HEARD_MS = 100


SILENCE_PEAK = 64  # ignore_silence: frames whose peak |sample| is below this are silence


def _lettered(i: int) -> str:
    """1 -> "wa", 26 -> "wz", 27 -> "wba": a fake word with no digits."""
    out = ""
    while True:
        i, r = divmod(i - 1, 26)
        out = chr(ord("a") + r) + out
        if i == 0:
            return "w" + out


def _word_count(ms: int) -> int:
    return 0 if ms < MIN_HEARD_MS else max(1, ms // MS_PER_WORD)


async def _sleep(clock: Any, ms: int) -> None:
    """Sleep on `clock` (FakeClock time) when it can, else on the wall clock."""
    if clock is not None and hasattr(clock, "sleep_ms"):
        await clock.sleep_ms(ms)
    else:
        await asyncio.sleep(ms / 1000)


def _is_silent(frame: bytes) -> bool:
    n = len(frame) // 2
    if n == 0:
        return True
    return int(np.max(np.abs(np.frombuffer(frame[: n * 2], dtype=np.int16).astype(np.int32)))) < (
        SILENCE_PEAK
    )


class FakeSTTStream:
    def __init__(self, owner: FakeSTT, lang: str) -> None:
        self._owner = owner
        self.lang = lang
        self._bytes = 0
        self._words = 0
        self._partials: asyncio.Queue[str | None] = asyncio.Queue()
        self._closed = False
        self.pushed_frames = 0
        self.detected_lang: str | None = None  # set by end(), as SarvamSTTStream does
        self.detected_confidence: float | None = None
        self._heard = 0  # bytes that count towards words (all, or voiced only)
        self._scripted_partials = owner._pop_partials()

    def _text(self, n_words: int) -> str:
        scripted = self._owner._peek()
        if scripted is not None:
            return " ".join(scripted.split()[:n_words])
        if self._owner.lettered:
            # ends a clause like a real final, so the chunker may commit it (L9)
            return " ".join(_lettered(i) for i in range(1, n_words + 1)) + "."
        return " ".join(f"w{i}" for i in range(1, n_words + 1))

    async def push(self, frame: bytes) -> None:
        if self._closed:
            return
        self._bytes += len(frame)
        self.pushed_frames += 1
        if not (self._owner.ignore_silence and _is_silent(frame)):
            self._heard += len(frame)
        if self._scripted_partials is not None:
            ms = bytes_to_ms(self._bytes)
            while self._scripted_partials and self._scripted_partials[0][0] <= ms:
                self._partials.put_nowait(self._scripted_partials.popleft()[1])
            return
        n = _word_count(bytes_to_ms(self._heard))
        if n > self._words:
            self._words = n
            text = self._text(n)
            if text:
                self._partials.put_nowait(text)

    async def end(self) -> str:
        if self._owner.final_delay_ms > 0:
            await _sleep(self._owner.clock, self._owner.final_delay_ms)
        ms = bytes_to_ms(self._heard)
        if ms < MIN_HEARD_MS:
            final = ""
        else:
            scripted = self._owner._pop()
            final = scripted if scripted is not None else self._text(_word_count(ms))
            if self._owner._detected:  # else leave whatever the test set
                entry = self._owner._pop_detected()
                if isinstance(entry, tuple):
                    self.detected_lang, self.detected_confidence = entry
                else:
                    self.detected_lang = entry
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
    then).

    partials: optional per-utterance scripts (one list per open(), in order) of
    (audio_ms, text): text is emitted as a partial once audio_ms of audio has been pushed.
    A scripted utterance emits only those partials ([] = none). ignore_silence: only
    voiced frames count towards words. final_delay_ms sleeps on `clock` when given.
    detected: the language reported on each final (stream.detected_lang), in order, or a
    (lang, confidence) pair (also sets detected_confidence); None entries and an exhausted
    script report None.
    lettered: words are "wa wb wc ..." instead of "w1 w2 w3 ..." (a text ending in a digit
    is never committed as a chunk, interp/chunker.py)."""

    name = "fake"

    def __init__(
        self,
        script: list[str] | None = None,
        final_delay_ms: int = 0,
        *,
        partials: list[list[tuple[int, str]]] | None = None,
        ignore_silence: bool = False,
        clock: Any = None,
        detected: list[str | tuple[str, float] | None] | None = None,
        lettered: bool = False,
    ) -> None:
        self.lettered = lettered
        self._script: deque[str] = deque(script or [])
        self._detected: deque[str | tuple[str, float] | None] = deque(detected or [])
        self.final_delay_ms = final_delay_ms
        self._partial_script: deque[list[tuple[int, str]]] | None = (
            deque(partials) if partials is not None else None
        )
        self.ignore_silence = ignore_silence
        self.clock = clock
        self.opened = 0
        self.langs: list[str] = []

    def _peek(self) -> str | None:
        return self._script[0] if self._script else None

    def _pop(self) -> str | None:
        return self._script.popleft() if self._script else None

    def _pop_detected(self) -> str | tuple[str, float] | None:
        return self._detected.popleft() if self._detected else None

    def _pop_partials(self) -> deque[tuple[int, str]] | None:
        if self._partial_script is None:
            return None
        return deque(sorted(self._partial_script.popleft())) if self._partial_script else deque()

    async def open(self, lang: str) -> FakeSTTStream:
        self.opened += 1
        self.langs.append(lang)
        return FakeSTTStream(self, lang)


class FakeMT:
    """translate() returns f"[{tgt}] {text}", or the dictionary entry for text. delay_ms
    sleeps on `clock` when given; `calls` counts every call, `cancelled` those cancelled
    mid-delay, `texts` the inputs."""

    name = "fake"

    def __init__(
        self,
        prefix: str = "[tgt] ",
        dictionary: dict[str, str] | None = None,
        delay_ms: int = 0,
        *,
        clock: Any = None,
    ) -> None:
        # `prefix` is kept for signature compatibility; the target code replaces "tgt".
        self.prefix = prefix
        self.dictionary = dict(dictionary or {})
        self.delay_ms = delay_ms
        self.clock = clock
        self.calls = 0
        self.cancelled = 0
        self.texts: list[str] = []

    async def translate(self, text: str, src: str, tgt: str) -> str:
        self.calls += 1
        self.texts.append(text)
        if self.delay_ms > 0:
            try:
                await _sleep(self.clock, self.delay_ms)
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        if text in self.dictionary:
            return self.dictionary[text]
        return self.prefix.replace("tgt", tgt, 1) + text


class FakeTTS:
    """Yields a quiet tone of len(text) * ms_per_char ms as PCM16@16k in chunk_ms chunks.
    ttfb_ms > 0 sleeps before the first chunk (on `clock` when given); realtime sleeps
    chunk_ms (wall clock) between chunks."""

    name = "fake"
    TONE_HZ = 330.0
    AMPLITUDE = 2000

    def __init__(
        self,
        ms_per_char: int = 60,
        ttfb_ms: int = 0,
        chunk_ms: int = 100,
        realtime: bool = False,
        *,
        clock: Any = None,
    ) -> None:
        self.ms_per_char = ms_per_char
        self.ttfb_ms = ttfb_ms
        self.chunk_ms = chunk_ms
        self.realtime = realtime
        self.clock = clock
        self.requests: list[tuple[str, str, str]] = []

    def _pcm(self, ms: int) -> bytes:
        t = np.arange(SAMPLE_RATE * ms // 1000) / SAMPLE_RATE
        return (np.sin(2 * math.pi * self.TONE_HZ * t) * self.AMPLITUDE).astype(np.int16).tobytes()

    async def synth(self, text: str, voice: str, lang: str) -> AsyncIterator[bytes]:
        self.requests.append((text, voice, lang))
        pcm = self._pcm(len(text) * self.ms_per_char)
        step = ms_to_bytes(self.chunk_ms)
        if self.ttfb_ms > 0:
            await _sleep(self.clock, self.ttfb_ms)
        for i in range(0, len(pcm), step):
            if i and self.realtime:
                await asyncio.sleep(self.chunk_ms / 1000)
            yield pcm[i : i + step]


class FakeLID:
    """identify() returns the next scripted LangGuess, else `default` (lang None). A
    scripted language outside the call's candidates comes back as None, as the real engine
    would. `calls` records (pcm bytes, candidates); delay_ms sleeps on `clock` when given."""

    name = "fake"

    def __init__(
        self,
        script: list[LangGuess] | None = None,
        *,
        default: LangGuess | None = None,
        delay_ms: int = 0,
        clock: Any = None,
    ) -> None:
        self._script: deque[LangGuess] = deque(script or [])
        self.default = default or LangGuess(None, 0.0, "lid")
        self.delay_ms = delay_ms
        self.clock = clock
        self.calls: list[tuple[int, tuple[str, ...]]] = []

    async def identify(self, pcm16_16k: bytes, candidates: Sequence[str]) -> LangGuess:
        self.calls.append((len(pcm16_16k), tuple(candidates)))
        if self.delay_ms > 0:
            await _sleep(self.clock, self.delay_ms)
        g = self._script.popleft() if self._script else self.default
        if g.lang is not None and g.lang not in candidates:
            return LangGuess(None, g.confidence, g.source)
        return g


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

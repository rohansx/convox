"""One Interpreter per direction: frames -> VAD -> endpoint -> STT -> MT -> TTS, handing
finished Translations to a sink (the FloorController). It never touches a transport.

The frame loop only does VAD and endpointing. Each utterance gets its own worker task
(STT stream, final, MT, TTS first byte), fed through a queue, so a slow provider never
holds up endpointing of later speech. Workers deliver to the sink strictly in utterance
order: each waits for the previous one to finish delivering.

Decisions where the spec is open:
- seq counts delivered translations per direction from 0; discarded utterances
  (backchannels, noise, empty finals, provider errors) do not consume a seq.
- Gender, unless given, is detected once from the audio of the first utterance that
  reaches TTS (backchannels and noise don't count).
- Utterances under min_utterance_ms are dropped (sink.on_speech_end then
  on_utterance_discarded), not merged into the next one.
- When the frame stream ends mid-utterance, the utterance is force-ended and translated.

Speculative MT (pair.spec_mt, interp/speculate.py): the frame loop feeds each utterance's
Speculator with silence/speech frames and the partial pump with STT partials, so MT can
start on the latest partial during the EOU wait. The worker reuses that result only when
normalize(final) matches; otherwise it translates the final as usual. timings gain
spec_hit (the final matched the speculated text) and spec_used (its translation was
delivered), as 0/1. t_mt stays the time the translation was available to the worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from dubhash.clock import Clock
from dubhash.config.pairs import PairConfig
from dubhash.interp.contracts import MT, STT, TTS, VAD, STTStream
from dubhash.interp.endpoint import Discard, Endpointer, EndpointEvent, SpeechStart, Utterance
from dubhash.interp.gender import detect_gender
from dubhash.interp.speculate import SpecOutcome, Speculator
from dubhash.types import Side, Translation, other

log = logging.getLogger(__name__)

Gender = Literal["m", "f"]
BACKCHANNEL_MAX_WORDS = 2


@runtime_checkable
class InterpreterSink(Protocol):
    """What an Interpreter reports to. Methods may be async or plain; both are accepted."""

    async def on_speech_start(self, side: Side) -> None: ...

    async def on_speech_end(self, side: Side) -> None: ...

    async def on_translation(self, tr: Translation) -> None: ...

    async def on_utterance_discarded(self, side: Side) -> None: ...

    async def on_partial(self, side: Side, text: str) -> None: ...


async def _maybe_await(result: Awaitable[None] | None) -> None:
    if inspect.isawaitable(result):
        await result


class _Discarded(Exception):
    """Internal: this utterance ends without a translation (not an error)."""


@dataclass(slots=True, eq=False)
class _Job:
    t_speech_start: int
    prev: asyncio.Event | None
    queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    utterance: Utterance | None = None
    ended: bool = False
    task: asyncio.Task[None] | None = None
    spec: Speculator | None = None


class Interpreter:
    def __init__(
        self,
        call_id: str,
        side: Side,
        pair: PairConfig,
        stt: STT,
        mt: MT,
        tts: TTS,
        vad: VAD,
        clock: Clock,
        sink: InterpreterSink,
        gender: Gender | None = None,
    ) -> None:
        self.call_id = call_id
        self.side = side
        self.pair = pair
        self.stt = stt
        self.mt = mt
        self.tts = tts
        self.vad = vad
        self.clock = clock
        self.sink = sink
        self.gender: Gender | None = gender
        self.src_lang = pair.side(side).lang
        self.tgt = pair.side(other(side))
        self.endpointer = Endpointer(eou_ms=pair.eou_ms, backchannel_max_ms=pair.backchannel_max_ms)
        self._seq = 0
        self._current: _Job | None = None
        self._last_done: asyncio.Event | None = None
        self._jobs: set[_Job] = set()
        self._closed = False
        # speculative MT counters, summed over finished utterances (for bench/metrics)
        self.spec_started = 0
        self.spec_cancelled = 0
        self.spec_hits = 0
        self.spec_used = 0

    # -- frame loop ---------------------------------------------------------------

    async def run(self, frames: AsyncIterator[bytes]) -> None:
        """Process frames until they end (then deliver outstanding work) or cancellation."""
        await self.run_until_frames_end(frames)
        await self.drain()

    async def run_until_frames_end(self, frames: AsyncIterator[bytes]) -> None:
        """Like run(), but returns as soon as frames end, leaving workers running."""
        async for frame in frames:
            if self._closed:
                return
            now = self.clock.now_ms()
            prob = self.vad.speech_prob(frame)
            events = self.endpointer.feed(frame, prob, now)
            if self._current is not None and not any(isinstance(e, SpeechStart) for e in events):
                self._current.queue.put_nowait(frame)
            await self._handle(events, frame)
            self._speculate(now)
        await self.force_eou()

    def _speculate(self, now: int) -> None:
        job = self._current
        if job is None or job.spec is None or not self.endpointer.in_speech:
            return
        t_last = self.endpointer.t_last_speech
        if t_last == now:
            job.spec.on_speech(now)
        else:
            job.spec.on_silence(now, t_last, t_last - self.endpointer.t_speech_start)

    async def force_eou(self) -> None:
        """End the current utterance now (the STT vendor signalled end-of-utterance)."""
        await self._handle(self.endpointer.force_eou(self.clock.now_ms()), b"")

    async def drain(self) -> None:
        """Wait until every started utterance has been delivered or discarded."""
        while self._jobs:
            tasks = [j.task for j in list(self._jobs) if j.task is not None]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self) -> None:
        self._closed = True
        tasks = [j.task for j in list(self._jobs) if j.task is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
        self._current = None

    async def _handle(self, events: list[EndpointEvent], frame: bytes) -> None:
        for ev in events:
            if isinstance(ev, SpeechStart):
                job = _Job(t_speech_start=ev.t, prev=self._last_done)
                if self.pair.spec_mt:
                    job.spec = Speculator(
                        self._translate,
                        after_ms=self.pair.spec_after_ms,
                        backchannel_max_ms=self.pair.backchannel_max_ms,
                    )
                self._last_done = job.done
                job.queue.put_nowait(ev.preroll + frame)
                self._current = job
                self._jobs.add(job)
                job.task = asyncio.create_task(self._work(job), name=f"interp-{self.side}")
                await _maybe_await(self.sink.on_speech_start(self.side))
            elif isinstance(ev, Utterance | Discard):
                job = self._current
                self._current = None
                if job is None:
                    continue
                if isinstance(ev, Utterance):
                    job.utterance = ev
                    log.debug(
                        "vad.eou",
                        extra={
                            "call_id": self.call_id,
                            "side": self.side,
                            "t_eou": ev.t_eou,
                            "utt_ms": ev.duration_ms,
                            "backchannel": ev.short,
                        },
                    )
                job.queue.put_nowait(None)
                await _maybe_await(self.sink.on_speech_end(self.side))

    # -- per-utterance worker -----------------------------------------------------

    async def _work(self, job: _Job) -> None:
        stage = "stt"
        provider = self.stt.name
        stream: STTStream | None = None
        pump: asyncio.Task[None] | None = None
        tr: Translation | None = None
        try:
            try:
                stream = await self.stt.open(self.src_lang)
                pump = asyncio.create_task(self._pump_partials(stream, job))
                while (item := await job.queue.get()) is not None:
                    await stream.push(item)
                job.ended = True
                u = job.utterance
                if u is None:
                    raise _Discarded
                final = (await stream.end()).strip()
                t_stt_final = self.clock.now_ms()
                log.info(
                    "stt.final",
                    extra={
                        "call_id": self.call_id,
                        "side": self.side,
                        "provider": provider,
                        "latency_ms": t_stt_final - u.t_eou,
                    },
                )
                words = len(final.split())
                if not final or (u.short and words <= BACKCHANNEL_MAX_WORDS):
                    raise _Discarded

                stage, provider = "mt", self.mt.name
                spec = await job.spec.take(final) if job.spec else SpecOutcome(None, False)
                if spec.text is not None:
                    tgt_text = spec.text
                else:
                    tgt_text = await self._translate(final)
                t_mt = self.clock.now_ms()
                self.spec_hits += spec.hit
                self.spec_used += spec.text is not None

                stage, provider = "tts", self.tts.name
                if self.gender is None:
                    self.gender = detect_gender(u.audio)
                voice = self.tgt.voice(self.gender)
                it = aiter(self.tts.synth(tgt_text, voice, self.tgt.lang))
                try:
                    first = await anext(it)
                except StopAsyncIteration:
                    raise RuntimeError("tts produced no audio") from None
                t_tts_ttfb = self.clock.now_ms()
                log.info(
                    "tts.ttfb",
                    extra={
                        "call_id": self.call_id,
                        "provider": provider,
                        "voice": voice,
                        "latency_ms": t_tts_ttfb - t_mt,
                    },
                )
                tr = Translation(
                    side=self.side,
                    seq=-1,
                    src_text=final,
                    tgt_text=tgt_text,
                    audio=_prepend(first, it),
                    voice=voice,
                    timings={
                        "t_speech_start": u.t_start,
                        "t_speech_end": u.t_end,
                        "t_eou": u.t_eou,
                        "t_stt_final": t_stt_final,
                        "t_mt": t_mt,
                        "t_tts_ttfb": t_tts_ttfb,
                        "spec_hit": int(spec.hit),
                        "spec_used": int(spec.text is not None),
                    },
                    source_audio=u.audio,
                )
            except _Discarded:
                tr = None
            except Exception as e:
                tr = None
                log.warning(
                    "provider.error",
                    extra={
                        "call_id": self.call_id,
                        "side": self.side,
                        "provider": provider,
                        "stage": stage,
                        "error": repr(e),
                        "reconnecting": False,
                    },
                )
                if not job.ended:  # keep sink events in order: wait for this utterance's end
                    while await job.queue.get() is not None:
                        pass
            finally:
                if pump is not None:
                    pump.cancel()
                if job.spec is not None:
                    job.spec.cancel()
                    self.spec_started += job.spec.started
                    self.spec_cancelled += job.spec.cancelled
                if stream is not None:
                    with contextlib.suppress(Exception):
                        await stream.aclose()

            if job.prev is not None:
                await job.prev.wait()
            if tr is None:
                await _maybe_await(self.sink.on_utterance_discarded(self.side))
            else:
                tr.seq = self._seq
                self._seq += 1
                await _maybe_await(self.sink.on_translation(tr))
        finally:
            job.done.set()
            self._jobs.discard(job)

    async def _translate(self, text: str) -> str:
        return await self.mt.translate(text, self.src_lang, self.tgt.lang)

    async def _pump_partials(self, stream: STTStream, job: _Job) -> None:
        try:
            async for text in stream.partials():
                if job.spec is not None and not job.ended:
                    job.spec.on_partial(text, self.clock.now_ms())
                await _maybe_await(self.sink.on_partial(self.side, text))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # partials are cosmetic; never fail the utterance
            log.debug("stt.partials_error", extra={"call_id": self.call_id, "error": repr(e)})


async def _prepend(first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    try:
        yield first
        async for chunk in rest:
            yield chunk
    finally:
        aclose: Any = getattr(rest, "aclose", None)
        if aclose is not None:
            await aclose()

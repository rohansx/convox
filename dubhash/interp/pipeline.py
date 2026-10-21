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

MT skip: when the source side's mt_lang already is the target language (stt
sarvam-translate + mt_src en-IN towards an English listener), the final is delivered as the
translation with no MT call and t_mt = t_stt_final.

Early final (pair.early_final_ms > 0, open question L3): once silence inside an utterance
has lasted early_final_ms, the frame loop queues an _EarlyFinal marker and the worker ends
the STT stream then, so the final, MT and TTS first byte overlap the rest of the EOU wait.
If a speech frame arrives before EOU, a _Resume marker discards that result (MT/TTS are
cancelled; the old stream's end() finishes in the background so its socket returns to
the warm pool) and the worker opens a new stream and pushes the whole utterance buffered
so far (pre-roll, speech and the silence gap) before continuing live. So the delivered
translation always covers the complete utterance.
- Delivery is never before EOU: the worker only delivers after the utterance's EOU, so
  the floor's turn-taking is unchanged; with early final it simply has the audio ready.
- The backchannel rule uses the speech span at the early final, which equals the
  utterance's span when it is used (speech after it would have discarded it).
- No early final for speech shorter than the Endpointer's min_utterance_ms (noise).
- Speculation on partials does not start while an early final is pending.
- timings gain early_final_used (0/1), early_final_restarts (count) and, when used,
  t_early_final (when end() was sent; t_stt_final may then precede t_eou). With
  early_final_ms = 0 the timings are exactly as before.
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


@dataclass(frozen=True, slots=True)
class _EarlyFinal:
    """Queue marker: silence reached early_final_ms; end the STT stream now."""

    speech_ms: int  # speech span so far (for the backchannel rule)


class _Resume:
    """Queue marker: speech resumed after an early final; discard it and restart STT."""


_RESUME = _Resume()
_Item = bytes | _EarlyFinal | _Resume | None


@dataclass(slots=True)
class _Stage:
    name: str
    provider: str


@dataclass(slots=True, eq=False)
class _Ready:
    """A final, translated and synthesised up to its first audio chunk."""

    final: str
    tgt_text: str
    t_stt_final: int
    t_mt: int
    t_tts_ttfb: int
    voice: str
    first: bytes
    rest: AsyncIterator[bytes]
    spec: SpecOutcome
    spec_eligible: bool


@dataclass(slots=True, eq=False)
class _Job:
    t_speech_start: int
    prev: asyncio.Event | None
    queue: asyncio.Queue[_Item] = field(default_factory=asyncio.Queue)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    utterance: Utterance | None = None
    ended: bool = False
    task: asyncio.Task[None] | None = None
    spec: Speculator | None = None
    early_pending: bool = False  # an early final was requested and speech has not resumed


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
        # The STT already outputs the target language (e.g. Marathi speech -> English final
        # for an English listener): no MT call, and t_mt = t_stt_final.
        self.mt_skip = pair.side(side).mt_lang == self.tgt.lang
        self.endpointer = Endpointer(
            eou_ms=pair.eou_ms,
            backchannel_max_ms=pair.backchannel_max_ms,
            max_utterance_ms=pair.max_utterance_ms,
        )
        self._seq = 0
        self._current: _Job | None = None
        self._last_done: asyncio.Event | None = None
        self._jobs: set[_Job] = set()
        self._closed = False
        # speculative MT counters, summed over finished utterances (for bench/metrics)
        self.spec_eligible = 0  # utterances that reached MT with speculation enabled
        self.spec_started = 0
        self.spec_cancelled = 0
        self.spec_hits = 0
        self.spec_used = 0
        # early-final counters (pair.early_final_ms), same idea
        self.early_started = 0  # early end() calls
        self.early_restarts = 0  # early finals discarded because speech resumed
        self.early_used = 0  # delivered translations that came from an early final
        self._bg: set[asyncio.Task[Any]] = set()

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
            job = self._current
            if job is not None and not any(isinstance(e, SpeechStart) for e in events):
                ep = self.endpointer
                if job.early_pending and ep.in_speech and prob > ep.speech_threshold:
                    job.early_pending = False  # speech resumed: restart before this frame
                    job.queue.put_nowait(_RESUME)
                job.queue.put_nowait(frame)
                self._early_final(job, now)
            await self._handle(events, frame)
            self._speculate(now)
        await self.force_eou()

    def _early_final(self, job: _Job, now: int) -> None:
        ep = self.endpointer
        after = self.pair.early_final_ms
        if after <= 0 or job.early_pending or not ep.in_speech:
            return
        if now - ep.t_last_speech < after or ep.speech_ms < ep.min_utterance_ms:
            return
        job.early_pending = True
        job.queue.put_nowait(_EarlyFinal(ep.speech_ms))

    def _speculate(self, now: int) -> None:
        job = self._current
        if job is None or job.spec is None or not self.endpointer.in_speech:
            return
        if job.early_pending:
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
        tasks += list(self._bg)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
        self._current = None

    async def _handle(self, events: list[EndpointEvent], frame: bytes) -> None:
        for ev in events:
            if isinstance(ev, SpeechStart):
                job = _Job(t_speech_start=ev.t, prev=self._last_done)
                if self.pair.spec_mt and not self.mt_skip:
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
                            "forced": ev.forced,
                        },
                    )
                job.queue.put_nowait(None)
                await _maybe_await(self.sink.on_speech_end(self.side))

    # -- per-utterance worker -----------------------------------------------------

    async def _work(self, job: _Job) -> None:
        stage = _Stage("stt", self.stt.name)
        stream: STTStream | None = None
        pump: asyncio.Task[None] | None = None
        early: asyncio.Task[_Ready] | None = None
        t_early = 0
        restarts = 0
        audio: list[bytes] = []  # everything pushed for this utterance, for a restart
        early_on = self.pair.early_final_ms > 0
        tr: Translation | None = None
        try:
            try:
                stream = await self.stt.open(self.src_lang)
                pump = asyncio.create_task(self._pump_partials(stream, job))
                while (item := await job.queue.get()) is not None:
                    if isinstance(item, bytes):
                        if early_on:
                            audio.append(item)
                        if stream is not None:
                            await stream.push(item)
                    elif isinstance(item, _EarlyFinal):
                        if stream is None:
                            continue
                        self.early_started += 1
                        t_early = self.clock.now_ms()  # when end() is sent
                        ending, stream = self._end_in_background(stream), None
                        early = asyncio.create_task(
                            self._early(ending, item.speech_ms, b"".join(audio), job, stage)
                        )
                    elif early is not None:  # _Resume: speech after the early final
                        self._discard(early)
                        early = None
                        restarts += 1
                        self.early_restarts += 1
                        stage.name, stage.provider = "stt", self.stt.name
                        if pump is not None:
                            pump.cancel()
                        stream = await self.stt.open(self.src_lang)
                        pump = asyncio.create_task(self._pump_partials(stream, job))
                        for frame in audio:
                            await stream.push(frame)
                job.ended = True
                u = job.utterance
                if u is None:
                    raise _Discarded
                from_early = early is not None
                if early is not None:
                    task, early = early, None
                    ready = await task
                else:
                    assert stream is not None
                    final = (await stream.end()).strip()
                    t_stt_final = self.clock.now_ms()
                    self._log_final(t_stt_final - u.t_eou)
                    ready = await self._finish(final, u.short, u.audio, job, stage, t_stt_final)
                self.spec_eligible += ready.spec_eligible
                self.spec_hits += ready.spec.hit
                self.spec_used += ready.spec.text is not None
                timings = {
                    "t_speech_start": u.t_start,
                    "t_speech_end": u.t_end,
                    "t_eou": u.t_eou,
                    "t_stt_final": ready.t_stt_final,
                    "t_mt": ready.t_mt,
                    "t_tts_ttfb": ready.t_tts_ttfb,
                    "spec_hit": int(ready.spec.hit),
                    "spec_used": int(ready.spec.text is not None),
                }
                if early_on:
                    if from_early:
                        timings["t_early_final"] = t_early
                        self.early_used += 1
                    timings["early_final_used"] = int(from_early)
                    timings["early_final_restarts"] = restarts
                tr = Translation(
                    side=self.side,
                    seq=-1,
                    src_text=ready.final,
                    tgt_text=ready.tgt_text,
                    audio=_prepend(ready.first, ready.rest),
                    voice=ready.voice,
                    timings=timings,
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
                        "provider": stage.provider,
                        "stage": stage.name,
                        "error": repr(e),
                        "reconnecting": False,
                    },
                )
                if not job.ended:  # keep sink events in order: wait for this utterance's end
                    while await job.queue.get() is not None:
                        pass
            finally:
                if early is not None:
                    self._discard(early)
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

    def _log_final(self, latency_ms: int) -> None:
        log.info(
            "stt.final",
            extra={
                "call_id": self.call_id,
                "side": self.side,
                "provider": self.stt.name,
                "latency_ms": latency_ms,
            },
        )

    def _end_in_background(self, stream: STTStream) -> asyncio.Task[str]:
        """end() then aclose() the stream in a task of its own, so discarding an early final
        never interrupts end() (a socket that settles returns to the warm pool)."""

        async def end_and_close() -> str:
            try:
                return await stream.end()
            finally:
                with contextlib.suppress(Exception):
                    await stream.aclose()

        task = asyncio.create_task(end_and_close(), name=f"stt-end-{self.side}")
        self._keep(task)
        return task

    def _keep(self, task: asyncio.Future[Any]) -> None:
        self._bg.add(task)  # type: ignore[arg-type]
        task.add_done_callback(self._bg.discard)  # type: ignore[arg-type]
        task.add_done_callback(_retrieve)

    async def _early(
        self, ending: asyncio.Task[str], speech_ms: int, audio: bytes, job: _Job, stage: _Stage
    ) -> _Ready:
        t0 = self.clock.now_ms()
        final = (await asyncio.shield(ending)).strip()
        t_stt_final = self.clock.now_ms()
        self._log_final(t_stt_final - t0)
        short = speech_ms < self.pair.backchannel_max_ms
        return await self._finish(final, short, audio, job, stage, t_stt_final)

    async def _finish(
        self, final: str, short: bool, audio: bytes, job: _Job, stage: _Stage, t_stt_final: int
    ) -> _Ready:
        """STT final -> MT -> TTS first chunk. Raises _Discarded for empty/backchannel."""
        words = len(final.split())
        if not final or (short and words <= BACKCHANNEL_MAX_WORDS):
            raise _Discarded

        if self.mt_skip:
            spec, tgt_text, t_mt = SpecOutcome(None, False), final, t_stt_final
        else:
            stage.name, stage.provider = "mt", self.mt.name
            spec = await job.spec.take(final) if job.spec else SpecOutcome(None, False)
            tgt_text = spec.text if spec.text is not None else await self._translate(final)
            t_mt = self.clock.now_ms()

        stage.name, stage.provider = "tts", self.tts.name
        if self.gender is None:
            self.gender = detect_gender(audio)
        voice = self.tgt.voice(self.gender)
        it = aiter(self.tts.synth(tgt_text, voice, self.tgt.lang))
        try:
            first = await anext(it)
        except StopAsyncIteration:
            raise RuntimeError("tts produced no audio") from None
        except BaseException:
            await _aclose(it)
            raise
        t_tts_ttfb = self.clock.now_ms()
        log.info(
            "tts.ttfb",
            extra={
                "call_id": self.call_id,
                "provider": stage.provider,
                "voice": voice,
                "latency_ms": t_tts_ttfb - t_mt,
            },
        )
        return _Ready(
            final=final,
            tgt_text=tgt_text,
            t_stt_final=t_stt_final,
            t_mt=t_mt,
            t_tts_ttfb=t_tts_ttfb,
            voice=voice,
            first=first,
            rest=it,
            spec=spec,
            spec_eligible=job.spec is not None,
        )

    def _discard(self, early: asyncio.Task[_Ready]) -> None:
        """Drop an early result: cancel it, or close its TTS stream if it already finished."""
        if not early.done():
            early.cancel()

        def close(t: asyncio.Task[_Ready]) -> None:
            if t.cancelled() or t.exception() is not None:
                return
            self._keep(asyncio.ensure_future(_aclose(t.result().rest)))

        early.add_done_callback(close)

    async def _translate(self, text: str) -> str:
        return await self.mt.translate(text, self.pair.side(self.side).mt_lang, self.tgt.lang)

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


def _retrieve(task: asyncio.Future[Any]) -> None:
    """Mark a background task's exception as retrieved (it may never be awaited)."""
    if not task.cancelled():
        with contextlib.suppress(BaseException):
            task.exception()


async def _aclose(it: AsyncIterator[bytes]) -> None:
    aclose: Any = getattr(it, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await aclose()


async def _prepend(first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    try:
        yield first
        async for chunk in rest:
            yield chunk
    finally:
        aclose: Any = getattr(rest, "aclose", None)
        if aclose is not None:
            await aclose()

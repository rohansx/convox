"""CallSession: one per call. Owns the two legs, two Interpreters (A->B, B->A), one
FloorController and the TurnLog, all run by one task inside one asyncio.TaskGroup
(docs/architecture/system-overview.md#callsession).

Lifecycle: "ringing" until both legs are attached, then "active": the opening line is
synthesised with each side's own TTS and sent to its leg, and interpreting starts. The
call ends ("ended") when either leg's frames end (the other leg is closed), on hangup(),
or if the session task itself fails. Teardown always closes the floor (whatever is still
queued or playing is ended as clipped, so the turn log stays complete), the interpreters,
both legs and the providers.

Decisions where the spec is open:
- The opening line is the one send() outside the FloorController. It happens before the
  floor plays anything, is not marked, and does not hold up interpreting: frames that
  arrive while it is being synthesised are dropped; interpreting starts once it is sent.
  It is streamed to the leg chunk by chunk as the TTS produces it.
- Prompts (opening, line check) use the side's female voice (`voice_f`).
- In bridge mode there is no opening line and no interpreter; the floor starts in bypass.
  After DTMF escape the interpreters are cancelled and frames are forwarded raw.
- Backchannel turns are recognised from what the interpreter reports: a discarded
  utterance whose STT final was non-empty, at most 2 words, and shorter than
  backchannel_max_ms (t_speech_end is estimated as t_eou - eou_ms).
- The session owns `providers` and acloses them at the end (build one set per call).
- The interpreter reports speech end at EOU (eou_ms after the last voiced frame), but the
  floor's barge-in timer needs the end of speech itself, or listener speech of only
  barge_in_min_ms - eou_ms (650 ms) would clear playback. The sink therefore watches the
  VAD and tells the floor speech ended after SPEECH_END_HANGOVER_MS of silence (and
  started again if speech resumes before EOU); the EOU speech-end is then not repeated.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from dubhash.audio.frames import FRAME_BYTES, bytes_to_ms
from dubhash.audio.mixer import render_playback
from dubhash.clock import Clock, MonotonicClock
from dubhash.config.pairs import PairConfig
from dubhash.events import EventBus
from dubhash.interp.contracts import STT, VAD, STTStream
from dubhash.interp.pipeline import BACKCHANNEL_MAX_WORDS, Interpreter
from dubhash.legs.base import LegTransport
from dubhash.session.floor import FloorController
from dubhash.session.prompts import line_check_text, opening_text
from dubhash.session.providers import SIDES, Providers
from dubhash.session.turn_log import TurnLog
from dubhash.types import CallEvent, EventKind, Side, Translation, other

log = logging.getLogger(__name__)

Status = Literal["ringing", "active", "ended"]
Mode = Literal["interpret", "bridge"]

PROMPT_TIMEOUT_S = 10.0
SPEECH_END_HANGOVER_MS = 200  # silence after which the floor hears "speech ended"
SPEECH_THRESHOLD = 0.5  # same as the Endpointer's
PROMPT_CHUNK = FRAME_BYTES * 5  # 100 ms


class _Stop(Exception):
    """Raised inside the TaskGroup to end the call."""


@dataclass(slots=True, eq=False)
class _Utt:
    """One utterance between the interpreter's speech start and its delivery."""

    t_start: int
    t_eou: int | None = None
    stream: bool = False  # an STT stream was opened for it
    final: str | None = None
    t_stt_final: int | None = None


class _TrackedStream:
    def __init__(self, inner: STTStream, utt: _Utt | None, clock: Clock) -> None:
        self._inner = inner
        self._utt = utt
        self._clock = clock

    async def push(self, frame: bytes) -> None:
        await self._inner.push(frame)

    async def end(self) -> str:
        final = await self._inner.end()
        if self._utt is not None:
            self._utt.final = final
            self._utt.t_stt_final = self._clock.now_ms()
        return final

    def partials(self) -> AsyncIterator[str]:
        return self._inner.partials()

    async def aclose(self) -> None:
        await self._inner.aclose()


class _TrackedSTT:
    """Wraps a side's STT so the session sees each utterance's final transcript (to log
    backchannels, which the interpreter discards without saying why)."""

    def __init__(self, inner: STT, utts: deque[_Utt], clock: Clock) -> None:
        self.inner = inner
        self.name = inner.name
        self._utts = utts
        self._clock = clock

    async def open(self, lang: str) -> STTStream:
        # Workers open their stream first thing, in utterance order: bind to the oldest
        # utterance without one before awaiting anything.
        utt = next((u for u in self._utts if not u.stream), None)
        if utt is not None:
            utt.stream = True
        return _TrackedStream(await self.inner.open(lang), utt, self._clock)


class _TrackedVAD:
    """Wraps a side's VAD to remember when that side last had a voiced frame."""

    def __init__(self, inner: VAD, clock: Clock) -> None:
        self.inner = inner
        self._clock = clock
        self.voiced = False
        self.last_voiced = clock.now_ms()

    def speech_prob(self, frame: bytes) -> float:
        p = self.inner.speech_prob(frame)
        self.voiced = p > SPEECH_THRESHOLD
        if self.voiced:
            self.last_voiced = self._clock.now_ms()
        return p

    def reset(self) -> None:
        self.inner.reset()


class _Sink:
    """Interpreter -> floor adapter. Also tracks utterances in flight and publishes STT
    partials for the console."""

    def __init__(self, session: CallSession) -> None:
        self._s = session
        self.utts: dict[Side, deque[_Utt]] = {s: deque() for s in SIDES}
        self.vads: dict[Side, _TrackedVAD] = {}
        self._ended_early: dict[Side, bool] = {s: False for s in SIDES}

    async def voice_check(self, side: Side) -> None:
        """Called between frames: end speech for the floor after a short silence, and
        restart it if the same utterance resumes."""
        vad = self.vads.get(side)
        if vad is None:
            return
        if not any(u.t_eou is None for u in self.utts[side]):
            self._ended_early[side] = False
            return
        if self._ended_early[side]:
            if vad.voiced:
                self._ended_early[side] = False
                await self._s.floor.on_speech_start(side)
        elif not vad.voiced and self._s.clock.now_ms() - vad.last_voiced >= SPEECH_END_HANGOVER_MS:
            self._ended_early[side] = True
            await self._s.floor.on_speech_end(side)

    @property
    def pending(self) -> int:
        return sum(len(q) for q in self.utts.values())

    async def on_speech_start(self, side: Side) -> None:
        self.utts[side].append(_Utt(t_start=self._s.clock.now_ms()))
        self._ended_early[side] = False
        await self._s.floor.on_speech_start(side)

    async def on_speech_end(self, side: Side) -> None:
        utt = next((u for u in self.utts[side] if u.t_eou is None), None)
        if utt is not None:
            utt.t_eou = self._s.clock.now_ms()
        if self._ended_early[side]:
            self._ended_early[side] = False  # the floor already heard it
            return
        await self._s.floor.on_speech_end(side)

    async def on_translation(self, tr: Translation) -> None:
        if self.utts[tr.side]:
            self.utts[tr.side].popleft()
        await self._s.floor.on_translation(tr)

    async def on_utterance_discarded(self, side: Side) -> None:
        utt = self.utts[side].popleft() if self.utts[side] else None
        if utt is not None and self._is_backchannel(utt):
            t_end = max(utt.t_start, (utt.t_eou or utt.t_start) - self._s.pair.eou_ms)
            self._s.turn_log.on_backchannel(
                side,
                src_text=(utt.final or "").strip(),
                t_speech_start=utt.t_start,
                t_speech_end=t_end,
                t_eou=utt.t_eou,
                t_stt_final=utt.t_stt_final,
            )
        await self._s.floor.on_utterance_discarded(side)

    async def on_partial(self, side: Side, text: str) -> None:
        self._s._publish("partial", {"side": side, "text": text})

    def _is_backchannel(self, utt: _Utt) -> bool:
        words = (utt.final or "").split()
        if not words or len(words) > BACKCHANNEL_MAX_WORDS or utt.t_eou is None:
            return False
        speech_ms = utt.t_eou - self._s.pair.eou_ms - utt.t_start + 20
        return speech_ms < self._s.pair.backchannel_max_ms


class CallSession:
    def __init__(
        self,
        call_id: str,
        pair: PairConfig,
        providers: Providers,
        *,
        bus: EventBus,
        writer: Any | None = None,
        clock: Clock | None = None,
        mode: Mode = "interpret",
        escape_dtmf: str = "0",
        record: bool = False,
        tick_ms: int = 20,
        opening_line: bool = True,
    ) -> None:
        self.call_id = call_id
        self.pair = pair
        self.providers = providers
        self.bus = bus
        self.writer = writer
        self.clock: Clock = clock if clock is not None else MonotonicClock()
        self.mode: Mode = mode
        self.record = record
        self.tick_ms = tick_ms
        self.opening_line = opening_line
        self.status: Status = "ringing"
        self.legs: dict[Side, LegTransport] = {}
        self.escape_used = False
        self.end_reason: str | None = None

        self.turn_log = TurnLog(
            call_id, providers=providers, clock=self.clock, bus=bus, writer=writer, record=record
        )
        self._render_partial = functools.partial(render_playback, duck_db=pair.duck_db)
        self.floor = FloorController(
            call_id,
            self.legs,  # filled by attach_leg; the floor keeps this dict
            self.clock,
            backchannel_max_ms=pair.backchannel_max_ms,
            barge_in_min_ms=pair.barge_in_min_ms,
            escape_dtmf=escape_dtmf,
            render=self._render,
            events=bus,
            line_check=self._line_check_audio,
            on_played=self.turn_log.on_played,
            on_bypass=self._on_bypass,
            bypass=mode == "bridge",
        )
        self._sink = _Sink(self)
        self.interpreters: dict[Side, Interpreter] = {}
        if mode == "interpret":
            for s in SIDES:
                self._sink.vads[s] = _TrackedVAD(providers.vad_factory(), self.clock)
                self.interpreters[s] = Interpreter(
                    call_id,
                    s,
                    pair,
                    _TrackedSTT(providers.stt[s], self._sink.utts[s], self.clock),  # type: ignore[arg-type]
                    providers.mt,
                    providers.tts[other(s)],  # synthesise in the TARGET side's language
                    self._sink.vads[s],  # type: ignore[arg-type]
                    self.clock,
                    self._sink,
                )

        self._forwarding = mode == "bridge"
        self._interpreting = False
        self._frames: dict[Side, asyncio.Queue[bytes | None]] = {s: asyncio.Queue() for s in SIDES}
        self._interp_tasks: dict[Side, asyncio.Task[None]] = {}
        self._prompt_cache: dict[tuple[str, Side], bytes] = {}
        self._tg: asyncio.TaskGroup | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._closed = asyncio.Event()
        self._t_active: int | None = None
        self._t_end: int | None = None
        self._t_bypass: int | None = None

    # --- public API ------------------------------------------------------------------------

    async def attach_leg(self, side: Side, leg: LegTransport) -> None:
        """Attach a connected leg. Interpreting starts once both legs are attached."""
        if self.status == "ended":
            with contextlib.suppress(Exception):
                await leg.close()
            raise RuntimeError(f"call {self.call_id} has ended")
        if side in self.legs:
            raise ValueError(f"leg {side} already attached to {self.call_id}")
        self.legs[side] = leg
        self._publish("leg", {"side": side, "status": "attached"})
        if len(self.legs) == 2 and self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"call-{self.call_id}")

    async def hangup(self, reason: str = "hangup") -> None:
        if self.status == "ended":
            return
        if self.end_reason is None:
            self.end_reason = reason
        if self._task is None:  # still ringing: nothing is running yet
            for side, leg in list(self.legs.items()):
                await self._close_leg(side, leg)
                self._publish("leg", {"side": side, "status": "closed"})
            await self._finish()
            return
        self._stop.set()
        if asyncio.current_task() is not self._task:
            await self.wait_closed()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def tick(self) -> None:
        """One floor tick (the tick task calls this every tick_ms)."""
        try:
            await self.floor.tick()
        except Exception as e:
            log.exception("session.tick_error", extra={"call_id": self.call_id, "error": repr(e)})

    @property
    def idle(self) -> bool:
        """Nothing in flight: no utterance being interpreted, nothing queued or playing."""
        return (
            self._sink.pending == 0
            and self.floor.playing is None
            and not self.floor.queue
            and self.floor.state == "IDLE"
        )

    def summary(self) -> dict[str, Any]:
        s = self.turn_log.summary()
        end = self._t_end if self._t_end is not None else self.clock.now_ms()
        duration_ms = 0 if self._t_active is None else max(0, end - self._t_active)
        return {
            "call_id": self.call_id,
            "status": self.status,
            "mode": self.mode,
            "pair": self.pair.key,
            "turns": s["turns"],
            "p50_first_audio_ms": s["p50_first_audio_ms"],
            "p95_first_audio_ms": s["p95_first_audio_ms"],
            "escape_used": self.escape_used,
            "duration_s": round(duration_ms / 1000),
        }

    # --- the call task ---------------------------------------------------------------------

    async def _run(self) -> None:
        self.status = "active"
        self._t_active = self.clock.now_ms()
        self._publish("call", {"status": "active"})
        try:
            async with asyncio.TaskGroup() as tg:
                self._tg = tg
                tg.create_task(self._watch_stop(), name=f"{self.call_id}-stop")
                self.floor.quiet_from(self._t_active)  # not from when the call was created
                await self.floor.start()
                for s in SIDES:
                    tg.create_task(self._pump(s), name=f"{self.call_id}-pump-{s}")
                tg.create_task(self._tick_loop(), name=f"{self.call_id}-tick")
                if self.mode == "interpret":
                    if self.opening_line:
                        await self._play_opening()
                    for s in SIDES:
                        if not self._forwarding:
                            self._interp_tasks[s] = tg.create_task(
                                self._interpret(s), name=f"{self.call_id}-interp-{s}"
                            )
                    self._interpreting = not self._forwarding
        except* _Stop:
            pass
        except* Exception as eg:
            log.error(
                "session.failed",
                extra={"call_id": self.call_id, "error": repr(eg.exceptions)},
            )
            if self.end_reason is None:
                self.end_reason = "error"
        finally:
            self._tg = None
            await self._teardown()

    async def _watch_stop(self) -> None:
        await self._stop.wait()
        raise _Stop

    def _request_stop(self, reason: str) -> None:
        if self.end_reason is None:
            self.end_reason = reason
        self._stop.set()

    async def _pump(self, side: Side) -> None:
        """Leg frames -> this side's interpreter, or straight across in bypass/bridge."""
        leg = self.legs[side]
        q = self._frames[side]
        try:
            async for frame in leg.frames():
                if self._forwarding:
                    await self.floor.forward(side, frame)
                elif self._interpreting and side in self._interp_tasks:
                    q.put_nowait(frame)
                # else: the opening line is still going out; interpreting hasn't started
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(
                "leg.error", extra={"call_id": self.call_id, "side": side, "error": repr(e)}
            )
        finally:
            q.put_nowait(None)
            self._publish("leg", {"side": side, "status": "closed"})
            log.info("leg.close", extra={"call_id": self.call_id, "side": side})
            self._request_stop(f"leg_{side}_closed")

    async def _frames_of(self, side: Side) -> AsyncIterator[bytes]:
        q = self._frames[side]
        while (frame := await q.get()) is not None:
            await self._sink.voice_check(side)  # the previous frame has been through VAD
            yield frame

    async def _interpret(self, side: Side) -> None:
        interp = self.interpreters[side]
        try:
            await interp.run_until_frames_end(self._frames_of(side))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # VAD or interpreter bug: lose this direction, keep the call
            log.exception(
                "provider.error",
                extra={"call_id": self.call_id, "side": side, "stage": "interp", "error": repr(e)},
            )
            self._publish(
                "provider_error", {"provider": "interpreter", "stage": "interp", "error": repr(e)}
            )
            self._interp_tasks.pop(side, None)

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(self.tick_ms / 1000)
            await self.tick()

    def _on_bypass(self) -> None:
        """Floor callback (lock held): DTMF escape. Forward raw frames from now on."""
        self.escape_used = True
        self._forwarding = True
        self._interpreting = False
        self._t_bypass = self.clock.now_ms()
        tasks = list(self._interp_tasks.values())
        self._interp_tasks.clear()
        for t in tasks:
            t.cancel()
        if self._tg is not None:
            self._tg.create_task(self._close_interpreters(), name=f"{self.call_id}-interp-stop")

    async def _close_interpreters(self) -> None:
        for interp in self.interpreters.values():
            with contextlib.suppress(Exception):
                await interp.aclose()

    async def _teardown(self) -> None:
        self._forwarding = False
        self._interpreting = False
        for t in list(self._interp_tasks.values()):
            t.cancel()
        self._interp_tasks.clear()
        try:
            await self.floor.aclose()
        except Exception as e:
            log.warning(
                "session.floor_close_error", extra={"call_id": self.call_id, "error": repr(e)}
            )
        await self._close_interpreters()
        for side, leg in list(self.legs.items()):
            await self._close_leg(side, leg)
        await self._aclose_providers()
        await self._finish()

    async def _finish(self) -> None:
        self.status = "ended"
        self._t_end = self.clock.now_ms()
        summary = self.summary()
        self._publish("call", {**summary, "reason": self.end_reason or "hangup"})
        if self.writer is not None:
            try:
                duration_ms = 0 if self._t_active is None else self._t_end - self._t_active
                interp_end = self._t_bypass if self._t_bypass is not None else self._t_end
                interpreted_ms = (
                    0
                    if self._t_active is None or self.mode != "interpret"
                    else interp_end - self._t_active
                )
                self.writer.finish_call(
                    self.call_id,
                    duration_s=round(duration_ms / 1000),
                    interpreted_s=round(interpreted_ms / 1000),
                    escape_used=self.escape_used,
                    turns=list(self.turn_log.turns),
                )
            except Exception:
                log.exception("session.finish_call_failed", extra={"call_id": self.call_id})
        self._closed.set()

    async def _close_leg(self, side: Side, leg: LegTransport) -> None:
        try:
            await leg.close()
        except Exception as e:
            log.warning(
                "leg.close_error", extra={"call_id": self.call_id, "side": side, "error": repr(e)}
            )

    async def _aclose_providers(self) -> None:
        p = self.providers
        seen: set[int] = set()
        for obj in [*p.stt.values(), p.mt, getattr(p.mt, "inner", None), *p.tts.values()]:
            if obj is None or id(obj) in seen:
                continue
            seen.add(id(obj))
            aclose = getattr(obj, "aclose", None)
            if aclose is None:
                continue
            try:
                res = aclose()
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                log.warning(
                    "provider.close_error", extra={"call_id": self.call_id, "error": repr(e)}
                )

    # --- audio -----------------------------------------------------------------------------

    def _render(self, tr: Translation) -> AsyncIterator[bytes]:
        return self._render_partial(tr.audio, source_audio=tr.source_audio)

    async def _synth(self, side: Side, text: str, leg: LegTransport | None = None) -> bytes:
        """Synthesise a prompt in the side's female voice; with `leg`, stream each chunk to
        it as it arrives (no dead air while a long prompt is synthesised)."""
        cfg = self.pair.side(side)
        tts = self.providers.tts[side]
        out = bytearray()
        async with asyncio.timeout(PROMPT_TIMEOUT_S):
            async for chunk in tts.synth(text, cfg.voice_f, cfg.lang):
                out += chunk
                if leg is not None and chunk:
                    await leg.send(chunk)
        return bytes(out)

    async def _prompt(
        self, kind: str, side: Side, text: str, leg: LegTransport | None = None
    ) -> bytes:
        """The prompt's audio (cached per call). With `leg`, it is also sent to the leg:
        streamed while synthesising, or in one piece from the cache."""
        key = (kind, side)
        if key in self._prompt_cache:
            if leg is not None:
                await leg.send(self._prompt_cache[key])
        else:
            self._prompt_cache[key] = await self._synth(side, text, leg)
        return self._prompt_cache[key]

    async def _play_opening(self) -> None:
        async def one(side: Side) -> None:
            text = opening_text(
                self.pair.side(side).lang, self.pair.side(other(side)).lang, record=self.record
            )
            audio = await self._prompt("opening", side, text, self.legs[side])
            # the callers hear it until about t0 + its length: that is not silence
            self.floor.quiet_from(t0 + bytes_to_ms(len(audio)))

        t0 = self.clock.now_ms()
        results = await asyncio.gather(*(one(s) for s in SIDES), return_exceptions=True)
        for side, res in zip(SIDES, results, strict=True):
            if isinstance(res, BaseException):
                log.warning(
                    "session.opening_error",
                    extra={"call_id": self.call_id, "side": side, "error": repr(res)},
                )
                self._publish(
                    "provider_error",
                    {
                        "provider": self.providers.tts[side].name,
                        "stage": "opening",
                        "error": repr(res),
                    },
                )

    async def _line_check_audio(self, side: Side) -> AsyncIterator[bytes]:
        audio = await self._prompt("line_check", side, line_check_text(self.pair.side(side).lang))
        for i in range(0, len(audio), PROMPT_CHUNK):
            yield audio[i : i + PROMPT_CHUNK]

    def _publish(self, kind: EventKind, payload: dict[str, Any]) -> None:
        self.bus.publish(CallEvent(self.call_id, self.clock.now_ms(), kind, payload))

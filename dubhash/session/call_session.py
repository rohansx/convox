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
- The opening line goes through the FloorController (play_prompt): streamed to each leg
  chunk by chunk as the TTS produces it, then marked. Interpreting starts at activation,
  so speech during the opening is interpreted, not dropped; its translation queues
  normally, and the floor holds playback to a leg until that leg's opening mark comes
  back (or the opening's length plus PROMPT_GRACE_MS, if the mark never does).
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

Cross-talk and echo (session/crosstalk.py): every mic frame goes through the gate in the
VAD wrapper. Echo of a leg's own playback, and on co-located calls the other speaker's
voice picked up at a distance, is fed to the endpointer as silence, so it never starts an
utterance. An utterance that starts while the other side is already talking is "suspect":
its speech is not reported to the floor (no barge-in, no floor grab) until the gate clears
it. At STT end() the gate judges the whole utterance; cross-talk returns an empty final
without waiting for the vendor (the stream settles in the background, keeping its socket
warm), so the interpreter discards it. On co-located calls a final whose detected language
is the other side's is discarded too. Each decision is published as a "crosstalk" event.

Phrases (L10): with a `vertical`, the call's phrase pack (config/phrases/<vertical>.yaml) is
prefilled into the MT and TTS caches in the background from call creation
(session/prefill.py, at providers.prefill_rps, pausing while a turn is in flight), and the
interpreters match finals against it. An ack counts as a turn only if, at its speech start,
nothing was playing or queued, the floor was free (or already the speaker's) and the other
side had no utterance in flight; otherwise it stays a backchannel. With the pack's
numbers_readback, every translation goes through the number read-back
(interp/numbers.py); a change is published as a "number_fix" event.

Next-turn prewarm (L10): when a translation starts playing to a leg, that leg is likely to
answer, so its STT socket (in its language) and the TTS socket that will voice its reply
(the speaker's side) are prewarmed in the background.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from dubhash.audio.earcon import EARCON
from dubhash.audio.frames import FRAME_BYTES, FRAME_MS, bytes_to_ms
from dubhash.audio.mixer import render_playback
from dubhash.clock import Clock, MonotonicClock
from dubhash.config.pairs import PairConfig, SideConfig, default_side, effective_pair
from dubhash.events import EventBus
from dubhash.interp.contracts import STT, VAD, STTStream
from dubhash.interp.langid import LangGuess
from dubhash.interp.numbers import readback as number_readback
from dubhash.interp.phrases import load_pack
from dubhash.interp.pipeline import BACKCHANNEL_MAX_WORDS, Interpreter
from dubhash.legs.base import LegTransport
from dubhash.session.crosstalk import CrosstalkGate, Verdict
from dubhash.session.floor import FloorController
from dubhash.session.language import (
    MENU_DIGITS,
    STT_AUTO,
    AutoLang,
    LanguageResolver,
    LangUpdate,
    indic_share,
)
from dubhash.session.prefill import PrefillStats, prefill_phrases
from dubhash.session.prompts import (
    auto_opening_text,
    language_menu_text,
    line_check_text,
    opening_text,
    partner_auto_opening_text,
    short_opening_text,
)
from dubhash.session.providers import SIDES, Providers, prewarm
from dubhash.session.turn_log import TurnLog
from dubhash.types import CallEvent, EventKind, Side, Translation, other

log = logging.getLogger(__name__)

Status = Literal["ringing", "active", "ended"]
Mode = Literal["interpret", "bridge"]
# "full": the disclosure sentence (+ the record sentence), PSTN's default. "short": the two
# language names and "speak" (~1.5-2 s), loopback's and /join's default. "off": none (L7).
OpeningLine = Literal["short", "full", "off"]
OPENING_LINES: tuple[OpeningLine, ...] = ("short", "full", "off")


def opening_mode(value: bool | str) -> OpeningLine:
    """Normalise an opening_line setting; True/False (the old flag) mean full/off."""
    if value is True or value is False:
        return "full" if value else "off"
    if value not in OPENING_LINES:
        raise ValueError(f"opening_line must be one of {OPENING_LINES}, not {value!r}")
    return value  # type: ignore[return-value]


PROMPT_TIMEOUT_S = 10.0
SPEECH_END_HANGOVER_MS = 200  # silence after which the floor hears "speech ended"
SPEECH_THRESHOLD = 0.5  # same as the Endpointer's
PROMPT_CHUNK = FRAME_BYTES * 5  # 100 ms
# One "crosstalk" event per run of gated voiced frames, once the run is GATED_EVENT_FRAMES
# long (a genuine onset ramp can lose a frame or two; the preroll keeps that audio). A
# pause over GATED_EVENT_GAP_MS starts a new run.
GATED_EVENT_FRAMES = 3
GATED_EVENT_GAP_MS = 1000


MENU_LANG = "hi-IN"  # the keypad menu is read in Hindi (then English) by a Hindi voice
MENU_TIMEOUT_S = 20.0
_FOREVER = 1 << 62


class _Stop(Exception):
    """Raised inside the TaskGroup to end the call."""


@dataclass(slots=True, eq=False)
class _LangEvidence:
    """What an auto side's utterance says about its language, gathered at the STT final
    and judged once the utterance is delivered or discarded."""

    speech_ms: int
    stt_lang: str | None
    text: str | None
    audio: bytes  # for language ID, run once the utterance is over
    stt_confidence: float | None = None


@dataclass(slots=True, eq=False)
class _Utt:
    """One utterance between the interpreter's speech start and its delivery."""

    t_start: int
    t_eou: int | None = None
    stream: bool = False  # an STT stream was opened for it
    final: str | None = None
    t_stt_final: int | None = None
    suspect: bool = False  # started while the other side talked; not told to the floor yet
    crosstalk: str | None = None  # why it was discarded as cross-talk ("level" | "lang")
    lang_ev: _LangEvidence | None = None  # lang "auto": this utterance's language evidence


class _Judge(Protocol):
    """The session's cross-talk verdicts for one side's utterances, at STT end()."""

    def before_final(self, utt: _Utt) -> str | None: ...

    def after_final(self, utt: _Utt, lang: str | None) -> str | None: ...

    def settle(self, stream: STTStream) -> None: ...

    @property
    def wants_audio(self) -> bool: ...

    def on_final(
        self,
        utt: _Utt,
        final: str,
        lang: str | None,
        audio: bytes,
        text: str | None,
        lang_confidence: float | None = None,
    ) -> None: ...


class _TrackedStream:
    def __init__(
        self, inner: STTStream, utt: _Utt | None, clock: Clock, judge: _Judge | None = None
    ) -> None:
        self._inner = inner
        self._utt = utt
        self._clock = clock
        self._judge = judge
        self._settling = False
        # lang "auto": the utterance's audio for language ID, and its last partial (plain
        # transcription in the opened language, also on the pivot route)
        self._audio: list[bytes] | None = (
            [] if utt is not None and getattr(judge, "wants_audio", False) else None
        )
        self.last_partial: str | None = None

    async def push(self, frame: bytes) -> None:
        if self._audio is not None:
            self._audio.append(frame)
        await self._inner.push(frame)

    async def end(self) -> str:
        utt, judge = self._utt, self._judge
        if utt is not None and judge is not None:
            utt.crosstalk = judge.before_final(utt)
            if utt.crosstalk is not None:  # no need to wait for the vendor's final
                self._settling = True
                judge.settle(self._inner)
                utt.final, utt.t_stt_final = "", self._clock.now_ms()
                return ""
        final = await self._inner.end()
        if utt is not None and judge is not None:
            utt.crosstalk = judge.after_final(utt, getattr(self._inner, "detected_lang", None))
            if utt.crosstalk is not None:
                final = ""
        if utt is not None:
            utt.final = final
            utt.t_stt_final = self._clock.now_ms()
        if utt is not None and judge is not None and self._audio is not None:
            if utt.crosstalk is None:
                detected = getattr(self._inner, "detected_lang", None)
                conf = getattr(self._inner, "detected_confidence", None)
                audio = b"".join(self._audio)
                judge.on_final(utt, final, detected, audio, self.last_partial, conf)
        return final

    def partials(self) -> AsyncIterator[str]:
        return self._track_partials(self._inner.partials())

    async def _track_partials(self, it: AsyncIterator[str]) -> AsyncIterator[str]:
        async for text in it:
            self.last_partial = text
            yield text

    async def aclose(self) -> None:
        if not self._settling:  # else the settle task closes it after end()
            await self._inner.aclose()


class _TrackedSTT:
    """Wraps a side's STT so the session sees each utterance's final transcript (to log
    backchannels, which the interpreter discards without saying why)."""

    def __init__(
        self, inner: STT, utts: deque[_Utt], clock: Clock, judge: _Judge | None = None
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self._utts = utts
        self._clock = clock
        self._judge = judge

    async def open(self, lang: str) -> STTStream:
        # Workers open their stream first thing, in utterance order: bind to the oldest
        # utterance without one before awaiting anything.
        utt = next((u for u in self._utts if not u.stream), None)
        if utt is not None:
            utt.stream = True
        elif self._utts and self._utts[-1].t_eou is None:
            # an early-final restart (L3): a second stream for the utterance still open
            utt = self._utts[-1]
        return _TrackedStream(await self.inner.open(lang), utt, self._clock, self._judge)


class _TrackedVAD:
    """Wraps a side's VAD to remember when that side last had a voiced frame, and to feed
    frames the cross-talk gate rejects (echo, the other speaker) as silence."""

    def __init__(
        self,
        inner: VAD,
        clock: Clock,
        side: Side | None = None,
        gate: CrosstalkGate | None = None,
        in_utterance: Callable[[], bool] | None = None,
        on_gated: Callable[[Side, Verdict], None] | None = None,
    ) -> None:
        self.inner = inner
        self._clock = clock
        self._side = side
        self._gate = gate
        self._in_utterance = in_utterance
        self._on_gated = on_gated  # once per run of gated frames
        self._run: tuple[Verdict, int, int] | None = None  # verdict, frames, last ms
        self.voiced = False
        self.last_voiced = clock.now_ms()

    def speech_prob(self, frame: bytes) -> float:
        p = self.inner.speech_prob(frame)  # always: the inner VAD may keep state
        voiced = p > SPEECH_THRESHOLD
        if self._gate is not None and self._side is not None:
            now = self._clock.now_ms()
            db = self._gate.on_frame(self._side, frame, now, voiced)
            if voiced:
                in_utt = self._in_utterance() if self._in_utterance is not None else False
                verdict = self._gate.frame_verdict(self._side, now, db, in_utterance=in_utt)
                if verdict is None:
                    self._run = None
                else:
                    p, voiced = 0.0, False
                    run = self._run
                    if run is None or run[0] != verdict or now - run[2] > GATED_EVENT_GAP_MS:
                        run = (verdict, 0, now)
                    self._run = run = (verdict, run[1] + 1, now)
                    if run[1] == GATED_EVENT_FRAMES and self._on_gated is not None:
                        self._on_gated(self._side, verdict)
        self.voiced = voiced
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
        self.judges: dict[Side, _SideJudge] = {s: _SideJudge(self, s) for s in SIDES}
        self.settling: set[asyncio.Task[Any]] = set()

    @property
    def gate(self) -> CrosstalkGate:
        return self._s.gate

    def open_utt(self, side: Side) -> _Utt | None:
        return next((u for u in self.utts[side] if u.t_eou is None), None)

    async def voice_check(self, side: Side) -> None:
        """Called between frames: end speech for the floor after a short silence, and
        restart it if the same utterance resumes."""
        vad = self.vads.get(side)
        if vad is None:
            return
        utt = self.open_utt(side)
        if utt is not None and utt.suspect:
            if not self.gate.provisional(side, utt.t_start, self._s.clock.now_ms()):
                return
            utt.suspect = False  # genuine after all: the floor hears it from now
            self._ended_early[side] = not vad.voiced
            await self._s.floor.on_speech_start(side)
            if not vad.voiced:
                await self._s.floor.on_speech_end(side)
            return
        if utt is None:
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
        now = self._s.clock.now_ms()
        utt = _Utt(t_start=now, suspect=self.gate.other_talking_first(side, now))
        self.utts[side].append(utt)
        self.gate.utt_start(side, now)
        self._ended_early[side] = False
        if not utt.suspect:
            await self._s.floor.on_speech_start(side)

    async def on_speech_end(self, side: Side) -> None:
        utt = self.open_utt(side)
        if utt is not None:
            utt.t_eou = self._s.clock.now_ms()
            self.gate.utt_end(side, utt.t_eou)
            self._s._lang_ready(side, utt)  # an early final's evidence is final now
            if utt.suspect:
                return  # the floor never heard it start
        if self._ended_early[side]:
            self._ended_early[side] = False  # the floor already heard it
            return
        await self._s.floor.on_speech_end(side)

    async def on_translation(self, tr: Translation) -> None:
        if self.utts[tr.side]:
            self._s._lang_utt_done(tr.side, self.utts[tr.side].popleft())
        await self._s.floor.on_translation(tr)

    async def on_utterance_discarded(self, side: Side) -> None:
        utt = self.utts[side].popleft() if self.utts[side] else None
        if utt is not None:
            self._s._lang_utt_done(side, utt)
        if utt is not None and utt.crosstalk is not None:
            self.gate.forget(side, utt.t_start)
            self._s._crosstalk(side, utt.crosstalk, "eou", t_speech_start=utt.t_start)
        elif utt is not None and self._is_backchannel(utt):
            t_end = max(utt.t_start, (utt.t_eou or utt.t_start) - self._s.pair.eou_ms)
            self._s.turn_log.on_backchannel(
                side,
                src_text=(utt.final or "").strip(),
                t_speech_start=utt.t_start,
                t_speech_end=t_end,
                t_eou=utt.t_eou,
                t_stt_final=utt.t_stt_final,
            )
        if utt is not None and utt.suspect:
            return  # the floor never heard it
        await self._s.floor.on_utterance_discarded(side)

    async def on_partial(self, side: Side, text: str) -> None:
        self._s._publish("partial", {"side": side, "text": text})

    def _is_backchannel(self, utt: _Utt) -> bool:
        words = (utt.final or "").split()
        if not words or len(words) > BACKCHANNEL_MAX_WORDS or utt.t_eou is None:
            return False
        speech_ms = utt.t_eou - self._s.pair.eou_ms - utt.t_start + 20
        return speech_ms < self._s.pair.backchannel_max_ms


class _SideJudge:
    """_Judge for one side: the gate's verdict on a whole utterance, and the language
    check on its final."""

    def __init__(self, sink: _Sink, side: Side) -> None:
        self._sink = sink
        self._side = side

    def before_final(self, utt: _Utt) -> str | None:
        s = self._sink._s
        o = s.gate.utterance_verdict(self._side, utt.t_start, s.clock.now_ms())
        return None if o is None else "level"

    def after_final(self, utt: _Utt, lang: str | None) -> str | None:
        s = self._sink._s
        if not s.co_located or not lang:
            return None
        if not (s.lang_resolved(self._side) and s.lang_resolved(other(self._side))):
            return None  # lang "auto": the sides' languages are not known yet
        base = lang.split("-")[0].lower()
        mine = s.pair.side(self._side).lang.split("-")[0].lower()
        theirs = s.pair.side(other(self._side)).lang.split("-")[0].lower()
        return "lang" if base == theirs and base != mine else None

    @property
    def wants_audio(self) -> bool:
        return self._side in self._sink._s.resolvers

    def on_final(
        self,
        utt: _Utt,
        final: str,
        lang: str | None,
        audio: bytes,
        text: str | None,
        lang_confidence: float | None = None,
    ) -> None:
        self._sink._s._lang_final(self._side, utt, final, lang, audio, text, lang_confidence)

    def settle(self, stream: STTStream) -> None:
        """end() then aclose() a cross-talk stream in the background: a socket that
        settles goes back to the warm pool."""

        async def run() -> None:
            try:
                with contextlib.suppress(Exception):
                    await stream.end()
            finally:
                with contextlib.suppress(Exception):
                    await stream.aclose()

        task = asyncio.create_task(run(), name=f"stt-settle-{self._side}")
        self._sink.settling.add(task)
        task.add_done_callback(self._sink.settling.discard)


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
        opening_line: bool | OpeningLine = "full",
        co_located: bool = False,
        duck_original: bool | None = None,
        auto: AutoLang | None = None,
        vertical: str | None = None,
        next_turn_prewarm: bool = True,
    ) -> None:
        """co_located: both phones are in one room (/join, loopback), so each mic also
        hears the other speaker: the cross-talk gate is on, and by default the listener
        gets no ducked original (they heard it live). duck_original (None = not
        co_located) plays the original under the first pair.duck_ms of each translation
        at pair.duck_db (telephony). auto: the sides whose language is detected on the
        call (lang "auto", session/language.py); `pair` is then the starting pair
        (config.pairs.auto_pair) and self.pair follows the detected languages."""
        self.call_id = call_id
        self.pair = pair
        self._base_pair = pair
        self.auto = auto
        self.resolvers: dict[Side, LanguageResolver] = (
            {s: LanguageResolver(s, h, auto.config) for s, h in auto.hints.items()}
            if auto is not None
            else {}
        )
        self._lang_lock: dict[Side, asyncio.Lock] = {s: asyncio.Lock() for s in SIDES}
        self._lang_tasks: set[asyncio.Task[Any]] = set()
        self._menu_until: dict[Side, int] = {}  # side -> end of its keypad menu window
        self._same_lang = False  # bypass entered because both sides speak one language
        self.providers = providers
        self.bus = bus
        self.writer = writer
        self.clock: Clock = clock if clock is not None else MonotonicClock()
        self.mode: Mode = mode
        self.record = record
        self.tick_ms = tick_ms
        self.opening_line: OpeningLine = opening_mode(opening_line)
        self.co_located = co_located
        self.duck_original = (not co_located) if duck_original is None else duck_original
        self.status: Status = "ringing"
        self.legs: dict[Side, LegTransport] = {}
        self.escape_used = False
        self.end_reason: str | None = None

        self.gate = CrosstalkGate(co_located=co_located)
        self.vertical = vertical
        self.phrases = load_pack(vertical) if vertical else None
        self.next_turn_prewarm = next_turn_prewarm
        readback = self.phrases is not None and self.phrases.numbers_readback
        self.turn_log = TurnLog(
            call_id, providers=providers, clock=self.clock, bus=bus, writer=writer, record=record
        )
        self._render_partial = functools.partial(
            render_playback, duck_db=pair.duck_db, duck_ms=pair.duck_ms
        )
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
            on_played=self._on_played,
            on_bypass=self._on_bypass,
            bypass=mode == "bridge",
            dtmf_hook=self._dtmf_hook if self.resolvers else None,
        )
        self._sink = _Sink(self)
        self.interpreters: dict[Side, Interpreter] = {}
        if mode == "interpret":
            for s in SIDES:
                self._sink.vads[s] = _TrackedVAD(
                    providers.vad_factory(),
                    self.clock,
                    s,
                    self.gate,
                    functools.partial(self._in_utterance, s),
                    self._on_gated,
                )
                self.interpreters[s] = Interpreter(
                    call_id,
                    s,
                    pair,
                    _TrackedSTT(  # type: ignore[arg-type]
                        providers.stt[s], self._sink.utts[s], self.clock, self._sink.judges[s]
                    ),
                    providers.mt,
                    providers.tts[other(s)],  # synthesise in the TARGET side's language
                    self._sink.vads[s],  # type: ignore[arg-type]
                    self.clock,
                    self._sink,
                    phrases=self.phrases,
                    ack_ok=functools.partial(self._ack_ok, s),
                    readback=functools.partial(self._readback, s) if readback else None,
                )
        if self.resolvers:
            self._retarget()

        self._forwarding = mode == "bridge"
        self._interpreting = False
        self._frames: dict[Side, asyncio.Queue[bytes | None]] = {s: asyncio.Queue() for s in SIDES}
        self._interp_tasks: dict[Side, asyncio.Task[None]] = {}
        self._prompt_cache: dict[tuple[str, Side, str], bytes] = {}
        self._tg: asyncio.TaskGroup | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._closed = asyncio.Event()
        self._t_active: int | None = None
        self._t_end: int | None = None
        self._t_bypass: int | None = None
        self.prewarm_task: asyncio.Task[None] | None = None
        self.prefill_task: asyncio.Task[PrefillStats] | None = None
        self.prefill_stats = PrefillStats()
        self._warm_next: set[asyncio.Task[Any]] = set()

    def start_prewarm(self) -> None:
        """Open the provider sockets now (call creation), in the background, so the first
        utterance does not pay the STT/TTS/MT handshakes (L7). Done again at activation."""
        if self.mode != "interpret" or self.status == "ended":
            return
        if self.prewarm_task is None or self.prewarm_task.done():
            self.prewarm_task = asyncio.get_running_loop().create_task(
                prewarm(
                    self.providers,
                    self.pair,
                    {s: self._stt_lang(s) for s in self.resolvers} or None,
                ),
                name=f"{self.call_id}-prewarm",
            )
        if self.phrases is not None and self.prefill_task is None and self.providers.prefill_rps:
            self.prefill_task = asyncio.get_running_loop().create_task(
                prefill_phrases(
                    self.phrases,
                    self.pair,
                    self.providers,
                    clock=self.clock,
                    rps=self.providers.prefill_rps,
                    busy=lambda: self.status == "active" and not self.idle,
                    readback=self._prefill_readback if self.phrases.numbers_readback else None,
                    stats=self.prefill_stats,
                    call_id=self.call_id,
                ),
                name=f"{self.call_id}-prefill",
            )

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
            await self._stop_prewarm()
            await self._aclose_providers()  # prewarmed sockets
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
        extra = {"langs": self.lang_states()} if self.resolvers else {}
        return {
            **extra,
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
                self.start_prewarm()  # sockets warmed at creation may have gone stale
                await self.floor.start()
                for s in SIDES:
                    tg.create_task(self._pump(s), name=f"{self.call_id}-pump-{s}")
                tg.create_task(self._tick_loop(), name=f"{self.call_id}-tick")
                if self.mode == "interpret":
                    for s in SIDES:
                        if not self._forwarding:
                            self._interp_tasks[s] = tg.create_task(
                                self._interpret(s), name=f"{self.call_id}-interp-{s}"
                            )
                    self._interpreting = not self._forwarding
                    if self.opening_line != "off":
                        await self._play_opening()
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
                # else: bridge-to-interpret switch in progress, or teardown
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
        """Floor callback (lock held): DTMF escape, or the same language on both sides
        (lang "auto"). Forward raw frames from now on."""
        if not self._same_lang:
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
        for t in list(self._sink.settling) + list(self._lang_tasks):
            t.cancel()
        try:
            await self.floor.aclose()
        except Exception as e:
            log.warning(
                "session.floor_close_error", extra={"call_id": self.call_id, "error": repr(e)}
            )
        await self._close_interpreters()
        for side, leg in list(self.legs.items()):
            await self._close_leg(side, leg)
        await self._stop_prewarm()
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

    async def _stop_prewarm(self) -> None:
        tasks = [self.prewarm_task, self.prefill_task, *self._warm_next]
        self.prewarm_task = None
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task

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
        if self.next_turn_prewarm and self.status == "active":
            self._prewarm_next(other(tr.side), tr.side)
        source = tr.source_audio if self.duck_original else b""
        frames = self._render_partial(tr.audio, source_audio=source)
        return self._echo_tap(other(tr.side), frames)

    async def _echo_tap(self, listener: Side, frames: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """The listener's outbound frames, as the echo guard's reference level (the
        translation's, so the earcon frames are left out)."""
        self.gate.playback_start(listener)
        skip = len(EARCON) // FRAME_BYTES
        try:
            async for f in frames:
                if skip:
                    skip -= 1
                else:
                    self.gate.playback_frame(listener, f)
                yield f
        finally:
            aclose = getattr(frames, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    def _prewarm_next(self, listener: Side, speaker: Side) -> None:
        """The listener will likely answer: open its STT socket and the TTS socket for its
        reply's voice (the speaker's language) now, while it listens (L10)."""
        jobs: list[Any] = []
        stt_warm = getattr(self.providers.stt[listener], "prewarm", None)
        if stt_warm is not None:
            jobs.append(stt_warm(self.pair.side(listener).lang))
        tts_warm = getattr(self.providers.tts[speaker], "prewarm", None)
        if tts_warm is not None:
            interp = self.interpreters.get(listener)
            gender = interp.gender if interp is not None else None
            jobs.append(tts_warm(self.pair.side(speaker).voice(gender or "f")))
        if not jobs:
            return

        async def run() -> None:
            res = await asyncio.gather(*jobs, return_exceptions=True)
            for r in res:
                if isinstance(r, BaseException):
                    log.warning(
                        "provider.prewarm_failed",
                        extra={"call_id": self.call_id, "stage": "next_turn", "error": repr(r)},
                    )

        task = asyncio.get_running_loop().create_task(run(), name=f"{self.call_id}-warm-next")
        self._warm_next.add(task)
        task.add_done_callback(self._warm_next.discard)

    def _ack_ok(self, side: Side) -> bool:
        """At an utterance's speech start: may a short ack from `side` be a real turn?"""
        f = self.floor
        return (
            f.state in ("IDLE", "A_SPEAKING" if side == "a" else "B_SPEAKING")
            and f.playing is None
            and not f.queue
            and not self._sink.utts[other(side)]
        )

    def _readback(self, side: Side, src: str, tgt: str) -> str:
        fixed, fix = number_readback(src, tgt, self.pair.side(other(side)).lang)
        if fix is not None:
            payload = {
                "side": side,
                "missing": len(fix.missing),
                "replaced": fix.replaced,
                "appended": fix.appended,
            }
            log.info("interp.number_fix", extra={"call_id": self.call_id, **payload})
            self._publish("number_fix", payload)
        return fixed

    def _prefill_readback(self, side: Side, src: str, tgt: str) -> str:
        return number_readback(src, tgt, self.pair.side(other(side)).lang)[0]

    def _on_played(self, tr: Translation) -> None:
        self.gate.playback_end(other(tr.side), self.clock.now_ms())
        self.turn_log.on_played(tr)

    def _in_utterance(self, side: Side) -> bool:
        return self._sink.open_utt(side) is not None

    def _on_gated(self, side: Side, verdict: Verdict) -> None:
        self._crosstalk(side, "echo" if verdict == "echo" else "level", "frame")

    def _crosstalk(self, side: Side, reason: str, stage: str, **extra: Any) -> None:
        """Speech dropped by the cross-talk/echo gate: reason "level" | "lang" | "echo",
        stage "frame" (never became an utterance) | "eou" (an utterance, no turn)."""
        payload = {"side": side, "reason": reason, "stage": stage, **extra}
        log.info("crosstalk.drop", extra={"call_id": self.call_id, **payload})
        self._publish("crosstalk", payload)

    async def _prompt_chunks(
        self, kind: str, side: Side, text: str, lang: str | None = None
    ) -> AsyncIterator[bytes]:
        """A prompt's audio in the side's female voice (or `lang`'s), chunk by chunk as the
        TTS produces it (no dead air while a long prompt is synthesised), cached per call
        and language."""
        cfg = self.pair.side(side) if lang is None else self._side_cfg(lang)
        key = (kind, side, cfg.lang)
        if key in self._prompt_cache:
            yield self._prompt_cache[key]
            return
        out = bytearray()
        async for chunk in self.providers.tts[side].synth(text, cfg.voice_f, cfg.lang):
            out += chunk
            yield chunk
        self._prompt_cache[key] = bytes(out)

    async def _prompt(self, kind: str, side: Side, text: str) -> bytes:
        """The prompt's whole audio (cached per call)."""
        async with asyncio.timeout(PROMPT_TIMEOUT_S):
            return b"".join([c async for c in self._prompt_chunks(kind, side, text)])

    async def _play_opening(self) -> None:
        async def one(side: Side) -> None:
            me, them = self.pair.side(side).lang, self.pair.side(other(side)).lang
            resolver = self.resolvers.get(side)
            if resolver is not None:  # neutral: "speak in your language" (+ English)
                text = auto_opening_text(resolver.hint)
            elif not self.lang_resolved(other(side)):  # never name a language not known
                text = partner_auto_opening_text(me)
            elif self.opening_line == "short":
                text = short_opening_text(me, them)
            else:
                text = opening_text(me, them, record=self.record)
            async with asyncio.timeout(PROMPT_TIMEOUT_S):
                sent = await self.floor.play_prompt(
                    side, self._prompt_chunks("opening", side, text)
                )
            # the callers hear it until about t0 + its length: that is not silence
            self.floor.quiet_from(t0 + bytes_to_ms(sent))

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

    # --- lang "auto" (session/language.py, docs/tech-spec/language-detection.md) ----------

    def lang_resolved(self, side: Side) -> bool:
        """A fixed side, or an auto side whose language is CONFIRMED."""
        r = self.resolvers.get(side)
        return r is None or r.resolved

    def lang_states(self) -> dict[Side, dict[str, Any]]:
        out: dict[Side, dict[str, Any]] = {}
        for s in SIDES:
            r = self.resolvers.get(s)
            if r is None:
                out[s] = {"lang": self.pair.side(s).lang, "state": "CONFIRMED", "auto": False}
            else:
                out[s] = {"lang": r.lang, "state": r.state.value, "auto": True}
        return out

    def _lang_of(self, side: Side) -> str:
        r = self.resolvers.get(side)
        return self._base_pair.side(side).lang if r is None else r.target

    def _stt_lang(self, side: Side) -> str:
        if self.auto is not None and self.auto.stt_open == STT_AUTO:
            return STT_AUTO
        return self._lang_of(side)

    def _side_cfg(self, lang: str) -> SideConfig:
        sides = self.auto.sides if self.auto is not None else {}
        return sides.get(lang) or default_side(lang)

    def _retarget(self) -> None:
        """self.pair and both interpreters follow the auto sides' current languages: the
        target (MT, TTS voice) of whoever listens to them, and their own STT language."""
        assert self.auto is not None
        langs = {s: self._lang_of(s) for s in self.resolvers}
        self.pair = effective_pair(self._base_pair, langs, self.auto.sides)
        for s, interp in self.interpreters.items():
            interp.retarget(self.pair, stt_lang=self._stt_lang(s) if s in langs else None)

    def _spawn_lang(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro, name=f"{self.call_id}-lang")
        self._lang_tasks.add(task)
        task.add_done_callback(self._lang_done)
        return task

    def _lang_done(self, task: asyncio.Task[Any]) -> None:
        self._lang_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.warning("lang.error", extra={"call_id": self.call_id, "error": repr(exc)})

    def _lang_final(
        self,
        side: Side,
        utt: _Utt,
        final: str,
        stt_lang: str | None,
        audio: bytes,
        text: str | None,
        stt_confidence: float | None = None,
    ) -> None:
        """An auto side's STT final: keep the evidence on the utterance (when it is long
        enough to decide on). An early-final restart replaces it, so each utterance counts
        once, and language ID runs once, when it is judged."""
        r = self.resolvers.get(side)
        if r is None:
            return
        utt.lang_ev = None
        vad = self._sink.vads.get(side)
        end = vad.last_voiced if vad is not None else self.clock.now_ms()
        speech_ms = end - utt.t_start + FRAME_MS
        if not final.strip() or r.pinned or speech_ms < r.config.min_speech_ms:
            return
        utt.lang_ev = _LangEvidence(speech_ms, stt_lang, text or final, audio, stt_confidence)
        self._lang_ready(side, utt)

    def _lang_ready(self, side: Side, utt: _Utt) -> None:
        """Judge the utterance's evidence once it has both its final and its EOU (after
        EOU no early-final restart can replace the final), without waiting for MT/TTS."""
        if utt.t_eou is not None and utt.lang_ev is not None:
            self._lang_utt_done(side, utt)

    def _lang_utt_done(self, side: Side, utt: _Utt) -> None:
        """Judge the utterance's language evidence, if not judged yet."""
        ev, utt.lang_ev = utt.lang_ev, None
        if ev is not None and side in self.resolvers:
            self._spawn_lang(self._resolve(side, ev))

    async def _resolve(self, side: Side, ev: _LangEvidence) -> None:
        r = self.resolvers[side]
        async with self._lang_lock[side]:  # FIFO: utterances are judged in order
            guess = await self._lid_guess(side, ev.audio, r)
            if self.floor.bypass:
                return
            log.info(
                "lang.evidence",
                extra={
                    "call_id": self.call_id,
                    "side": side,
                    "speech_ms": ev.speech_ms,
                    "stt_lang": ev.stt_lang,
                    "stt_confidence": ev.stt_confidence,
                    "lid_lang": guess.lang if guess else None,
                    "lid_confidence": guess.confidence if guess else None,
                    "indic_share": round(indic_share(ev.text), 2),
                },
            )
            update = r.observe(
                speech_ms=ev.speech_ms,
                stt_lang=ev.stt_lang,
                lid=guess,
                text=ev.text,
                stt_confidence=ev.stt_confidence,
            )
            if update is not None:
                await self._apply_lang(update)

    async def _lid_guess(self, side: Side, audio: bytes, r: LanguageResolver) -> LangGuess | None:
        lid = self.providers.lid
        if lid is None or not audio or r.pinned:
            return None
        try:
            return await lid.identify(audio, r.config.supported)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._lid_failed(side, exc)

    def _lid_failed(self, side: Side, exc: Exception) -> None:
        name = getattr(self.providers.lid, "name", "lid")
        log.warning(
            "provider.error",
            extra={
                "call_id": self.call_id,
                "side": side,
                "provider": name,
                "stage": "lid",
                "error": repr(exc),
            },
        )
        self._publish("provider_error", {"provider": name, "stage": "lid", "error": repr(exc)})

    async def _apply_lang(self, up: LangUpdate) -> None:
        payload = up.payload()
        if up.menu:
            self._menu_until[up.side] = _FOREVER  # digits mean language from now
        self._publish("lang", payload)
        log.info("lang.update", extra={"call_id": self.call_id, **payload})
        if up.menu:
            self._spawn_lang(self._play_menu(up.side))
            return
        self._retarget()
        await self._check_same_lang(up.side)

    async def _check_same_lang(self, side: Side) -> None:
        """Both sides resolved to one language: nothing to interpret, bridge the call."""
        if self.floor.bypass or not all(self.lang_resolved(s) for s in SIDES):
            return
        if self._lang_of("a") != self._lang_of("b"):
            return
        self._same_lang = True
        log.info(
            "session.same_lang_bridge",
            extra={"call_id": self.call_id, "side": side, "lang": self._lang_of(side)},
        )
        await self.floor.enter_bypass(side, reason="same_lang")

    async def _play_menu(self, side: Side) -> None:
        """The keypad language menu on `side`'s leg; digits mean language until
        menu_window_ms after it finishes."""
        assert self.auto is not None
        try:
            async with asyncio.timeout(MENU_TIMEOUT_S):
                chunks = self._prompt_chunks("menu", side, language_menu_text(), lang=MENU_LANG)
                await self.floor.play_prompt(side, chunks, tag="menu", cuttable=False)
        except Exception as e:
            log.warning(
                "session.menu_error",
                extra={"call_id": self.call_id, "side": side, "error": repr(e)},
            )
        finally:
            if side in self._menu_until:
                self._menu_until[side] = self.clock.now_ms() + self.auto.menu_window_ms

    def _dtmf_hook(self, side: Side, digit: str) -> bool:
        """Floor DTMF hook (lock held). Inside a side's menu window its digits pick the
        language (1-6; others are ignored); outside it they keep their floor meaning."""
        until = self._menu_until.get(side)
        if until is None:
            return False
        if self.clock.now_ms() > until:
            del self._menu_until[side]
            return False
        lang = MENU_DIGITS.get(digit)
        if lang is None:
            log.info("lang.menu_digit_ignored", extra={"call_id": self.call_id, "side": side})
            return True
        del self._menu_until[side]
        self._spawn_lang(self._apply_lang(self.resolvers[side].choose(lang)))
        return True

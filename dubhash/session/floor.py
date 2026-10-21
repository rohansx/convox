"""FloorController: decides who hears what, and when (docs/tech-spec/floor-controller.md).

The only code allowed to call send/clear/mark on a leg. Every public handler takes one
asyncio.Lock, so handlers never interleave. Playback runs as one task per Translation,
outside the lock; a handler that cuts playback short cancels and awaits that task before
touching the leg, so no frame is sent after a clear().

Prompts (play_prompt, the opening line): the session's own audio to one leg, sent here like
everything else and followed by a mark. Until that mark comes back, or the prompt's length
plus grace_ms has passed, the leg is "hearing its prompt". A translation for that leg does
not wait for the prompt (L7: callers who spoke at once waited up to ~5 s): when the usual
rules start it, the prompt is CUT. No more of it is sent, its mark is not sent (or no
longer awaited), and the translation's playback clears the leg before its first frame, so
whatever the phone still had queued of the prompt is dropped. Playback to the other leg
is never affected.

Chunked utterances (L9, Translation.final_chunk): a chunk of A's ongoing utterance plays
to B under the usual rule (floor IDLE or A's), while A is still speaking. Once one of its
chunks has started, A is the floor's "holder" until its final chunk starts (or the rest
is discarded, B barges in, or the 4 s rescue fires): when a playback ends with nothing of
A's queued the state is A_SPEAKING, not IDLE, and only A's next chunk may start, so B's
translation never lands in the middle of A's utterance. Each chunk is its own playback
(mark, barge-in, clip), so the invariants hold per chunk.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from dubhash.audio.frames import FRAME_BYTES, bytes_to_ms
from dubhash.clock import Clock
from dubhash.events import EventBus
from dubhash.legs.base import LegTransport
from dubhash.types import CallEvent, EventKind, Side, Translation, other

log = logging.getLogger(__name__)

State = Literal["IDLE", "A_SPEAKING", "B_SPEAKING", "PLAYING_AB", "PLAYING_BA"]

PROMPT_TAG = "opening"
PROMPT_GRACE_MS = 1500  # after the prompt's own length, stop waiting for its mark
_NEVER = 2**62

Render = Callable[[Translation], AsyncIterator[bytes]]


async def passthrough(tr: Translation) -> AsyncIterator[bytes]:
    async for chunk in tr.audio:
        yield chunk


def _speaking(side: Side) -> State:
    return "A_SPEAKING" if side == "a" else "B_SPEAKING"


def _playing(src: Side) -> State:
    return "PLAYING_AB" if src == "a" else "PLAYING_BA"


@dataclass(slots=True, eq=False)
class _Playback:
    tr: Translation
    listener: Side
    sent: int = 0  # bytes handed to the listener leg
    audio: bytearray = field(default_factory=bytearray)  # what was sent, for DTMF replay
    failed: bool = False
    task: asyncio.Task[None] | None = None
    clear_first: bool = False  # the listener was hearing a prompt: clear it before playing


class FloorController:
    def __init__(
        self,
        call_id: str,
        legs: dict[Side, LegTransport],
        clock: Clock,
        *,
        backchannel_max_ms: int = 600,
        barge_in_min_ms: int = 1200,
        force_play_silence_ms: int = 4000,
        line_check_silence_ms: int = 10000,
        escape_dtmf: str = "0",
        replay_dtmf: str = "1",
        render: Render | None = None,
        events: EventBus | None = None,
        line_check: Callable[[Side], AsyncIterator[bytes]] | None = None,
        on_played: Callable[[Translation], None] | None = None,
        on_bypass: Callable[[], None] | None = None,
        bypass: bool = False,
        dtmf_hook: Callable[[Side, str], bool] | None = None,
    ) -> None:
        """bypass=True starts in passthrough (bridge mode): forward() relays frames from
        the first frame, with no bypass event and no on_bypass call. dtmf_hook(side, digit)
        sees every digit first (after the "dtmf" event); True means it took the digit (the
        keypad language menu, session/language.py) and the floor's 1/0 meanings are
        skipped."""
        self.call_id = call_id
        self._legs = legs
        self._clock = clock
        # Duration-based backchannel detection is the interpreter's job; the controller
        # only needs barge_in_min_ms (speech shorter than that never interrupts).
        self.backchannel_max_ms = backchannel_max_ms
        self.barge_in_min_ms = barge_in_min_ms
        self.force_play_silence_ms = force_play_silence_ms
        self.line_check_silence_ms = line_check_silence_ms
        self.escape_dtmf = escape_dtmf
        self.replay_dtmf = replay_dtmf
        self._render: Render = render or passthrough
        self._events = events
        self._line_check = line_check
        self._on_played = on_played
        self._on_bypass = on_bypass
        self._dtmf_hook = dtmf_hook

        self._lock = asyncio.Lock()
        self._state: State = "IDLE"
        self._queue: deque[Translation] = deque()
        self._pb: _Playback | None = None
        self._bypass = bypass
        self._bg: set[asyncio.Task[Any]] = set()
        self._speaking: set[Side] = set()
        self._barge_since: int | None = None  # listener speech start during playback
        now = clock.now_ms()
        self._last_speech = now  # last speech start/end on either leg
        self._quiet_since = now  # last speech or end of playback
        self._line_checked = False  # once per silence period
        self._line_tasks: dict[Side, asyncio.Task[None]] = {}
        # DTMF replay: the rendered audio last sent to each listener side
        self._last_heard: dict[Side, tuple[Translation, bytes]] = {}
        self._internal: set[int] = set()  # id() of replay Translations made here
        self._replay_seq = 0
        # legs hearing a prompt: side -> (mark tag, deadline ms; _NEVER while sending)
        self._prompts: dict[Side, tuple[str, int]] = {}
        self._prompt_sent: dict[Side, int] = {}  # bytes of the current prompt sent so far
        self._uncut: set[Side] = set()  # legs whose prompt holds translations (the menu)
        # chunked utterances (L9): the side whose utterance has started playing a chunk
        # and not yet its final chunk. It keeps the floor: only its chunks start until then
        self._holder: Side | None = None

    # --- public view ---------------------------------------------------------------------

    @property
    def state(self) -> State:
        return self._state

    @property
    def queue(self) -> deque[Translation]:
        return self._queue

    @property
    def playing(self) -> Translation | None:
        return self._pb.tr if self._pb else None

    @property
    def bypass(self) -> bool:
        return self._bypass

    def prompt_playing(self, side: Side) -> bool:
        """The leg is still hearing a prompt (its mark has not come back)."""
        return side in self._prompts

    # --- lifecycle -------------------------------------------------------------------------

    def quiet_from(self, t_ms: int) -> None:
        """Count line-check silence from t_ms (may be in the future: the end of a prompt
        still playing to the callers). Never moves the start of the silence back."""
        self._quiet_since = max(self._quiet_since, t_ms)
        self._line_checked = False

    async def start(self) -> None:
        """Register mark and DTMF callbacks on both legs."""
        for side, leg in self._legs.items():
            leg.on_mark(self._mark_cb(side))
            leg.on_dtmf(self._dtmf_cb(side))

    def _mark_cb(self, side: Side) -> Callable[[str], None]:
        return lambda tag: self._spawn(self.on_mark(side, tag))

    def _dtmf_cb(self, side: Side) -> Callable[[str], None]:
        return lambda digit: self._spawn(self.on_dtmf(side, digit))

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    async def play_prompt(
        self,
        side: Side,
        audio: AsyncIterator[bytes],
        *,
        tag: str = PROMPT_TAG,
        grace_ms: int = PROMPT_GRACE_MS,
        cuttable: bool = True,
    ) -> int:
        """Send a session prompt (the opening line) to `side` as it is produced, then
        mark it. The leg counts as hearing it until the mark comes back, or until the
        prompt's length plus grace_ms after its first byte was sent. A translation that
        starts for this leg meanwhile cuts it: sending stops (the rest of `audio` is
        closed unsent) and no mark is sent. cuttable=False (the keypad language menu): a
        translation for this leg waits until the prompt is over instead. Returns the bytes
        sent. If `audio` fails, the hold ends at once and the error propagates."""
        leg = self._legs[side]
        async with self._lock:
            self._prompts[side] = (tag, _NEVER)
            self._prompt_sent[side] = 0
            if cuttable:
                self._uncut.discard(side)
            else:
                self._uncut.add(side)
        sent = 0
        t0: int | None = None

        def live() -> bool:
            return self._prompts.get(side, ("",))[0] == tag

        try:
            async for chunk in audio:
                if not live():
                    break  # cut by a translation
                if not chunk:
                    continue
                if t0 is None:
                    t0 = self._clock.now_ms()
                await leg.send(chunk)
                sent += len(chunk)
                self._prompt_sent[side] = sent
            if live():
                await leg.mark(tag)
            else:
                await _aclose_iter(audio)
        except BaseException:
            self._spawn(self._end_prompt(side, tag))
            aclose = getattr(audio, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()
            raise
        deadline = (t0 if t0 is not None else self._clock.now_ms()) + bytes_to_ms(sent)
        async with self._lock:
            if self._prompts.get(side, ("",))[0] == tag:
                self._prompts[side] = (tag, deadline + grace_ms)
        return sent

    async def _end_prompt(self, side: Side, tag: str) -> None:
        async with self._lock:
            self._release_prompt(side, tag)

    def _release_prompt(self, side: Side, tag: str) -> bool:
        """End the hold on `side` if it is for `tag`; start what was waiting for it."""
        held = self._prompts.get(side)
        if held is None or held[0] != tag:
            return False
        del self._prompts[side]
        self._uncut.discard(side)
        self.quiet_from(self._clock.now_ms())
        if self._bypass or self._pb is not None or not self._queue:
            return True
        if self._state in ("IDLE", _speaking(self._queue[0].side)):
            self._drain()
        return True

    async def aclose(self) -> None:
        """Stop playback and background tasks. Whatever is still playing or queued is
        ended as clipped, so no Translation goes unaccounted for. Legs are not touched."""
        async with self._lock:
            pb = self._pb
            if pb is not None:
                await self._stop_task(pb)
                self._pb = None
                self._end(pb.tr, clipped_ms=bytes_to_ms(pb.sent))
            while self._queue:
                self._end(self._queue.popleft(), clipped_ms=0)
        for task in list(self._bg):
            task.cancel()
        for task in list(self._bg):
            with contextlib.suppress(BaseException):
                await task

    # --- events ----------------------------------------------------------------------------

    async def on_speech_start(self, side: Side) -> None:
        async with self._lock:
            if self._bypass:
                return
            self._speaking.add(side)
            self._activity()
            if self._state == "IDLE":
                self._set_state(_speaking(side))
            elif self._pb is not None and side == self._pb.listener:
                self._barge_since = self._clock.now_ms()  # §2.1 rule 3: listener only

    async def on_speech_end(self, side: Side) -> None:
        async with self._lock:
            self._speaking.discard(side)
            if self._bypass:
                return
            self._activity()
            if self._pb is not None and side == self._pb.listener:
                self._barge_since = None  # under barge_in_min_ms: never interrupts

    async def on_utterance_discarded(self, side: Side) -> None:
        """§2.1 rule 2: the floor holder's utterance produced no translation."""
        async with self._lock:
            if self._holder == side:  # the rest of a chunked utterance: nothing more comes
                self._holder = None
            if self._bypass or self._state != _speaking(side):
                return
            self._set_state("IDLE")
            self._drain()

    async def on_translation(self, tr: Translation) -> None:
        async with self._lock:
            if self._bypass:
                self._end(tr, clipped_ms=0)
                await _close_audio(tr)
                return
            self._accept(tr)

    async def on_mark(self, side: Side, tag: str) -> None:
        async with self._lock:
            if self._release_prompt(side, tag):
                return
            pb = self._pb
            if pb is None or side != pb.listener or tag != pb.tr.tag:
                return  # stale (e.g. after a clear) or foreign
            await self._stop_task(pb)
            self._playback_ended(pb)
            self._end(pb.tr, clipped_ms=bytes_to_ms(pb.sent) if pb.failed else None)
            if not self._drain():
                self._set_state(self._after_playback())

    async def on_dtmf(self, side: Side, digit: str) -> None:
        async with self._lock:
            self._publish("dtmf", {"side": side, "digit": digit})
            log.info("floor.dtmf", extra={"side": side, "digit": digit})
            if self._bypass:
                return
            if self._dtmf_hook is not None and self._dtmf_hook(side, digit):
                return
            if digit == self.escape_dtmf:
                await self._enter_bypass(side, {"digit": digit})
            elif digit == self.replay_dtmf:
                self._replay(side)

    async def enter_bypass(self, side: Side, *, reason: str) -> None:
        """Passthrough for the rest of the call, entered by the session (e.g. "same_lang":
        both sides turned out to speak one language). As DTMF escape, but the "bypass"
        event carries the reason instead of a digit. A no-op if already in bypass."""
        async with self._lock:
            if not self._bypass:
                await self._enter_bypass(side, {"reason": reason})

    async def forward(self, side: Side, frame: bytes) -> None:
        """Bypass passthrough: a frame from `side` goes to the other leg at 0 dB.
        A no-op until bypass is on."""
        if self._bypass:
            await self._legs[other(side)].send(frame)

    async def on_barge_in(self, side: Side) -> None:
        async with self._lock:
            await self._barge_in(side)

    async def tick(self) -> None:
        async with self._lock:
            if self._bypass:
                return
            now = self._clock.now_ms()
            for side, (tag, deadline) in list(self._prompts.items()):
                if now >= deadline:  # the mark never came back
                    log.warning("floor.prompt_mark_timeout", extra={"side": side, "tag": tag})
                    self._release_prompt(side, tag)
            pb = self._pb
            if (
                pb is not None
                and self._barge_since is not None
                and now - self._barge_since >= self.barge_in_min_ms
            ):
                await self._barge_in(pb.listener)
            if self._pb is not None or self._speaking:
                return
            silent = now - self._last_speech >= self.force_play_silence_ms
            if silent and self._holder is not None:  # its final chunk never came
                self._holder = None
                if not self._drain() and self._state != "IDLE":
                    self._set_state("IDLE")
            elif self._queue and silent:
                self._drain()  # rule 5: rescue a held floor
            elif (
                not self._queue
                and not self._line_checked
                and now - self._quiet_since >= self.line_check_silence_ms
            ):
                self._line_checked = True
                self._start_line_check()

    # --- internals (call with the lock held) ---------------------------------------------

    def _accept(self, tr: Translation) -> None:
        """§2.1 rule 1: play now if the floor is free or the source's, else enqueue.
        Either way it goes through the queue, so arrival order holds."""
        self._queue.append(tr)
        if self._state in ("IDLE", _speaking(tr.side)):
            self._drain()
        else:
            self._publish_state(self._state, self._state)

    def _replay(self, listener: Side) -> None:
        heard = self._last_heard.get(listener)
        if heard is None:
            return
        orig, audio = heard
        self._replay_seq -= 1  # negative seq: a distinct tag, never a turn
        tr = Translation(
            side=orig.side,
            seq=self._replay_seq,
            src_text=orig.src_text,
            tgt_text=orig.tgt_text,
            audio=_frames(audio),
            voice=orig.voice,
        )
        self._internal.add(id(tr))
        self._accept(tr)

    async def _enter_bypass(self, side: Side, why: dict[str, str]) -> None:
        """Rule 6: plain mixer for the rest of the call. Nothing is dropped silently."""
        pb = self._pb
        if pb is not None:
            await self._cut(pb)
        while self._queue:
            tr = self._queue.popleft()
            self._end(tr, clipped_ms=0)
            await _close_audio(tr)
        for s in list(self._line_tasks):
            self._stop_line_check(s)
        self._bypass = True
        self._holder = None
        self._prompts.clear()
        self._uncut.clear()
        self._speaking.clear()
        self._publish("bypass", {"side": side, **why})
        log.info("floor.bypass", extra={"side": side})
        if self._state != "IDLE":
            self._set_state("IDLE")
        if self._on_bypass is not None:
            self._on_bypass()

    def _activity(self) -> None:
        self._last_speech = self._quiet_since = self._clock.now_ms()
        self._line_checked = False

    def _start_line_check(self) -> None:
        if self._line_check is None:
            return
        self._publish("line_check", {"sides": list(self._legs)})
        for side in self._legs:
            self._line_tasks[side] = self._spawn(self._send_line_check(side))

    async def _send_line_check(self, side: Side) -> None:
        assert self._line_check is not None
        leg = self._legs[side]
        try:
            async for chunk in self._line_check(side):
                await leg.send(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("floor.line_check_error", extra={"side": side, "error": repr(e)})

    def _stop_line_check(self, side: Side) -> None:
        task = self._line_tasks.pop(side, None)
        if task is not None:
            task.cancel()

    def _playback_ended(self, pb: _Playback) -> None:
        if pb.audio:
            self._last_heard[pb.listener] = (pb.tr, bytes(pb.audio))
        self._pb = None
        self._barge_since = None
        self._quiet_since = self._clock.now_ms()
        self._line_checked = False

    async def _barge_in(self, side: Side) -> None:
        """Rule 4: the listener spoke past barge_in_min_ms. Clear, clip, take the floor."""
        pb = self._pb
        if self._bypass or pb is None or side != pb.listener:
            return
        clipped_ms = await self._cut(pb)
        if self._holder == pb.tr.side:  # the listener takes the floor from a chunked one
            self._holder = None
        self._publish(
            "barge_in",
            {"side": side, "listener": side, "clipped_seq": pb.tr.seq, "clipped_ms": clipped_ms},
        )
        log.info("floor.barge_in", extra={"side": side, "clipped_ms": clipped_ms})
        self._set_state(_speaking(side))

    async def _cut(self, pb: _Playback) -> int:
        """Stop playback, clear the listener leg and end the item as clipped.

        clipped_ms approximates "ms played" by the bytes handed to the leg, read as 16 kHz
        PCM16 (render output, earcon included). The transport may still have held some of
        it in its buffer, so this is an upper bound on what the listener heard.
        """
        await self._stop_task(pb)
        self._playback_ended(pb)
        with contextlib.suppress(Exception):
            await self._legs[pb.listener].clear()
        clipped_ms = bytes_to_ms(pb.sent)
        self._end(pb.tr, clipped_ms=clipped_ms)
        return clipped_ms

    def _after_playback(self) -> State:
        """The state when a playback ended and nothing else starts: the holder of a chunked
        utterance keeps the floor (more of it is coming), else IDLE."""
        return _speaking(self._holder) if self._holder is not None else "IDLE"

    def _drain(self) -> bool:
        """Start the head of the queue if nothing is playing. True if something started.
        While a chunked utterance holds the floor (L9), only its own next chunk starts;
        other items wait behind it."""
        if self._pb is not None or not self._queue:
            return False
        if other(self._queue[0].side) in self._uncut and other(self._queue[0].side) in (
            self._prompts
        ):
            return False  # the listener is hearing the menu: wait for its end
        if self._holder is None:
            tr = self._queue.popleft()
        else:
            tr = next((t for t in self._queue if t.side == self._holder), None)
            if tr is None:
                return False
            self._queue.remove(tr)
        if id(tr) in self._internal:  # a DTMF replay is no part of an utterance
            pass
        elif not tr.final_chunk:
            self._holder = tr.side
        elif self._holder == tr.side:
            self._holder = None
        pb = _Playback(tr, other(tr.side))
        if pb.listener in self._prompts:  # still hearing a prompt: cut it (L7)
            pb.clear_first = True
            self._cut_prompt(pb.listener)
        self._pb = pb
        self._stop_line_check(pb.listener)
        # a listener already talking when playback starts is talking over it
        self._barge_since = self._clock.now_ms() if pb.listener in self._speaking else None
        self._set_state(_playing(tr.side))
        pb.task = self._spawn(self._play(pb))
        return True

    def _cut_prompt(self, side: Side) -> None:
        """A translation is starting for `side`: end its prompt now. play_prompt stops
        sending at its next chunk; the playback clears the leg before its first frame."""
        tag, _ = self._prompts.pop(side)
        sent_ms = bytes_to_ms(self._prompt_sent.get(side, 0))
        log.info("floor.prompt_cut", extra={"side": side, "tag": tag, "sent_ms": sent_ms})
        self._publish("prompt_cut", {"side": side, "tag": tag, "sent_ms": sent_ms})

    async def _play(self, pb: _Playback) -> None:
        tr = pb.tr
        leg = self._legs[pb.listener]
        # replays carry audio that was already rendered once
        stream = tr.audio if id(tr) in self._internal else self._render(tr)
        try:
            if pb.clear_first:  # drop what the transport still holds of the cut prompt
                with contextlib.suppress(Exception):
                    await leg.clear()
            try:
                async for chunk in stream:
                    if not chunk:
                        continue
                    if pb.sent == 0:
                        tr.timings["t_play_start"] = self._clock.now_ms()
                    await leg.send(chunk)
                    pb.sent += len(chunk)
                    pb.audio += chunk
            except asyncio.CancelledError:
                raise
            except Exception as e:  # TTS or leg died mid-utterance: end what was sent
                pb.failed = True
                log.warning("floor.play_error", extra={"tag": tr.tag, "error": repr(e)})
            try:
                await leg.mark(tr.tag)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # no mark will ever come: end it here
                pb.failed = True
                log.warning("floor.mark_error", extra={"tag": tr.tag, "error": repr(e)})
                self._spawn(self._abandon(pb))
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    async def _abandon(self, pb: _Playback) -> None:
        async with self._lock:
            if self._pb is not pb:
                return
            self._playback_ended(pb)
            self._end(pb.tr, clipped_ms=bytes_to_ms(pb.sent))
            if not self._drain():
                self._set_state(self._after_playback())

    async def _stop_task(self, pb: _Playback) -> None:
        task = pb.task
        if task is None or task.done():
            return
        if task is asyncio.current_task():
            return
        task.cancel()
        with contextlib.suppress(BaseException):
            await task

    def _end(self, tr: Translation, *, clipped_ms: int | None) -> None:
        now = self._clock.now_ms()
        tr.timings.setdefault("t_play_start", now)
        tr.timings["t_play_end"] = now
        tr.clipped_ms = clipped_ms
        if id(tr) in self._internal:
            self._internal.discard(id(tr))
            return
        if self._on_played is not None:
            self._on_played(tr)

    def _set_state(self, to: State) -> None:
        frm = self._state
        self._state = to
        self._publish_state(frm, to)

    def _publish_state(self, frm: State, to: State) -> None:
        depth = len(self._queue)
        log.info("floor.state", extra={"from": frm, "to": to, "queue_depth": depth})
        self._publish("state", {"from": frm, "to": to, "queue_depth": depth})

    def _publish(self, kind: EventKind, payload: dict[str, Any]) -> None:
        if self._events is not None:
            self._events.publish(CallEvent(self.call_id, self._clock.now_ms(), kind, payload))


async def _frames(audio: bytes) -> AsyncIterator[bytes]:
    for i in range(0, len(audio), FRAME_BYTES):
        yield audio[i : i + FRAME_BYTES]


async def _aclose_iter(it: Any) -> None:
    aclose = getattr(it, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await aclose()


async def _close_audio(tr: Translation) -> None:
    aclose = getattr(tr.audio, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await aclose()

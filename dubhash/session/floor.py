"""FloorController: decides who hears what, and when (docs/tech-spec/floor-controller.md).

The only code allowed to call send/clear/mark on a leg. Every public handler takes one
asyncio.Lock, so handlers never interleave. Playback runs as one task per Translation,
outside the lock; a handler that cuts playback short cancels and awaits that task before
touching the leg, so no frame is sent after a clear().
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
    ) -> None:
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

        self._lock = asyncio.Lock()
        self._state: State = "IDLE"
        self._queue: deque[Translation] = deque()
        self._pb: _Playback | None = None
        self._bypass = False
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

    # --- lifecycle -------------------------------------------------------------------------

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
            pb = self._pb
            if pb is None or side != pb.listener or tag != pb.tr.tag:
                return  # stale (e.g. after a clear) or foreign
            await self._stop_task(pb)
            self._playback_ended(pb)
            self._end(pb.tr, clipped_ms=bytes_to_ms(pb.sent) if pb.failed else None)
            if not self._drain():
                self._set_state("IDLE")

    async def on_dtmf(self, side: Side, digit: str) -> None:
        async with self._lock:
            self._publish("dtmf", {"side": side, "digit": digit})
            log.info("floor.dtmf", extra={"side": side, "digit": digit})
            if self._bypass:
                return
            if digit == self.escape_dtmf:
                await self._enter_bypass(side, digit)
            elif digit == self.replay_dtmf:
                self._replay(side)

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
            pb = self._pb
            if (
                pb is not None
                and self._barge_since is not None
                and now - self._barge_since >= self.barge_in_min_ms
            ):
                await self._barge_in(pb.listener)
            if self._pb is not None or self._speaking:
                return
            if self._queue and now - self._last_speech >= self.force_play_silence_ms:
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

    async def _enter_bypass(self, side: Side, digit: str) -> None:
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
        self._speaking.clear()
        self._publish("bypass", {"side": side, "digit": digit})
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

    def _drain(self) -> bool:
        """Start the head of the queue if nothing is playing. True if something started."""
        if self._pb is not None or not self._queue:
            return False
        tr = self._queue.popleft()
        pb = _Playback(tr, other(tr.side))
        self._pb = pb
        self._stop_line_check(pb.listener)
        # a listener already talking when playback starts is talking over it
        self._barge_since = self._clock.now_ms() if pb.listener in self._speaking else None
        self._set_state(_playing(tr.side))
        pb.task = self._spawn(self._play(pb))
        return True

    async def _play(self, pb: _Playback) -> None:
        tr = pb.tr
        leg = self._legs[pb.listener]
        # replays carry audio that was already rendered once
        stream = tr.audio if id(tr) in self._internal else self._render(tr)
        try:
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
                self._set_state("IDLE")

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


async def _close_audio(tr: Translation) -> None:
    aclose = getattr(tr.audio, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await aclose()

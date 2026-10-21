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

from dubhash.audio.frames import bytes_to_ms
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
            if self._state == "IDLE":
                self._set_state(_speaking(side))

    async def on_speech_end(self, side: Side) -> None:
        async with self._lock:
            pass

    async def on_utterance_discarded(self, side: Side) -> None:
        """§2.1 rule 2: the floor holder's utterance produced no translation."""
        async with self._lock:
            if self._bypass or self._state != _speaking(side):
                return
            self._set_state("IDLE")
            self._drain()

    async def on_translation(self, tr: Translation) -> None:
        async with self._lock:
            self._queue.append(tr)
            if self._state in ("IDLE", _speaking(tr.side)):
                self._drain()
            else:
                self._publish_state(self._state, self._state)

    async def on_mark(self, side: Side, tag: str) -> None:
        async with self._lock:
            pb = self._pb
            if pb is None or side != pb.listener or tag != pb.tr.tag:
                return  # stale (e.g. after a clear) or foreign
            await self._stop_task(pb)
            self._pb = None
            self._end(pb.tr, clipped_ms=bytes_to_ms(pb.sent) if pb.failed else None)
            if not self._drain():
                self._set_state("IDLE")

    async def on_dtmf(self, side: Side, digit: str) -> None:
        async with self._lock:
            self._publish("dtmf", {"side": side, "digit": digit})

    async def tick(self) -> None:
        async with self._lock:
            pass

    # --- internals (call with the lock held) ---------------------------------------------

    def _drain(self) -> bool:
        """Start the head of the queue if nothing is playing. True if something started."""
        if self._pb is not None or not self._queue:
            return False
        tr = self._queue.popleft()
        pb = _Playback(tr, other(tr.side))
        self._pb = pb
        self._set_state(_playing(tr.side))
        pb.task = self._spawn(self._play(pb))
        return True

    async def _play(self, pb: _Playback) -> None:
        tr = pb.tr
        leg = self._legs[pb.listener]
        stream = self._render(tr)
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
            except Exception as e:  # TTS died mid-utterance: end what was sent
                pb.failed = True
                log.warning("floor.render_error", extra={"tag": tr.tag, "error": repr(e)})
            await leg.mark(tr.tag)
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

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

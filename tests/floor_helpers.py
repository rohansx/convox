"""Test doubles and a rig for FloorController tests.

Every Translation built by the rig carries audio frames filled with one marker byte, so
the shared send log tells which Translation each frame belongs to. Line-check frames use
LINE_CHECK_MARKER. The rig checks the floor invariants over that log after every step.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from dubhash.audio.frames import FRAME_BYTES
from dubhash.clock import FakeClock
from dubhash.events import EventBus
from dubhash.session.floor import FloorController
from dubhash.types import CallEvent, Side, Translation, other

LINE_CHECK_MARKER = 0xEE


@dataclass(slots=True)
class LogEntry:
    side: Side
    op: str  # "send" | "clear" | "mark"
    data: bytes = b""
    tag: str = ""

    @property
    def marker(self) -> int | None:
        return self.data[0] if self.op == "send" and self.data else None


class RecordingLeg:
    """LegTransport double: records send/clear/mark into a log shared by both legs and
    lets the test fire mark and DTMF callbacks."""

    def __init__(self, side: Side, log: list[LogEntry]) -> None:
        self.leg_id: Side = side
        self.log = log
        self.mark_cbs: list[Callable[[str], None]] = []
        self.dtmf_cbs: list[Callable[[str], None]] = []
        self.closed = False

    async def frames(self) -> AsyncIterator[bytes]:
        return
        yield b""  # pragma: no cover

    async def send(self, pcm16_16k: bytes) -> None:
        self.log.append(LogEntry(self.leg_id, "send", pcm16_16k))

    async def clear(self) -> None:
        self.log.append(LogEntry(self.leg_id, "clear"))

    async def mark(self, tag: str) -> None:
        self.log.append(LogEntry(self.leg_id, "mark", tag=tag))

    def on_mark(self, cb: Callable[[str], None]) -> None:
        self.mark_cbs.append(cb)

    def on_dtmf(self, cb: Callable[[str], None]) -> None:
        self.dtmf_cbs.append(cb)

    async def close(self) -> None:
        self.closed = True

    def fire_mark(self, tag: str) -> None:
        for cb in self.mark_cbs:
            cb(tag)

    def fire_dtmf(self, digit: str) -> None:
        for cb in self.dtmf_cbs:
            cb(digit)

    # views over the shared log
    def entries(self, op: str | None = None) -> list[LogEntry]:
        return [e for e in self.log if e.side == self.leg_id and (op is None or e.op == op)]

    def sent_markers(self) -> list[int]:
        """Distinct markers in the order they first appeared on this leg."""
        out: list[int] = []
        for e in self.entries("send"):
            if e.marker is not None and e.marker not in out:
                out.append(e.marker)
        return out


class RecordingBus(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[CallEvent] = []

    def publish(self, event: CallEvent) -> None:
        self.events.append(event)
        super().publish(event)

    def kinds(self, kind: str) -> list[CallEvent]:
        return [e for e in self.events if e.kind == kind]


def frames_of(marker: int, n: int) -> list[bytes]:
    return [bytes([marker]) * FRAME_BYTES for _ in range(n)]


async def tts(chunks: list[bytes]) -> AsyncIterator[bytes]:
    """FakeTTS-style stream: yields its chunks, giving the loop a turn between them."""
    for c in chunks:
        await asyncio.sleep(0)
        yield c


class FeedAudio:
    """An audio iterator the test feeds by hand, to model TTS underruns."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()

    def push(self, *chunks: bytes) -> None:
        for c in chunks:
            self._q.put_nowait(c)

    def end(self) -> None:
        self._q.put_nowait(None)

    def __aiter__(self) -> FeedAudio:
        return self

    async def __anext__(self) -> bytes:
        c = await self._q.get()
        if c is None:
            raise StopAsyncIteration
        return c


async def failing_tts(chunks: list[bytes]) -> AsyncIterator[bytes]:
    """Models a provider socket killed mid-utterance."""
    for c in chunks:
        await asyncio.sleep(0)
        yield c
    raise ConnectionError("provider socket closed")


async def settle(rounds: int = 30) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


async def line_check_audio(side: Side) -> AsyncIterator[bytes]:
    for c in frames_of(LINE_CHECK_MARKER, 3):
        await asyncio.sleep(0)
        yield c


@dataclass
class Rig:
    clock: FakeClock = field(default_factory=FakeClock)
    log: list[LogEntry] = field(default_factory=list)
    bus: RecordingBus = field(default_factory=RecordingBus)
    played: list[Translation] = field(default_factory=list)
    handed: list[Translation] = field(default_factory=list)
    bypassed: int = 0
    markers: dict[int, Translation] = field(default_factory=dict)
    fc: FloorController = field(init=False)
    legs: dict[Side, RecordingLeg] = field(init=False)

    def __post_init__(self) -> None:
        self.legs = {"a": RecordingLeg("a", self.log), "b": RecordingLeg("b", self.log)}
        self.fc = self.make()

    def make(self, **kw: object) -> FloorController:
        def on_bypass() -> None:
            self.bypassed += 1

        kw.setdefault("line_check", line_check_audio)
        return FloorController(
            "c_test",
            dict(self.legs),  # type: ignore[arg-type]
            self.clock,
            events=self.bus,
            on_played=self.played.append,
            on_bypass=on_bypass,
            **kw,  # type: ignore[arg-type]
        )

    def tr(
        self, side: Side, seq: int, n_frames: int = 5, audio: AsyncIterator[bytes] | None = None
    ) -> Translation:
        marker = len(self.markers) + 1
        assert marker < LINE_CHECK_MARKER
        if audio is None:
            audio = tts(frames_of(marker, n_frames))
        t = Translation(side, seq, f"src {side}{seq}", f"tgt {side}{seq}", audio, "voice")
        self.markers[marker] = t
        return t

    async def hand(self, t: Translation) -> None:
        self.handed.append(t)
        await self.fc.on_translation(t)
        await self.step()

    async def step(self) -> None:
        await settle()
        self.check()

    async def advance(self, ms: int, tick_every: int = 50) -> None:
        """Advance the fake clock in tick_every steps, calling tick() each time."""
        left = ms
        while left > 0:
            d = min(tick_every, left)
            self.clock.advance(d)
            left -= d
            await self.fc.tick()
            await settle(5)
        await self.step()

    async def finish(self) -> None:
        """Fire the mark for the item now playing (as the transport would)."""
        p = self.fc.playing
        assert p is not None, "nothing playing"
        listener = other(p.side)
        await settle()
        assert any(e.tag == p.tag for e in self.legs[listener].entries("mark")), "no mark sent"
        leg = self.legs[listener]
        if leg.mark_cbs:
            leg.fire_mark(p.tag)
        else:
            await self.fc.on_mark(listener, p.tag)
        await self.step()

    def sent_tags(self, side: Side) -> list[str]:
        return [self.markers[m].tag for m in self.legs[side].sent_markers() if m in self.markers]

    def check(self) -> None:
        check_invariants(self)


def check_invariants(rig: Rig) -> None:
    fc = rig.fc
    # 1. never two playing: once a Translation's frames start on a leg, no other
    # Translation's frames appear anywhere until its mark is sent or its leg is cleared.
    current: tuple[Side, int] | None = None
    for e in rig.log:
        if e.op == "send" and e.marker in rig.markers:
            if current is None:
                current = (e.side, e.marker)
            else:
                assert current == (e.side, e.marker), f"overlap: {current} vs {e}"
        elif e.op in ("mark", "clear") and current is not None and e.side == current[0]:
            current = None
    # 2. nothing dropped: every handed Translation is queued, playing, or ended once
    ids = [id(t) for t in rig.played]
    assert len(ids) == len(set(ids)), "a Translation ended twice"
    for t in rig.handed:
        where = (t is fc.playing) + any(t is q for q in fc.queue) + any(t is p for p in rig.played)
        assert where == 1, f"{t.tag} is in {where} places"
    for t in rig.played:
        assert "t_play_start" in t.timings and "t_play_end" in t.timings
    # 4. per-direction order of playback starts follows hand-in order
    for side in ("a", "b"):
        handed = [t.tag for t in rig.handed if t.side == side]
        started = [tag for tag in rig.sent_tags(other(side)) if tag in handed]
        assert started == [tag for tag in handed if tag in started], (side, started, handed)
    assert fc.state in ("IDLE", "A_SPEAKING", "B_SPEAKING", "PLAYING_AB", "PLAYING_BA")
    if fc.playing is not None:
        assert fc.state == ("PLAYING_AB" if fc.playing.side == "a" else "PLAYING_BA")


def check_all_ended(rig: Rig) -> None:
    assert {id(t) for t in rig.handed} == {id(t) for t in rig.played}

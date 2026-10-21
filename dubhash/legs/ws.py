"""The websocket adapter every network leg talks through, plus the audio plumbing the legs
share: inbound reframing/resampling, outbound 100 ms chunking, chunk-gap detection and
playout-time mark emulation.

`WSLike` is the subset of Starlette's `WebSocket` the legs use, so the API passes its
socket straight in. Tests use `ws_pair()`, an in-memory pair with the same surface.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from dubhash.audio.frames import SAMPLE_RATE, SAMPLE_WIDTH, Reframer, ms_to_bytes
from dubhash.audio.resample import Resampler
from dubhash.clock import Clock

log = logging.getLogger(__name__)

WIRE_ALIGN = 320  # Exotel: every outbound media payload is a multiple of 320 bytes
_BYTES_PER_MS = SAMPLE_RATE * SAMPLE_WIDTH / 1000  # 32.0 at 16 kHz


@runtime_checkable
class WSLike(Protocol):
    async def receive_text(self) -> str: ...

    async def receive_bytes(self) -> bytes: ...

    async def receive(self) -> Mapping[str, Any]:
        """ASGI message: {"type": "websocket.receive", "text" | "bytes": ...} or
        {"type": "websocket.disconnect"}. Used where text and binary are mixed."""
        ...

    async def send_text(self, data: str) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


class WSClosed(Exception):
    """Raised by MemoryWS when receiving from or sending on a closed socket."""


_DISCONNECT: dict[str, Any] = {"type": "websocket.disconnect", "code": 1000}


class MemoryWS:
    """One end of an in-memory websocket pair. Messages are ASGI-shaped dicts."""

    def __init__(self) -> None:
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.peer: MemoryWS | None = None
        self.closed = False
        self._peer_gone = False

    async def receive(self) -> Mapping[str, Any]:
        if self._peer_gone and self._inbox.empty():
            return _DISCONNECT
        msg = await self._inbox.get()
        if msg["type"] == "websocket.disconnect":
            self._peer_gone = True
        return msg

    async def receive_text(self) -> str:
        msg = await self.receive()
        if msg["type"] == "websocket.disconnect":
            raise WSClosed
        if "text" not in msg:
            raise TypeError("expected a text message")
        return msg["text"]

    async def receive_bytes(self) -> bytes:
        msg = await self.receive()
        if msg["type"] == "websocket.disconnect":
            raise WSClosed
        if "bytes" not in msg:
            raise TypeError("expected a binary message")
        return msg["bytes"]

    async def send_text(self, data: str) -> None:
        self._deliver({"type": "websocket.receive", "text": data})

    async def send_bytes(self, data: bytes) -> None:
        self._deliver({"type": "websocket.receive", "bytes": data})

    async def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        self.closed = True
        if self.peer is not None and not self.peer.closed:
            self.peer._inbox.put_nowait({"type": "websocket.disconnect", "code": code})

    def drain(self) -> list[str | bytes]:
        """Everything received so far, without waiting (test helper)."""
        out: list[str | bytes] = []
        while not self._inbox.empty():
            msg = self._inbox.get_nowait()
            if msg["type"] == "websocket.disconnect":
                self._peer_gone = True
                continue
            out.append(msg["text"] if "text" in msg else msg["bytes"])
        return out

    def _deliver(self, msg: dict[str, Any]) -> None:
        if self.closed or self.peer is None or self.peer.closed:
            raise WSClosed
        self.peer._inbox.put_nowait(msg)


def ws_pair() -> tuple[MemoryWS, MemoryWS]:
    """(server_end, client_end). Hand the server end to a leg; script the client end."""
    a, b = MemoryWS(), MemoryWS()
    a.peer, b.peer = b, a
    return a, b


class InboundAudio:
    """Wire PCM16 at any rate in, 640-byte PCM16@16k frames out through `frames()`."""

    def __init__(self, wire_rate: int = SAMPLE_RATE) -> None:
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._reframer = Reframer()
        self._rs = Resampler(wire_rate, SAMPLE_RATE)
        self.wire_rate = wire_rate
        self.ended = False

    def set_rate(self, wire_rate: int) -> None:
        if wire_rate != self.wire_rate:
            self.wire_rate = wire_rate
            self._rs = Resampler(wire_rate, SAMPLE_RATE)

    def push(self, pcm16_wire: bytes) -> None:
        if self.ended:
            return
        for frame in self._reframer.push(self._rs.process(pcm16_wire)):
            self._queue.put_nowait(frame)

    def end(self) -> None:
        """Flush the resampler and the zero-padded partial frame, then end frames()."""
        if self.ended:
            return
        for frame in self._reframer.push(self._rs.flush()):
            self._queue.put_nowait(frame)
        tail = self._reframer.flush(pad=True)
        if tail is not None:
            self._queue.put_nowait(tail)
        self.ended = True
        self._queue.put_nowait(None)

    async def frames(self) -> AsyncIterator[bytes]:
        while (frame := await self._queue.get()) is not None:
            yield frame


class OutboundAudio:
    """PCM16@16k in; wire-rate PCM16 chunks of `chunk_ms` out (each a multiple of 320 B)."""

    def __init__(self, chunk_ms: int = 100, wire_rate: int = SAMPLE_RATE) -> None:
        self.chunk_ms = chunk_ms
        self.wire_rate = wire_rate
        self.chunk_bytes = self._chunk_bytes(wire_rate)
        self._rs = Resampler(SAMPLE_RATE, wire_rate)
        self._buf = bytearray()

    def _chunk_bytes(self, rate: int) -> int:
        n = ms_to_bytes(self.chunk_ms, rate)
        if n <= 0 or n % WIRE_ALIGN:
            raise ValueError(f"{self.chunk_ms} ms at {rate} Hz is not a multiple of 320 bytes")
        return n

    def set_rate(self, wire_rate: int) -> None:
        self.chunk_bytes = self._chunk_bytes(wire_rate)
        self.wire_rate = wire_rate
        self._rs = Resampler(SAMPLE_RATE, wire_rate)
        self._buf.clear()

    def add(self, pcm16_16k: bytes) -> list[bytes]:
        self._buf += self._rs.process(pcm16_16k)
        n = len(self._buf) // self.chunk_bytes * self.chunk_bytes
        out = [bytes(self._buf[i : i + self.chunk_bytes]) for i in range(0, n, self.chunk_bytes)]
        del self._buf[:n]
        return out

    def flush_final(self) -> bytes | None:
        """End of a playback: drain the resampler and return the remainder zero-padded
        to a multiple of 320 bytes (or None if nothing is buffered)."""
        if self.wire_rate != SAMPLE_RATE:
            self._buf += self._rs.flush()
            self._rs = Resampler(SAMPLE_RATE, self.wire_rate)
        if not self._buf:
            return None
        n = -(-len(self._buf) // WIRE_ALIGN) * WIRE_ALIGN
        rest = bytes(self._buf).ljust(n, b"\x00")
        self._buf.clear()
        return rest

    def clear(self) -> None:
        self._buf.clear()
        self._rs = Resampler(SAMPLE_RATE, self.wire_rate)


class ChunkGapDetector:
    """Watches the provider's media `chunk` counter; returns the missing span in ms."""

    def __init__(self) -> None:
        self._last: int | None = None

    def observe(self, chunk: int | str | None, chunk_ms: int) -> int:
        if chunk is None:
            return 0
        try:
            n = int(chunk)
        except (TypeError, ValueError):
            return 0
        last, self._last = self._last, n
        if last is None or n <= last + 1:
            return 0
        return (n - last - 1) * chunk_ms


class PlayoutTracker:
    """Emulates playback-done callbacks from bytes sent and a clock, for legs whose far
    end does not ack marks (FakeLeg, WebRtcLeg). Audio plays at real time from when it
    was sent, or from the end of the audio already queued, whichever is later.

    With autopoll the tracker schedules its own checks on the event loop (real-time
    clocks). Without it, the owner calls `poll()` after advancing a FakeClock.
    """

    def __init__(self, clock: Clock, fire: Callable[[str], None], *, autopoll: bool = True):
        self.clock = clock
        self._fire = fire
        self._autopoll = autopoll
        self._end_ms = float(clock.now_ms())
        self._pending: deque[tuple[float, str]] = deque()
        self._handle: asyncio.Handle | None = None

    def played(self, nbytes_16k: int) -> None:
        now = self.clock.now_ms()
        self._end_ms = max(self._end_ms, now) + nbytes_16k / _BYTES_PER_MS

    def mark(self, tag: str) -> None:
        self._pending.append((self._end_ms, tag))
        if self._end_ms <= self.clock.now_ms():
            asyncio.get_running_loop().call_soon(self.poll)
        elif self._autopoll:
            self._schedule()

    def poll(self) -> None:
        now = self.clock.now_ms()
        while self._pending and self._pending[0][0] <= now:
            _, tag = self._pending.popleft()
            self._fire(tag)

    def unplayed_bytes(self) -> int:
        ms = max(0.0, self._end_ms - self.clock.now_ms())
        return int(ms * _BYTES_PER_MS) // SAMPLE_WIDTH * SAMPLE_WIDTH

    def clear(self) -> int:
        """Forget queued audio and its marks; return how many bytes had not played."""
        unplayed = self.unplayed_bytes()
        self._pending.clear()
        self._end_ms = float(self.clock.now_ms())
        self._cancel()
        return unplayed

    def close(self) -> None:
        self._pending.clear()
        self._cancel()

    def _schedule(self) -> None:
        self._cancel()
        if not self._pending:
            return
        delay_ms = max(1.0, self._pending[0][0] - self.clock.now_ms())
        self._handle = asyncio.get_running_loop().call_later(delay_ms / 1000, self._tick)

    def _tick(self) -> None:
        self._handle = None
        self.poll()
        self._schedule()

    def _cancel(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

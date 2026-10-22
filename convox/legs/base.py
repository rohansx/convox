"""LegTransport: everything above the transport sees 20 ms PCM16@16k frames in, a sink
of audio out, and three control signals. Exotel and Plivo differ only inside their class.
Only the FloorController (session/floor.py) may call send/clear/mark."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Protocol, runtime_checkable

from convox.types import Side


@runtime_checkable
class LegTransport(Protocol):
    leg_id: Side

    def frames(self) -> AsyncIterator[bytes]:
        """640-byte PCM16@16k frames as they arrive. Ends when the leg closes."""
        ...

    async def send(self, pcm16_16k: bytes) -> None:
        """Queue audio for playout; the transport chunks by provider rules."""
        ...

    async def clear(self) -> None:
        """Drop queued, not-yet-played audio (barge-in)."""
        ...

    async def mark(self, tag: str) -> None:
        """Ask for an on_mark(tag) callback when all audio sent before this has played."""
        ...

    def on_mark(self, cb: Callable[[str], None]) -> None: ...

    def on_dtmf(self, cb: Callable[[str], None]) -> None: ...

    async def close(self) -> None: ...

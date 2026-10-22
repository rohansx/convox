"""Provider interfaces for the interpreter pipeline. Real implementations live in
convox/providers/, fakes in convox/providers/fakes.py. Every implementation must pass
the shared contract tests in tests/contract/."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class STTStream(Protocol):
    """One utterance of streaming recognition. Opened per utterance (or reset per
    utterance on a warm socket)."""

    async def push(self, frame: bytes) -> None:
        """Feed one PCM16@16k frame (any length, normally 640 bytes)."""
        ...

    async def end(self) -> str:
        """Signal end of utterance; return the final transcript ("" if nothing heard)."""
        ...

    def partials(self) -> AsyncIterator[str]:
        """Partial transcripts as they arrive (for the console). May yield nothing."""
        ...

    async def aclose(self) -> None: ...


@runtime_checkable
class STT(Protocol):
    name: str

    async def open(self, lang: str) -> STTStream:
        """lang is BCP-47 with region, e.g. "hi-IN"."""
        ...


@runtime_checkable
class MT(Protocol):
    name: str

    async def translate(self, text: str, src: str, tgt: str) -> str: ...


@runtime_checkable
class TTS(Protocol):
    name: str

    def synth(self, text: str, voice: str, lang: str) -> AsyncIterator[bytes]:
        """Stream PCM16 mono @16k chunks as they are synthesised."""
        ...


@runtime_checkable
class VAD(Protocol):
    def speech_prob(self, frame: bytes) -> float:
        """Probability [0, 1] that this 20 ms PCM16@16k frame contains speech."""
        ...

    def reset(self) -> None: ...

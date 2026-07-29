"""The caller's voice stack.

Cost methods are named per protocol rather than sharing one `cost_usd`: a single
class may act as both ends of the stack, and identically-named methods with
different signatures would silently shadow one another.

Convox needs two things to hold a spoken conversation: something to speak with
(the caller's TTS) and something to hear with (an independent STT used to score
the agent's audio, and to transcribe the agent where the platform exposes no
transcript of its own).

Both are plugin points. Real providers — Sarvam, ElevenLabs, Cartesia, OpenAI,
Deepgram, local Whisper/Piper — implement these two protocols and nothing else
in the system needs to know which one is configured.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class TTS(Protocol):
    """Turns caller text into audio.

    The text handed here is the trial's ground truth: it is recorded *before*
    synthesis, which is what makes every downstream recognition measurement an
    exact comparison rather than an estimate.
    """

    name: str

    def synthesize(self, text: str, *, sample_rate: int, rate: float = 1.0) -> np.ndarray:
        """Render `text` as float32 samples in [-1, 1]."""
        ...

    def synthesis_cost_usd(self, text: str) -> float:
        ...


@runtime_checkable
class STT(Protocol):
    """Turns audio back into text."""

    name: str

    def transcribe(self, samples: np.ndarray, *, sample_rate: int) -> str:
        ...

    def transcription_cost_usd(self, samples: np.ndarray, sample_rate: int) -> float:
        ...

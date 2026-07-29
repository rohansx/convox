"""Voice provider registry.

Real providers (Sarvam, ElevenLabs, Cartesia, OpenAI, Deepgram, local
Whisper/Piper) register here and are selected by name from `convox.yaml`. The
offline reference codec is registered by default so the audio path works with no
credentials at all — which is what makes it testable in CI.
"""

from __future__ import annotations

from collections.abc import Callable

from convox.voice.base import STT, TTS
from convox.voice.tone import ToneVoice

_TTS: dict[str, Callable[[], TTS]] = {}
_STT: dict[str, Callable[[], STT]] = {}


def register_tts(name: str, factory: Callable[[], TTS]) -> None:
    _TTS[name] = factory


def register_stt(name: str, factory: Callable[[], STT]) -> None:
    _STT[name] = factory


def build_tts(name: str) -> TTS:
    factory = _TTS.get(name)
    if factory is None:
        raise ValueError(f"unknown TTS provider {name!r} (registered: {', '.join(sorted(_TTS))})")
    return factory()


def build_stt(name: str) -> STT:
    factory = _STT.get(name)
    if factory is None:
        raise ValueError(f"unknown STT provider {name!r} (registered: {', '.join(sorted(_STT))})")
    return factory()


def tts_names() -> list[str]:
    return sorted(_TTS)


def stt_names() -> list[str]:
    return sorted(_STT)


register_tts("tone", ToneVoice)
register_stt("tone", ToneVoice)

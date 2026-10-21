"""Language identification: which language a caller is speaking when a side's language is
`auto` (real phone calls, where we often don't know it up front).

Shared contract between the session (which decides when to ask and what to do with the
answer) and the engines (dubhash/providers/sarvam_lid.py, FakeLID in providers/fakes.py).
An engine only ever answers with one of the `candidates` it was given, and answers
lang=None when it is not confident enough: the session then falls back (keypad menu, or
"not supported"). It never guesses a language we have not measured.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Measured end to end and allowed to go live. Anything else must fall back, never be claimed.
SUPPORTED_LANGS: tuple[str, ...] = ("hi-IN", "mr-IN", "gu-IN", "kn-IN", "ta-IN", "en-IN")

# Where a guess came from.
SOURCES = ("stt_final", "lid", "hint", "dtmf")

# Sarvam's realtime socket writes Odia as or-IN; REST and our config use od-IN.
_ALIASES = {"or-IN": "od-IN"}


@dataclass(frozen=True, slots=True)
class LangGuess:
    lang: str | None  # BCP-47 with region, one of the candidates; None = not sure
    confidence: float  # 0..1, the engine's own score (for None: the score it rejected)
    source: str  # "stt_final" | "lid" | "hint" | "dtmf"


@runtime_checkable
class LanguageID(Protocol):
    name: str

    async def identify(self, pcm16_16k: bytes, candidates: Sequence[str]) -> LangGuess:
        """Identify the language of an utterance (PCM16 mono 16 kHz), restricted to
        `candidates`. lang is None when unsure or when the language is not a candidate."""
        ...


def normalise(lang: str | None) -> str | None:
    """`hi-in` -> `hi-IN`, `or-IN` -> `od-IN`; None/empty -> None."""
    if not lang:
        return None
    base, _, region = str(lang).strip().partition("-")
    code = f"{base.lower()}-{region.upper()}" if region else base.lower()
    return _ALIASES.get(code, code)


def pick(
    lang: str | None,
    confidence: float,
    candidates: Sequence[str],
    *,
    threshold: float,
    source: str = "lid",
) -> LangGuess:
    """The guess an engine returns for a raw detection: `lang` only if it is one of the
    candidates and `confidence >= threshold`, else None (keeping the confidence)."""
    code = normalise(lang)
    allowed = {normalise(c) for c in candidates}
    if code is None or code not in allowed or confidence < threshold:
        return LangGuess(None, confidence, source)
    return LangGuess(code, confidence, source)

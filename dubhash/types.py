"""Shared value types. Every module imports these; none of them do I/O."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

Side = Literal["a", "b"]
Direction = Literal["ab", "ba"]


def other(side: Side) -> Side:
    return "b" if side == "a" else "a"


def direction(src: Side) -> Direction:
    return "ab" if src == "a" else "ba"


@dataclass(slots=True)
class Translation:
    """One interpreted utterance, handed from an Interpreter to the FloorController.

    `side` is the source side; the audio plays to the other leg. `audio` is a live
    iterator from TTS and can be consumed once.
    """

    side: Side
    seq: int
    src_text: str
    tgt_text: str
    audio: AsyncIterator[bytes]  # PCM16 mono @16k chunks, any length (mixer reframes)
    voice: str
    timings: dict[str, int] = field(default_factory=dict)  # t_* call-relative ms
    source_audio: bytes = b""  # the speaker's buffered utterance, ducked under the translation
    clipped_ms: int | None = None  # set by the controller when cut short
    # Chunked interpretation (L9): the chunks of one utterance share utterance_id (per
    # direction) and count chunk 0, 1, ...; final_chunk is False while more of the same
    # utterance is still coming (the speaker is still talking). None/0/True: a whole
    # utterance, as before.
    utterance_id: int | None = None
    chunk: int = 0
    final_chunk: bool = True

    @property
    def tag(self) -> str:
        return f"{self.side}:{self.seq}"


@dataclass(slots=True)
class Turn:
    """One row of the turn log. Field names match the `turns` table exactly."""

    call_id: str
    side: Side
    seq: int
    t_speech_start: int | None = None
    t_speech_end: int | None = None
    t_eou: int | None = None
    t_stt_final: int | None = None
    t_mt: int | None = None
    t_tts_ttfb: int | None = None
    t_play_start: int | None = None
    t_play_end: int | None = None
    src_text: str | None = None
    tgt_text: str | None = None
    backchannel: bool = False
    clipped_ms: int | None = None
    stt_provider: str | None = None
    mt_provider: str | None = None
    tts_provider: str | None = None
    voice: str | None = None
    # L9 (chunked interpretation): not columns of the `turns` table yet (the writer leaves
    # them out); in turn events and the recorder. t_utt_start: the utterance's speech start.
    utterance_id: int | None = None
    chunk: int | None = None
    t_utt_start: int | None = None


EventKind = Literal[
    "state",  # floor state change: {from, to, queue_depth}
    "turn",  # a Turn finished: {turn: Turn as dict}
    "partial",  # stt partial for the console: {side, text}
    "dtmf",  # {side, digit}
    "barge_in",  # {side, clipped_ms}
    "bypass",  # {side}
    "gap",  # {side, gap_ms}
    "provider_error",  # {provider, stage, error}
    "leg",  # {side, status}
    "call",  # {status}
    "line_check",  # {sides}: "aap line par hain?" played after 10 s silence
    "crosstalk",  # {side, reason: level|lang|echo, stage: frame|eou}: speech dropped
    "prompt_cut",  # {side, tag, sent_ms}: a translation cut the opening line short (L7)
    "lang",  # {side, lang, state, confidence, source[, menu]}: lang "auto" (language.py)
    "number_fix",  # {side, missing, replaced, appended}: the number read-back changed MT (L10)
]


@dataclass(slots=True)
class CallEvent:
    call_id: str
    ts_ms: int  # call-relative ms
    kind: EventKind
    payload: dict[str, Any] = field(default_factory=dict)

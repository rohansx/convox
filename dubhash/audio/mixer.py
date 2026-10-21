"""Builds the listener's outbound stream for one playback: earcon, then TTS at 0 dB with the
speaker's original mixed underneath at duck_db (-14 dB by default).

Decisions (open questions in docs/tech-spec/audio.md section 6):
- The earcon plays at translation start, immediately before the TTS audio.
- The ducked original is the speaker's buffered utterance replayed under the translation,
  aligned to the first TTS sample, not the speaker's live frames.

All sums are done in int32 and saturated to int16; nothing ever wraps.
"""

from __future__ import annotations

import numpy as np

_I16_MIN = -32768
_I16_MAX = 32767


def db_to_gain(db: float) -> float:
    return 10 ** (db / 20)


def _saturate(acc: np.ndarray) -> tuple[bytes, bool]:
    clipped = bool((acc > _I16_MAX).any() or (acc < _I16_MIN).any())
    return np.clip(acc, _I16_MIN, _I16_MAX).astype(np.int16).tobytes(), clipped


def mix(base: bytes, overlay: bytes, overlay_db: float) -> tuple[bytes, bool]:
    """base at unity + overlay at overlay_db. Output length == len(base); overlay is
    zero-padded or truncated. Returns (pcm16, clipped)."""
    b = np.frombuffer(base, dtype=np.int16)
    o = np.frombuffer(overlay, dtype=np.int16)[: len(b)]
    if len(o) == 0:
        return base, False
    acc = b.astype(np.int32)
    acc[: len(o)] += np.rint(o.astype(np.float32) * db_to_gain(overlay_db)).astype(np.int32)
    return _saturate(acc)


def passthrough_gain(frame: bytes, db: float = 0.0) -> bytes:
    """Bypass mode: forward a frame at db gain (0 dB returns it unchanged)."""
    if db == 0.0:
        return frame
    x = np.frombuffer(frame, dtype=np.int16).astype(np.float32) * db_to_gain(db)
    return _saturate(np.rint(x).astype(np.int32))[0]

"""The earcon: a soft 120 ms two-note "ding" (PCM16 mono 16 kHz, about -12 dBFS peak).

Generated once at import. Length is exactly 6 frames (3,840 bytes)."""

from __future__ import annotations

import numpy as np

from convox.audio.frames import FRAME_BYTES, SAMPLE_RATE, ms_to_bytes

EARCON_MS = 120
EARCON_PEAK_DBFS = -12.0
_NOTES_HZ = (880.0, 1320.0)  # A5 then E6: a gentle rising fifth
_FADE_MS = 10


def _note(hz: float, n: int, fade: int) -> np.ndarray:
    t = np.arange(n) / SAMPLE_RATE
    x = np.sin(2 * np.pi * hz * t)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, fade))  # raised cosine 0 -> 1
    env = np.ones(n)
    env[:fade] = ramp
    env[-fade:] = ramp[::-1]
    return x * env


def _build() -> bytes:
    total = ms_to_bytes(EARCON_MS) // 2
    half = total // 2
    fade = SAMPLE_RATE * _FADE_MS // 1000
    x = np.concatenate([_note(_NOTES_HZ[0], half, fade), _note(_NOTES_HZ[1], total - half, fade)])
    x *= 10 ** (EARCON_PEAK_DBFS / 20) * 32767
    pcm = np.rint(x).astype(np.int16).tobytes()
    assert len(pcm) % FRAME_BYTES == 0
    return pcm


EARCON: bytes = _build()

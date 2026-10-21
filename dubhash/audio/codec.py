"""G.711 mu-law <-> PCM16 (for Plivo's audio/x-mulaw;rate=8000 fallback)."""

from __future__ import annotations

import numpy as np

_BIAS = 0x84
_CLIP = 32635


def _build_decode_table() -> np.ndarray:
    u = ~np.arange(256, dtype=np.int32) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return np.where(sign != 0, -sample, sample).astype(np.int16)


_DECODE = _build_decode_table()


def ulaw_decode(data: bytes) -> bytes:
    return _DECODE[np.frombuffer(data, dtype=np.uint8)].tobytes()


def ulaw_encode(pcm16: bytes) -> bytes:
    x = np.frombuffer(pcm16, dtype=np.int16).astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    mag = np.minimum(np.abs(x), _CLIP) + _BIAS
    exponent = np.floor(np.log2(mag)).astype(np.int32) - 7
    exponent = np.clip(exponent, 0, 7)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    u = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return u.astype(np.uint8).tobytes()

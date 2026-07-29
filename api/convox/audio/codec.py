"""Telephony codec simulation.

Testing an agent over a clean 16 kHz WebRTC link tells you how it behaves in a
studio. Production is usually a phone: 8 kHz, µ-law or a-law companded, with the
quantisation noise and 300–3400 Hz band limit that come with it. Running the same
scenario through a codec is often the difference between a passing suite and
discovering that an agent's digit recognition falls apart on real calls.

G.711 is implemented directly rather than via the standard library's `audioop`,
which was removed in Python 3.13.
"""

from __future__ import annotations

import numpy as np

from convox.audio import pcm

_BIAS = 0x84
_CLIP = 32635
_ULAW_EXP = np.array(
    [0, 132, 396, 924, 1980, 4092, 8316, 16764],
    dtype=np.int32,
)


def _ulaw_encode(pcm16: np.ndarray) -> np.ndarray:
    sign = (pcm16 < 0).astype(np.uint8) * 0x80
    magnitude = np.minimum(np.abs(pcm16.astype(np.int32)), _CLIP) + _BIAS

    exponent = np.zeros_like(magnitude)
    for shift in range(7, 0, -1):
        mask = (magnitude >= (1 << (shift + 5))) & (exponent == 0)
        exponent[mask] = shift
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4).astype(np.uint8) | mantissa.astype(np.uint8))).astype(np.uint8)


def _ulaw_decode(encoded: np.ndarray) -> np.ndarray:
    value = (~encoded).astype(np.int32)
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    magnitude = _ULAW_EXP[exponent] + (mantissa << (exponent + 3))
    return np.where(sign != 0, -magnitude, magnitude).astype(np.int32)


_ALAW_SEG_END = np.array([0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF], dtype=np.int32)


def _alaw_encode(pcm16: np.ndarray) -> np.ndarray:
    sign = (pcm16 >= 0).astype(np.uint8) * 0x80
    magnitude = np.minimum(np.abs(pcm16.astype(np.int32)), 32635)

    segment = np.clip(np.searchsorted(_ALAW_SEG_END, magnitude, side="left"), 0, 7).astype(np.int32)

    mantissa = np.where(
        segment == 0,
        (magnitude >> 4) & 0x0F,
        (magnitude >> (segment + 3)) & 0x0F,
    )
    encoded = (sign | (segment << 4).astype(np.uint8) | mantissa.astype(np.uint8)).astype(np.uint8)
    return encoded ^ 0x55


def _alaw_decode(encoded: np.ndarray) -> np.ndarray:
    value = (encoded ^ 0x55).astype(np.int32)
    sign = value & 0x80
    segment = (value >> 4) & 0x07
    mantissa = value & 0x0F

    magnitude = np.where(
        segment == 0,
        (mantissa << 4) + 8,
        ((mantissa << 4) + 0x108) << (segment - 1),
    )
    return np.where(sign != 0, magnitude, -magnitude).astype(np.int32)


def _companding_roundtrip(samples: np.ndarray, law: str) -> np.ndarray:
    pcm16 = np.clip(samples, -1.0, 1.0) * pcm.PCM16_SCALE
    pcm16 = pcm16.astype(np.int32)
    if law == "ulaw":
        decoded = _ulaw_decode(_ulaw_encode(pcm16))
    else:
        decoded = _alaw_decode(_alaw_encode(pcm16))
    return (decoded.astype(np.float32) / pcm.PCM16_SCALE).astype(np.float32)


#: Codec name → (network sample rate, band limits, companding law).
PROFILES: dict[str, dict] = {
    "none": {"rate": None, "band": None, "law": None},
    "g711u": {"rate": 8000, "band": (300.0, 3400.0), "law": "ulaw"},
    "g711a": {"rate": 8000, "band": (300.0, 3400.0), "law": "alaw"},
    "g722": {"rate": 16000, "band": (50.0, 7000.0), "law": None},
    "gsm": {"rate": 8000, "band": (300.0, 3400.0), "law": "ulaw"},
}


def available() -> list[str]:
    return sorted(PROFILES)


def roundtrip(samples: np.ndarray, sample_rate: int, codec: str) -> np.ndarray:
    """Encode and decode through a codec, returning audio at the original rate.

    The damage is what matters: band limiting, resampling loss, and companding
    quantisation all survive the round trip, which is the point.
    """
    profile = PROFILES.get(codec)
    if profile is None:
        raise ValueError(f"unknown codec {codec!r} (available: {', '.join(available())})")
    if codec == "none" or len(samples) == 0:
        return samples

    working = samples
    network_rate = profile["rate"]
    if network_rate and network_rate != sample_rate:
        working = pcm.resample(working, sample_rate, network_rate)
        rate = network_rate
    else:
        rate = sample_rate

    if profile["band"]:
        low, high = profile["band"]
        working = pcm.bandpass(working, rate, low, min(high, rate * 0.49))

    if profile["law"]:
        working = _companding_roundtrip(working, profile["law"])

    if rate != sample_rate:
        working = pcm.resample(working, rate, sample_rate)
    return working.astype(np.float32)

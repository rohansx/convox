"""Synthetic audio for tests: voiced harmonic "speech" bursts, silence, and scripted
conversations, all PCM16 mono @16k."""

from __future__ import annotations

import numpy as np

from convox.audio.frames import FRAME_BYTES, SAMPLE_RATE


def _n(ms: int) -> int:
    return SAMPLE_RATE * ms // 1000


def silence(ms: int, noise: int = 0, seed: int = 0) -> bytes:
    """Digital silence, or low white noise of peak `noise` (int16 units)."""
    if noise <= 0:
        return b"\x00\x00" * _n(ms)
    rng = np.random.default_rng(seed)
    return rng.integers(-noise, noise + 1, _n(ms)).astype(np.int16).tobytes()


def tone(ms: int, hz: float = 440.0, amp: int = 8000) -> bytes:
    t = np.arange(_n(ms)) / SAMPLE_RATE
    return (np.sin(2 * np.pi * hz * t) * amp).astype(np.int16).tobytes()


def harmonic(ms: int, f0: float = 140.0, amp: int = 8000, harmonics: int = 8) -> bytes:
    """A steady voiced signal: f0 plus harmonics with 1/k amplitude roll-off."""
    t = np.arange(_n(ms)) / SAMPLE_RATE
    x = np.zeros_like(t)
    for k in range(1, harmonics + 1):
        if k * f0 >= SAMPLE_RATE / 2:
            break
        x += np.sin(2 * np.pi * k * f0 * t) / k
    peak = np.max(np.abs(x)) if len(x) else 0.0
    if peak:
        x /= peak
    return (x * amp).astype(np.int16).tobytes()


def speech(ms: int, f0: float = 140.0, amp: int = 8000, syllable_ms: int = 200) -> bytes:
    """Voiced harmonic burst with a syllable-rate amplitude envelope (never fully silent
    inside, so energy VADs see one continuous segment) and 20 ms fades at both ends."""
    x = np.frombuffer(harmonic(ms, f0, amp), dtype=np.int16).astype(np.float64)
    n = len(x)
    t = np.arange(n) / SAMPLE_RATE
    env = 0.65 + 0.35 * np.sin(2 * np.pi * (1000 / syllable_ms) * t)
    fade = min(_n(20), n // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade)
        env[:fade] *= ramp
        env[n - fade :] *= ramp[::-1]
    return (x * env).astype(np.int16).tobytes()


def conversation(script: list[tuple[str, int]], f0: float = 140.0) -> bytes:
    """Concatenate segments like [("silence", 300), ("speech", 1200), ...]."""
    out = bytearray()
    for kind, ms in script:
        if kind == "speech":
            out += speech(ms, f0=f0)
        elif kind == "silence":
            out += silence(ms)
        else:
            raise ValueError(f"unknown segment kind: {kind}")
    return bytes(out)


def frames(pcm: bytes, frame_bytes: int = FRAME_BYTES) -> list[bytes]:
    """Split PCM into 640-byte frames; the last partial frame is zero-padded."""
    out = [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]
    if out and len(out[-1]) < frame_bytes:
        out[-1] = out[-1].ljust(frame_bytes, b"\x00")
    return out


def dbfs(pcm: bytes) -> float:
    """RMS level of PCM16 in dBFS (-120 for digital silence)."""
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float64)
    if x.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(x * x)))
    return -120.0 if rms <= 0 else 20 * float(np.log10(rms / 32768.0))


def pink_noise(ms: int, level_dbfs: float = -40.0, seed: int = 0) -> bytes:
    """Pink (1/f power) noise at an RMS of level_dbfs: room noise, fans, traffic."""
    n = _n(ms)
    if n == 0:
        return b""
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    k = np.arange(len(spec), dtype=np.float64)
    k[0] = 1.0
    x = np.fft.irfft(spec / np.sqrt(k), n)
    x *= 10 ** (level_dbfs / 20) * 32768.0 / max(float(np.sqrt(np.mean(x * x))), 1e-12)
    return np.clip(x, -32768, 32767).astype(np.int16).tobytes()


def mix(a: bytes, b: bytes) -> bytes:
    """Sample-wise sum of two PCM16 signals (the shorter is zero-padded), saturated."""
    x = np.frombuffer(a, dtype=np.int16).astype(np.int32)
    y = np.frombuffer(b, dtype=np.int16).astype(np.int32)
    n = max(len(x), len(y))
    out = np.zeros(n, dtype=np.int32)
    out[: len(x)] += x
    out[: len(y)] += y
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def with_noise(pcm: bytes, snr_db: float, ref: bytes | None = None, seed: int = 0) -> bytes:
    """pcm with pink noise over its whole length, snr_db below the level of `ref` (the
    speech alone; default pcm itself)."""
    level = dbfs(ref if ref is not None else pcm) - snr_db
    return mix(pcm, pink_noise(len(pcm) * 1000 // (2 * SAMPLE_RATE), level, seed))

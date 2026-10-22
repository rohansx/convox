"""Crude speaker-gender guess from pitch, used only to pick voice_m / voice_f.

Median autocorrelation F0 over the voiced frames of the first 2 s; F0 below 165 Hz is
"m". When no voiced frame is found (silence, noise, too little audio) it returns "m":
the default is arbitrary, it just has to be deterministic. The threshold needs tuning
on the bench corpus (open question: spec Q5).
"""

from __future__ import annotations

from typing import Literal

import numpy as np

from convox.audio.frames import SAMPLE_RATE

Gender = Literal["m", "f"]

THRESHOLD_HZ = 165.0
DEFAULT: Gender = "m"
ANALYSIS_MS = 2000
WINDOW = 1024  # 64 ms: room for lags down to F0_MIN
HOP = 512
F0_MIN, F0_MAX = 70.0, 400.0
MIN_RMS = 32768 * 10 ** (-40 / 20)  # -40 dBFS
MIN_CORR = 0.5  # normalised autocorrelation peak needed to call a frame voiced
PEAK_TOLERANCE = 0.9  # take the shortest lag within 90% of the best (avoids octave drops)


def _frame_f0(x: np.ndarray) -> float | None:
    if np.sqrt(np.mean(x * x)) < MIN_RMS:
        return None
    x = x - x.mean()
    lag_lo = int(SAMPLE_RATE / F0_MAX)
    lag_hi = int(SAMPLE_RATE / F0_MIN)
    lags = np.arange(lag_lo, lag_hi + 1)
    n = len(x)
    spec = np.fft.rfft(x, 2 * n)
    acf = np.fft.irfft(spec * np.conj(spec))[: lag_hi + 1]
    cum = np.concatenate([[0.0], np.cumsum(x * x)])
    e_head = cum[n - lags]  # energy of x[:-lag]
    e_tail = cum[n] - cum[lags]  # energy of x[lag:]
    den = np.sqrt(e_head * e_tail)
    r = np.divide(acf[lags], den, out=np.zeros(len(lags)), where=den > 0)
    best = r.max()
    if best < MIN_CORR:
        return None
    # first local maximum that is close to the global best
    for i in range(1, len(r) - 1):
        if r[i] >= PEAK_TOLERANCE * best and r[i] >= r[i - 1] and r[i] >= r[i + 1]:
            break
    else:
        i = int(r.argmax())
    lag = float(lags[i])
    if 0 < i < len(r) - 1:  # parabolic interpolation around the peak
        denom = r[i - 1] - 2 * r[i] + r[i + 1]
        if denom != 0:
            lag += 0.5 * (r[i - 1] - r[i + 1]) / denom
    return SAMPLE_RATE / lag


def estimate_f0(pcm16: bytes) -> float | None:
    """Median F0 in Hz over voiced frames of the first 2 s, or None if none are voiced."""
    n = SAMPLE_RATE * ANALYSIS_MS // 1000
    x = np.frombuffer(pcm16[: (len(pcm16) // 2) * 2], dtype=np.int16)[:n].astype(np.float64)
    f0s = []
    for start in range(0, len(x) - WINDOW + 1, HOP):
        f0 = _frame_f0(x[start : start + WINDOW])
        if f0 is not None:
            f0s.append(f0)
    return float(np.median(f0s)) if f0s else None


def detect_gender(pcm16: bytes) -> Gender:
    f0 = estimate_f0(pcm16)
    if f0 is None:
        return DEFAULT
    return "m" if f0 < THRESHOLD_HZ else "f"

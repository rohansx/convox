"""PCM audio primitives.

Everything downstream — the channel simulator, the analysers, the voice codecs —
works on float32 samples in [-1, 1] at a known sample rate, and converts to
interleaved PCM16 bytes only at the transport boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PCM16_SCALE = 32767.0


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int = 16000
    #: Frame size used on the wire. 20 ms is the telephony convention.
    frame_ms: int = 20

    @property
    def frame_samples(self) -> int:
        return int(self.sample_rate * self.frame_ms / 1000)

    @property
    def frame_bytes(self) -> int:
        return self.frame_samples * 2


NARROWBAND = AudioFormat(sample_rate=8000)
WIDEBAND = AudioFormat(sample_rate=16000)


def to_pcm16(samples: np.ndarray) -> bytes:
    """float32 [-1, 1] → little-endian PCM16 bytes, clipped at full scale."""
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * PCM16_SCALE).astype("<i2").tobytes()


def from_pcm16(payload: bytes) -> np.ndarray:
    if not payload:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / PCM16_SCALE


def duration_ms(samples: np.ndarray, sample_rate: int) -> int:
    return int(len(samples) / sample_rate * 1000)


def silence(ms: int, sample_rate: int) -> np.ndarray:
    return np.zeros(int(sample_rate * ms / 1000), dtype=np.float32)


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear resampling.

    Deliberately simple: the point of downsampling here is to reproduce the
    bandwidth loss of a narrowband telephone channel, and linear interpolation
    plus the anti-alias filter below is faithful enough for that. Anything that
    needs archival-quality resampling should use a real resampler.
    """
    if source_rate == target_rate or len(samples) == 0:
        return samples
    if target_rate < source_rate:
        samples = lowpass(samples, source_rate, cutoff_hz=target_rate * 0.45)
    duration = len(samples) / source_rate
    target_len = max(1, int(duration * target_rate))
    source_x = np.linspace(0.0, duration, num=len(samples), endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, samples).astype(np.float32)


def lowpass(samples: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    """Zero-phase FFT brick-wall filter — used for anti-aliasing and band limits."""
    if len(samples) == 0:
        return samples
    spectrum = np.fft.rfft(samples)
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate)
    spectrum[freqs > cutoff_hz] = 0
    return np.fft.irfft(spectrum, n=len(samples)).astype(np.float32)


def bandpass(samples: np.ndarray, sample_rate: int, low_hz: float, high_hz: float) -> np.ndarray:
    if len(samples) == 0:
        return samples
    spectrum = np.fft.rfft(samples)
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate)
    spectrum[(freqs < low_hz) | (freqs > high_hz)] = 0
    return np.fft.irfft(spectrum, n=len(samples)).astype(np.float32)


def rms(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def db(value: float) -> float:
    return 20 * float(np.log10(max(value, 1e-12)))


def frames(samples: np.ndarray, frame_samples: int, *, pad: bool = True):
    """Split into fixed-size frames, zero-padding the tail when asked."""
    if frame_samples <= 0:
        raise ValueError("frame_samples must be positive")
    total = len(samples)
    count = -(-total // frame_samples) if pad else total // frame_samples
    for i in range(count):
        chunk = samples[i * frame_samples : (i + 1) * frame_samples]
        if pad and len(chunk) < frame_samples:
            chunk = np.pad(chunk, (0, frame_samples - len(chunk)))
        yield chunk


def concat(*parts: np.ndarray) -> np.ndarray:
    usable = [p for p in parts if len(p)]
    return np.concatenate(usable).astype(np.float32) if usable else np.zeros(0, dtype=np.float32)


def mix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Sum two signals, zero-extending the shorter one."""
    length = max(len(a), len(b))
    out = np.zeros(length, dtype=np.float32)
    out[: len(a)] += a
    out[: len(b)] += b
    return out


def fade(samples: np.ndarray, sample_rate: int, ms: float = 5.0) -> np.ndarray:
    """Raised-cosine fade in/out, so concatenated segments do not click."""
    n = min(int(sample_rate * ms / 1000), len(samples) // 2)
    if n <= 0:
        return samples
    ramp = 0.5 * (1 - np.cos(np.linspace(0, np.pi, n)))
    out = samples.copy()
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out

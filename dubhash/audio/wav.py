"""WAV fixture I/O (stdlib wave). read_wav always returns PCM16 mono 16 kHz."""

from __future__ import annotations

import io
import wave
from os import PathLike

import numpy as np

from dubhash.audio.frames import SAMPLE_RATE, SAMPLE_WIDTH
from dubhash.audio.resample import Resampler

type StrPath = str | PathLike[str]


def read_wav(path: StrPath) -> bytes:
    """Read a 16-bit WAV; downmix to mono (mean of channels) and resample to 16 kHz."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != SAMPLE_WIDTH:
            raise ValueError(f"{path}: only 16-bit PCM is supported, got {8 * w.getsampwidth()}")
        channels, rate = w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2")
    if channels > 1:
        x = np.rint(x.reshape(-1, channels).astype(np.float32).mean(axis=1)).astype(np.int16)
    pcm = x.astype(np.int16).tobytes()
    if rate != SAMPLE_RATE:
        r = Resampler(rate, SAMPLE_RATE)
        pcm = r.process(pcm) + r.flush()
    return pcm


def _write(fh: wave.Wave_write, pcm16: bytes, rate: int) -> None:
    fh.setnchannels(1)
    fh.setsampwidth(SAMPLE_WIDTH)
    fh.setframerate(rate)
    fh.writeframes(pcm16)


def write_wav(path: StrPath, pcm16: bytes, rate: int = SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as w:
        _write(w, pcm16, rate)


def pcm_to_wav_bytes(pcm16: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        _write(w, pcm16, rate)
    return buf.getvalue()

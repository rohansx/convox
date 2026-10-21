"""Streaming PCM16 resampling with soxr. Used only at the leg boundary (8k <-> 16k)."""

from __future__ import annotations

import numpy as np
import soxr


class Resampler:
    def __init__(self, in_rate: int, out_rate: int) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self._passthrough = in_rate == out_rate
        self._stream = None if self._passthrough else soxr.ResampleStream(
            in_rate, out_rate, 1, dtype="int16", quality="HQ"
        )

    def process(self, pcm16: bytes) -> bytes:
        if self._passthrough:
            return pcm16
        x = np.frombuffer(pcm16, dtype=np.int16)
        return self._stream.resample_chunk(x).tobytes()

    def flush(self) -> bytes:
        if self._passthrough:
            return b""
        return self._stream.resample_chunk(np.zeros(0, dtype=np.int16), last=True).tobytes()

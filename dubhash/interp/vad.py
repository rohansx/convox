"""Silero VAD v5 (onnx) behind the VAD Protocol. One instance per leg: it holds the
model's recurrent state.

The v5 graph takes `input` (1, 64 + 512) float32 in [-1, 1] — the last 64 samples of
the previous window as context, then 512 new samples — plus `state` (2, 1, 128) and
`sr` (int64 scalar). Windows are fed back to back without overlap, because the recurrent
state assumes contiguous audio; audio.md's "512-sample window on a 20 ms hop" would feed
overlapping audio into the RNN. Incoming 20 ms frames (320 samples) are buffered, and
speech_prob returns the probability of the latest completed window (0.0 before the
first one).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from dubhash.audio.frames import SAMPLE_RATE, SAMPLE_WIDTH

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "silero_vad.onnx"
WINDOW = 512
CONTEXT = 64


class VADModelError(RuntimeError):
    pass


class SileroVAD:
    def __init__(self, model_path: Path = DEFAULT_MODEL_PATH, threads: int = 1) -> None:
        if not Path(model_path).exists():
            raise VADModelError(
                f"silero VAD model not found at {model_path}; download silero_vad.onnx (v5) "
                "from github.com/snakers4/silero-vad or use EnergyVAD"
            )
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = threads
            opts.inter_op_num_threads = 1
            self._session = ort.InferenceSession(
                str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
            )
        except Exception as e:  # corrupt file, wrong onnxruntime, ...
            raise VADModelError(f"could not load silero VAD model at {model_path}: {e}") from e
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._prob = 0.0
        self.windows_run = 0

    def speech_prob(self, frame: bytes) -> float:
        n = len(frame) // SAMPLE_WIDTH
        x = np.frombuffer(frame[: n * SAMPLE_WIDTH], dtype=np.int16).astype(np.float32) / 32768.0
        buf = np.concatenate([self._pending, x])
        full = len(buf) // WINDOW * WINDOW
        for start in range(0, full, WINDOW):
            self._prob = self._run(buf[start : start + WINDOW])
        self._pending = buf[full:]
        return self._prob

    def _run(self, window: np.ndarray) -> float:
        inp = np.concatenate([self._context, window])[np.newaxis, :]
        out, self._state = self._session.run(
            None, {"input": inp, "state": self._state, "sr": self._sr}
        )
        self._context = window[-CONTEXT:]
        self.windows_run += 1
        return float(out[0][0])

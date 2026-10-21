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

import math
from collections import deque
from pathlib import Path

import numpy as np

from dubhash.audio.frames import SAMPLE_RATE, SAMPLE_WIDTH
from dubhash.interp.contracts import VAD

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


class NoiseFloorGate:
    """VAD wrapper (L7): a frame is speech only when the inner VAD says so AND its RMS is
    more than margin_db above an adaptive noise floor. Otherwise speech_prob returns 0.0.

    Real rooms broke EOU: a TV, other people talking or phone AGC pumping the noise up in
    every pause keep Silero above 0.5 between the caller's sentences, so the 550 ms of
    silence never came. The floor is the running level of the room:
    - it follows frames judged non-speech (EMA, alpha per frame, ~1 s time constant);
    - if nearly every frame of the last few seconds is above floor + margin (no pauses at
      all: the noise itself got louder, AGC), it jumps to the window's quiet_pct
      percentile, which for speech over noise is the noise in the pauses;
    - digital silence (the phone page sends zeros while it plays) is ignored;
    - it is clamped to [min_db, max_db], so loud speech is never gated entirely.
    Speech must be loud for onset_frames frames in a row to open the gate (a lone noise
    frame over the threshold is not a word); the Endpointer's pre-roll keeps that audio.
    reset() resets the inner VAD only: the room does not change between utterances.
    """

    def __init__(
        self,
        inner: VAD,
        *,
        margin_db: float = 6.0,
        threshold: float = 0.5,
        init_db: float = -60.0,
        min_db: float = -70.0,
        max_db: float = -25.0,
        alpha: float = 0.05,
        window_frames: int = 250,  # 5 s of 20 ms frames
        min_window_frames: int = 50,
        quiet_pct: float = 10.0,
        zero_db: float = -85.0,
        onset_frames: int = 2,
    ) -> None:
        self.inner = inner
        self.margin_db = margin_db
        self.threshold = threshold
        self.min_db, self.max_db = min_db, max_db
        self.alpha = alpha
        self.min_window_frames = min_window_frames
        self.quiet_pct = quiet_pct
        self.zero_db = zero_db
        self.onset_frames = onset_frames
        self._loud_run = 0
        self.floor_db = init_db
        self.last_db = -120.0
        self.gated = 0  # frames the inner VAD called speech that the floor turned down
        self.resets = 0
        self._window: deque[float] = deque(maxlen=window_frames)

    def speech_prob(self, frame: bytes) -> float:
        p = self.inner.speech_prob(frame)
        db = self.last_db = frame_dbfs(frame)
        if db <= self.zero_db:
            return 0.0
        loud = db > self.floor_db + self.margin_db
        self._loud_run = self._loud_run + 1 if loud else 0
        if not (loud and p > self.threshold):
            self.floor_db += self.alpha * (db - self.floor_db)
        self._window.append(db)
        if len(self._window) >= self.min_window_frames:
            quiet = float(np.percentile(np.fromiter(self._window, float), self.quiet_pct))
            if quiet > self.floor_db + self.margin_db:
                self.floor_db = quiet
                self.resets += 1
        self.floor_db = min(self.max_db, max(self.min_db, self.floor_db))
        open_ = self._loud_run >= self.onset_frames
        if not open_ and p > self.threshold:
            self.gated += 1
        return p if open_ else 0.0

    def reset(self) -> None:
        self.inner.reset()


def frame_dbfs(frame: bytes) -> float:
    """RMS level of a PCM16 frame in dBFS (-120 for digital silence)."""
    n = len(frame) // SAMPLE_WIDTH
    if n == 0:
        return -120.0
    x = np.frombuffer(frame[: n * SAMPLE_WIDTH], dtype=np.int16).astype(np.float64)
    ms = float(np.mean(x * x))
    return -120.0 if ms <= 0 else 10 * math.log10(ms) - 20 * math.log10(32768.0)

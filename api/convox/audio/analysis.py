"""Audio-layer analysis — the failures a transcript cannot show you.

An agent can produce a perfectly correct sentence and still fail the call: the
audio clips, the last word is cut off by a teardown race, the TTS glitches into
noise, or the reply never arrives as sound at all. None of that is visible in
text, which is why transcript-only evaluation misses a large share of real
production failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from convox.audio import pcm

#: Frame size for energy analysis. Short enough to locate speech edges to within
#: a barge-in measurement's tolerance.
ANALYSIS_FRAME_MS = 10

#: Absolute energy floor below which nothing counts as speech, regardless of how
#: the adaptive threshold lands. Without it, a silent line's own noise gets
#: normalised into "speech".
ABSOLUTE_SPEECH_RMS = 0.01


@dataclass
class SpeechSegment:
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class AudioReport:
    duration_ms: int = 0
    rms: float = 0.0
    peak: float = 0.0
    snr_db: float | None = None
    clipping_ratio: float = 0.0
    silence_ratio: float = 1.0
    artifact_score: float = 0.0
    truncated: bool = False
    segments: list[SpeechSegment] = field(default_factory=list)

    @property
    def speech_ms(self) -> int:
        return sum(s.duration_ms for s in self.segments)


def frame_energy(samples: np.ndarray, sample_rate: int, frame_ms: int = ANALYSIS_FRAME_MS) -> np.ndarray:
    frame_samples = max(1, int(sample_rate * frame_ms / 1000))
    usable = len(samples) - (len(samples) % frame_samples)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    reshaped = samples[:usable].reshape(-1, frame_samples)
    return np.sqrt(np.mean(np.square(reshaped, dtype=np.float64), axis=1)).astype(np.float32)


def detect_speech(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_ms: int = ANALYSIS_FRAME_MS,
    min_speech_ms: int = 60,
    max_gap_ms: int = 250,
) -> list[SpeechSegment]:
    """Energy-based voice activity detection.

    The threshold adapts to the noise floor rather than being fixed, so a caller
    on a noisy street is not reported as talking continuously.
    """
    energies = frame_energy(samples, sample_rate, frame_ms)
    if len(energies) == 0:
        return []

    floor = float(np.percentile(energies, 10))
    ceiling = float(np.percentile(energies, 95))
    if ceiling < ABSOLUTE_SPEECH_RMS:
        return []
    # A steady, loud signal is continuous speech — not an absence of it. Keying
    # only off dynamic range would report exactly the opposite.
    threshold = max(floor + (ceiling - floor) * 0.15, ABSOLUTE_SPEECH_RMS * 0.5)

    active = energies > threshold
    segments: list[SpeechSegment] = []
    start: int | None = None
    gap_frames = max(1, max_gap_ms // frame_ms)
    silent_run = 0

    for index, is_active in enumerate(active):
        if is_active:
            if start is None:
                start = index
            silent_run = 0
        elif start is not None:
            silent_run += 1
            if silent_run >= gap_frames:
                end = index - silent_run + 1
                segments.append(SpeechSegment(start * frame_ms, end * frame_ms))
                start = None
                silent_run = 0

    if start is not None:
        segments.append(SpeechSegment(start * frame_ms, len(active) * frame_ms))

    return [s for s in segments if s.duration_ms >= min_speech_ms]


def estimate_snr_db(samples: np.ndarray, sample_rate: int) -> float | None:
    """Speech energy versus the noise floor between utterances."""
    energies = frame_energy(samples, sample_rate)
    if len(energies) < 10:
        return None
    noise = float(np.percentile(energies, 10))
    speech = float(np.percentile(energies, 90))
    if noise <= 0 or speech <= noise:
        return None
    return round(pcm.db(speech) - pcm.db(noise), 2)


def clipping_ratio(samples: np.ndarray, threshold: float = 0.995) -> float:
    if len(samples) == 0:
        return 0.0
    return round(float(np.mean(np.abs(samples) >= threshold)), 5)


def detect_truncation(samples: np.ndarray, sample_rate: int, *, tail_ms: int = 40) -> bool:
    """Did the audio stop mid-utterance rather than decaying naturally?

    A common and entirely transcript-invisible bug: the call tears down before the
    final TTS chunk plays, so the text says one thing and the caller heard another.
    """
    if len(samples) < sample_rate // 10:
        return False
    tail_samples = max(1, int(sample_rate * tail_ms / 1000))
    tail_rms = pcm.rms(samples[-tail_samples:])
    overall_rms = pcm.rms(samples)
    if overall_rms <= 0:
        return False
    # Speech releases into silence at the end of an utterance. Audio that is still
    # at a substantial fraction of its own loudness on the final frames stopped
    # because something cut it, not because the speaker finished.
    return bool(tail_rms > max(overall_rms * 0.5, ABSOLUTE_SPEECH_RMS))


def artifact_score(samples: np.ndarray, sample_rate: int) -> float:
    """Detect garbled synthesis via frame-to-frame spectral discontinuity.

    Natural speech evolves smoothly; a TTS glitch produces sudden spectral jumps
    that a listener hears as a burst of noise and a transcript never records.
    """
    frame_samples = max(64, int(sample_rate * 0.02))
    usable = len(samples) - (len(samples) % frame_samples)
    if usable < frame_samples * 4:
        return 0.0

    spectra = np.abs(np.fft.rfft(samples[:usable].reshape(-1, frame_samples), axis=1))
    norms = np.linalg.norm(spectra, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = spectra / norms

    distances = np.linalg.norm(np.diff(normalized, axis=0), axis=1)
    if len(distances) == 0:
        return 0.0
    # Only frames that are loud enough to be audible count as artefacts.
    energies = frame_energy(samples[:usable], sample_rate, frame_ms=int(frame_samples / sample_rate * 1000))
    loud = energies[1:] > max(float(np.percentile(energies, 50)), 1e-4)
    if not loud.any():
        return 0.0
    return round(float(np.percentile(distances[loud], 95)), 4)


def analyse(samples: np.ndarray, sample_rate: int) -> AudioReport:
    if len(samples) == 0:
        return AudioReport()

    segments = detect_speech(samples, sample_rate)
    total_ms = pcm.duration_ms(samples, sample_rate)
    speech_ms = sum(s.duration_ms for s in segments)

    return AudioReport(
        duration_ms=total_ms,
        rms=round(pcm.rms(samples), 5),
        peak=round(float(np.max(np.abs(samples))), 5),
        snr_db=estimate_snr_db(samples, sample_rate),
        clipping_ratio=clipping_ratio(samples),
        silence_ratio=round(1.0 - (speech_ms / total_ms if total_ms else 0.0), 4),
        artifact_score=artifact_score(samples, sample_rate),
        truncated=detect_truncation(samples, sample_rate),
        segments=segments,
    )

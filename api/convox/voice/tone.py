"""An offline, deterministic voice — the reference codec.

The problem this solves: an audio testing pipeline that can only be exercised with
a paid API key is a pipeline nobody can trust, because its own tests cannot run.

`ToneVoice` is a matched TTS/STT pair that encodes text as a sequence of pure
tones inside the telephony band and decodes it by spectral peak-picking. That
makes the entire audio path — synthesis, channel degradation, recognition,
scoring — runnable in CI with no network and no credentials, while degrading the
way a real recogniser degrades: add noise, drop packets, or squeeze the signal
through a narrowband codec, and the decoder starts making substitution errors, so
measured word error rate rises for the same reasons it would in production.

It is **not** speech, and it is not a substitute for testing against real ASR. It
is a reference instrument: fully deterministic, so a failure means the pipeline
changed rather than that a model felt different today. Real providers implement
the same two protocols and drop straight in.
"""

from __future__ import annotations

import numpy as np

from convox.audio import pcm

#: Symbols the codec can carry. Anything else collapses to a space, the way an
#: ASR drops what it cannot resolve.
ALPHABET = " abcdefghijklmnopqrstuvwxyz0123456789.,?'-"

#: Symbol tones live inside the 300–3400 Hz telephony passband so they survive
#: G.711 and 8 kHz resampling intact.
BASE_HZ = 500.0
SPACING_HZ = 70.0

#: The sync tone sits below the symbol range so it can be isolated by band-pass
#: filtering even when the utterance is buried in noise.
SYNC_HZ = 350.0
SYNC_MS = 120

#: Real synthesis releases into a short silence at the end of an utterance.
#: Emitting one matters for more than realism: it is the signal that separates a
#: complete reply from one truncated by a teardown race.
TAIL_SILENCE_MS = 150

#: 80 ms per symbol works out to roughly 150 words per minute — close enough to
#: natural pace that utterance durations, and therefore timing metrics, are
#: realistic rather than arbitrary.
SYMBOL_MS = 80


def symbol_frequency(index: int) -> float:
    return BASE_HZ + index * SPACING_HZ


_FREQUENCIES = np.array([symbol_frequency(i) for i in range(len(ALPHABET))])
_MAX_HZ = float(_FREQUENCIES[-1])


def normalize_text(text: str) -> str:
    lowered = text.casefold()
    return "".join(c if c in ALPHABET else " " for c in lowered)


class ToneVoice:
    """Matched encoder/decoder. One instance can act as both TTS and STT."""

    name = "tone"

    def __init__(self, *, symbol_ms: int = SYMBOL_MS, amplitude: float = 0.6) -> None:
        self.symbol_ms = symbol_ms
        self.amplitude = amplitude

    # ── TTS ──

    def synthesize(self, text: str, *, sample_rate: int, rate: float = 1.0) -> np.ndarray:
        symbol_ms = max(10, int(self.symbol_ms / max(rate, 0.1)))
        symbol_samples = int(sample_rate * symbol_ms / 1000)

        parts = [self._tone(SYNC_HZ, int(sample_rate * SYNC_MS / 1000), sample_rate)]
        for char in normalize_text(text):
            index = ALPHABET.index(char)
            parts.append(self._tone(symbol_frequency(index), symbol_samples, sample_rate))
        parts.append(pcm.silence(TAIL_SILENCE_MS, sample_rate))
        return pcm.concat(*parts) * self.amplitude

    def _tone(self, frequency: float, length: int, sample_rate: int) -> np.ndarray:
        if length <= 0:
            return np.zeros(0, dtype=np.float32)
        t = np.arange(length) / sample_rate
        return pcm.fade(np.sin(2 * np.pi * frequency * t).astype(np.float32), sample_rate, ms=3.0)

    def synthesis_cost_usd(self, text: str) -> float:
        """Free: the reference codec runs locally."""
        return 0.0

    # ── STT ──

    def transcribe(self, samples: np.ndarray, *, sample_rate: int, rate: float = 1.0) -> str:
        if len(samples) == 0:
            return ""

        start = self._find_sync(samples, sample_rate)
        if start is None:
            return ""

        symbol_ms = max(10, int(self.symbol_ms / max(rate, 0.1)))
        symbol_samples = int(sample_rate * symbol_ms / 1000)
        if symbol_samples <= 0:
            return ""

        cursor = start + int(sample_rate * SYNC_MS / 1000)
        # Bound decoding to where the signal actually ends. Codec ring-out and
        # quantisation noise leave enough energy in the trailing silence to be
        # mistaken for one more symbol, which would append a phantom character to
        # every utterance that passed through a telephony codec.
        end = self._signal_end(samples, sample_rate)
        decoded: list[str] = []

        while cursor + symbol_samples <= end:
            window = samples[cursor : cursor + symbol_samples]
            cursor += symbol_samples
            char = self._decode_symbol(window, sample_rate)
            if char is None:
                # A dropped packet leaves a hole; record it as a lost symbol
                # rather than inventing a character.
                decoded.append(" ")
            else:
                decoded.append(char)

        return "".join(decoded).strip()

    def _signal_end(self, samples: np.ndarray, sample_rate: int) -> int:
        """Index just past the last frame carrying real signal."""
        frame = max(1, int(sample_rate * 0.01))
        energies = np.array(
            [pcm.rms(samples[i : i + frame]) for i in range(0, len(samples) - frame, frame)]
        )
        if len(energies) == 0:
            return len(samples)
        peak = float(np.max(energies))
        if peak <= 0:
            return len(samples)
        voiced = np.flatnonzero(energies > peak * 0.2)
        if not len(voiced):
            return len(samples)
        return min(len(samples), int((voiced[-1] + 1) * frame))

    def _find_sync(self, samples: np.ndarray, sample_rate: int) -> int | None:
        """Locate the preamble so symbol framing is aligned despite leading silence."""
        band = pcm.bandpass(samples, sample_rate, SYNC_HZ - 60, SYNC_HZ + 60)
        frame = max(1, int(sample_rate * 0.01))
        energies = np.array([pcm.rms(band[i : i + frame]) for i in range(0, len(band) - frame, frame)])
        if len(energies) == 0:
            return None
        peak = float(np.max(energies))
        if peak <= 1e-4:
            return None
        above = np.flatnonzero(energies > peak * 0.5)
        return int(above[0] * frame) if len(above) else None

    def _decode_symbol(self, window: np.ndarray, sample_rate: int) -> str | None:
        if pcm.rms(window) < 1e-4:
            return None

        spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
        freqs = np.fft.rfftfreq(len(window), d=1.0 / sample_rate)

        usable = (freqs >= BASE_HZ - SPACING_HZ) & (freqs <= _MAX_HZ + SPACING_HZ)
        if not usable.any():
            return None

        peak_hz = float(freqs[usable][int(np.argmax(spectrum[usable]))])
        index = int(np.argmin(np.abs(_FREQUENCIES - peak_hz)))
        return ALPHABET[index]

    def transcription_cost_usd(self, samples: np.ndarray, sample_rate: int) -> float:
        return 0.0

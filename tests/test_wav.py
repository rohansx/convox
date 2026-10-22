import io
import wave
from pathlib import Path

import numpy as np
import pytest

from convox.audio.wav import pcm_to_wav_bytes, read_wav, write_wav


def _sine(rate: int, ms: int, hz: float = 440.0, amp: int = 8000) -> np.ndarray:
    t = np.arange(rate * ms // 1000) / rate
    return (np.sin(2 * np.pi * hz * t) * amp).astype(np.int16)


def _write_raw(path: Path, samples: np.ndarray, rate: int, channels: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())


def test_roundtrip_16k_mono(tmp_path: Path):
    pcm = _sine(16000, 200).tobytes()
    p = tmp_path / "a.wav"
    write_wav(p, pcm)
    assert read_wav(p) == pcm
    with wave.open(str(p), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)


def test_read_8k_resamples_to_16k(tmp_path: Path):
    p = tmp_path / "8k.wav"
    _write_raw(p, _sine(8000, 1000), 8000, 1)
    out = np.frombuffer(read_wav(p), dtype=np.int16)
    assert abs(len(out) - 16000) < 50
    # still a 440 Hz tone at 16 kHz: compare to a fresh reference away from the edges
    ref = _sine(16000, 1000).astype(float)
    n = min(len(out), len(ref))
    mid = slice(2000, n - 2000)
    corr = np.corrcoef(out[:n][mid].astype(float), ref[:n][mid])[0, 1]
    assert abs(corr) > 0.95


def test_write_8k_then_read_back_16k(tmp_path: Path):
    p = tmp_path / "w8k.wav"
    write_wav(p, _sine(8000, 500).tobytes(), rate=8000)
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == 8000
    assert abs(len(read_wav(p)) // 2 - 8000) < 50


def test_read_stereo_downmixes(tmp_path: Path):
    left = np.full(320, 1000, dtype=np.int16)
    right = np.full(320, 3000, dtype=np.int16)
    p = tmp_path / "st.wav"
    _write_raw(p, np.stack([left, right], axis=1).reshape(-1), 16000, 2)
    out = np.frombuffer(read_wav(p), dtype=np.int16)
    assert len(out) == 320 and np.all(out == 2000)


def test_read_rejects_non_16bit(tmp_path: Path):
    p = tmp_path / "u8.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(16000)
        w.writeframes(b"\x80" * 100)
    with pytest.raises(ValueError):
        read_wav(p)


def test_pcm_to_wav_bytes():
    pcm = _sine(16000, 20).tobytes()
    data = pcm_to_wav_bytes(pcm)
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        assert w.readframes(w.getnframes()) == pcm

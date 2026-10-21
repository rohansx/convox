import numpy as np

from dubhash.audio.codec import ulaw_decode, ulaw_encode
from dubhash.audio.frames import FRAME_BYTES, Reframer
from dubhash.audio.resample import Resampler


def test_reframer_exact_frames_and_padding():
    r = Reframer()
    assert r.push(b"\x01" * 1000) == [b"\x01" * FRAME_BYTES]
    assert r.pending == 360
    frames = r.push(b"\x02" * 600)
    assert len(frames) == 1 and len(frames[0]) == FRAME_BYTES
    tail = r.flush()
    assert tail is not None and len(tail) == FRAME_BYTES and tail.endswith(b"\x00")
    assert r.flush() is None


def _sine(rate: int, ms: int, hz: float = 440.0) -> bytes:
    t = np.arange(rate * ms // 1000) / rate
    return (np.sin(2 * np.pi * hz * t) * 8000).astype(np.int16).tobytes()


def test_resampler_8k_to_16k_doubles_length():
    r = Resampler(8000, 16000)
    out = r.process(_sine(8000, 1000)) + r.flush()
    assert abs(len(out) // 2 - 16000) < 50


def test_resampler_passthrough():
    r = Resampler(16000, 16000)
    x = _sine(16000, 20)
    assert r.process(x) == x and r.flush() == b""


def test_ulaw_roundtrip_close():
    x = _sine(8000, 100)
    y = np.frombuffer(ulaw_decode(ulaw_encode(x)), dtype=np.int16).astype(int)
    ref = np.frombuffer(x, dtype=np.int16).astype(int)
    assert len(y) == len(ref)
    assert np.max(np.abs(y - ref)) < 400


def test_pink_noise_level_and_spectrum():
    from tests.audio_fixtures import dbfs, pink_noise

    pcm = pink_noise(2000, -35.0)
    assert abs(dbfs(pcm) - -35.0) < 0.5
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    p = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / 16000)
    low, high = p[(f > 100) & (f < 200)].mean(), p[(f > 3200) & (f < 6400)].mean()
    assert 10 * np.log10(low / high) > 10  # ~3 dB per octave: about 15 dB over 5 octaves


def test_with_noise_sets_the_snr_against_the_speech():
    from tests.audio_fixtures import dbfs, silence, speech, with_noise

    sp = speech(1000)
    noisy = with_noise(silence(500) + sp + silence(500), 15.0, ref=sp)
    noise_only = noisy[:16000]  # the first 500 ms
    assert abs((dbfs(sp) - dbfs(noise_only)) - 15.0) < 1.5

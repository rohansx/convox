from pathlib import Path

import pytest

from convox.audio.frames import FRAME_BYTES
from convox.interp.contracts import VAD
from convox.interp.vad import DEFAULT_MODEL_PATH, SileroVAD, VADModelError
from tests.audio_fixtures import frames, silence, speech

needs_model = pytest.mark.skipif(not DEFAULT_MODEL_PATH.exists(), reason="silero model missing")


@needs_model
def test_satisfies_protocol():
    assert isinstance(SileroVAD(), VAD)


@needs_model
def test_silence_is_low():
    vad = SileroVAD()
    probs = [vad.speech_prob(f) for f in frames(silence(1000))]
    assert max(probs) < 0.2


@needs_model
def test_low_noise_is_low():
    vad = SileroVAD()
    probs = [vad.speech_prob(f) for f in frames(silence(1000, noise=40))]
    assert max(probs) < 0.2


@needs_model
def test_probs_in_range_and_windows_counted():
    vad = SileroVAD()
    fs = frames(silence(200) + speech(1000) + silence(200))
    probs = [vad.speech_prob(f) for f in fs]
    assert all(0.0 <= p <= 1.0 for p in probs)
    # 1400 ms = 22400 samples -> 43 full 512-sample windows
    assert vad.windows_run == 22400 // 512


@needs_model
def test_first_frame_before_a_full_window_returns_zero():
    vad = SileroVAD()
    assert vad.speech_prob(silence(20)) == 0.0
    assert vad.windows_run == 0


@needs_model
def test_accepts_odd_chunk_sizes():
    vad = SileroVAD()
    pcm = silence(500)
    for i in range(0, len(pcm), 222):
        vad.speech_prob(pcm[i : i + 222])
    assert vad.windows_run == 8000 // 512


@needs_model
def test_reset_is_deterministic():
    vad = SileroVAD()
    fs = frames(speech(600) + silence(200))
    first = [vad.speech_prob(f) for f in fs]
    vad.reset()
    assert vad.windows_run == 0
    second = [vad.speech_prob(f) for f in fs]
    assert first == pytest.approx(second)


def test_missing_model_raises_clear_error(tmp_path: Path):
    with pytest.raises(VADModelError, match="silero"):
        SileroVAD(model_path=tmp_path / "nope.onnx")


@needs_model
def test_frame_cost_is_small():
    import time

    vad = SileroVAD()
    f = speech(20)
    assert len(f) == FRAME_BYTES
    t0 = time.perf_counter()
    for _ in range(50):
        vad.speech_prob(f)
    assert (time.perf_counter() - t0) / 50 < 0.01  # well under the 20 ms frame budget


@needs_model
def test_voiced_harmonic_burst_scores_higher_than_silence():
    vad = SileroVAD()
    sil = [vad.speech_prob(f) for f in frames(silence(300))]
    voiced = [vad.speech_prob(f) for f in frames(speech(1500))][10:]
    assert sum(voiced) / len(voiced) > 0.5 > max(sil)

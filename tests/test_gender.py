import pytest

from convox.interp.gender import detect_gender, estimate_f0
from tests.audio_fixtures import harmonic, silence, speech, tone


@pytest.mark.parametrize("f0", [100.0, 120.0, 140.0])
def test_low_pitch_is_male(f0):
    assert detect_gender(harmonic(1500, f0=f0)) == "m"


@pytest.mark.parametrize("f0", [200.0, 220.0, 260.0])
def test_high_pitch_is_female(f0):
    assert detect_gender(harmonic(1500, f0=f0)) == "f"


@pytest.mark.parametrize("f0", [120.0, 220.0])
def test_f0_estimate_is_close(f0):
    est = estimate_f0(harmonic(1000, f0=f0))
    assert est is not None and abs(est - f0) / f0 < 0.05


def test_pure_tone_and_enveloped_speech():
    assert detect_gender(tone(800, hz=230)) == "f"
    assert detect_gender(speech(1200, f0=120)) == "m"
    assert detect_gender(speech(1200, f0=220)) == "f"


def test_ignores_silence_around_voice():
    pcm = silence(300) + harmonic(800, f0=220) + silence(300)
    assert detect_gender(pcm) == "f"


def test_only_first_two_seconds_used():
    pcm = harmonic(2000, f0=220) + harmonic(3000, f0=110)
    assert detect_gender(pcm) == "f"


def test_unknown_defaults_to_male():
    assert estimate_f0(silence(1000)) is None
    assert detect_gender(silence(1000)) == "m"
    assert detect_gender(b"") == "m"
    assert detect_gender(silence(1000, noise=20)) == "m"

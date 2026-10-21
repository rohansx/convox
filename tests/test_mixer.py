import numpy as np
import pytest

from dubhash.audio.earcon import EARCON
from dubhash.audio.frames import FRAME_BYTES
from dubhash.audio.mixer import db_to_gain, mix, passthrough_gain


def _const(value: int, samples: int) -> bytes:
    return np.full(samples, value, dtype=np.int16).tobytes()


def _arr(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.int16)


# --- db_to_gain / mix ---------------------------------------------------------


def test_db_to_gain():
    assert db_to_gain(0.0) == 1.0
    assert db_to_gain(-14.0) == pytest.approx(0.1995, abs=1e-4)
    assert db_to_gain(-6.0) == pytest.approx(0.501, abs=1e-3)


def test_mix_ducks_overlay_by_14db():
    base = _const(0, 320)
    overlay = _const(10000, 320)
    out, clipped = mix(base, overlay, -14.0)
    assert not clipped
    ratio = _arr(out).astype(float) / 10000
    assert np.allclose(ratio, 0.1995, atol=1e-3)


def test_mix_adds_base_at_unity():
    out, clipped = mix(_const(1000, 320), _const(-5000, 320), -14.0)
    assert not clipped
    assert np.all(_arr(out) == 1000 + round(-5000 * db_to_gain(-14.0)))


def test_mix_saturates_full_scale_without_wrap():
    out, clipped = mix(_const(32767, 320), _const(32767, 320), -14.0)
    assert clipped
    assert np.all(_arr(out) == 32767)
    out, clipped = mix(_const(-32768, 320), _const(-32768, 320), -14.0)
    assert clipped
    assert np.all(_arr(out) == -32768)


def test_mix_output_length_equals_base():
    base = _const(100, 320)
    out, _ = mix(base, _const(100, 100), 0.0)  # overlay shorter: zero-padded
    a = _arr(out)
    assert len(out) == len(base)
    assert np.all(a[:100] == 200) and np.all(a[100:] == 100)
    out, _ = mix(base, _const(100, 1000), 0.0)  # overlay longer: truncated
    assert len(out) == len(base)


def test_mix_empty_overlay_is_identity():
    base = _const(1234, 320)
    assert mix(base, b"", -14.0) == (base, False)


def test_passthrough_gain():
    f = _const(1000, 320)
    assert passthrough_gain(f) == f
    assert np.all(_arr(passthrough_gain(f, -6.0)) == round(1000 * db_to_gain(-6.0)))
    assert np.all(_arr(passthrough_gain(_const(30000, 320), 6.0)) == 32767)


# --- earcon -------------------------------------------------------------------


def test_earcon_is_six_frames_soft_and_faded():
    assert len(EARCON) == 6 * FRAME_BYTES
    x = _arr(EARCON).astype(float) / 32768
    peak = np.abs(x).max()
    assert 0.1 < peak <= 10 ** (-12 / 20) + 1e-3  # about -12 dBFS peak
    assert abs(x[0]) < 0.01 and abs(x[-1]) < 0.01  # fades in and out

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from convox.audio.wav import read_wav
from convox.providers.fakes import EnergyVAD
from tools import gen_fixtures as gf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _read(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2"), w.getframerate()


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if len(x) else 0.0


def test_render_is_deterministic():
    for spec in gf.SPECS:
        a = gf.render(spec)
        b = gf.render(spec)
        assert a.dtype == np.int16
        assert np.array_equal(a, b)


def test_specs_cover_8k_16k_and_noisy():
    rates = {s.rate for s in gf.SPECS}
    assert rates == {8000, 16000}
    assert any(s.noise > 0 for s in gf.SPECS)
    assert any(s.noise == 0 for s in gf.SPECS)


def test_length_matches_segments():
    for spec in gf.SPECS:
        x = gf.render(spec)
        total_ms = sum(ms for _, ms in spec.segments)
        assert len(x) == spec.rate * total_ms // 1000


def test_speech_segments_are_loud_and_silence_is_quiet():
    for spec in gf.SPECS:
        x = gf.render(spec)
        for kind, start, end in gf.segment_bounds(spec):
            seg = x[spec.rate * start // 1000 : spec.rate * end // 1000]
            if kind == "speech":
                assert _rms(seg) > 1500, (spec.name, start)
            else:
                limit = 1.0 if spec.noise == 0 else spec.noise
                assert _rms(seg) <= limit, (spec.name, start)
        assert int(np.max(np.abs(x.astype(np.int32)))) < 32767  # never clips


def test_noisy_variant_has_noise_floor_in_silence():
    spec = next(s for s in gf.SPECS if s.noise > 0)
    x = gf.render(spec)
    kind, start, end = next(b for b in gf.segment_bounds(spec) if b[0] == "silence")
    assert _rms(x[spec.rate * start // 1000 : spec.rate * end // 1000]) > 0


def test_write_all_creates_wavs_and_manifest(tmp_path: Path):
    paths = gf.write_all(tmp_path)
    assert {p.name for p in paths} >= {s.name for s in gf.SPECS}
    for spec in gf.SPECS:
        x, rate = _read(tmp_path / spec.name)
        assert rate == spec.rate
        assert np.array_equal(x, gf.render(spec))
    manifest = json.loads((tmp_path / gf.MANIFEST).read_text())
    assert [m["name"] for m in manifest] == [s.name for s in gf.SPECS]
    assert manifest[0]["segments"][0]["kind"] in {"speech", "silence"}


def test_energy_vad_finds_the_speech_bursts():
    spec = next(s for s in gf.SPECS if s.rate == 8000 and s.noise == 0)
    pcm16k = np.frombuffer(
        gf.to_16k(gf.render(spec), spec.rate).tobytes(), dtype=np.int16
    ).tobytes()
    vad = EnergyVAD()
    for kind, start, end in gf.segment_bounds(spec):
        mid = (start + end) // 2
        frame = pcm16k[mid * 32 : mid * 32 + 640]
        prob = vad.speech_prob(frame)
        assert (prob > 0.5) == (kind == "speech"), (kind, mid, prob)


@pytest.mark.parametrize("spec", gf.SPECS, ids=lambda s: s.name)
def test_committed_fixture_matches_generator(spec):
    path = FIXTURES / spec.name
    assert path.exists(), "run: uv run python tools/gen_fixtures.py"
    x, rate = _read(path)
    assert rate == spec.rate
    want = gf.render(spec)
    assert len(x) == len(want)
    # tolerate 1 LSB of float rounding across platforms
    assert int(np.max(np.abs(x.astype(np.int32) - want.astype(np.int32)))) <= 1


def test_committed_fixtures_stay_small():
    total = sum(p.stat().st_size for p in FIXTURES.glob("*.wav"))
    assert total < 500 * 1024


def test_committed_fixture_reads_back_as_16k():
    pcm = read_wav(FIXTURES / "synth_8k.wav")
    assert len(pcm) % 2 == 0 and len(pcm) > 16000


def test_check_mode_reports_drift(tmp_path: Path):
    gf.write_all(tmp_path)
    assert gf.check(tmp_path) == []
    (tmp_path / gf.SPECS[0].name).write_bytes(b"RIFFjunk")
    assert gf.check(tmp_path) == [gf.SPECS[0].name]


def test_main_writes_to_out_dir(tmp_path: Path):
    assert gf.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / gf.SPECS[0].name).exists()
    assert gf.main(["--out", str(tmp_path), "--check"]) == 0

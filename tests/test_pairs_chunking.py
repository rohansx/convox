"""Per-pair chunked interpretation settings (L9)."""

from pathlib import Path

from dubhash.config.pairs import PairConfig, load_pairs

PAIRS_YAML = Path(__file__).resolve().parent.parent / "dubhash" / "config" / "pairs.yaml"


def test_defaults_are_off_300_1200():
    p = load_pairs(PAIRS_YAML)["fake-fake"]
    assert (p.chunking, p.chunk_pause_ms, p.chunk_min_ms) == (False, 300, 1200)
    assert PairConfig.__dataclass_fields__["chunking"].default is False


def test_yaml_values_are_read(tmp_path):
    f = tmp_path / "pairs.yaml"
    f.write_text(
        "x:\n"
        "  a: { lang: hi-IN, stt: fake, tts: fake, voice_m: m, voice_f: f }\n"
        "  b: { lang: kn-IN, stt: fake, tts: fake, voice_m: m, voice_f: f }\n"
        "  chunking: true\n  chunk_pause_ms: 250\n  chunk_min_ms: 1000\n"
    )
    p = load_pairs(f)["x"]
    assert (p.chunking, p.chunk_pause_ms, p.chunk_min_ms) == (True, 250, 1000)
    assert p.swapped().chunking is True


ON = ("mr-hi", "mr-en", "gu-en")  # measured live with meaning and numbers intact (L9)


def test_chunking_is_on_for_the_pairs_measured_live_and_off_elsewhere():
    for name, p in load_pairs(PAIRS_YAML).items():
        want = (name in ON, 300, 1200)
        assert (p.chunking, p.chunk_pause_ms, p.chunk_min_ms) == want, name

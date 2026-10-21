"""early_final_ms: finalise STT after this much silence, before EOU (open question L3)."""

from pathlib import Path

from dubhash.config.pairs import PairConfig, load_pairs

PAIRS_FILE = Path(__file__).resolve().parent.parent / "dubhash" / "config" / "pairs.yaml"


def test_early_final_defaults_to_off():
    pairs = load_pairs(PAIRS_FILE)
    p = pairs["hi-kn-direct"]
    assert PairConfig(key="x", a=p.a, b=p.b).early_final_ms == 0
    assert pairs["fake-fake"].early_final_ms == 0


def test_early_final_loads_from_yaml(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "x:\n"
        "  a: { lang: ta-IN, stt: fake, tts: fake, voice_m: m, voice_f: f }\n"
        "  b: { lang: kn-IN, stt: fake, tts: fake, voice_m: m, voice_f: f }\n"
        "  early_final_ms: 200\n"
        "y:\n"
        "  a: { lang: ta-IN, stt: fake, tts: fake, voice_m: m, voice_f: f }\n"
        "  b: { lang: kn-IN, stt: fake, tts: fake, voice_m: m, voice_f: f }\n"
    )
    pairs = load_pairs(f)
    assert pairs["x"].early_final_ms == 200
    assert pairs["y"].early_final_ms == 0

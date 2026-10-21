"""early_final_ms: finalise STT after this much silence, before EOU (open question L3)."""

from pathlib import Path

from dubhash.config.pairs import PairConfig, load_pairs

PAIRS_FILE = Path(__file__).resolve().parent.parent / "dubhash" / "config" / "pairs.yaml"


def test_early_final_defaults_to_off():
    pairs = load_pairs(PAIRS_FILE)
    p = pairs["hi-kn-direct"]
    assert PairConfig(key="x", a=p.a, b=p.b).early_final_ms == 0
    assert pairs["fake-fake"].early_final_ms == 0


MEASURED_ON = {"hi-kn", "mr-en"}  # pairs whose early final was measured live (L3, L4)


def test_early_final_on_only_where_measured():
    """L3: live hi-kn first-audio p50 1727 -> 1377 ms with 200, no words lost. L4: mr-en."""
    pairs = load_pairs(PAIRS_FILE)
    for key in MEASURED_ON:
        assert pairs[key].early_final_ms == 200, key
    for key, p in pairs.items():
        if key not in MEASURED_ON:
            assert p.early_final_ms == 0, key


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

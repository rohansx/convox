from pathlib import Path

from convox.config.pairs import find_pair, load_pairs

PAIRS_FILE = Path(__file__).resolve().parent.parent / "convox" / "config" / "pairs.yaml"


def test_mt_src_defaults_to_none_and_mt_lang_falls_back_to_lang():
    hk = load_pairs(PAIRS_FILE)["hi-kn-direct"]
    assert hk.a.mt_src is None
    assert hk.a.mt_lang == "hi-IN"


def test_pivot_pair_uses_speech_translate_stt_and_english_mt_source():
    pairs = load_pairs(PAIRS_FILE)
    pv = pairs["hi-kn"]
    assert pv.a.lang == "hi-IN" and pv.a.stt == "sarvam-translate"
    assert pv.a.mt_src == "en-IN" and pv.a.mt_lang == "en-IN"
    assert pv.b.mt_src is None and pv.b.mt_lang == "kn-IN"
    # the pivot is the default for the hi/kn language lookup (21 Oct decision)
    assert find_pair(pairs, "hi-IN", "kn-IN") is pv


def test_mt_src_loads_from_yaml(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "x:\n"
        "  a: { lang: ta-IN, stt: fake, tts: fake, voice_m: m, voice_f: f, mt_src: en-IN }\n"
        "  b: { lang: kn-IN, stt: fake, tts: fake, voice_m: m, voice_f: f }\n"
    )
    x = load_pairs(f)["x"]
    assert x.a.mt_lang == "en-IN" and x.b.mt_lang == "kn-IN"

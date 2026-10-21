"""Decisions of 21 Oct: Bulbul speaks Hindi (no ElevenLabs on the default path), and
hi->kn uses the speech->English pivot (open question L1)."""

from dubhash.config.pairs import find_pair, load_pairs
from dubhash.config.settings import Settings
from dubhash.ward.service import ward_pair

PAIRS = load_pairs(Settings(_env_file=None).pairs_file)


def test_hi_kn_default_uses_pivot_on_the_hindi_side():
    p = find_pair(PAIRS, "hi-IN", "kn-IN")
    assert p is not None and p.key == "hi-kn"
    assert p.a.stt == "sarvam-translate" and p.a.mt_lang == "en-IN"
    assert p.b.stt == "sarvam" and p.b.mt_lang == "kn-IN"


def test_direct_route_kept_as_opt_in():
    d = PAIRS["hi-kn-direct"]
    assert d.a.stt == "sarvam" and d.a.mt_lang == "hi-IN"


def test_no_default_pair_speaks_with_elevenlabs():
    for key, p in PAIRS.items():
        for side in (p.a, p.b):
            assert side.tts != "eleven", f"{key} {side.lang} still uses eleven"


def test_ward_speaks_hindi_with_bulbul():
    p = ward_pair("mr-IN", "hi-IN", PAIRS)
    assert p.b.tts == "bulbul" and p.b.voice_f.startswith("bulbul:")
    q = ward_pair("en-IN", "ta-IN", {})
    assert q.a.tts == "bulbul"


def test_marathi_pairs_with_hindi_and_english():
    """Stage demo (docs/plan/demo-script.md): a Marathi speaker with a Hindi or English one."""
    hi = find_pair(PAIRS, "mr-IN", "hi-IN")
    en = find_pair(PAIRS, "mr-IN", "en-IN")
    assert hi is not None and en is not None
    assert en.a.stt == "sarvam" and en.b.stt == "sarvam"
    assert en.a.tts == en.b.tts == "bulbul"
    assert en.b.voice_m.startswith("bulbul:en-")

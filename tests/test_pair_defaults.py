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


def test_mr_en_marathi_side_speaks_english_straight_from_stt():
    p = PAIRS["mr-en"]
    assert (p.a.lang, p.b.lang) == ("mr-IN", "en-IN")
    assert p.a.stt == "sarvam-translate" and p.a.mt_lang == "en-IN"  # == target: MT skipped
    assert p.b.stt == "sarvam" and p.b.mt_lang == "en-IN"  # then en -> mr MT
    assert p.b.tts == "bulbul" and p.b.voice_f.startswith("bulbul:en-")
    assert p.a.tts == "bulbul" and p.a.voice_f.startswith("bulbul:mr-")


def test_one_entry_per_language_pair():
    """find_pair swaps orientation, so no pair is written twice (en-mr next to mr-en)."""
    seen: dict[frozenset[str], str] = {}
    for key, p in PAIRS.items():
        if "fake" in key or key.endswith("-direct"):
            continue
        langs = frozenset((p.a.lang, p.b.lang))
        assert langs not in seen, f"{key} duplicates {seen[langs]}"
        seen[langs] = key


def test_mr_hi_default_pivots_both_sides_through_english():
    """L4: live first-audio heard p50 ~2.5 s direct -> ~1.56 s pivot + early final."""
    p = find_pair(PAIRS, "mr-IN", "hi-IN")
    assert p is not None and p.key == "mr-hi"
    for side in (p.a, p.b):
        assert side.stt == "sarvam-translate" and side.mt_lang == "en-IN"
    assert p.early_final_ms == 200
    d = PAIRS["mr-hi-direct"]
    assert (d.a.stt, d.b.stt) == ("sarvam", "sarvam") and d.early_final_ms == 0
    assert (d.a.mt_lang, d.b.mt_lang) == ("mr-IN", "hi-IN")

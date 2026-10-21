"""find_pair accepts either orientation: one pairs.yaml entry serves both side orders."""

import dataclasses

from dubhash.config.pairs import PairConfig, SideConfig, find_pair, load_pairs, orient
from dubhash.config.settings import Settings

PAIRS = load_pairs(Settings(_env_file=None).pairs_file)


def _side(lang: str, **kw) -> SideConfig:
    return SideConfig(lang=lang, stt="sarvam", tts="bulbul", voice_m="m", voice_f="f", **kw)


def test_swapped_exchanges_sides_and_keeps_everything_else():
    p = PairConfig(
        key="mr-en",
        a=_side("mr-IN", mt_src="en-IN"),
        b=_side("en-IN"),
        eou_ms=600,
        early_final_ms=200,
    )
    q = p.swapped()
    assert (q.a, q.b) == (p.b, p.a)
    assert q.key == p.key
    assert dataclasses.replace(q, a=p.a, b=p.b) == p
    assert q.swapped() == p


def test_find_pair_matches_the_reversed_orientation():
    pairs = {"mr-en": PairConfig(key="mr-en", a=_side("mr-IN"), b=_side("en-IN"))}
    p = find_pair(pairs, "en-IN", "mr-IN")
    assert p is not None and p.a.lang == "en-IN" and p.b.lang == "mr-IN"
    assert p == pairs["mr-en"].swapped()


def test_find_pair_prefers_the_exact_orientation():
    exact = PairConfig(key="en-mr", a=_side("en-IN"), b=_side("mr-IN"))
    pairs = {"mr-en": PairConfig(key="mr-en", a=_side("mr-IN"), b=_side("en-IN")), "en-mr": exact}
    assert find_pair(pairs, "en-IN", "mr-IN") is exact


def test_find_pair_still_skips_fake_pairs_and_unknown_languages():
    assert find_pair(PAIRS, "kn-IN", "hi-IN").a.lang == "kn-IN"  # hi-kn, swapped
    assert find_pair(PAIRS, "ta-IN", "kn-IN") is None


def test_orient_by_languages():
    p = PAIRS["hi-kn"]
    assert orient(p, "hi-IN", "kn-IN") is p
    assert orient(p, "kn-IN", "hi-IN") == p.swapped()
    assert orient(p, "hi-IN", "ta-IN") is None


def test_english_and_hindi_users_both_reach_a_marathi_teammate():
    for me in ("en-IN", "hi-IN"):
        for a, b in ((me, "mr-IN"), ("mr-IN", me)):
            p = find_pair(PAIRS, a, b)
            assert p is not None and (p.a.lang, p.b.lang) == (a, b), (a, b)

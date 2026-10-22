from convox.providers.fakes import FakeMT, FakeSTT
from tests.audio_fixtures import harmonic
from tools import pivot_probe as pp


def test_percentile_is_nearest_rank():
    xs = [100, 300, 200, 500, 400, 600, 700, 800, 900, 1000]
    assert pp.pct(xs, 50) == 500
    assert pp.pct(xs, 95) == 1000
    assert pp.pct([42], 95) == 42
    assert pp.pct([], 50) is None


def test_missing_keeps_matches_alternatives_case_insensitively():
    keeps = ("Sharma", "302|three hundred two", "B")
    assert pp.missing_keeps("Sharma ji's parcel, flat 302, block B.", keeps) == []
    assert pp.missing_keeps("Parcel for flat three hundred and two", keeps) == [
        "Sharma",
        "302|three hundred two",
        "B",
    ]
    # whole words only: "B" must not match inside "Brother"
    assert pp.missing_keeps("Brother", ("B",)) == ["B"]


def test_sentences_have_keeps():
    assert len(pp.SENTENCES) == 10
    assert all(s.hi and s.keeps for s in pp.SENTENCES)


async def test_run_routes_with_fakes_times_each_stage():
    pcm = harmonic(400, f0=150.0)
    stt_a = FakeSTT(script=["मैं गेट पर हूँ"])
    stt_b = FakeSTT(script=["I am at the gate"])
    mt = FakeMT()
    row = await pp.run_sentence(
        pp.SENTENCES[0], pcm, stt_a=stt_a, stt_b=stt_b, mt=mt, realtime=False
    )
    assert row.a_text == "मैं गेट पर हूँ"
    assert row.a_kn == "[kn-IN] मैं गेट पर हूँ"
    assert row.b_en == "I am at the gate"
    assert row.b_kn == "[kn-IN] I am at the gate"
    assert row.hi_en == "[en-IN] मैं गेट पर हूँ"
    assert mt.calls == 3
    for k in ("a_stt_ms", "a_mt_ms", "b_stt_ms", "b_mt_ms", "hi_en_ms"):
        assert getattr(row, k) >= 0
    assert row.a_total_ms == row.a_stt_ms + row.a_mt_ms
    assert row.b_total_ms == row.b_stt_ms + row.b_mt_ms
    assert row.b_missing == ["2|two"]  # the fake English dropped the gate number

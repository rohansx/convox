import pytest

from dubhash.interp.mt_cache import CachedMT
from dubhash.providers.fakes import FakeMT


async def test_hit_skips_inner_and_counts():
    inner = FakeMT()
    mt = CachedMT(inner)
    a = await mt.translate("Namaste", "hi-IN", "kn-IN")
    b = await mt.translate("  namaste ", "hi-IN", "kn-IN")
    assert a == b == "[kn-IN] Namaste"
    assert inner.calls == 1
    assert (mt.hits, mt.misses) == (1, 1)


async def test_key_includes_langs():
    inner = FakeMT()
    mt = CachedMT(inner)
    await mt.translate("haan", "hi-IN", "kn-IN")
    await mt.translate("haan", "hi-IN", "ta-IN")
    await mt.translate("haan", "mr-IN", "kn-IN")
    assert inner.calls == 3 and mt.hits == 0


async def test_lru_eviction():
    inner = FakeMT()
    mt = CachedMT(inner, maxsize=2)
    await mt.translate("a", "hi-IN", "kn-IN")
    await mt.translate("b", "hi-IN", "kn-IN")
    await mt.translate("a", "hi-IN", "kn-IN")  # a is now most recent
    await mt.translate("c", "hi-IN", "kn-IN")  # evicts b
    assert len(mt) == 2
    await mt.translate("a", "hi-IN", "kn-IN")
    assert inner.calls == 3
    await mt.translate("b", "hi-IN", "kn-IN")
    assert inner.calls == 4


async def test_errors_are_not_cached():
    class Flaky:
        name = "flaky"

        def __init__(self) -> None:
            self.n = 0

        async def translate(self, text: str, src: str, tgt: str) -> str:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("boom")
            return "ok"

    mt = CachedMT(Flaky())
    with pytest.raises(RuntimeError):
        await mt.translate("x", "hi-IN", "kn-IN")
    assert await mt.translate("x", "hi-IN", "kn-IN") == "ok"


def test_name_and_last_hit_flag():
    mt = CachedMT(FakeMT())
    assert mt.name == "fake"
    assert mt.last_hit is False


async def test_key_drops_a_final_full_stop_but_keeps_a_question():
    inner = FakeMT()
    mt = CachedMT(inner)
    await mt.translate("Theek hai.", "hi-IN", "kn-IN")
    await mt.translate("theek hai", "hi-IN", "kn-IN")
    await mt.translate("theek hai?", "hi-IN", "kn-IN")
    assert inner.calls == 2


async def test_has_reports_a_hit_without_counting_it():
    mt = CachedMT(FakeMT())
    assert not mt.has("haan", "hi-IN", "kn-IN")
    await mt.translate("haan", "hi-IN", "kn-IN")
    assert mt.has("Haan", "hi-IN", "kn-IN")
    assert (mt.hits, mt.misses) == (0, 1)


async def test_prefill_fills_the_shared_phrase_store_once():
    """Phrase-pack translations are not user text: they are shared by every call in the
    process, so the second call's prefill costs no MT at all."""
    shared: dict = {}
    inner1, inner2 = FakeMT(), FakeMT()
    call1, call2 = CachedMT(inner1, shared=shared), CachedMT(inner2, shared=shared)
    assert await call1.prefill("OTP batao", "hi-IN", "kn-IN") == "[kn-IN] OTP batao"
    assert await call2.prefill("OTP batao", "hi-IN", "kn-IN") == "[kn-IN] OTP batao"
    assert (inner1.calls, inner2.calls) == (1, 0)
    assert await call2.translate("otp batao", "hi-IN", "kn-IN") == "[kn-IN] OTP batao"
    assert inner2.calls == 0 and call2.hits == 1


async def test_ordinary_turns_never_reach_the_shared_store():
    shared: dict = {}
    mt = CachedMT(FakeMT(), shared=shared)
    await mt.translate("my flat is 302", "en-IN", "hi-IN")
    assert shared == {}

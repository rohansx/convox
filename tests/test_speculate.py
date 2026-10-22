"""Speculative MT: normalize() and the per-utterance Speculator (unit level). The
Interpreter-level behaviour (a)-(d) is in tests/test_pipeline.py."""

import asyncio

from convox.clock import FakeClock
from convox.interp.speculate import Speculator, normalize
from convox.providers.fakes import FakeMT


def test_normalize_casefolds_strips_punctuation_and_collapses_spaces():
    assert normalize("  Main Gate, ke  BAHAR hoon! ") == "main gate ke bahar hoon"
    assert normalize("क्या आप आ रहे हैं?") == normalize("क्या आप  आ रहे हैं")
    assert normalize("मैं गेट पर हूँ।") == "मैं गेट पर हूँ"  # danda is punctuation
    assert normalize("...") == ""
    assert normalize("gate no. 2") == "gate no 2"


def make(clock: FakeClock, mt: FakeMT, **kw) -> Speculator:
    async def translate(text: str) -> str:
        return await mt.translate(text, "hi-IN", "kn-IN")

    return Speculator(translate, **kw)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_no_trigger_before_after_ms_or_without_partial():
    clock = FakeClock()
    mt = FakeMT()
    sp = make(clock, mt, after_ms=200)
    sp.on_silence(now=1200, t_last_speech=1000, speech_ms=1000)  # no partial yet
    sp.on_partial("main gate par", now=900)
    sp.on_silence(now=1180, t_last_speech=1000, speech_ms=1000)  # 180 ms < 200
    await settle()
    assert mt.calls == 0 and sp.started == 0
    sp.on_silence(now=1200, t_last_speech=1000, speech_ms=1000)
    await settle()
    assert mt.calls == 1 and sp.started == 1 and mt.texts == ["main gate par"]
    sp.on_silence(now=1220, t_last_speech=1000, speech_ms=1000)  # same partial: no restart
    await settle()
    assert mt.calls == 1
    sp.cancel()


async def test_unstable_partial_waits_for_stable_ms():
    """A partial from during speech must be stable for stable_ms; one that arrived after
    the last speech frame (the newest) triggers at once."""
    clock = FakeClock()
    mt = FakeMT(delay_ms=1000, clock=clock)
    sp = make(clock, mt, after_ms=100, stable_ms=150)
    sp.on_partial("ek do", now=990)
    sp.on_silence(now=1100, t_last_speech=1000, speech_ms=1000)  # 110 ms stable < 150
    await settle()
    assert mt.calls == 0
    sp.on_silence(now=1140, t_last_speech=1000, speech_ms=1000)  # 150 ms stable
    await settle()
    assert mt.calls == 1
    sp.on_partial("ek do teen", now=1150)  # newest, arrived after speech ended
    sp.on_silence(now=1160, t_last_speech=1000, speech_ms=1000)
    await settle()
    assert mt.calls == 2 and mt.texts[-1] == "ek do teen"
    assert sp.cancelled == 1 and mt.cancelled == 1
    sp.cancel()


async def test_speech_resuming_cancels():
    clock = FakeClock()
    mt = FakeMT(delay_ms=1000, clock=clock)
    sp = make(clock, mt)
    sp.on_partial("ek do teen", now=900)
    sp.on_silence(now=1200, t_last_speech=1000, speech_ms=1000)
    await settle()
    sp.on_speech(now=1220)
    await settle()
    assert mt.calls == 1 and mt.cancelled == 1 and sp.cancelled == 1
    assert sp.started == 1


async def test_backchannel_sized_utterance_is_not_speculated():
    clock = FakeClock()
    mt = FakeMT()
    sp = make(clock, mt, backchannel_max_ms=600)
    sp.on_partial("haan", now=300)
    sp.on_silence(now=700, t_last_speech=400, speech_ms=400)
    await settle()
    assert mt.calls == 0


async def test_take_matching_final_reuses_result():
    clock = FakeClock()
    mt = FakeMT(delay_ms=1000, clock=clock)
    sp = make(clock, mt)
    sp.on_partial("main gate ke bahar hoon", now=900)
    sp.on_silence(now=1200, t_last_speech=1000, speech_ms=1000)
    await settle()
    clock.advance(2200)
    out = await sp.take("Main gate ke bahar hoon.")
    assert out.hit and out.text == "[kn-IN] main gate ke bahar hoon"
    assert mt.calls == 1


async def test_take_awaits_running_speculation():
    clock = FakeClock()
    mt = FakeMT(delay_ms=1000, clock=clock)
    sp = make(clock, mt)
    sp.on_partial("ek do teen", now=900)
    sp.on_silence(now=1200, t_last_speech=1000, speech_ms=1000)
    await settle()
    take = asyncio.create_task(sp.take("ek do teen"))
    await settle()
    assert not take.done()
    clock.advance(1000)
    out = await take
    assert out.hit and out.text == "[kn-IN] ek do teen"


async def test_take_non_matching_final_cancels_and_returns_nothing():
    clock = FakeClock()
    mt = FakeMT(delay_ms=1000, clock=clock)
    sp = make(clock, mt)
    sp.on_partial("ek do teen", now=900)
    sp.on_silence(now=1200, t_last_speech=1000, speech_ms=1000)
    await settle()
    out = await sp.take("ek do teen chaar")
    await settle()
    assert not out.hit and out.text is None
    assert mt.cancelled == 1 and sp.cancelled == 1


async def test_take_without_speculation():
    sp = make(FakeClock(), FakeMT())
    out = await sp.take("kuch bhi")
    assert not out.hit and out.text is None


async def test_failed_speculation_is_a_hit_but_not_used():
    class Boom:
        async def __call__(self, text: str) -> str:
            raise RuntimeError("mt down")

    sp = Speculator(Boom())
    sp.on_partial("ek do teen", now=900)
    sp.on_silence(now=1200, t_last_speech=1000, speech_ms=1000)
    await settle()
    out = await sp.take("ek do teen")
    assert out.hit and out.text is None


async def test_cancel_during_take_cancels_speculation():
    clock = FakeClock()
    mt = FakeMT(delay_ms=1000, clock=clock)
    sp = make(clock, mt)
    sp.on_partial("ek do teen", now=900)
    sp.on_silence(now=1200, t_last_speech=1000, speech_ms=1000)
    await settle()
    take = asyncio.create_task(sp.take("ek do teen"))
    await settle()
    take.cancel()
    await asyncio.gather(take, return_exceptions=True)
    await settle()
    assert mt.cancelled == 1

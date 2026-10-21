"""Phrase cache in a call (L10): background prefill, instant acks, the number read-back
event and the next-turn prewarm."""

from __future__ import annotations

import asyncio

from dubhash.clock import FakeClock
from dubhash.interp.mt_cache import CachedMT
from dubhash.interp.phrases import load_pack
from dubhash.providers.fakes import FakeMT, FakeSTT, FakeTTS
from dubhash.providers.tts_cache import CachedTTS, TTSAudioCache
from dubhash.session.prefill import prefill_phrases
from tests.audio_fixtures import conversation
from tests.session.test_call_session import PAIR, attach_both, make, providers
from tests.session.test_prewarm import WarmSTT, WarmTTS
from tools.bench import drive_lockstep

PACK = load_pack("delivery")


def cached_providers(mt=None, tts_a=None, tts_b=None, stt=None, rps=0.0):
    store = TTSAudioCache()
    p = providers(stt=stt)
    p.mt = CachedMT(mt or FakeMT())
    p.tts = {"a": CachedTTS(tts_a or FakeTTS(), store), "b": CachedTTS(tts_b or FakeTTS(), store)}
    p.prefill_rps = rps
    return p


async def test_prefill_fills_both_directions_in_both_voices():
    p = cached_providers()
    st = await prefill_phrases(PACK, PAIR, p, clock=FakeClock(), rps=0)
    assert st.done and st.errors == 0
    hi, kn = PACK.texts("hi-IN"), PACK.texts("kn-IN")
    assert st.lines == len(hi) + len(kn)
    assert st.mt_calls == len(hi) + len(kn)
    assert st.tts_calls == 2 * (len(hi) + len(kn))  # voice_m and voice_f
    for text in hi:
        assert p.mt.has(text, "hi-IN", "kn-IN")
        out = await p.mt.translate(text, "hi-IN", "kn-IN")
        assert p.tts["b"].has(out, PAIR.b.voice_m, "kn-IN")
        assert p.tts["b"].has(out, PAIR.b.voice_f, "kn-IN")
    for text in kn:
        assert p.mt.has(text, "kn-IN", "hi-IN")


async def test_a_second_prefill_costs_no_vendor_request():
    p = cached_providers()
    await prefill_phrases(PACK, PAIR, p, clock=FakeClock(), rps=0)
    st = await prefill_phrases(PACK, PAIR, p, clock=FakeClock(), rps=0)
    assert st.mt_calls == st.tts_calls == 0 and st.cached > 0


async def test_acks_go_first_and_the_sides_interleave():
    tts_b, tts_a = FakeTTS(), FakeTTS()
    p = cached_providers(tts_a=tts_a, tts_b=tts_b)
    await prefill_phrases(PACK, PAIR, p, clock=FakeClock(), rps=0)
    first_hi = [t for t, _, _ in tts_b.requests[:4]]
    assert first_hi[0] == "[kn-IN] ठीक है" and first_hi[2] == "[kn-IN] हाँ"
    assert tts_a.requests[0][0] == "[hi-IN] ಸರಿ"  # kn acks (b's side) early too


async def test_prefill_is_rate_limited_and_waits_while_the_call_is_busy():
    clock = FakeClock()
    busy = [True]
    p = cached_providers()
    task = asyncio.create_task(
        prefill_phrases(PACK, PAIR, p, clock=clock, rps=2, busy=lambda: busy[0])
    )
    for _ in range(10):
        clock.advance(100)
        await asyncio.sleep(0)
    assert p.mt.inner.calls == 0  # busy: nothing sent
    busy[0] = False
    calls = []
    for _ in range(40):  # 2 s at 50 ms steps
        clock.advance(50)
        for _ in range(5):
            await asyncio.sleep(0)
        calls.append(p.mt.inner.calls + len(p.tts["a"].inner.requests + p.tts["b"].inner.requests))
    assert 4 <= calls[-1] <= 5  # 2 requests/s
    task.cancel()


async def test_prefill_never_blocks_the_call():
    """A slow TTS (FakeClock time) and a slow prefill rate: the call goes active at once and
    its turn is synthesised as fast as without a prefill."""
    tts_b = FakeTTS(ttfb_ms=200)
    p = cached_providers(tts_b=tts_b, stt={"a": FakeSTT(script=["mera flat 302 hai"]),
                                           "b": FakeSTT()}, rps=1)  # fmt: skip
    session, legs, clock, bus = make(prov=p, vertical="delivery")
    tts_b.clock = clock
    session.start_prewarm()
    assert session.prefill_task is not None and not session.prefill_task.done()
    await attach_both(session, legs)
    await asyncio.sleep(0)
    assert session.status == "active"
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 2500)])
    await drive_lockstep(session, legs, clock, {"a": pcm, "b": b""})
    await session.wait_closed()
    (turn,) = session.turn_log.turns
    assert turn.t_tts_ttfb - turn.t_mt == 200
    assert not session.prefill_stats.done  # still going when the call ended (cancelled)
    assert session.prefill_task.done()


async def test_an_ack_turn_in_a_call_costs_no_mt_or_tts():
    mt, tts_b = FakeMT(), FakeTTS()
    p = cached_providers(mt=mt, tts_b=tts_b, stt={"a": FakeSTT(script=["theek hai"]),
                                                  "b": FakeSTT()})  # fmt: skip
    await prefill_phrases(PACK, PAIR, p, clock=FakeClock(), rps=0)
    mt.calls, tts_b.requests = 0, []
    session, legs, clock, bus = make(prov=p, vertical="delivery")
    await attach_both(session, legs)
    pcm = conversation([("silence", 300), ("speech", 400), ("silence", 1500)])
    await drive_lockstep(session, legs, clock, {"a": pcm, "b": b""})
    await session.wait_closed()
    (turn,) = session.turn_log.turns
    assert not turn.backchannel and turn.tgt_text == "[kn-IN] ठीक है"
    assert mt.calls == 0 and tts_b.requests == []
    assert turn.t_tts_ttfb == turn.t_mt == turn.t_stt_final


async def test_without_a_vertical_an_ack_is_still_a_backchannel():
    p = cached_providers(stt={"a": FakeSTT(script=["theek hai"]), "b": FakeSTT()})
    session, legs, clock, bus = make(prov=p)
    await attach_both(session, legs)
    pcm = conversation([("silence", 300), ("speech", 400), ("silence", 1500)])
    await drive_lockstep(session, legs, clock, {"a": pcm, "b": b""})
    await session.wait_closed()
    (turn,) = session.turn_log.turns
    assert turn.backchannel


async def test_ack_ok_follows_the_floor():
    session, legs, clock, bus = make(vertical="delivery")
    await attach_both(session, legs)
    await asyncio.sleep(0)
    assert session._ack_ok("a") and session._ack_ok("b")
    await session.floor.on_speech_start("b")
    assert not session._ack_ok("a")  # B holds the floor: A's "haan" is a backchannel
    assert session._ack_ok("b")
    await session.hangup()


async def test_a_wrong_number_is_read_back_and_published():
    mt = FakeMT(dictionary={"OTP 4729 hai": "OTP 47296 ide"})
    p = cached_providers(mt=mt, stt={"a": FakeSTT(script=["OTP 4729 hai"]), "b": FakeSTT()})
    session, legs, clock, bus = make(prov=p, vertical="delivery")
    await attach_both(session, legs)
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 1500)])
    await drive_lockstep(session, legs, clock, {"a": pcm, "b": b""})
    await session.wait_closed()
    (turn,) = session.turn_log.turns
    assert turn.tgt_text == "OTP ನಾಲ್ಕು ಏಳು ಎರಡು ಒಂಬತ್ತು ide"
    (ev,) = bus.kinds("number_fix")
    assert ev.payload == {"side": "a", "missing": 1, "replaced": 1, "appended": 0}


async def test_playback_prewarms_the_listeners_stt_and_its_reply_voice():
    stt = {"a": WarmSTT(), "b": WarmSTT()}
    stt["a"]._script.append("main gate ke bahar hoon")
    p = providers(stt=stt, tts={"a": WarmTTS(), "b": WarmTTS()})
    session, legs, clock, bus = make(prov=p)
    await attach_both(session, legs)
    await asyncio.sleep(0)
    for _ in range(5):
        await asyncio.sleep(0)
    before_b, before_tts_a = len(stt["b"].warmed), len(p.tts["a"].warmed)
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 1500)])
    await drive_lockstep(session, legs, clock, {"a": pcm, "b": b""})
    await session.wait_closed()
    assert stt["b"].warmed[before_b:] == [PAIR.b.lang]  # B will answer: its STT is warm
    assert p.tts["a"].warmed[before_tts_a:] == [PAIR.a.voice_f]  # and A's reply voice
    assert len(stt["a"].warmed) == 1  # A (who spoke) is not re-warmed


async def test_next_turn_prewarm_can_be_turned_off():
    stt = {"a": WarmSTT(), "b": WarmSTT()}
    stt["a"]._script.append("main gate ke bahar hoon")
    p = providers(stt=stt, tts={"a": WarmTTS(), "b": WarmTTS()})
    session, legs, clock, bus = make(prov=p, next_turn_prewarm=False)
    await attach_both(session, legs)
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 1500)])
    await drive_lockstep(session, legs, clock, {"a": pcm, "b": b""})
    await session.wait_closed()
    assert len(stt["b"].warmed) == 1  # activation only

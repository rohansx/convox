"""CallSession lifecycle, modes and failure handling (the e2e conversation is in
tests/test_e2e_fake.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dubhash.audio.earcon import EARCON
from dubhash.clock import FakeClock
from dubhash.config.pairs import load_pairs
from dubhash.legs.fake import FakeLeg
from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from dubhash.session.call_session import CallSession
from dubhash.session.prompts import line_check_text
from dubhash.session.providers import Providers
from tests.audio_fixtures import conversation, speech
from tests.floor_helpers import RecordingBus
from tools.bench import drive_lockstep

PAIR = load_pairs(Path(__file__).resolve().parents[2] / "dubhash" / "config" / "pairs.yaml")[
    "fake-fake"
]


def providers(**kw) -> Providers:
    return Providers(
        stt=kw.get("stt") or {"a": FakeSTT(), "b": FakeSTT()},
        mt=kw.get("mt") or FakeMT(),
        tts=kw.get("tts") or {"a": FakeTTS(), "b": FakeTTS()},
        vad_factory=kw.get("vad_factory") or EnergyVAD,
    )


def make(mode="interpret", opening_line=False, prov=None, **kw):
    clock = FakeClock()
    bus = RecordingBus()
    legs = {s: FakeLeg(s, clock=clock) for s in ("a", "b")}
    session = CallSession(
        "c_t",
        PAIR,
        prov or providers(),
        bus=bus,
        clock=clock,
        mode=mode,
        opening_line=opening_line,
        **kw,
    )
    return session, legs, clock, bus


async def attach_both(session, legs):
    await session.attach_leg("a", legs["a"])
    await session.attach_leg("b", legs["b"])


def other_tasks() -> set[asyncio.Task]:
    return {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}


async def test_ringing_until_both_legs_then_active():
    session, legs, _, bus = make()
    await session.attach_leg("a", legs["a"])
    assert session.status == "ringing" and bus.kinds("call") == []
    with pytest.raises(ValueError):
        await session.attach_leg("a", FakeLeg("a"))
    await session.attach_leg("b", legs["b"])
    await asyncio.sleep(0)
    assert session.status == "active"
    assert [e.payload["status"] for e in bus.kinds("call")] == ["active"]
    await session.hangup()
    assert session.status == "ended"


async def test_hangup_while_ringing_closes_the_attached_leg():
    session, legs, _, bus = make()
    await session.attach_leg("a", legs["a"])
    await session.hangup("no_answer")
    await session.wait_closed()
    assert session.status == "ended" and legs["a"].closed
    assert [e.payload["status"] for e in bus.kinds("call")] == ["ended"]
    assert bus.kinds("call")[-1].payload["reason"] == "no_answer"
    with pytest.raises(RuntimeError):
        await session.attach_leg("b", legs["b"])
    assert legs["b"].closed


async def test_hangup_while_active_tears_down_without_orphans():
    before = other_tasks()
    prov = providers()
    closed: list[str] = []

    async def aclose():
        closed.append("tts")

    prov.tts["a"].aclose = aclose  # type: ignore[attr-defined]
    session, legs, clock, bus = make(prov=prov)
    await attach_both(session, legs)
    legs["a"].feed(speech(400))
    await asyncio.sleep(0.01)
    await session.hangup("api")
    assert session.status == "ended" and legs["a"].closed and legs["b"].closed
    assert bus.kinds("call")[-1].payload["reason"] == "api"
    assert sorted(
        e.payload["side"] for e in bus.kinds("leg") if e.payload["status"] == "closed"
    ) == [
        "a",
        "b",
    ]
    assert closed == ["tts"]
    await asyncio.sleep(0)
    assert other_tasks() - before == set()
    await session.hangup()  # idempotent


async def test_one_leg_ending_hangs_up_the_other():
    session, legs, _, bus = make()
    await attach_both(session, legs)
    legs["a"].end_input()
    await asyncio.wait_for(session.wait_closed(), 2)
    assert legs["b"].closed and session.status == "ended"
    assert bus.kinds("call")[-1].payload["reason"] == "leg_a_closed"


async def test_bridge_mode_forwards_both_ways_without_interpreting():
    session, legs, clock, bus = make(mode="bridge", opening_line=True)
    assert session.interpreters == {}
    a, b = speech(400, f0=150.0), speech(400, f0=230.0)
    await attach_both(session, legs)
    await drive_lockstep(session, legs, clock, {"a": a, "b": b})
    await session.wait_closed()
    # exactly the other side's frames (then the driver's trailing silence): no opening line
    for out, src in ((legs["b"].outbound, a), (legs["a"].outbound, b)):
        assert out.startswith(src) and out[len(src) :] == b"\x00" * (len(out) - len(src))
    s = session.summary()
    assert s["mode"] == "bridge" and s["escape_used"] is False and s["turns"] == 0
    assert bus.kinds("bypass") == []


async def test_interpreter_crash_on_one_side_keeps_the_other_direction():
    class Boom(EnergyVAD):
        def speech_prob(self, frame):
            raise RuntimeError("vad died")

    vads = iter([Boom(), EnergyVAD()])  # side a's VAD crashes, side b's works
    prov = providers(vad_factory=lambda: next(vads))
    session, legs, clock, bus = make(prov=prov)
    await attach_both(session, legs)
    pcm = conversation([("silence", 300), ("speech", 1200), ("silence", 4000)])
    await drive_lockstep(session, legs, clock, {"a": pcm, "b": pcm})
    await session.wait_closed()
    assert bus.kinds("provider_error")
    assert [t.side for t in session.turn_log.turns] == ["b"]
    assert EARCON in legs["a"].outbound and EARCON not in legs["b"].outbound


async def test_opening_line_failure_is_logged_and_interpreting_continues():
    class BadTTS(FakeTTS):
        async def synth(self, text, voice, lang):
            raise ConnectionError("tts down")
            yield b""  # pragma: no cover

    prov = providers(tts={"a": BadTTS(), "b": FakeTTS()})
    session, legs, clock, bus = make(prov=prov, opening_line=True)
    await attach_both(session, legs)
    pcm = conversation([("silence", 300), ("speech", 1200), ("silence", 6000)])
    await drive_lockstep(session, legs, clock, {"a": pcm, "b": b""})
    await session.wait_closed()
    errs = bus.kinds("provider_error")
    assert errs and errs[0].payload["stage"] == "opening"
    # side b still got its opening line and A's translation
    assert [t.side for t in session.turn_log.turns] == ["a"]
    assert legs["b"].outbound.count(EARCON) == 1


async def test_partials_are_published():
    session, legs, clock, bus = make()
    await attach_both(session, legs)
    pcm = conversation([("silence", 300), ("speech", 1200), ("silence", 4000)])
    await drive_lockstep(session, legs, clock, {"a": pcm})
    await session.wait_closed()
    partials = bus.kinds("partial")
    assert partials and all(p.payload["side"] == "a" for p in partials)
    assert partials[-1].payload["text"].startswith("w1")


async def test_line_check_after_10s_silence_in_each_language():
    tts = {"a": FakeTTS(), "b": FakeTTS()}
    session, legs, clock, bus = make(prov=providers(tts=tts))
    await attach_both(session, legs)
    await drive_lockstep(session, legs, clock, {"a": conversation([("silence", 10500)])})
    await session.wait_closed()
    (ev,) = bus.kinds("line_check")
    assert sorted(ev.payload["sides"]) == ["a", "b"]
    for side in ("a", "b"):
        text = line_check_text(PAIR.side(side).lang)
        assert tts[side].requests == [(text, PAIR.side(side).voice_f, PAIR.side(side).lang)]
        assert len(legs[side].outbound) == len(text) * 60 * 32


async def test_listener_speech_under_barge_in_min_is_queued_not_cleared():
    # A1 plays to B; B talks 900 ms over it (past backchannel, under barge-in): queued.
    stt = {
        "a": FakeSTT(["bhaiya main gate ke bahar hoon aur building"]),
        "b": FakeSTT(["ruko main aa raha"]),
    }
    session, legs, clock, bus = make(prov=providers(stt=stt))
    await attach_both(session, legs)
    pcm_a = conversation([("silence", 300), ("speech", 1500), ("silence", 8000)])
    pcm_b = conversation([("silence", 2600), ("speech", 900), ("silence", 6300)])
    await drive_lockstep(session, legs, clock, {"a": pcm_a, "b": pcm_b})
    await session.wait_closed()
    assert bus.kinds("barge_in") == []
    assert [e for e in legs["b"].log if e[0] == "clear"] == []
    a1, b1 = session.turn_log.turns
    assert (a1.side, a1.clipped_ms, b1.side, b1.clipped_ms) == ("a", None, "b", None)
    assert b1.t_eou < a1.t_play_end <= b1.t_play_start


async def test_record_flag_reaches_the_writer():
    class W:
        def __init__(self):
            self.records: list[bool] = []
            self.finished = 0

        def enqueue_turn(self, turn, record):
            self.records.append(record)
            return True

        def finish_call(self, call_id, **kw):
            self.finished += 1
            return kw

    w = W()
    session, legs, clock, bus = make(writer=w, record=True)
    await attach_both(session, legs)
    pcm = conversation([("silence", 300), ("speech", 1200), ("silence", 4000)])
    await drive_lockstep(session, legs, clock, {"a": pcm})
    await session.wait_closed()
    assert w.records == [True] and w.finished == 1

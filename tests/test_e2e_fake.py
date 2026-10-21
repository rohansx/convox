"""Day-2/3 proof: two FakeLegs, all-fake providers, the real floor, mixer and interpreters,
one CallSession, on a FakeClock advanced 20 ms per frame (tools.bench.drive_lockstep)."""

from __future__ import annotations

from pathlib import Path

from dubhash.audio.earcon import EARCON
from dubhash.clock import FakeClock
from dubhash.config.pairs import load_pairs
from dubhash.legs.fake import FakeLeg
from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from dubhash.session.call_session import CallSession
from dubhash.session.prompts import opening_text
from dubhash.session.providers import Providers
from dubhash.types import Turn
from tests.audio_fixtures import conversation, speech
from tests.floor_helpers import RecordingBus
from tools.bench import drive_lockstep

PAIRS = load_pairs(Path(__file__).resolve().parent.parent / "dubhash" / "config" / "pairs.yaml")
PAIR = PAIRS["fake-fake"]
MS_PER_CHAR = 60  # FakeTTS: 60 ms per char is 3 frames, so every utterance is frame-aligned

TIMINGS = (
    "t_speech_start",
    "t_speech_end",
    "t_eou",
    "t_stt_final",
    "t_mt",
    "t_tts_ttfb",
    "t_play_start",
    "t_play_end",
)


class RecordingWriter:
    def __init__(self) -> None:
        self.turns: list[tuple[Turn, bool]] = []
        self.finished: list[dict] = []

    def enqueue_turn(self, turn: Turn, record: bool) -> bool:
        self.turns.append((turn, record))
        return True

    def finish_call(self, call_id: str, **kw) -> dict:
        self.finished.append({"call_id": call_id, **kw})
        return kw


def tts_pcm(text: str) -> bytes:
    return FakeTTS(ms_per_char=MS_PER_CHAR)._pcm(len(text) * MS_PER_CHAR)


def make(script_a, script_b, **kw):
    clock = FakeClock()
    bus = RecordingBus()
    writer = RecordingWriter()
    providers = Providers(
        stt={"a": FakeSTT(script=script_a), "b": FakeSTT(script=script_b)},
        mt=FakeMT(),
        tts={"a": FakeTTS(ms_per_char=MS_PER_CHAR), "b": FakeTTS(ms_per_char=MS_PER_CHAR)},
        vad_factory=EnergyVAD,
    )
    legs = {s: FakeLeg(s, clock=clock, playout=True) for s in ("a", "b")}
    session = CallSession("c_e2e", PAIR, providers, bus=bus, writer=writer, clock=clock, **kw)
    return session, legs, clock, bus, writer


def check_turn_timings(turn: Turn) -> None:
    values = [getattr(turn, k) for k in TIMINGS if getattr(turn, k) is not None]
    assert values == sorted(values), turn


async def test_scripted_conversation_end_to_end():
    a_says = ["bhaiya main gate ke bahar hoon", "ek minute ruko"]
    b_says = ["kaunsa building chahiye aapko bataiye", "haan"]
    session, legs, clock, bus, writer = make(a_says, b_says)
    # A: opening plays (~5 s), A speaks, later talks briefly (0.8 s) over B's playback.
    pcm_a = conversation(
        [("silence", 5600), ("speech", 1500), ("silence", 6400), ("speech", 800)]
        + [("silence", 4000)]
    )
    # B: replies after hearing A, then a short "haan" during A's second playback.
    pcm_b = conversation(
        [("silence", 10600), ("speech", 1800), ("silence", 3400), ("speech", 400)]
        + [("silence", 2100)]
    )
    await session.attach_leg("a", legs["a"])
    assert session.status == "ringing"
    await session.attach_leg("b", legs["b"])

    await drive_lockstep(session, legs, clock, {"a": pcm_a, "b": pcm_b})
    assert session.floor.state == "IDLE" and not session.floor.queue
    await session.wait_closed()

    # lifecycle
    assert session.status == "ended"
    calls = [e.payload["status"] for e in bus.kinds("call")]
    assert calls == ["active", "ended"]
    leg_events = [(e.payload["side"], e.payload["status"]) for e in bus.kinds("leg")]
    assert leg_events[:2] == [("a", "attached"), ("b", "attached")]
    assert sorted(leg_events[2:]) == [("a", "closed"), ("b", "closed")]
    assert legs["a"].closed and legs["b"].closed

    # turns: A1, B1, B's backchannel (logged when discarded), A2 (queued behind B1)
    turns = session.turn_log.turns
    assert [(t.side, t.src_text, t.backchannel) for t in turns] == [
        ("a", a_says[0], False),
        ("b", b_says[0], False),
        ("b", "haan", True),
        ("a", a_says[1], False),
    ]
    assert [t.seq for t in turns] == [0, 1, 2, 3]
    for t in turns:
        check_turn_timings(t)
    spoken = [t for t in turns if not t.backchannel]
    starts = [t.t_speech_start for t in spoken]
    assert starts == sorted(starts)
    plays = [t.t_play_start for t in spoken]
    assert plays == sorted(plays)
    assert all(t.clipped_ms is None for t in spoken)  # every translation played in full
    a2 = turns[3]
    assert a2.t_eou < turns[1].t_play_end <= a2.t_play_start  # queued, then played after B1
    bc = turns[2]
    assert bc.tgt_text is None and bc.t_mt is None and bc.t_play_start is None
    assert [t for t, _ in writer.turns] == turns
    assert all(rec is False for _, rec in writer.turns)

    # the backchannel was never translated or synthesised
    tts_a = session.providers.tts["a"]
    assert not any("haan" in text for text, _, _ in tts_a.requests)  # type: ignore[attr-defined]

    # outbound audio: opening line, then earcon + translation, per leg
    out_a, out_b = legs["a"].outbound, legs["b"].outbound
    assert out_a.startswith(tts_pcm(opening_text("hi-IN", "kn-IN")))
    assert out_b.startswith(tts_pcm(opening_text("kn-IN", "hi-IN")))
    assert out_a.count(EARCON) == 1 and out_b.count(EARCON) == 2
    for t in spoken:
        out = out_b if t.side == "a" else out_a
        tail = tts_pcm(t.tgt_text)[-6400:]  # last 200 ms: past the ducked original
        assert tail in out, t.tgt_text
    assert [e for e in legs["a"].log if e[0] == "clear"] == []
    assert [e for e in legs["b"].log if e[0] == "clear"] == []

    # summary and the end-of-call row
    s = session.summary()
    assert s["call_id"] == "c_e2e" and s["status"] == "ended" and s["mode"] == "interpret"
    assert s["pair"] == "fake-fake" and s["turns"] == 3 and s["escape_used"] is False
    assert s["p50_first_audio_ms"] is not None and s["p95_first_audio_ms"] is not None
    assert 18 <= s["duration_s"] <= 20
    (fin,) = writer.finished
    assert fin["call_id"] == "c_e2e" and fin["escape_used"] is False


async def test_barge_in_clears_and_clips():
    a_says = ["bhaiya main gate ke bahar hoon aur building number bataiye"]
    b_says = ["ruko ruko main aa raha hoon abhi"]
    session, legs, clock, bus, writer = make(a_says, b_says, opening_line=False)
    # A1 plays to B from ~2.1 s for ~3.6 s; B talks over it for 1.5 s from 2.6 s.
    pcm_a = conversation([("silence", 300), ("speech", 1500), ("silence", 8000)])
    pcm_b = conversation([("silence", 2600), ("speech", 1500), ("silence", 5700)])
    await session.attach_leg("a", legs["a"])
    await session.attach_leg("b", legs["b"])
    await drive_lockstep(session, legs, clock, {"a": pcm_a, "b": pcm_b})
    await session.wait_closed()

    turns = session.turn_log.turns
    a1 = next(t for t in turns if t.side == "a")
    assert a1.clipped_ms is not None and a1.clipped_ms > 0
    assert [e for e in legs["b"].log if e[0] == "clear"]  # B's leg was cleared
    (barge,) = bus.kinds("barge_in")
    assert barge.payload["side"] == "b" and barge.payload["clipped_ms"] == a1.clipped_ms
    # B's interruption was itself translated and played to A
    b1 = next(t for t in turns if t.side == "b")
    assert b1.src_text == b_says[0] and b1.clipped_ms is None and b1.t_play_start > a1.t_play_end
    assert EARCON in legs["a"].outbound
    assert session.floor.state == "IDLE"


async def test_dtmf_0_bypass_forwards_audio_both_ways():
    session, legs, clock, bus, writer = make(["ek do teen char"], [], opening_line=False)
    after_a = speech(1000, f0=210.0)
    after_b = speech(1000, f0=95.0)
    pcm_a = conversation([("silence", 1000)]) + after_a + conversation([("silence", 500)])
    pcm_b = conversation([("silence", 1500)]) + after_b

    def press(i: int) -> None:
        if i == 10:  # 200 ms in
            legs["a"].inject_dtmf("0")

    await session.attach_leg("a", legs["a"])
    await session.attach_leg("b", legs["b"])
    await drive_lockstep(session, legs, clock, {"a": pcm_a, "b": pcm_b}, on_frame=press)
    await session.wait_closed()

    assert session.floor.bypass
    assert session.summary()["escape_used"] is True
    assert bus.kinds("bypass")
    # raw frames forwarded at 0 dB, both ways
    assert after_a in legs["b"].outbound
    assert after_b in legs["a"].outbound
    # nothing was interpreted after the escape
    assert session.turn_log.turns == []
    assert EARCON not in legs["a"].outbound and EARCON not in legs["b"].outbound
    assert writer.finished[0]["escape_used"] is True

"""Chunked interpretation through a whole CallSession (L9): FakeLegs, all-fake providers,
FakeClock, lockstep. A's long utterance is cut at its comma pause; chunk 0 plays to B
while A is still speaking, chunk 1 follows; both are turns of one utterance. The
cross-talk gate and the echo guard still keep B's mic from making turns."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from dubhash.audio.earcon import EARCON
from dubhash.clock import FakeClock
from dubhash.config.pairs import load_pairs
from dubhash.legs.fake import FakeLeg
from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from dubhash.session.call_session import CallSession
from dubhash.session.providers import Providers
from dubhash.types import Translation
from tests.audio_fixtures import silence, speech, tone
from tests.floor_helpers import RecordingBus
from tests.session.test_crosstalk_session import at_dbfs, gain_db
from tools.bench import drive_lockstep

PAIRS = load_pairs(Path(__file__).resolve().parents[2] / "dubhash" / "config" / "pairs.yaml")
CHUNKED = dataclasses.replace(
    PAIRS["fake-fake"], early_final_ms=200, chunking=True, chunk_pause_ms=300, chunk_min_ms=1200
)
C0, C1 = "bhaiya main gate ke bahar hoon,", "aap neeche aa jaiye please."  # clause ends (L9)
# A: 1.5 s clause, 400 ms comma pause, 1.5 s clause (speech 300..3700 ms)
A_VOICE = at_dbfs(
    silence(300) + speech(1500) + silence(400) + speech(1500, f0=150.0) + silence(5000), -20.0
)


async def run(inbound, *, pair=CHUNKED, b_script=("kaunsa building chahiye aapko",), **kw):
    clock = FakeClock()
    bus = RecordingBus()
    prov = Providers(
        stt={"a": FakeSTT(script=[C0, C1]), "b": FakeSTT(script=list(b_script))},
        mt=FakeMT(),
        tts={"a": FakeTTS(), "b": FakeTTS()},
        vad_factory=kw.pop("vad", EnergyVAD),
    )
    legs = {s: FakeLeg(s, clock=clock) for s in ("a", "b")}
    session = CallSession("c_ch", pair, prov, bus=bus, clock=clock, opening_line=False, **kw)
    await session.attach_leg("a", legs["a"])
    await session.attach_leg("b", legs["b"])
    await drive_lockstep(session, legs, clock, inbound)
    await session.wait_closed()
    return session, bus, legs


async def test_chunk_plays_to_b_while_a_still_speaks_and_both_are_turns():
    session, _, legs = await run({"a": A_VOICE, "b": silence(len(A_VOICE) // 32)})
    turns = session.turn_log.turns
    assert [(t.side, t.src_text, t.chunk) for t in turns] == [("a", C0, 0), ("a", C1, 1)]
    t0, t1 = turns
    assert t0.utterance_id == t1.utterance_id is not None
    assert t0.t_utt_start == t1.t_utt_start == t0.t_speech_start
    assert t0.t_play_start < 3700  # heard before A finished speaking
    assert t1.t_play_start >= t0.t_play_end  # one behind the other
    assert t0.clipped_ms is None and t1.clipped_ms is None
    assert session.idle and session.floor.state == "IDLE"
    # one earcon per utterance: the second chunk continues without one
    assert bytes(legs["b"].outbound).count(EARCON[:640]) == 1


async def test_the_session_keeps_the_utterance_open_until_its_final_chunk():
    """The sink's in-flight utterance (STT binding, cross-talk judge, backchannel log) ends
    with the final chunk, not the first."""
    prov = Providers(
        stt={"a": FakeSTT(), "b": FakeSTT()},
        mt=FakeMT(),
        tts={"a": FakeTTS(), "b": FakeTTS()},
        vad_factory=EnergyVAD,
    )
    session = CallSession("c_s", CHUNKED, prov, bus=RecordingBus(), clock=FakeClock())
    handed = []

    async def on_translation(tr):
        handed.append(tr)

    session.floor.on_translation = on_translation  # type: ignore[method-assign]
    sink = session._sink
    await sink.on_speech_start("a")
    first = await session.interpreters["a"].stt.open("hi-IN")  # chunk 0's stream
    a0 = Translation("a", 0, "x", "y", _no_audio(), "v", utterance_id=0, final_chunk=False)
    await sink.on_translation(a0)
    assert len(sink.utts["a"]) == 1
    second = await session.interpreters["a"].stt.open("hi-IN")  # chunk 1's stream
    assert second._utt is first._utt is sink.utts["a"][0]  # type: ignore[attr-defined]
    await sink.on_translation(Translation("a", 1, "x", "y", _no_audio(), "v", utterance_id=0))
    assert len(sink.utts["a"]) == 0 and len(handed) == 2


async def _no_audio():
    return
    yield b""  # pragma: no cover


async def test_unchunked_pair_is_one_turn():
    off = dataclasses.replace(CHUNKED, chunking=False)
    session, _, legs = await run(
        {"a": A_VOICE, "b": silence(len(A_VOICE) // 32)},
        pair=off,
    )
    assert [(t.side, t.chunk) for t in session.turn_log.turns] == [("a", 0)]


async def test_cross_talk_on_bs_mic_still_makes_no_b_turn_with_chunks():
    session, bus, _ = await run({"a": A_VOICE, "b": gain_db(A_VOICE, -12.0)}, co_located=True)
    assert [(t.side, t.chunk) for t in session.turn_log.turns] == [("a", 0), ("a", 1)]
    assert bus.kinds("crosstalk")


async def test_echo_of_a_chunk_on_bs_mic_never_barges_in():
    """B's mic picks up chunk 0's playback 14 dB down while A keeps talking."""
    echo = tone(2500, hz=FakeTTS.TONE_HZ, amp=FakeTTS.AMPLITUDE // 5)
    b_mic = silence(2800) + echo + silence(4000)
    session, bus, _ = await run(
        {"a": A_VOICE, "b": b_mic}, vad=lambda: EnergyVAD(threshold_dbfs=-55.0)
    )
    turns = session.turn_log.turns
    assert [(t.side, t.chunk, t.clipped_ms) for t in turns] == [("a", 0, None), ("a", 1, None)]
    assert bus.kinds("barge_in") == []

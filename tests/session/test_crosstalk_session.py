"""Cross-talk and echo through a whole CallSession (FakeLegs, all-fake providers, FakeClock).

Two phones in one room: B's mic also carries A's voice, about 12 dB down. That must not
become a B turn. Two people genuinely talking over each other at similar levels keep the
normal floor behaviour. A leg's own playback picked up by its mic (echo) never starts
speech or a barge-in."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from convox.clock import FakeClock
from convox.config.pairs import load_pairs
from convox.legs.fake import FakeLeg
from convox.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from convox.session.call_session import CallSession
from convox.session.providers import Providers
from tests.audio_fixtures import silence, speech, tone
from tests.floor_helpers import RecordingBus
from tools.bench import drive_lockstep

PAIR = load_pairs(Path(__file__).resolve().parents[2] / "convox" / "config" / "pairs.yaml")[
    "fake-fake"
]
A_SAYS = "bhaiya main gate ke bahar hoon"
B_SAYS = "kaunsa building chahiye aapko"


def at_dbfs(pcm: bytes, dbfs: float) -> bytes:
    """Scale so the voiced part's RMS is `dbfs`."""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    voiced = x[np.abs(x) > 0]
    rms = float(np.sqrt(np.mean(voiced * voiced)))
    gain = 32768 * 10 ** (dbfs / 20) / rms
    return np.clip(np.rint(x * gain), -32768, 32767).astype(np.int16).tobytes()


def gain_db(pcm: bytes, db: float) -> bytes:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) * 10 ** (db / 20)
    return np.rint(x).astype(np.int16).tobytes()


async def run(inbound, *, b_script=(B_SAYS,), vad=EnergyVAD, **kw):
    clock = FakeClock()
    bus = RecordingBus()
    prov = Providers(
        stt={"a": FakeSTT(script=[A_SAYS]), "b": FakeSTT(script=list(b_script))},
        mt=FakeMT(),
        tts={"a": FakeTTS(), "b": FakeTTS()},
        vad_factory=vad,
    )
    legs = {s: FakeLeg(s, clock=clock) for s in ("a", "b")}
    session = CallSession("c_xt", PAIR, prov, bus=bus, clock=clock, opening_line=False, **kw)
    await session.attach_leg("a", legs["a"])
    await session.attach_leg("b", legs["b"])
    await drive_lockstep(session, legs, clock, inbound)
    await session.wait_closed()
    return session, bus, legs


def turns(session):
    return [(t.side, t.src_text, t.clipped_ms) for t in session.turn_log.turns]


A_VOICE = at_dbfs(silence(300) + speech(1500) + silence(3000), -20.0)


async def test_quiet_copy_of_a_on_bs_mic_makes_no_b_turn():
    b_mic = gain_db(A_VOICE, -12.0)  # the same voice at -32 dBFS
    session, bus, _ = await run({"a": A_VOICE, "b": b_mic}, co_located=True)
    assert turns(session) == [("a", A_SAYS, None)]
    events = bus.kinds("crosstalk")
    assert events and all(e.payload["side"] == "b" for e in events)
    assert {e.payload["reason"] for e in events} == {"level"}


async def test_cross_talk_that_starts_first_on_bs_mic_is_dropped_at_eou():
    """Jitter: B's copy reaches the server 100 ms before A's own audio, so B's utterance
    opens first; it is recognised as cross-talk when it ends."""
    b_mic = gain_db(A_VOICE[3200:], -12.0)  # 100 ms earlier
    session, bus, _ = await run({"a": A_VOICE, "b": b_mic}, co_located=True)
    assert turns(session) == [("a", A_SAYS, None)]
    stages = {e.payload["stage"] for e in bus.kinds("crosstalk")}
    assert "eou" in stages
    assert session.idle and session.floor.state == "IDLE"


async def test_without_co_location_the_copy_is_a_turn():
    """PSTN legs are separate lines: the gate is off (and this is what it prevents)."""
    b_mic = gain_db(A_VOICE, -12.0)
    session, bus, _ = await run({"a": A_VOICE, "b": b_mic})
    assert ("b", B_SAYS) in [(s, t) for s, t, _ in turns(session)]
    assert bus.kinds("crosstalk") == []


async def test_two_real_speakers_at_similar_levels_keep_the_normal_floor():
    a = at_dbfs(silence(300) + speech(1500, f0=140.0) + silence(4000), -20.0)
    b = at_dbfs(silence(700) + speech(1500, f0=230.0) + silence(3600), -22.0)
    gated, bus, _ = await run({"a": a, "b": b}, co_located=True)
    plain, _, _ = await run({"a": a, "b": b})
    assert sorted(turns(gated)) == sorted(turns(plain))
    assert {s for s, _, _ in turns(gated)} == {"a", "b"}
    assert bus.kinds("crosstalk") == []


TTS_DBFS = 20 * np.log10(FakeTTS.AMPLITUDE / np.sqrt(2) / 32768)  # about -27.3


def sensitive_vad() -> EnergyVAD:
    return EnergyVAD(threshold_dbfs=-55.0)  # hears the quiet echo as speech


async def test_echo_of_the_playback_on_the_listeners_mic_never_barges_in():
    """A's translation plays to B from about 2.4 s; B's mic picks it up 14 dB down."""
    echo = tone(1500, hz=FakeTTS.TONE_HZ, amp=FakeTTS.AMPLITUDE // 5)
    b_mic = silence(2700) + echo + silence(2000)
    session, bus, legs = await run({"a": A_VOICE, "b": b_mic}, vad=sensitive_vad)
    assert turns(session) == [("a", A_SAYS, None)]  # played in full, no B turn
    assert bus.kinds("barge_in") == []
    assert [e for e in legs["b"].log if e[0] == "clear"] == []
    assert {e.payload["reason"] for e in bus.kinds("crosstalk")} == {"echo"}


async def test_quiet_speech_over_the_playback_still_barges_in():
    """A caller 8 dB under the playback level is a person, not echo (PSTN speech levels)."""
    b_mic = silence(2700) + at_dbfs(speech(2000, f0=230.0), TTS_DBFS - 8) + silence(3000)
    session, bus, _ = await run({"a": A_VOICE, "b": b_mic}, vad=sensitive_vad)
    assert bus.kinds("barge_in")
    assert ("b", B_SAYS) in [(s, t) for s, t, _ in turns(session)]


async def test_loud_speech_over_the_playback_still_barges_in():
    b_mic = silence(2700) + at_dbfs(speech(2000, f0=230.0), -18.0) + silence(3000)
    session, bus, _ = await run({"a": A_VOICE, "b": b_mic})
    assert bus.kinds("barge_in")
    assert ("b", B_SAYS) in [(s, t) for s, t, _ in turns(session)]


class LangSTT(FakeSTT):
    """FakeSTT whose streams report a detected language, as Sarvam's finals can."""

    def __init__(self, lang: str, **kw) -> None:
        super().__init__(**kw)
        self.detected = lang

    async def open(self, lang: str):
        stream = await super().open(lang)
        stream.detected_lang = self.detected  # type: ignore[attr-defined]
        return stream


async def lang_run(detected: str, **kw):
    clock = FakeClock()
    bus = RecordingBus()
    prov = Providers(
        stt={"a": FakeSTT(script=[A_SAYS]), "b": LangSTT(detected, script=[B_SAYS])},
        mt=FakeMT(),
        tts={"a": FakeTTS(), "b": FakeTTS()},
        vad_factory=EnergyVAD,
    )
    legs = {s: FakeLeg(s, clock=clock) for s in ("a", "b")}
    session = CallSession("c_lang", PAIR, prov, bus=bus, clock=clock, opening_line=False, **kw)
    await session.attach_leg("a", legs["a"])
    await session.attach_leg("b", legs["b"])
    b = at_dbfs(silence(300) + speech(1500, f0=230.0) + silence(3000), -20.0)
    await drive_lockstep(session, legs, clock, {"b": b})
    await session.wait_closed()
    return session, bus


async def test_final_in_the_other_sides_language_is_cross_talk():
    session, bus = await lang_run("hi-IN", co_located=True)  # b speaks kn-IN
    assert turns(session) == []
    (ev,) = bus.kinds("crosstalk")
    assert ev.payload["reason"] == "lang" and ev.payload["stage"] == "eou"


async def test_language_check_keeps_own_language_and_is_off_on_pstn():
    session, _ = await lang_run("kn-IN", co_located=True)
    assert [s for s, _, _ in turns(session)] == ["b"]
    session, _ = await lang_run("hi-IN")
    assert [s for s, _, _ in turns(session)] == ["b"]

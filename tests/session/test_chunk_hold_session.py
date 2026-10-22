"""The L9 stall through a whole CallSession (live c_uiaxt3slo6): a chunked utterance's hold
must never outlive the utterance. FakeLegs, fake providers, FakeClock, lockstep.

The path that stalled: A's chunk 0 is committed while A's previous translation is still
playing to B, so it waits in the floor's queue. The rest of A's utterance is then
discarded (an empty final), and the discard reaches the floor before chunk 0 has started,
so it found no hold to release. Chunk 0 then started and took the hold, which nothing
released but the 4 s rescue: B's reply sat ready in the queue for ~4 s."""

from __future__ import annotations

from convox.clock import FakeClock
from convox.legs.fake import FakeLeg
from convox.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from convox.session.call_session import CallSession
from convox.session.providers import Providers
from tests.audio_fixtures import silence, speech
from tests.floor_helpers import RecordingBus
from tests.session.test_chunked_session import CHUNKED
from tests.session.test_crosstalk_session import at_dbfs
from tools.bench import drive_lockstep

LONG = "main abhi ghar par nahi hoon, shaam ko aaunga."
C0 = "bhaiya main gate ke bahar hoon,"
REPLY = "theek hai main aata hoon."


def _voice(*parts: tuple[str, int]) -> bytes:
    out = b""
    for kind, ms in parts:
        out += speech(ms) if kind == "speech" else silence(ms)
    return at_dbfs(out, -20.0)


# A: a long line (its translation plays ~3 s to B), then a chunked line whose chunk 0
# commits during that playback and whose rest (a short tail) comes back empty.
A_MIC = _voice(
    ("silence", 300),
    ("speech", 1500),
    ("silence", 700),
    ("speech", 1500),
    ("silence", 400),
    ("speech", 400),
    ("silence", 12000),
)
# B replies once A's translations have played.
B_MIC = _voice(("silence", 7800), ("speech", 1500), ("silence", 6000))


async def run(a_script: list[str], b_script: list[str], inbound: dict[str, bytes]):
    clock = FakeClock()
    bus = RecordingBus()
    prov = Providers(
        stt={"a": FakeSTT(script=a_script), "b": FakeSTT(script=b_script)},
        mt=FakeMT(),
        tts={"a": FakeTTS(), "b": FakeTTS()},
        vad_factory=EnergyVAD,
    )
    legs = {s: FakeLeg(s, clock=clock) for s in ("a", "b")}
    session = CallSession("c_hold", CHUNKED, prov, bus=bus, clock=clock, opening_line=False)
    await session.attach_leg("a", legs["a"])
    await session.attach_leg("b", legs["b"])
    await drive_lockstep(session, legs, clock, inbound)
    await session.wait_closed()
    return session, bus


async def test_a_chunk_queued_when_the_rest_is_discarded_does_not_stall_the_reply():
    session, bus = await run([LONG, C0, ""], [REPLY], {"a": A_MIC, "b": B_MIC})
    turns = session.turn_log.turns
    assert [(t.side, t.src_text, t.chunk) for t in turns] == [
        ("a", LONG, 0),
        ("a", C0, 0),
        ("b", REPLY, 0),
    ]
    a_long, a0, b = turns
    assert a0.t_play_start >= a_long.t_play_end  # chunk 0 did wait behind the long line
    assert b.t_play_start - b.t_tts_ttfb <= 500  # was ~4 s: the hold outlived A's utterance
    assert session.floor.state == "IDLE"
    assert bus.kinds("hold_released") == []  # released by the discard, not the timeout

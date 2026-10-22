"""_TrackedSTT binds each STT stream to its utterance, so backchannel turns get the final
that was actually used. With early final (L3) an utterance can open a second stream (a
restart after speech resumed); that stream belongs to the same, still-open utterance."""

from collections import deque

from convox.clock import FakeClock
from convox.providers.fakes import FakeSTT
from convox.session.call_session import _TrackedSTT, _Utt


async def test_restarted_stream_updates_the_current_utterance():
    clock = FakeClock()
    utts: deque[_Utt] = deque([_Utt(t_start=0)])
    stt = _TrackedSTT(FakeSTT(script=["ek do", "ek do teen chaar"]), utts, clock)
    early = await stt.open("hi-IN")
    await early.push(b"\x01\x00" * 3200)
    assert await early.end() == "ek do"
    clock.advance(300)
    restarted = await stt.open("hi-IN")  # speech resumed: same utterance, new stream
    await restarted.push(b"\x01\x00" * 6400)
    assert await restarted.end() == "ek do teen chaar"
    assert utts[0].final == "ek do teen chaar" and utts[0].t_stt_final == 300


async def test_next_utterance_still_binds_to_its_own_entry():
    clock = FakeClock()
    first = _Utt(t_start=0, t_eou=900)
    utts: deque[_Utt] = deque([first])
    stt = _TrackedSTT(FakeSTT(script=["one two three", "four five six"]), utts, clock)
    s1 = await stt.open("hi-IN")
    first.stream = True
    second = _Utt(t_start=1000)
    utts.append(second)
    s2 = await stt.open("hi-IN")
    await s1.push(b"\x01\x00" * 3200)
    await s2.push(b"\x01\x00" * 3200)
    await s1.end()
    await s2.end()
    assert second.stream and second.final == "four five six"


class _CrosstalkJudge:
    def __init__(self) -> None:
        self.settled: list[object] = []

    def before_final(self, utt: _Utt) -> str | None:
        return "level"

    def after_final(self, utt: _Utt, lang: str | None) -> str | None:
        return None

    def settle(self, stream: object) -> None:
        self.settled.append(stream)


async def test_cross_talk_returns_an_empty_final_and_hands_the_stream_to_settle():
    clock = FakeClock()
    utt = _Utt(t_start=0)
    judge = _CrosstalkJudge()
    stt = _TrackedSTT(FakeSTT(script=["ek do"]), deque([utt]), clock, judge)
    s = await stt.open("hi-IN")
    await s.push(b"\x01\x00" * 3200)
    assert await s.end() == ""
    assert utt.final == "" and utt.crosstalk == "level"
    (inner,) = judge.settled
    await s.aclose()  # the settle task owns the inner stream now
    assert not inner._closed  # type: ignore[attr-defined]

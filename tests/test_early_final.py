"""Early final (pair.early_final_ms, open question L3): finalise STT after a short silence so
MT/TTS run during the EOU wait; discard it and restart STT on the whole buffered utterance if
speech resumes before EOU. Delivery never happens before EOU.

Live-like budget on the FakeClock: STT final 290 ms after end(), MT 700 ms (pivot en->kn),
TTS first byte 250 ms.
"""

import asyncio
import dataclasses
from pathlib import Path

from dubhash.clock import FakeClock
from dubhash.config.pairs import load_pairs
from dubhash.interp.pipeline import Interpreter
from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from tests.audio_fixtures import conversation, frames
from tests.test_pipeline import RecordingSink

PAIRS = load_pairs(Path(__file__).resolve().parent.parent / "dubhash" / "config" / "pairs.yaml")
BASE = dataclasses.replace(PAIRS["hi-kn-direct"], spec_mt=False, early_final_ms=0)
EARLY = dataclasses.replace(BASE, early_final_ms=200)
STT_MS, MT_MS, TTS_MS = 290, 700, 250


async def feed(pcm: bytes, clock: FakeClock):
    """Like test_pipeline.feed, but lets the loop settle after each frame, so chains of
    tasks (end -> MT -> TTS) do not lag the FakeClock by whole frames."""
    for f in frames(pcm):
        clock.advance(20)
        yield f
        for _ in range(10):
            await asyncio.sleep(0)


class TimedSink(RecordingSink):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock)
        self.t_delivered: list[int] = []

    async def on_translation(self, tr):
        self.t_delivered.append(self.clock.now_ms())
        await super().on_translation(tr)


class TimedMT(FakeMT):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.t_start: list[int] = []

    async def translate(self, text, src, tgt):
        self.t_start.append(self.clock.now_ms())
        return await super().translate(text, src, tgt)


class SpySTT(FakeSTT):
    """FakeSTT that keeps every opened stream's pushed bytes and end() times."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.pushed: list[bytearray] = []
        self.t_end: list[int] = []

    async def open(self, lang):
        s = await super().open(lang)
        buf = bytearray()
        self.pushed.append(buf)
        push, end = s.push, s.end

        async def spy_push(frame):
            buf.extend(frame)
            await push(frame)

        async def spy_end():
            self.t_end.append(self.clock.now_ms())
            return await end()

        s.push, s.end = spy_push, spy_end
        return s


def make(pair, script, *, stt_ms=STT_MS, mt_ms=MT_MS, tts_ms=TTS_MS, mt_cls=None):
    clock = FakeClock()
    stt = SpySTT(script=script, final_delay_ms=stt_ms, clock=clock)
    mt = (mt_cls or TimedMT)(delay_ms=mt_ms, clock=clock)
    sink = TimedSink(clock)
    interp = Interpreter(
        call_id="c_test",
        side="a",
        pair=pair,
        stt=stt,
        mt=mt,
        tts=FakeTTS(ttfb_ms=tts_ms, clock=clock),
        vad=EnergyVAD(),
        clock=clock,
        sink=sink,
    )
    return clock, interp, sink, stt, mt


async def run(pair, script, pcm, **kw):
    clock, interp, sink, stt, mt = make(pair, script, **kw)
    await asyncio.wait_for(interp.run(feed(pcm, clock)), 5)
    await interp.aclose()
    return interp, sink, stt, mt


ONE = conversation([("silence", 300), ("speech", 1500), ("silence", 3000)])


async def test_a_mt_starts_during_eou_wait_and_delivery_is_at_max_of_eou_and_ready():
    _, off_sink, _, _ = await run(BASE, ["main gate ke bahar hoon"], ONE)
    interp, sink, stt, mt = await run(EARLY, ["main gate ke bahar hoon"], ONE)
    (off,) = off_sink.translations
    (tr,) = sink.translations
    t = tr.timings
    end = t["t_speech_end"]
    assert tr.src_text == off.src_text == "main gate ke bahar hoon"
    assert tr.tgt_text == off.tgt_text
    assert stt.opened == 1 and mt.calls == 1
    # STT finalised early_final_ms after the last speech frame, MT right after the final
    assert abs(t["t_early_final"] - (end + 200)) <= 40
    assert stt.t_end == [t["t_early_final"]]  # t_early_final: when end() was sent
    assert abs(mt.t_start[0] - (end + 200 + STT_MS)) <= 40
    # FakeClock sleepers wake on the next 20 ms frame
    assert STT_MS <= t["t_stt_final"] - t["t_early_final"] <= STT_MS + 20
    assert MT_MS <= t["t_mt"] - t["t_stt_final"] <= MT_MS + 20
    # the timings keep their meaning: EOU is still eou_ms after speech end
    assert t["t_eou"] - end >= BASE.eou_ms
    assert t["t_eou"] == off.timings["t_eou"]
    ready = end + 200 + STT_MS + MT_MS + TTS_MS
    assert abs(sink.t_delivered[0] - max(t["t_eou"], ready)) <= 40
    assert sink.t_delivered[0] >= t["t_eou"]
    assert off_sink.t_delivered[0] - sink.t_delivered[0] >= 300  # eou 550 - early 200
    assert (t["early_final_used"], t["early_final_restarts"]) == (1, 0)
    assert (interp.early_started, interp.early_used, interp.early_restarts) == (1, 1, 0)
    assert sink.kinds() == ["speech_start", "speech_end", "translation"]


async def test_a_ready_before_eou_is_held_until_eou():
    interp, sink, _, _ = await run(EARLY, ["main gate ke bahar hoon"], ONE, mt_ms=0, tts_ms=0)
    (tr,) = sink.translations
    assert tr.timings["t_tts_ttfb"] < tr.timings["t_eou"]
    assert sink.t_delivered[0] == tr.timings["t_eou"]  # never before EOU


PAUSE = conversation(
    [("silence", 300), ("speech", 1000), ("silence", 300), ("speech", 800), ("silence", 3000)]
)


async def test_b_resumed_speech_discards_early_final_and_restarts_with_full_audio():
    full = "ek do teen chaar paanch"
    interp, sink, stt, mt = await run(EARLY, ["ek do teen", full], PAUSE, stt_ms=50)
    (tr,) = sink.translations
    assert sink.kinds() == ["speech_start", "speech_end", "translation"]
    assert tr.src_text == full and tr.tgt_text == "[kn-IN] " + full
    # the MT that was in flight on the fragment was cancelled
    assert mt.texts == ["ek do teen", full] and mt.cancelled == 1
    assert stt.opened == 2
    first, second = (bytes(b) for b in stt.pushed)
    # the new stream got everything the first one had, then the gap and the rest, live
    assert second.startswith(first)
    # first: pre-roll 200 + speech 1000 + silence 200; second adds the rest of the gap
    # (100), the resumed speech (800) and silence up to its own early final (200)
    assert len(first) == 32 * 1400
    assert len(second) == len(first) + 32 * (100 + 800 + 200)
    assert second in PAUSE + b"\x00" * 640
    assert (tr.timings["early_final_used"], tr.timings["early_final_restarts"]) == (1, 1)
    assert (interp.early_started, interp.early_restarts, interp.early_used) == (2, 1, 1)


async def test_b_speech_resumes_while_the_early_final_is_still_pending():
    full = "ek do teen chaar paanch"
    interp, sink, stt, mt = await run(EARLY, ["ek do teen", full], PAUSE)  # final takes 290
    (tr,) = sink.translations
    assert tr.src_text == full
    assert mt.texts == [full]  # the early final arrived after the restart: never translated
    assert stt.opened == 2
    assert tr.timings["early_final_restarts"] == 1


async def test_c_off_keeps_the_old_timings_and_one_stream():
    interp, sink, stt, _ = await run(BASE, ["main gate ke bahar hoon"], ONE)
    (tr,) = sink.translations
    assert "t_early_final" not in tr.timings and "early_final_used" not in tr.timings
    assert stt.opened == 1 and interp.early_started == 0
    assert stt.t_end == [tr.timings["t_eou"]]


async def test_d_backchannel_early_final_is_discarded():
    pcm = conversation([("silence", 200), ("speech", 400), ("silence", 800)])
    interp, sink, stt, mt = await run(EARLY, ["haan"], pcm)
    assert sink.kinds() == ["speech_start", "speech_end", "discarded"]
    assert mt.calls == 0 and interp.early_started == 1


async def test_noise_blip_gets_no_early_final():
    pcm = conversation([("silence", 200), ("speech", 160), ("silence", 800)])
    interp, sink, stt, _ = await run(EARLY, ["should not be used"], pcm)
    assert sink.kinds() == ["speech_start", "speech_end", "discarded"]
    assert stt._peek() == "should not be used" and interp.early_started == 0


async def test_two_utterances_keep_order_and_seq():
    pcm = conversation(
        [("silence", 200), ("speech", 1000), ("silence", 800), ("speech", 1000), ("silence", 2500)]
    )
    _, sink, _, _ = await run(EARLY, ["pehla vaakya yahan", "doosra vaakya yahan"], pcm)
    assert [t.seq for t in sink.translations] == [0, 1]
    assert [t.src_text for t in sink.translations] == ["pehla vaakya yahan", "doosra vaakya yahan"]
    assert all(t.timings["early_final_used"] == 1 for t in sink.translations)


async def test_pivot_early_final_is_english_and_uses_mt_src():
    class LangMT(TimedMT):
        def __init__(self, **kw) -> None:
            super().__init__(**kw)
            self.langs: list[tuple[str, str]] = []

        async def translate(self, text, src, tgt):
            self.langs.append((src, tgt))
            return await super().translate(text, src, tgt)

    pivot = dataclasses.replace(PAIRS["hi-kn"], spec_mt=False, early_final_ms=200)
    _, sink, _, mt = await run(pivot, ["I am at the gate"], ONE, mt_cls=LangMT)
    assert mt.langs == [("en-IN", "kn-IN")]
    assert sink.translations[0].timings["early_final_used"] == 1


async def test_aclose_cancels_inflight_early_work():
    clock, interp, sink, stt, mt = make(EARLY, ["main gate ke bahar hoon"], mt_ms=10_000)
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 1000)])
    await interp.run_until_frames_end(feed(pcm, clock))
    assert mt.calls == 1
    await interp.aclose()
    for _ in range(5):
        await asyncio.sleep(0)
    assert mt.cancelled == 1
    assert sink.kinds() == ["speech_start", "speech_end"]


async def test_early_with_speculation_on_still_delivers_the_final():
    pair = dataclasses.replace(EARLY, spec_mt=True)
    interp, sink, _, mt = await run(pair, ["main gate ke bahar hoon"], ONE)
    (tr,) = sink.translations
    assert tr.tgt_text == "[kn-IN] main gate ke bahar hoon"
    assert mt.calls == 1
    assert tr.timings["early_final_used"] == 1

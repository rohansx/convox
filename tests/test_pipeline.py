import asyncio
import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from dubhash.clock import FakeClock
from dubhash.config.pairs import load_pairs
from dubhash.interp.pipeline import Interpreter, InterpreterSink
from dubhash.interp.vad import DEFAULT_MODEL_PATH, SileroVAD
from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from dubhash.types import Translation
from tests.audio_fixtures import conversation, frames, harmonic, silence

PAIRS = load_pairs(Path(__file__).resolve().parent.parent / "dubhash" / "config" / "pairs.yaml")
PAIR = PAIRS["hi-kn"]
SPEC_ON = dataclasses.replace(PAIR, spec_mt=True, spec_after_ms=200)
SPEC_OFF = dataclasses.replace(PAIR, spec_mt=False)


class RecordingSink:
    def __init__(self, clock: FakeClock, consume_audio: bool = True) -> None:
        self.clock = clock
        self.events: list[tuple] = []
        self.translations: list[Translation] = []
        self.audio: list[bytes] = []
        self.consume_audio = consume_audio

    async def on_speech_start(self, side):
        self.events.append(("speech_start", side))

    async def on_speech_end(self, side):
        self.events.append(("speech_end", side))

    async def on_translation(self, tr: Translation):
        self.events.append(("translation", tr.side, tr.seq))
        self.translations.append(tr)
        if self.consume_audio:
            self.audio.append(b"".join([c async for c in tr.audio]))

    async def on_utterance_discarded(self, side):
        self.events.append(("discarded", side))

    async def on_partial(self, side, text):
        self.events.append(("partial", side, text))

    def kinds(self, include_partials: bool = False) -> list[str]:
        return [e[0] for e in self.events if include_partials or e[0] != "partial"]


async def feed(pcm: bytes, clock: FakeClock) -> AsyncIterator[bytes]:
    for f in frames(pcm):
        clock.advance(20)
        yield f
        await asyncio.sleep(0)


def make(
    clock: FakeClock,
    sink=None,
    stt=None,
    mt=None,
    tts=None,
    side="a",
    gender=None,
    pair=None,
) -> tuple[Interpreter, RecordingSink]:
    sink = sink or RecordingSink(clock)
    interp = Interpreter(
        call_id="c_test",
        side=side,
        pair=pair or PAIR,
        stt=stt or FakeSTT(),
        mt=mt if mt is not None else FakeMT(),
        tts=tts or FakeTTS(),
        vad=EnergyVAD(),
        clock=clock,
        sink=sink,
        gender=gender,
    )
    return interp, sink


def test_sink_protocol_is_runtime_checkable():
    clock = FakeClock()
    assert isinstance(RecordingSink(clock), InterpreterSink)


async def test_single_utterance_event_order_and_translation():
    clock = FakeClock()
    interp, sink = make(clock, stt=FakeSTT(script=["bhaiya main gate ke bahar hoon"]))
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    await interp.aclose()
    assert sink.kinds() == ["speech_start", "speech_end", "translation"]
    (tr,) = sink.translations
    assert tr.side == "a" and tr.seq == 0
    assert tr.src_text == "bhaiya main gate ke bahar hoon"
    assert tr.tgt_text == "[kn-IN] bhaiya main gate ke bahar hoon"
    assert tr.voice == PAIR.b.voice_m  # 140 Hz synthetic speaker, target side b's voice
    assert len(sink.audio[0]) == len(tr.tgt_text) * 60 * 32
    assert tr.source_audio and len(tr.source_audio) % 2 == 0


async def test_timings_present_and_monotonic():
    clock = FakeClock()
    interp, sink = make(clock)
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    (tr,) = sink.translations
    keys = ["t_speech_start", "t_speech_end", "t_eou", "t_stt_final", "t_mt", "t_tts_ttfb"]
    assert list(tr.timings) == [*keys, "spec_hit", "spec_used"]
    assert tr.timings["spec_hit"] in (0, 1) and tr.timings["spec_used"] in (0, 1)
    values = [tr.timings[k] for k in keys]
    assert values == sorted(values)
    assert 300 <= tr.timings["t_speech_start"] <= 360
    assert tr.timings["t_eou"] - tr.timings["t_speech_end"] >= PAIR.eou_ms


async def test_backchannel_is_discarded():
    clock = FakeClock()
    stt = FakeSTT(script=["haan"])
    mt = FakeMT()
    interp, sink = make(clock, stt=stt, mt=mt)
    pcm = conversation([("silence", 200), ("speech", 400), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    assert sink.kinds() == ["speech_start", "speech_end", "discarded"]
    assert mt.calls == 0


async def test_short_utterance_with_many_words_is_translated():
    clock = FakeClock()
    interp, sink = make(clock, stt=FakeSTT(script=["haan ji theek hai"]))
    pcm = conversation([("silence", 200), ("speech", 400), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    assert sink.kinds() == ["speech_start", "speech_end", "translation"]


async def test_long_two_word_utterance_is_translated():
    clock = FakeClock()
    interp, sink = make(clock, stt=FakeSTT(script=["haan bilkul"]))
    pcm = conversation([("silence", 200), ("speech", 900), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    assert sink.kinds()[-1] == "translation"


async def test_noise_blip_under_min_utterance_is_discarded_without_stt_final():
    clock = FakeClock()
    stt = FakeSTT(script=["should not be used"])
    interp, sink = make(clock, stt=stt)
    pcm = conversation([("silence", 200), ("speech", 160), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    assert sink.kinds() == ["speech_start", "speech_end", "discarded"]
    assert stt._peek() == "should not be used"  # script not consumed


async def test_empty_final_discards():
    clock = FakeClock()
    interp, sink = make(clock, stt=FakeSTT(script=[""]))
    pcm = conversation([("silence", 200), ("speech", 1000), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    assert sink.kinds() == ["speech_start", "speech_end", "discarded"]


async def test_two_utterances_seq_in_order():
    clock = FakeClock()
    interp, sink = make(clock, stt=FakeSTT(script=["pehla vaakya yahan", "doosra vaakya yahan"]))
    pcm = conversation(
        [("silence", 200), ("speech", 1000), ("silence", 800), ("speech", 1000), ("silence", 800)]
    )
    await interp.run(feed(pcm, clock))
    assert sink.kinds() == [
        "speech_start",
        "speech_end",
        "translation",
        "speech_start",
        "speech_end",
        "translation",
    ]
    assert [t.seq for t in sink.translations] == [0, 1]
    assert [t.src_text for t in sink.translations] == ["pehla vaakya yahan", "doosra vaakya yahan"]
    assert sink.translations[1].timings["t_speech_start"] > sink.translations[0].timings["t_eou"]


async def test_backchannel_does_not_consume_seq():
    clock = FakeClock()
    interp, sink = make(clock, stt=FakeSTT(script=["haan", "ab asli baat karte hain"]))
    pcm = conversation(
        [("silence", 200), ("speech", 400), ("silence", 800), ("speech", 1000), ("silence", 800)]
    )
    await interp.run(feed(pcm, clock))
    assert sink.kinds()[2] == "discarded"
    assert [t.seq for t in sink.translations] == [0]


class GatedMT:
    """MT whose first call blocks until released; later calls return immediately."""

    name = "gated"

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.calls = 0

    async def translate(self, text: str, src: str, tgt: str) -> str:
        self.calls += 1
        if self.calls == 1:
            await self.release.wait()
        return f"T({text})"


async def test_slow_mt_does_not_block_endpointing_and_order_is_kept():
    clock = FakeClock()
    mt = GatedMT()
    interp, sink = make(clock, stt=FakeSTT(script=["first one here", "second one here"]), mt=mt)
    pcm = conversation(
        [("silence", 200), ("speech", 1000), ("silence", 800), ("speech", 1000), ("silence", 800)]
    )
    await interp.run_until_frames_end(feed(pcm, clock))
    # both utterances were endpointed while MT #1 was still blocked; #2 is ready but held
    assert sink.kinds() == ["speech_start", "speech_end", "speech_start", "speech_end"]
    assert mt.calls == 2
    mt.release.set()
    await interp.drain()
    assert [t.seq for t in sink.translations] == [0, 1]
    assert [t.src_text for t in sink.translations] == ["first one here", "second one here"]
    await interp.aclose()


class FailingMT:
    name = "failing"

    def __init__(self, fail_on: set[int]) -> None:
        self.fail_on = fail_on
        self.calls = 0

    async def translate(self, text: str, src: str, tgt: str) -> str:
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError("mt exploded")
        return f"T({text})"


async def test_provider_error_discards_and_continues(caplog):
    clock = FakeClock()
    # FailingMT counts calls; speculation would add calls, so it is off here
    interp, sink = make(clock, mt=FailingMT({1}), pair=SPEC_OFF)
    pcm = conversation(
        [("silence", 200), ("speech", 1000), ("silence", 800), ("speech", 1000), ("silence", 800)]
    )
    with caplog.at_level("WARNING"):
        await interp.run(feed(pcm, clock))
    assert sink.kinds() == [
        "speech_start",
        "speech_end",
        "discarded",
        "speech_start",
        "speech_end",
        "translation",
    ]
    assert sink.translations[0].seq == 0
    assert any("provider.error" in r.getMessage() for r in caplog.records)


class FailingOpenSTT(FakeSTT):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def open(self, lang: str):
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionError("socket refused")
        return await super().open(lang)


async def test_stt_open_failure_discards_and_continues():
    clock = FakeClock()
    interp, sink = make(clock, stt=FailingOpenSTT())
    pcm = conversation(
        [("silence", 200), ("speech", 1000), ("silence", 800), ("speech", 1000), ("silence", 800)]
    )
    await interp.run(feed(pcm, clock))
    assert sink.kinds() == [
        "speech_start",
        "speech_end",
        "discarded",
        "speech_start",
        "speech_end",
        "translation",
    ]


class EmptyTTS:
    name = "empty"

    async def synth(self, text: str, voice: str, lang: str):
        return
        yield b""


async def test_tts_with_no_audio_discards():
    clock = FakeClock()
    interp, sink = make(clock, tts=EmptyTTS())
    pcm = conversation([("silence", 200), ("speech", 1000), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    assert sink.kinds() == ["speech_start", "speech_end", "discarded"]


async def test_stt_receives_preroll_and_live_frames():
    clock = FakeClock()
    stt = FakeSTT()
    streams = []
    orig_open = stt.open

    async def spy_open(lang):
        s = await orig_open(lang)
        streams.append(s)
        return s

    stt.open = spy_open
    interp, sink = make(clock, stt=stt)
    pcm = conversation([("silence", 400), ("speech", 1000), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    assert stt.langs == ["hi-IN"]
    (s,) = streams
    # 200 ms pre-roll (10 frames) + 50 speech frames + trailing frames up to EOU
    assert s.pushed_frames >= 60


async def test_partials_forwarded_to_sink():
    clock = FakeClock()
    interp, sink = make(clock)
    pcm = conversation([("silence", 200), ("speech", 1000), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    partials = [e for e in sink.events if e[0] == "partial"]
    assert partials and all(e[1] == "a" for e in partials)
    assert partials[-1][2].startswith("w1")


async def test_gender_detected_once_from_first_utterance():
    clock = FakeClock()
    tts = FakeTTS()
    interp, sink = make(clock, tts=tts, side="b")
    pcm = (
        silence(200) + harmonic(1000, f0=220) + silence(800) + harmonic(1000, f0=110) + silence(800)
    )
    await interp.run(feed(pcm, clock))
    assert [t.voice for t in sink.translations] == [PAIR.a.voice_f, PAIR.a.voice_f]
    assert {r[2] for r in tts.requests} == {"hi-IN"}
    assert interp.gender == "f"


async def test_given_gender_overrides_detection():
    clock = FakeClock()
    interp, sink = make(clock, gender="f")
    pcm = conversation([("silence", 200), ("speech", 1000), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    assert sink.translations[0].voice == PAIR.b.voice_f


async def test_trailing_utterance_flushed_when_frames_end():
    clock = FakeClock()
    interp, sink = make(clock)
    pcm = conversation([("silence", 200), ("speech", 1000)])
    await interp.run(feed(pcm, clock))
    assert sink.kinds() == ["speech_start", "speech_end", "translation"]


async def test_force_eou_from_outside():
    clock = FakeClock()
    interp, sink = make(clock)

    async def frames_then_vendor_eou():
        async for f in feed(conversation([("silence", 200), ("speech", 1000)]), clock):
            yield f
        await interp.force_eou()
        assert sink.kinds() == ["speech_start", "speech_end"]
        async for f in feed(silence(100), clock):
            yield f

    await interp.run(frames_then_vendor_eou())
    assert sink.kinds() == ["speech_start", "speech_end", "translation"]
    assert sink.translations[0].timings["t_eou"] == 1200


async def test_aclose_cancels_inflight_work():
    clock = FakeClock()
    mt = GatedMT()
    interp, sink = make(clock, mt=mt, pair=SPEC_OFF)  # GatedMT gates by call count
    pcm = conversation([("silence", 200), ("speech", 1000), ("silence", 800)])
    await interp.run_until_frames_end(feed(pcm, clock))
    assert mt.calls == 1
    await interp.aclose()
    assert sink.kinds() == ["speech_start", "speech_end"]
    await interp.aclose()  # idempotent


async def test_run_cancellation_is_clean():
    clock = FakeClock()
    interp, sink = make(clock)

    async def endless():
        while True:
            clock.advance(20)
            yield silence(20)
            await asyncio.sleep(0)

    task = asyncio.create_task(interp.run(endless()))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await interp.aclose()


async def test_translation_audio_streams_first_chunk_then_rest():
    clock = FakeClock()
    sink = RecordingSink(clock, consume_audio=False)
    interp, _ = make(clock, sink=sink, tts=FakeTTS(chunk_ms=100))
    pcm = conversation([("silence", 200), ("speech", 1000), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    (tr,) = sink.translations
    chunks = [c async for c in tr.audio]
    assert [len(c) for c in chunks[:-1]] == [3200] * (len(chunks) - 1)
    assert sum(len(c) for c in chunks) == len(tr.tgt_text) * 60 * 32


@pytest.mark.skipif(not DEFAULT_MODEL_PATH.exists(), reason="silero model missing")
async def test_end_to_end_with_silero_vad():
    clock = FakeClock()
    sink = RecordingSink(clock)
    interp = Interpreter(
        call_id="c_test",
        side="a",
        pair=PAIR,
        stt=FakeSTT(script=["pehla vaakya yahan", "doosra vaakya yahan"]),
        mt=FakeMT(),
        tts=FakeTTS(),
        vad=SileroVAD(),
        clock=clock,
        sink=sink,
    )
    pcm = conversation(
        [("silence", 500), ("speech", 1500), ("silence", 900), ("speech", 1500), ("silence", 900)]
    )
    await interp.run(feed(pcm, clock))
    assert [t.seq for t in sink.translations] == [0, 1]
    assert sink.kinds().count("speech_start") == 2


# --- speculative MT (open question L1) ---------------------------------------------------
# Live-like budget on the FakeClock: STT final 290 ms after end(), MT 1200 ms.


def test_speculation_off_by_default_with_after_ms_200():
    """Off: Sarvam realtime partials lag the final, so live hit rate was 1/8 (L1)."""
    assert all(not p.spec_mt for p in PAIRS.values())
    assert all(p.spec_after_ms == 200 for p in PAIRS.values())
    from dubhash.config.pairs import PairConfig

    assert PairConfig(key="x", a=PAIR.a, b=PAIR.b).spec_mt is False


async def run_spec(pair, final, partials, pcm=None):
    clock = FakeClock()
    stt = FakeSTT(script=[final], partials=[partials], final_delay_ms=290, clock=clock)
    mt = FakeMT(delay_ms=1200, clock=clock)
    interp, sink = make(clock, stt=stt, mt=mt, pair=pair)
    pcm = pcm or conversation([("silence", 300), ("speech", 1500), ("silence", 3000)])
    await asyncio.wait_for(interp.run(feed(pcm, clock)), 5)
    await interp.aclose()
    return interp, sink, mt


async def test_spec_matching_partial_delivers_earlier():
    final = "main gate ke bahar hoon"
    partials = [(400, "main gate"), (1200, "main gate ke bahar hoon")]
    _, off_sink, off_mt = await run_spec(SPEC_OFF, final, partials)
    interp, sink, mt = await run_spec(SPEC_ON, final, partials)
    (off,) = off_sink.translations
    (tr,) = sink.translations
    assert tr.tgt_text == off.tgt_text == "[kn-IN] main gate ke bahar hoon"
    assert mt.calls == off_mt.calls == 1
    on_ms = tr.timings["t_mt"] - tr.timings["t_eou"]
    off_ms = off.timings["t_mt"] - off.timings["t_eou"]
    assert off_ms >= 290 + 1200
    assert on_ms <= off_ms - 500  # MT started ~350 ms before EOU instead of 290 ms after
    assert tr.timings["t_mt"] >= tr.timings["t_stt_final"]
    assert (tr.timings["spec_hit"], tr.timings["spec_used"]) == (1, 1)
    assert (off.timings["spec_hit"], off.timings["spec_used"]) == (0, 0)
    assert (interp.spec_started, interp.spec_hits, interp.spec_used) == (1, 1, 1)


async def test_spec_hit_ignores_case_punctuation_and_spacing():
    final = "Main gate ke  bahar hoon."
    _, sink, mt = await run_spec(SPEC_ON, final, [(1200, "main gate ke bahar hoon")])
    (tr,) = sink.translations
    assert tr.src_text == final.strip()
    assert mt.texts == ["main gate ke bahar hoon"] and mt.calls == 1
    assert tr.tgt_text == "[kn-IN] main gate ke bahar hoon"
    assert tr.timings["spec_used"] == 1


async def test_spec_non_matching_final_discards_speculation():
    final = "main gate ke bahar hoon"
    _, sink, mt = await run_spec(SPEC_ON, final, [(1200, "main gate ke bahar")])
    (tr,) = sink.translations
    assert tr.src_text == final
    assert tr.tgt_text == "[kn-IN] main gate ke bahar hoon"
    assert mt.texts == ["main gate ke bahar", final]
    assert mt.calls == 2 and mt.cancelled == 1
    assert (tr.timings["spec_hit"], tr.timings["spec_used"]) == (0, 0)


async def test_spec_cancelled_when_speech_resumes():
    pcm = conversation(
        [("silence", 300), ("speech", 1000), ("silence", 300), ("speech", 800), ("silence", 3000)]
    )
    final = "ek do teen chaar paanch"
    partials = [(800, "ek do teen"), (1900, final)]
    interp, sink, mt = await run_spec(SPEC_ON, final, partials, pcm=pcm)
    (tr,) = sink.translations
    assert sink.kinds() == ["speech_start", "speech_end", "translation"]
    assert mt.texts == ["ek do teen", final]
    assert mt.calls == 2 and mt.cancelled == 1
    assert interp.spec_started == 2 and interp.spec_cancelled == 1
    assert tr.tgt_text == "[kn-IN] ek do teen chaar paanch"
    assert tr.timings["spec_used"] == 1


async def test_spec_without_partials_behaves_as_before():
    final = "main gate ke bahar hoon"
    _, off_sink, off_mt = await run_spec(SPEC_OFF, final, [])
    interp, sink, mt = await run_spec(SPEC_ON, final, [])
    (off,) = off_sink.translations
    (tr,) = sink.translations
    assert mt.calls == off_mt.calls == 1 and mt.cancelled == 0
    assert interp.spec_started == 0
    assert tr.tgt_text == off.tgt_text
    assert tr.timings == off.timings


async def test_spec_not_run_for_discarded_backchannel():
    clock = FakeClock()
    stt = FakeSTT(script=["haan"], partials=[[(300, "haan")]], clock=clock)
    mt = FakeMT(delay_ms=1200, clock=clock)
    interp, sink = make(clock, stt=stt, mt=mt, pair=SPEC_ON)
    pcm = conversation([("silence", 200), ("speech", 400), ("silence", 800)])
    await asyncio.wait_for(interp.run(feed(pcm, clock)), 5)
    assert sink.kinds() == ["speech_start", "speech_end", "discarded"]
    assert mt.calls == 0


async def test_spec_cancelled_when_utterance_discarded():
    """Speculation started, then the final is empty: nothing delivered, task cancelled."""
    clock = FakeClock()
    stt = FakeSTT(script=[""], partials=[[(600, "kuch to bola tha")]], clock=clock)
    mt = FakeMT(delay_ms=5000, clock=clock)
    interp, sink = make(clock, stt=stt, mt=mt, pair=SPEC_ON)
    pcm = conversation([("silence", 200), ("speech", 1000), ("silence", 800)])
    await asyncio.wait_for(interp.run(feed(pcm, clock)), 5)
    for _ in range(5):
        await asyncio.sleep(0)
    assert sink.kinds() == ["speech_start", "speech_end", "discarded"]
    assert mt.calls == 1 and mt.cancelled == 1


async def test_spec_goes_through_cached_mt():
    from dubhash.interp.mt_cache import CachedMT

    clock = FakeClock()
    inner = FakeMT(delay_ms=1200, clock=clock)
    mt = CachedMT(inner)
    final = "main gate ke bahar hoon"
    stt = FakeSTT(
        script=[final, final],
        partials=[[(1200, final)], [(1200, final)]],
        final_delay_ms=290,
        clock=clock,
    )
    interp, sink = make(clock, stt=stt, mt=mt, pair=SPEC_ON)
    pcm = conversation(
        [("silence", 300), ("speech", 1500), ("silence", 3000), ("speech", 1500), ("silence", 1500)]
    )
    await asyncio.wait_for(interp.run(feed(pcm, clock)), 5)
    assert [t.tgt_text for t in sink.translations] == ["[kn-IN] " + final] * 2
    assert inner.calls == 1 and mt.hits == 1
    assert all(t.timings["spec_used"] == 1 for t in sink.translations)


class _SpyMT(FakeMT):
    def __init__(self) -> None:
        super().__init__()
        self.langs: list[tuple[str, str]] = []

    async def translate(self, text: str, src: str, tgt: str) -> str:
        self.langs.append((src, tgt))
        return await super().translate(text, src, tgt)


async def test_mt_source_language_comes_from_mt_src_when_set():
    clock = FakeClock()
    mt = _SpyMT()
    pivot = PAIRS["hi-kn-pivot"]
    interp = Interpreter(
        call_id="c_test",
        side="a",
        pair=pivot,
        stt=FakeSTT(script=["sir I am standing outside gate two"]),
        mt=mt,
        tts=FakeTTS(),
        vad=EnergyVAD(),
        clock=clock,
        sink=RecordingSink(clock),
    )
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    await interp.aclose()
    assert mt.langs == [("en-IN", "kn-IN")]


async def test_mt_source_language_defaults_to_side_lang():
    clock = FakeClock()
    mt = _SpyMT()
    interp, _ = make(clock, stt=FakeSTT(script=["bhaiya main gate ke bahar hoon"]), mt=mt)
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 800)])
    await interp.run(feed(pcm, clock))
    await interp.aclose()
    assert mt.langs == [("hi-IN", "kn-IN")]

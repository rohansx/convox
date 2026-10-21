"""Endpointer max_utterance_ms (L7): an utterance that has run max_utterance_ms (7 s) ends
at the next low-energy dip (a pause, or a frame 12 dB under the recent peak), so a noisy
room or a monologue never holds the translation back for ever, and no word is cut in the
middle. If no dip comes within max_dip_wait_ms (1 s), it ends anyway."""

from __future__ import annotations

import numpy as np

from dubhash.audio.frames import FRAME_BYTES, FRAME_MS
from dubhash.interp.endpoint import Endpointer, SpeechStart, Utterance


def loud(amp: int = 8000) -> bytes:
    return (np.full(FRAME_BYTES // 2, amp, dtype=np.int16)).tobytes()


class Driver:
    def __init__(self, ep: Endpointer) -> None:
        self.ep = ep
        self.i = 0
        self.events: list[tuple[object, int]] = []

    def run(self, ms: int, prob: float, frame: bytes) -> list:
        out = []
        for _ in range(ms // FRAME_MS):
            self.i += 1
            ev = self.ep.feed(frame, prob, self.i * FRAME_MS)
            out += ev
            self.events += [(e, self.i * FRAME_MS) for e in ev]
        return out


def test_default_is_seven_seconds():
    assert Endpointer().max_utterance_ms == 7000


def test_never_ending_speech_is_cut_at_the_first_dip_after_the_limit():
    d = Driver(Endpointer(eou_ms=550, max_utterance_ms=7000))
    d.run(20, 0.9, loud())  # t=20 start
    assert d.run(7200, 0.9, loud()) == []  # 7.2 s of speech, no dip yet
    ev = d.run(20, 0.9, loud(1000))  # 18 dB down: a dip between words
    assert [type(e) for e in ev] == [Utterance]
    u = ev[0]
    assert u.t_eou == 7240 and u.forced and u.duration_ms >= 7000
    ev = d.run(20, 0.9, loud())  # the speaker carries on: a new utterance at once
    assert [type(e) for e in ev] == [SpeechStart]


def test_a_pause_frame_is_a_dip_too():
    d = Driver(Endpointer(eou_ms=550, max_utterance_ms=3000))
    d.run(3100, 0.9, loud())
    ev = d.run(20, 0.1, loud(200))
    assert [type(e) for e in ev] == [Utterance] and ev[0].forced


def test_no_dip_at_all_ends_it_after_the_wait():
    d = Driver(Endpointer(eou_ms=550, max_utterance_ms=3000, max_dip_wait_ms=1000))
    d.run(3980, 0.9, loud())
    ends = [(e, t) for e, t in d.events if isinstance(e, Utterance)]
    assert ends == []
    d.run(40, 0.9, loud())
    (u, t), *_ = [(e, t) for e, t in d.events if isinstance(e, Utterance)]
    assert t == 4020 and u.forced  # 3 s + 1 s after its first frame (t=20)


def test_normal_utterances_are_not_forced():
    d = Driver(Endpointer(eou_ms=550))
    d.run(2000, 0.9, loud())
    ev = d.run(600, 0.0, loud(10))
    assert [type(e) for e in ev] == [Utterance] and not ev[0].forced


def test_zero_disables_it():
    d = Driver(Endpointer(eou_ms=550, max_utterance_ms=0))
    d.run(12000, 0.9, loud())
    d.run(20, 0.9, loud(100))
    assert not any(isinstance(e, Utterance) for e, _ in d.events)


def test_pair_config_carries_max_utterance_ms(tmp_path):
    from dubhash.config.pairs import load_pairs
    from dubhash.config.settings import PACKAGE_DIR

    pairs = load_pairs(PACKAGE_DIR / "config" / "pairs.yaml")
    assert all(p.max_utterance_ms == 7000 for p in pairs.values())
    y = tmp_path / "p.yaml"
    y.write_text(
        "x:\n  a: {lang: hi-IN, stt: fake, tts: fake, voice_m: m, voice_f: f}\n"
        "  b: {lang: kn-IN, stt: fake, tts: fake, voice_m: m, voice_f: f}\n"
        "  max_utterance_ms: 5000\n"
    )
    assert load_pairs(y)["x"].max_utterance_ms == 5000


async def test_the_interpreter_uses_the_pairs_limit():
    import dataclasses

    from dubhash.clock import FakeClock
    from dubhash.config.pairs import load_pairs
    from dubhash.config.settings import PACKAGE_DIR
    from dubhash.interp.pipeline import Interpreter
    from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
    from tests.test_pipeline import RecordingSink

    pairs = load_pairs(PACKAGE_DIR / "config" / "pairs.yaml")
    pair = dataclasses.replace(pairs["fake-fake"], max_utterance_ms=4000)
    clock = FakeClock()
    interp = Interpreter(
        "c", "a", pair, FakeSTT(), FakeMT(), FakeTTS(), EnergyVAD(), clock, RecordingSink(clock)
    )
    assert interp.endpointer.max_utterance_ms == 4000

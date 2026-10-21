from dubhash.audio.frames import FRAME_BYTES, FRAME_MS
from dubhash.interp.endpoint import Discard, Endpointer, SpeechStart, Utterance


def _frame(i: int) -> bytes:
    """A frame whose content identifies its index (for audio-slicing assertions)."""
    return (i % 256).to_bytes(1, "little") * FRAME_BYTES


class Driver:
    def __init__(self, ep: Endpointer) -> None:
        self.ep = ep
        self.i = 0
        self.events: list = []

    @property
    def now(self) -> int:
        return self.i * FRAME_MS

    def run(self, ms: int, prob: float) -> list:
        out = []
        for _ in range(ms // FRAME_MS):
            self.i += 1
            out += self.ep.feed(_frame(self.i), prob, self.now)
        self.events += out
        return out


def test_silence_emits_nothing():
    d = Driver(Endpointer())
    assert d.run(2000, 0.1) == []
    assert not d.ep.in_speech


def test_speech_start_then_utterance_at_eou():
    d = Driver(Endpointer(eou_ms=550))
    d.run(400, 0.0)  # frames 1..20, t=20..400
    ev = d.run(20, 0.9)  # frame 21, t=420
    assert len(ev) == 1 and isinstance(ev[0], SpeechStart)
    assert ev[0].t == 420
    d.run(980, 0.9)  # last speech frame 70 at t=1400
    assert d.run(520, 0.0) == []  # silence 520 ms < eou
    ev = d.run(40, 0.0)
    assert len(ev) == 1 and isinstance(ev[0], Utterance)
    u = ev[0]
    assert (u.t_start, u.t_end) == (420, 1400)
    assert u.t_eou == 1960  # first frame with silence >= 550 ms
    assert u.duration_ms == 1000
    assert not u.short
    assert not d.ep.in_speech


def test_preroll_included_in_audio_and_speech_start():
    d = Driver(Endpointer(preroll_ms=200, tail_ms=0))
    d.run(400, 0.0)  # frames 1..20
    (start,) = d.run(20, 0.9)  # frame 21
    assert start.preroll == b"".join(_frame(i) for i in range(11, 21))
    d.run(480, 0.9)  # frames 22..45
    (u,) = d.run(600, 0.0)
    assert u.audio == b"".join(_frame(i) for i in range(11, 46))


def test_preroll_shorter_at_stream_start():
    d = Driver(Endpointer(preroll_ms=200, tail_ms=0))
    d.run(40, 0.0)
    (start,) = d.run(20, 0.9)
    assert len(start.preroll) == 2 * FRAME_BYTES


def test_tail_keeps_some_trailing_silence():
    d = Driver(Endpointer(preroll_ms=0, tail_ms=100))
    d.run(20, 0.0)
    d.run(500, 0.9)  # frames 2..26
    (u,) = d.run(600, 0.0)
    assert u.audio == b"".join(_frame(i) for i in range(2, 32))


def test_short_gap_continues_utterance():
    d = Driver(Endpointer(eou_ms=550))
    d.run(500, 0.9)
    assert d.run(400, 0.0) == []  # gap < eou
    assert d.run(500, 0.9) == []  # no new SpeechStart
    (u,) = d.run(600, 0.0)
    assert u.t_start == 20 and u.t_end == 1400
    assert u.duration_ms == 1400
    assert sum(isinstance(e, SpeechStart) for e in d.events) == 1


def test_under_min_utterance_discards():
    d = Driver(Endpointer(min_utterance_ms=300))
    d.run(100, 0.0)
    d.run(200, 0.9)
    (ev,) = d.run(600, 0.0)
    assert isinstance(ev, Discard)
    assert ev.duration_ms == 200 and ev.t_start == 120
    # and the endpointer is ready for the next utterance
    assert isinstance(d.run(20, 0.9)[0], SpeechStart)


def test_short_flag_below_backchannel_max():
    d = Driver(Endpointer(min_utterance_ms=300, backchannel_max_ms=600))
    d.run(400, 0.9)
    (u,) = d.run(600, 0.0)
    assert isinstance(u, Utterance) and u.short and u.duration_ms == 400


def test_threshold_is_strictly_greater():
    d = Driver(Endpointer(speech_threshold=0.5))
    assert d.run(200, 0.5) == []
    assert isinstance(d.run(20, 0.51)[0], SpeechStart)


def test_force_eou_ends_now():
    d = Driver(Endpointer())
    d.run(800, 0.9)
    d.run(100, 0.0)
    (u,) = d.ep.force_eou(d.now)
    assert isinstance(u, Utterance)
    assert u.t_eou == 900 and u.t_end == 800
    assert not d.ep.in_speech
    assert d.run(1000, 0.0) == []


def test_force_eou_when_idle_is_noop():
    ep = Endpointer()
    assert ep.force_eou(100) == []


def test_force_eou_short_discards():
    d = Driver(Endpointer(min_utterance_ms=300))
    d.run(100, 0.9)
    (ev,) = d.ep.force_eou(d.now)
    assert isinstance(ev, Discard)


def test_two_utterances_back_to_back():
    d = Driver(Endpointer())
    for _ in range(2):
        d.run(700, 0.9)
        d.run(700, 0.0)
    kinds = [type(e).__name__ for e in d.events]
    assert kinds == ["SpeechStart", "Utterance", "SpeechStart", "Utterance"]
    u1, u2 = d.events[1], d.events[3]
    assert u2.t_start > u1.t_eou

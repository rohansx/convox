from convox.clock import FakeClock
from convox.session.providers import Providers
from convox.session.turn_log import TurnLog
from convox.types import Translation, Turn
from tests.floor_helpers import RecordingBus


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


def providers() -> Providers:
    return Providers(
        stt={"a": _Named("sarvam"), "b": _Named("deepgram")},  # type: ignore[dict-item]
        mt=_Named("sarvam-mt"),  # type: ignore[arg-type]
        tts={"a": _Named("eleven"), "b": _Named("bulbul")},  # type: ignore[dict-item]
        vad_factory=lambda: None,  # type: ignore[arg-type,return-value]
    )


class RecordingWriter:
    def __init__(self) -> None:
        self.turns: list[tuple[Turn, bool]] = []

    def enqueue_turn(self, turn: Turn, record: bool) -> bool:
        self.turns.append((turn, record))
        return True


async def _audio():
    yield b""


def tr(side, seq, **timings) -> Translation:
    return Translation(
        side=side,
        seq=seq,
        src_text=f"src {side}{seq}",
        tgt_text=f"tgt {side}{seq}",
        audio=_audio(),
        voice="bulbul:kn-male-1" if side == "a" else "eleven:hi-female-1",
        timings=dict(timings),
    )


TIMES = dict(
    t_speech_start=100,
    t_speech_end=1500,
    t_eou=2050,
    t_stt_final=2200,
    t_mt=2400,
    t_tts_ttfb=2700,
    t_play_start=2710,
    t_play_end=5000,
)


def make(record=False):
    clock = FakeClock(6000)
    bus = RecordingBus()
    writer = RecordingWriter()
    log = TurnLog("c_x", providers=providers(), bus=bus, clock=clock, writer=writer, record=record)
    return log, bus, writer


def test_played_translation_becomes_a_turn_with_merged_timings_and_providers():
    log, bus, writer = make(record=True)
    t = log.on_played(tr("a", 0, **TIMES))
    assert t == Turn(
        call_id="c_x",
        side="a",
        seq=0,
        **TIMES,
        src_text="src a0",
        tgt_text="tgt a0",
        backchannel=False,
        clipped_ms=None,
        stt_provider="sarvam",
        mt_provider="sarvam-mt",
        tts_provider="bulbul",  # target side b's TTS
        voice="bulbul:kn-male-1",
    )
    assert log.turns == [t]
    (ev,) = bus.kinds("turn")
    assert ev.call_id == "c_x" and ev.ts_ms == 6000 and ev.payload["turn"]["seq"] == 0
    assert writer.turns == [(t, True)]


def test_clipped_translation_and_direction_b():
    log, _, writer = make()
    x = tr("b", 0, **TIMES)
    x.clipped_ms = 840
    t = log.on_played(x)
    assert (t.side, t.clipped_ms, t.stt_provider, t.tts_provider) == (
        "b",
        840,
        "deepgram",
        "eleven",
    )
    assert writer.turns[0][1] is False  # record flag passed through


def test_seq_is_per_call_log_order_across_directions():
    log, _, _ = make()
    log.on_played(tr("a", 0, **TIMES))
    log.on_backchannel("b", src_text="haan", t_speech_start=3000, t_speech_end=3400, t_eou=3950)
    log.on_played(tr("b", 0, **TIMES))
    log.on_played(tr("a", 1, **TIMES))
    assert [(t.side, t.seq, t.backchannel) for t in log.turns] == [
        ("a", 0, False),
        ("b", 1, True),
        ("b", 2, False),
        ("a", 3, False),
    ]


def test_backchannel_turn_is_not_translated():
    log, bus, writer = make()
    t = log.on_backchannel(
        "b", src_text="haan", t_speech_start=3000, t_speech_end=3400, t_eou=3950, t_stt_final=4000
    )
    assert t.backchannel and t.src_text == "haan" and t.tgt_text is None
    assert (t.t_speech_start, t.t_speech_end, t.t_eou, t.t_stt_final) == (3000, 3400, 3950, 4000)
    assert t.t_mt is None and t.t_play_start is None
    assert (t.stt_provider, t.mt_provider, t.tts_provider, t.voice) == (
        "deepgram",
        None,
        None,
        None,
    )
    assert bus.kinds("turn") and writer.turns[0][0] is t


def test_summary_excludes_backchannels():
    log, _, _ = make()
    log.on_played(tr("a", 0, **TIMES))
    log.on_backchannel("b", src_text="haan", t_speech_start=3000, t_speech_end=3400, t_eou=3950)
    s = log.summary()
    assert s["turns"] == 1 and s["p50_first_audio_ms"] == 2710 - 1500


def test_works_without_writer_or_bus():
    log = TurnLog("c_x", providers=providers(), clock=FakeClock())
    log.on_played(tr("a", 0, **TIMES))
    assert len(log.turns) == 1


def test_interpreter_flags_are_kept_beside_the_turn():
    """L10: phrase/cache/number flags and stt_open_ms are not `turns` columns; they ride
    on the turn event and in TurnLog.flags (GET /v1/calls/{id}/turns shows them)."""
    log, bus, _ = make()
    flags = {"phrase_hit": 1, "ack": 1, "mt_cache_hit": 1, "tts_cache_hit": 1, "stt_open_ms": 3}
    t = log.on_played(tr("a", 0, **TIMES, **flags))
    assert log.flags[t.seq] == flags
    (ev,) = bus.kinds("turn")
    assert ev.payload["flags"] == flags
    b = log.on_backchannel("b", src_text="haan")
    assert log.flags.get(b.seq) is None


def test_turns_endpoint_shows_the_flags():
    from convox.api.calls import turn_json

    log, _, _ = make()
    t = log.on_played(tr("a", 0, **TIMES, phrase_hit=1))
    assert turn_json(t, False, log.flags.get(t.seq))["flags"] == {"phrase_hit": 1}
    assert "flags" not in turn_json(t, False)


def test_chunk_fields_reach_the_turn():
    """L9: every chunk of one utterance is a turn sharing utterance_id; t_utt_start is the
    utterance's own speech start (chunk 1 starts later)."""
    log, _, _ = make()
    x = tr("a", 3, **TIMES, t_utt_start=40)
    x.utterance_id, x.chunk = 5, 1
    t = log.on_played(x)
    assert (t.utterance_id, t.chunk, t.t_utt_start) == (5, 1, 40)


def test_unchunked_translation_defaults():
    log, _, _ = make()
    t = log.on_played(tr("a", 0, **TIMES))
    assert (t.utterance_id, t.chunk, t.t_utt_start) == (None, None, None)

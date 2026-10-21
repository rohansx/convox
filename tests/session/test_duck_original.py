"""The ducked original per call: off for co-located phones (/join, loopback: the listener
already heard the speaker in the room), a 600 ms cue at -20 dB on telephony calls."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from dubhash.audio.earcon import EARCON
from dubhash.audio.mixer import TRIM_LEAD_PEAK, db_to_gain
from dubhash.clock import FakeClock
from dubhash.config.pairs import load_pairs
from dubhash.legs.fake import FakeLeg
from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from dubhash.session.call_session import CallSession
from dubhash.session.providers import Providers
from tests.audio_fixtures import conversation
from tests.floor_helpers import RecordingBus
from tools.bench import drive_lockstep

PAIRS = load_pairs(Path(__file__).resolve().parents[2] / "dubhash" / "config" / "pairs.yaml")
PAIR = PAIRS["fake-fake"]
MS_PER_CHAR = 60
SPEECH_AMP = 8000  # tests.audio_fixtures.speech peak


def tts_pcm(text: str) -> bytes:
    """What the listener gets of the TTS: the mixer trims its near-silent first samples."""
    pcm = FakeTTS(ms_per_char=MS_PER_CHAR)._pcm(len(text) * MS_PER_CHAR)
    lead = int(np.flatnonzero(np.abs(arr(pcm)) > TRIM_LEAD_PEAK)[0])
    return pcm[2 * lead :]


def arr(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.int16).astype(np.float64)


def corr(x: np.ndarray, y: np.ndarray) -> float:
    n = min(len(x), len(y))
    x, y = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
    den = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return 0.0 if den == 0 else float((x * y).sum() / den)


async def one_turn(**kw):
    clock = FakeClock()
    bus = RecordingBus()
    prov = Providers(
        stt={"a": FakeSTT(script=["bhaiya main gate ke bahar hoon"]), "b": FakeSTT()},
        mt=FakeMT(),
        tts={"a": FakeTTS(ms_per_char=MS_PER_CHAR), "b": FakeTTS(ms_per_char=MS_PER_CHAR)},
        vad_factory=EnergyVAD,
    )
    legs = {s: FakeLeg(s, clock=clock) for s in ("a", "b")}
    session = CallSession("c_duck", PAIR, prov, bus=bus, clock=clock, opening_line=False, **kw)
    src = conversation([("silence", 300), ("speech", 1500), ("silence", 3000)])
    await session.attach_leg("a", legs["a"])
    await session.attach_leg("b", legs["b"])
    await drive_lockstep(session, legs, clock, {"a": src})
    await session.wait_closed()
    (turn,) = session.turn_log.turns
    return session, legs["b"].outbound, turn, src


def test_pairs_default_to_a_short_quiet_cue():
    assert PAIR.duck_db == -20 and PAIR.duck_ms == 600
    assert all(p.duck_db == -20 and p.duck_ms == 600 for p in PAIRS.values())


async def test_co_located_call_plays_no_original_by_default():
    session, out_b, turn, src = await one_turn(co_located=True)
    assert session.duck_original is False
    tts = tts_pcm(turn.tgt_text)
    assert out_b.startswith(EARCON + tts)
    assert out_b[len(EARCON) + len(tts) :].strip(b"\x00") == b""  # padding only
    body = arr(out_b[len(EARCON) :])
    assert abs(corr(body, arr(src[16 * 2 * 300 :]))) < 0.05


async def test_telephony_call_plays_the_original_only_for_the_first_600_ms_at_minus_20_db():
    session, out_b, turn, src = await one_turn()
    assert session.duck_original is True
    tts = tts_pcm(turn.tgt_text)
    body = out_b[len(EARCON) : len(EARCON) + len(tts)]
    residual = arr(body) - arr(tts)
    cue = 16 * 600
    assert np.all(residual[cue:] == 0)  # after 600 ms: the translation alone
    head = residual[:cue]
    assert np.abs(head).max() > 0
    assert np.abs(head).max() <= SPEECH_AMP * db_to_gain(-20.0) + 1


async def test_duck_original_can_be_forced_per_call():
    session, out_b, turn, _ = await one_turn(co_located=True, duck_original=True)
    tts = tts_pcm(turn.tgt_text)
    assert out_b[len(EARCON) : len(EARCON) + len(tts)] != tts

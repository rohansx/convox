"""NoiseFloorGate (interp/vad.py, L7): a frame counts as speech only when the VAD says so
AND its RMS is more than margin_db (6 dB) above an adaptive noise floor. A room with a TV
or other people talking keeps Silero above 0.5 between the caller's sentences, so EOU
never fired; phone AGC pumps the noise up in every pause."""

from __future__ import annotations

import pytest

from dubhash.interp.endpoint import Endpointer, SpeechStart, Utterance
from dubhash.interp.vad import DEFAULT_MODEL_PATH, NoiseFloorGate, SileroVAD
from tests.audio_fixtures import dbfs, frames, mix, pink_noise, silence, speech, with_noise

needs_model = pytest.mark.skipif(not DEFAULT_MODEL_PATH.exists(), reason="silero model missing")


class AlwaysSpeech:
    """Silero in a room with a TV on: every frame looks like speech."""

    def speech_prob(self, frame: bytes) -> float:
        return 0.9

    def reset(self) -> None:
        pass


def run(vad, pcm: bytes, ep: Endpointer | None = None):
    ep = ep or Endpointer(eou_ms=550)
    events = []
    for i, f in enumerate(frames(pcm)):
        events += [(e, i * 20) for e in ep.feed(f, vad.speech_prob(f), i * 20)]
    return events


def noisy_call(noise_db: float, snr_db: float = 15.0, speech_ms: int = 1200) -> bytes:
    """2 s of room noise, speech snr_db above it, 2 s more noise."""
    sp = speech(speech_ms)
    gap = 2000
    clean = silence(gap) + sp + silence(gap)
    level = dbfs(sp) - snr_db
    assert abs(level - noise_db) < 30
    return mix(clean, pink_noise(2 * gap + speech_ms, level, seed=3))


def test_without_the_gate_babble_never_ends_the_utterance():
    events = run(AlwaysSpeech(), noisy_call(-40))
    assert [type(e) for e, _ in events] == [SpeechStart]  # starts at once, never ends


@pytest.mark.parametrize("snr", [10, 15, 20])
def test_with_the_gate_eou_fires_550_ms_after_the_speech(snr):
    """Until the floor has learned the room (about 1 s) the babble is "speech"; after that
    only the caller is."""
    events = run(NoiseFloorGate(AlwaysSpeech()), noisy_call(-40, snr_db=snr))
    later = [(e, t) for e, t in events if getattr(e, "t_start", t) >= 1600]
    starts = [t for e, t in later if isinstance(e, SpeechStart)]
    ends = [e for e, _ in later if isinstance(e, Utterance)]
    assert len(starts) == 1 and len(ends) == 1, later
    assert 1900 <= starts[0] <= 2100  # the speech starts at 2000
    assert 3100 <= ends[0].t_end <= 3220  # ... and ends at 3200
    assert ends[0].t_eou - ends[0].t_end in range(540, 580)


def test_floor_settles_on_the_room_noise():
    gate = NoiseFloorGate(AlwaysSpeech())
    for f in frames(pink_noise(2000, -38.0, seed=1)):
        gate.speech_prob(f)
    assert -46 <= gate.floor_db <= -36


def test_floor_follows_agc_pumping_the_noise_up():
    """AGC raises the noise by 15 dB in a pause: within ~5 s it is noise again."""
    gate = NoiseFloorGate(AlwaysSpeech())
    for f in frames(pink_noise(2000, -55.0, seed=1)):
        gate.speech_prob(f)
    probs = [gate.speech_prob(f) for f in frames(pink_noise(6000, -40.0, seed=2))]
    assert max(probs[-50:]) < 0.5  # the last 1 s: gated
    assert -48 <= gate.floor_db <= -38


def test_digital_silence_does_not_drag_the_floor_down():
    """The phone page sends zeros while it plays (half duplex): that is no room noise."""
    gate = NoiseFloorGate(AlwaysSpeech())
    for f in frames(pink_noise(2000, -40.0, seed=1)):
        gate.speech_prob(f)
    before = gate.floor_db
    for f in frames(silence(1500)):
        assert gate.speech_prob(f) == 0.0
    assert abs(gate.floor_db - before) < 0.5


def test_quiet_room_speech_is_untouched():
    gate = NoiseFloorGate(AlwaysSpeech())
    probs = [gate.speech_prob(f) for f in frames(silence(500) + speech(1000) + silence(500))]
    assert all(p == 0.9 for p in probs[26:74])  # speech from 500 to 1500 ms
    assert all(p == 0.0 for p in probs[:24])


def test_reset_keeps_the_learned_floor():
    gate = NoiseFloorGate(AlwaysSpeech())
    for f in frames(pink_noise(2000, -40.0, seed=1)):
        gate.speech_prob(f)
    floor = gate.floor_db
    gate.reset()
    assert gate.floor_db == floor


# --- the real Silero with pink room noise ------------------------------------------------


@needs_model
@pytest.mark.parametrize("snr", [10, 15, 20])
def test_silero_with_the_gate_on_noisy_speech(snr):
    sp = speech(1200)
    pcm = with_noise(silence(1000) + sp + silence(1500), snr, ref=sp, seed=snr)
    events = run(NoiseFloorGate(SileroVAD()), pcm)
    ends = [e for e, _ in events if isinstance(e, Utterance)]
    assert len(ends) == 1, events
    u = ends[0]
    assert 900 <= u.t_start <= 1150 and 2050 <= u.t_end <= 2360
    assert u.t_eou - u.t_end in range(540, 580)


def test_every_call_gets_a_gated_vad():
    from dubhash.config.pairs import load_pairs
    from dubhash.config.settings import PACKAGE_DIR, Settings
    from dubhash.session.providers import build_providers

    pair = load_pairs(PACKAGE_DIR / "config" / "pairs.yaml")["mr-hi"]
    p = build_providers(pair, Settings(_env_file=None, dubhash_env="test"))  # type: ignore[call-arg]
    a, b = p.vad_factory(), p.vad_factory()
    assert isinstance(a, NoiseFloorGate) and a is not b and a.inner is not b.inner

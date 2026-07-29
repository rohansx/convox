"""The audio path: synthesis, channel damage, recognition, and what it all measures."""

import numpy as np
import pytest

from convox.audio import analysis, codec, impair, pcm
from convox.eval import wer
from convox.model.persona import ChannelConfig, EnvironmentConfig, Persona
from convox.sim.channel import ChannelSimulator
from convox.voice.tone import ToneVoice

SAMPLE_RATE = 16000
UTTERANCE = "my number is nine eight seven six five four three two one zero"


@pytest.fixture
def voice():
    return ToneVoice()


# ─── PCM and codecs ──────────────────────────────────────────────────────────


def test_pcm16_round_trips_without_drift():
    original = np.sin(np.linspace(0, 40 * np.pi, 4000)).astype(np.float32) * 0.5
    restored = pcm.from_pcm16(pcm.to_pcm16(original))
    assert np.max(np.abs(restored - original)) < 1e-4


def test_resampling_preserves_duration():
    samples = np.random.default_rng(0).standard_normal(16000).astype(np.float32)
    downsampled = pcm.resample(samples, 16000, 8000)
    assert abs(pcm.duration_ms(downsampled, 8000) - 1000) <= 1


@pytest.mark.parametrize("name", ["g711u", "g711a", "g722", "gsm"])
def test_codecs_degrade_without_destroying(name, voice):
    audio = voice.synthesize("hello there", sample_rate=SAMPLE_RATE)
    through = codec.roundtrip(audio, SAMPLE_RATE, name)

    assert len(through) == len(audio)
    assert pcm.rms(through) > 0
    # Companding is lossy — identical output would mean the codec did nothing.
    assert not np.allclose(through, audio)


def test_unknown_codec_is_rejected():
    with pytest.raises(ValueError, match="unknown codec"):
        codec.roundtrip(np.zeros(100, dtype=np.float32), SAMPLE_RATE, "mp3")


# ─── the reference voice ─────────────────────────────────────────────────────


def test_synthesis_runs_at_a_realistic_pace(voice):
    audio = voice.synthesize(UTTERANCE, sample_rate=SAMPLE_RATE)
    words_per_minute = len(UTTERANCE.split()) / (len(audio) / SAMPLE_RATE) * 60
    assert 120 <= words_per_minute <= 190, f"{words_per_minute:.0f} wpm is not a human pace"


def test_clean_audio_transcribes_exactly(voice):
    audio = voice.synthesize(UTTERANCE, sample_rate=SAMPLE_RATE)
    assert voice.transcribe(audio, sample_rate=SAMPLE_RATE) == UTTERANCE


def test_synthesis_is_deterministic(voice):
    first = voice.synthesize(UTTERANCE, sample_rate=SAMPLE_RATE)
    second = voice.synthesize(UTTERANCE, sample_rate=SAMPLE_RATE)
    assert np.array_equal(first, second)


def test_telephony_codec_does_not_break_recognition(voice):
    """8 kHz narrowband is the normal case, not a degraded one."""
    audio = voice.synthesize(UTTERANCE, sample_rate=SAMPLE_RATE)
    narrowband = codec.roundtrip(audio, SAMPLE_RATE, "g711u")
    assert voice.transcribe(narrowband, sample_rate=SAMPLE_RATE) == UTTERANCE


def test_noise_degrades_recognition_in_proportion_to_snr(voice):
    """The property the whole channel simulator exists to produce."""
    audio = voice.synthesize(UTTERANCE, sample_rate=SAMPLE_RATE)
    rng = np.random.default_rng(11)
    noise = impair.generate_noise("street_traffic_india", len(audio), SAMPLE_RATE, rng)

    rates = []
    for snr in (30, 10, 2):
        noisy = impair.mix_at_snr(audio, noise, snr)
        heard = voice.transcribe(noisy, sample_rate=SAMPLE_RATE)
        rates.append(wer.wer(UTTERANCE, heard).error_rate)

    assert rates[0] == 0.0, "a clean-ish line should decode perfectly"
    assert rates[-1] > rates[0], f"heavy noise did not degrade recognition: {rates}"


def test_mix_at_snr_hits_the_requested_ratio(voice):
    speech = voice.synthesize("hello", sample_rate=SAMPLE_RATE)
    noise = impair.generate_noise("cafe", len(speech), SAMPLE_RATE, np.random.default_rng(3))
    mixed = impair.mix_at_snr(speech, noise, 20.0)

    added = mixed[: len(speech)] - speech
    measured = pcm.db(pcm.rms(speech)) - pcm.db(pcm.rms(added))
    assert 18 <= measured <= 22, f"asked for 20 dB SNR, got {measured:.1f}"


def test_packet_loss_is_bursty_and_reported(voice):
    audio = voice.synthesize(UTTERANCE, sample_rate=SAMPLE_RATE)
    damaged, dropped = impair.apply_packet_loss(
        audio, SAMPLE_RATE, loss=0.15, burst=0.7, rng=np.random.default_rng(5)
    )
    assert dropped > 0
    assert pcm.rms(damaged) < pcm.rms(audio)


# ─── analysis ────────────────────────────────────────────────────────────────


def test_truncation_is_detected_only_when_audio_is_cut(voice):
    complete = voice.synthesize("your refill is booked", sample_rate=SAMPLE_RATE)
    assert analysis.detect_truncation(complete, SAMPLE_RATE) is False

    cut = complete[: int(len(complete) * 0.7)]
    assert analysis.detect_truncation(cut, SAMPLE_RATE) is True


def test_speech_detection_finds_utterances_between_silence(voice):
    audio = pcm.concat(
        pcm.silence(400, SAMPLE_RATE),
        voice.synthesize("hello", sample_rate=SAMPLE_RATE),
        pcm.silence(600, SAMPLE_RATE),
        voice.synthesize("goodbye", sample_rate=SAMPLE_RATE),
    )
    segments = analysis.detect_speech(audio, SAMPLE_RATE)
    assert len(segments) == 2
    assert segments[0].start_ms >= 350


def test_silence_contains_no_speech():
    assert analysis.detect_speech(pcm.silence(2000, SAMPLE_RATE), SAMPLE_RATE) == []


def test_artifacts_raise_the_artifact_score(voice):
    clean = voice.synthesize("your refill is booked for tomorrow", sample_rate=SAMPLE_RATE)
    midpoint = len(clean) // 2
    burst = (np.random.default_rng(0).standard_normal(SAMPLE_RATE // 20) * 0.5).astype(np.float32)
    glitched = pcm.concat(clean[:midpoint], burst, clean[midpoint:])

    assert analysis.artifact_score(glitched, SAMPLE_RATE) > analysis.artifact_score(clean, SAMPLE_RATE)


def test_clipping_is_measured():
    loud = np.ones(1000, dtype=np.float32)
    assert analysis.clipping_ratio(loud) == 1.0
    assert analysis.clipping_ratio(loud * 0.5) == 0.0


# ─── the channel simulator ───────────────────────────────────────────────────


def make_persona(**channel) -> Persona:
    return Persona(
        name="test",
        environment=EnvironmentConfig(**channel.pop("environment", {})),
        channel=ChannelConfig(**channel),
    )


def test_channel_is_deterministic_for_a_seed(voice):
    audio = voice.synthesize(UTTERANCE, sample_rate=SAMPLE_RATE)
    persona = make_persona(codec="g711u", environment={"noise_profile": "cafe", "snr_db": 15})

    first, _ = ChannelSimulator(persona, seed=42).process(audio, SAMPLE_RATE)
    second, _ = ChannelSimulator(persona, seed=42).process(audio, SAMPLE_RATE)
    other, _ = ChannelSimulator(persona, seed=43).process(audio, SAMPLE_RATE)

    assert np.array_equal(first, second), "same seed must reproduce the same conditions"
    assert not np.array_equal(first, other), "different seeds should differ"


def test_channel_reports_what_it_did(voice):
    audio = voice.synthesize("hello", sample_rate=SAMPLE_RATE)
    persona = make_persona(
        codec="g711u", network="4g_congested", environment={"noise_profile": "cafe", "snr_db": 12}
    )
    _, damage = ChannelSimulator(persona, seed=1).process(audio, SAMPLE_RATE)

    assert damage.codec == "g711u"
    assert damage.noise_profile == "cafe"
    assert damage.snr_db == 12
    assert damage.network == "4g_congested"


def test_clean_persona_leaves_audio_untouched(voice):
    audio = voice.synthesize("hello", sample_rate=SAMPLE_RATE)
    persona = make_persona()
    processed, damage = ChannelSimulator(persona, seed=1).process(audio, SAMPLE_RATE)

    assert np.array_equal(processed, audio)
    assert damage.codec == "none"


def test_unknown_network_profile_is_rejected(voice):
    persona = make_persona(network="carrier-pigeon")
    with pytest.raises(ValueError, match="unknown network profile"):
        ChannelSimulator(persona, seed=1).process(
            voice.synthesize("hi", sample_rate=SAMPLE_RATE), SAMPLE_RATE
        )


# ─── word error rate ─────────────────────────────────────────────────────────


def test_wer_counts_each_edit_type():
    result = wer.wer("one two three four", "one three four five")
    assert result.deletions == 1  # "two" lost
    assert result.insertions == 1  # "five" invented
    assert result.error_rate == pytest.approx(0.5)


def test_wer_is_zero_for_an_exact_match():
    assert wer.wer("hello there", "hello there").error_rate == 0.0


def test_wer_ignores_filler_words():
    assert wer.wer("um my name is rohan", "my name is rohan").error_rate == 0.0


def test_entity_error_rate_isolates_what_matters():
    """A number lost is a failure; a filler word lost is not."""
    reference = "my number is 9876543210 and the medication is metformin"

    filler_lost = wer.entity_error_rate(reference, "number 9876543210 medication metformin",
                                        ["9876543210", "metformin"])
    digit_lost = wer.entity_error_rate(reference, "my number is 9876543219 and the medication is metformin",
                                       ["9876543210", "metformin"])

    assert filler_lost.error_rate == 0.0
    assert digit_lost.error_rate > 0.0


def test_cer_is_finer_grained_than_wer():
    word_level = wer.wer("metformin", "metformim")
    char_level = wer.cer("metformin", "metformim")
    assert word_level.error_rate == 1.0  # the whole word counts as wrong
    assert char_level.error_rate < 0.2  # one character in nine

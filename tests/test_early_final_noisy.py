"""The 200 ms early final and the 550 ms EOU on noisy rooms (L7): Silero behind the
noise-floor gate, pink noise at 10-20 dB SNR over the whole stream (before, during and
after the speech), and on mid-sentence pauses filled with noise."""

from __future__ import annotations

import asyncio

import pytest

from convox.interp.vad import DEFAULT_MODEL_PATH, NoiseFloorGate, SileroVAD
from tests.audio_fixtures import silence, speech, with_noise
from tests.test_early_final import EARLY, feed, make

pytestmark = pytest.mark.skipif(not DEFAULT_MODEL_PATH.exists(), reason="silero model missing")


async def run_noisy(pcm: bytes, script: list[str]):
    clock, interp, sink, stt, mt = make(EARLY, script)
    interp.vad = NoiseFloorGate(SileroVAD())
    await asyncio.wait_for(interp.run(feed(pcm, clock)), 10)
    await interp.aclose()
    return interp, sink


@pytest.mark.parametrize("snr", [10, 15, 20])
async def test_early_final_at_200_and_eou_at_550_in_noise(snr):
    sp = speech(1500)
    pcm = with_noise(silence(1000) + sp + silence(2500), snr, ref=sp, seed=snr)
    interp, sink = await run_noisy(pcm, ["main gate ke bahar hoon"])
    (tr,) = sink.translations
    t = tr.timings
    assert 2400 <= t["t_speech_end"] <= 2700  # the speech ends at 2500 (Silero tail)
    assert 180 <= t["t_early_final"] - t["t_speech_end"] <= 260
    assert 540 <= t["t_eou"] - t["t_speech_end"] <= 600
    assert (t["early_final_used"], t["early_final_restarts"]) == (1, 0)


async def test_a_noisy_mid_sentence_pause_restarts_and_keeps_one_turn():
    a, b = speech(900), speech(900, f0=150)
    pcm = with_noise(silence(1000) + a + silence(350) + b + silence(2500), 15, ref=a, seed=7)
    interp, sink = await run_noisy(pcm, ["main gate ke bahar hoon"])
    (tr,) = sink.translations
    assert tr.timings["early_final_restarts"] == 1 and tr.timings["early_final_used"] == 1

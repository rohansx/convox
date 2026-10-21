"""Live vendor checks. Deselected by default; run with `uv run pytest -m live -s`.
Keys come from .env via Settings; each test skips when its key is missing."""

import asyncio
import time
import wave
from pathlib import Path

import pytest

from dubhash.audio.frames import FRAME_BYTES, SAMPLE_RATE
from dubhash.config.settings import Settings
from dubhash.providers.eleven_tts import ElevenTTS
from dubhash.providers.sarvam_mt import SarvamMT
from dubhash.providers.sarvam_stt import SarvamSTT
from dubhash.providers.sarvam_tts import BulbulTTS

pytestmark = pytest.mark.live

S = Settings()
need_sarvam = pytest.mark.skipif(not S.sarvam_api_key, reason="SARVAM_API_KEY not set")
need_eleven = pytest.mark.skipif(not S.elevenlabs_api_key, reason="ELEVENLABS_API_KEY not set")


async def _synth(tts, text: str, voice: str, lang: str) -> tuple[bytes, int, int]:
    t0 = time.monotonic()
    ttfb = -1
    chunks = []
    async for c in tts.synth(text, voice, lang):
        if ttfb < 0:
            ttfb = int((time.monotonic() - t0) * 1000)
        chunks.append(c)
    return b"".join(chunks), ttfb, int((time.monotonic() - t0) * 1000)


@need_sarvam
async def test_live_sarvam_translate_hi_to_kn():
    mt = SarvamMT(S.sarvam_api_key)
    t0 = time.monotonic()
    out = await mt.translate("namaste, aap kaise hain", "hi-IN", "kn-IN")
    ms = int((time.monotonic() - t0) * 1000)
    await mt.aclose()
    print(f"\n[live] mayura hi->kn {ms} ms: {out!r}")
    assert out.strip()


@need_sarvam
async def test_live_bulbul_kannada():
    tts = BulbulTTS(S.sarvam_api_key)
    pcm, ttfb, total = await _synth(tts, "ನಮಸ್ಕಾರ, ನೀವು ಹೇಗಿದ್ದೀರಿ?", "bulbul:kn-female-1", "kn-IN")
    await tts.aclose()
    print(f"\n[live] bulbul kn ttfb={ttfb} ms total={total} ms audio={len(pcm) / 32000:.2f} s")
    assert len(pcm) > SAMPLE_RATE  # > 0.5 s of audio


@need_eleven
async def test_live_eleven_hindi():
    tts = ElevenTTS(S.elevenlabs_api_key)
    pcm, ttfb, total = await _synth(tts, "नमस्ते, आप कैसे हैं?", "eleven:hi-female-1", "hi-IN")
    await tts.aclose()
    print(f"\n[live] eleven hi ttfb={ttfb} ms total={total} ms audio={len(pcm) / 32000:.2f} s")
    assert len(pcm) > SAMPLE_RATE


@need_sarvam
async def test_live_sarvam_stt_on_tts_audio(tmp_path: Path):
    tts = BulbulTTS(S.sarvam_api_key)
    pcm, _, _ = await _synth(tts, "नमस्ते, मैं गेट के बाहर हूँ।", "bulbul:shubh", "hi-IN")
    await tts.aclose()
    wav_path = tmp_path / "hi.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm + b"\x00" * FRAME_BYTES * 25)  # 0.5 s trailing silence
    with wave.open(str(wav_path), "rb") as w:
        audio = w.readframes(w.getnframes())

    stt = SarvamSTT(S.sarvam_api_key)
    t_conn = time.monotonic()
    stream = await stt.open("hi-IN")
    conn_ms = int((time.monotonic() - t_conn) * 1000)
    for i in range(0, len(audio), FRAME_BYTES * 5):  # 100 ms chunks, 5x real time
        await stream.push(audio[i : i + FRAME_BYTES * 5])
        await asyncio.sleep(0.02)
    t0 = time.monotonic()
    text = await stream.end()
    final_ms = int((time.monotonic() - t0) * 1000)
    partials = [p async for p in stream.partials()]

    # second utterance on the warm socket
    s2 = await stt.open("hi-IN")
    for i in range(0, len(audio), FRAME_BYTES * 5):
        await s2.push(audio[i : i + FRAME_BYTES * 5])
        await asyncio.sleep(0.02)
    text2 = await s2.end()
    await stt.aclose()
    print(
        f"\n[live] sarvam stt connect={conn_ms} ms final_after_end={final_ms} ms "
        f"partials={len(partials)} text={text!r} warm_second={text2!r}"
    )
    assert text.strip()
    assert text2.strip()

"""Generate the audio for the launch video (launch-video/, Remotion) from the real pipeline:
the auto story, a Kannada-speaking driver and a Hindi-speaking passenger in Bengaluru.

First the call without Convox (each hears the other's language and gives up), then the
same call with it: each line translated by Sarvam Mayura (speaker gender passed), spoken
by Bulbul in the speaker's voice and then in the interpreter's, with the number check.

    uv run python tools/launch_audio.py      # needs SARVAM_API_KEY

Writes launch-video/public/audio/*.wav and launch-video/src/call.json.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from convox.audio.earcon import EARCON  # noqa: E402
from convox.config.settings import Settings  # noqa: E402
from convox.interp.numbers import readback  # noqa: E402
from convox.providers.sarvam_mt import SarvamMT  # noqa: E402
from convox.providers.sarvam_tts import BulbulTTS  # noqa: E402
from tools.landing_voices import SAMPLE_RATE, ms, synth  # noqa: E402

VIDEO = ROOT / "launch-video"
AUDIO = VIDEO / "public" / "audio"
OUT = VIDEO / "src" / "call.json"
LANG = {"a": "kn-IN", "b": "hi-IN"}  # Basavaraj, the driver; Neha, the passenger
GENDER = {"a": "m", "b": "f"}
SPEAKER = {"a": "rahul", "b": "priya"}  # the speakers' own (synthetic) voices
VOICE = {"f": "ishita", "m": "shubh"}  # the interpreter's

# ruff: noqa: E501
WITHOUT = [
    ("a", "ಹಲೋ ಮೇಡಂ, ನೀವು ಎಲ್ಲಿದ್ದೀರಾ?", "Hello madam, where are you?"),
    ("b", "हैलो? सॉरी, मुझे कन्नड़ नहीं आती।", "Hello? Sorry, I don't speak Kannada."),
    ("a", "ಅಯ್ಯೋ, ನನಗೆ ಹಿಂದಿ ಬರಲ್ಲ ಮೇಡಂ.", "Oh no, I don't know Hindi, madam."),
]
WITH = [
    ("a", "ಹಲೋ ಮೇಡಂ, ನೀವು ಎಲ್ಲಿದ್ದೀರಾ?", "Hello madam, where are you?"),
    (
        "b",
        "मैं मेट्रो स्टेशन के गेट नंबर 2 पर हूँ, नीली ड्रेस में।",
        "I'm at gate number 2 of the metro station, in a blue dress.",
    ),
    ("a", "ಸರಿ, ಎರಡು ನಿಮಿಷದಲ್ಲಿ ಅಲ್ಲಿಗೆ ಬರ್ತೀನಿ.", "Okay, I'll be there in two minutes."),
    ("b", "ठीक है, मैं गेट पर ही इंतज़ार करती हूँ।", "Okay, I'll wait at the gate."),
]


def save(pcm: bytes, name: str) -> str:
    with wave.open(str(AUDIO / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return f"audio/{name}.wav"


async def main() -> None:
    s = Settings()
    if not s.sarvam_api_key:
        sys.exit("SARVAM_API_KEY is needed")
    mt, tts = SarvamMT(s.sarvam_api_key), BulbulTTS(s.sarvam_api_key)
    AUDIO.mkdir(parents=True, exist_ok=True)
    without, with_ = [], []
    try:
        for n, (side, src, en) in enumerate(WITHOUT):
            pcm, _ = await synth(tts, src, SPEAKER[side], LANG[side])
            without.append(
                {"side": side, "text": src, "en": en, "audio": save(pcm, f"raw-{n}"), "ms": ms(pcm)}
            )
            print(f"without {side}: {src}")
        for n, (side, src, en) in enumerate(WITH):
            other = "b" if side == "a" else "a"
            t0 = time.monotonic()
            tgt = await mt.translate(src, LANG[side], LANG[other], gender=GENDER[side])
            tgt = readback(src, tgt, LANG[other])[0]
            mt_ms = round((time.monotonic() - t0) * 1000)
            src_pcm, _ = await synth(tts, src, SPEAKER[side], LANG[side])
            tgt_pcm, tts_ms = await synth(tts, tgt, VOICE[GENDER[side]], LANG[other])
            with_.append(
                {
                    "side": side,
                    "src_text": src,
                    "tgt_text": tgt,
                    "en": en,
                    "src_audio": save(src_pcm, f"{n}-src"),
                    "tgt_audio": save(EARCON + tgt_pcm, f"{n}-tgt"),
                    "src_ms": ms(src_pcm),
                    "tgt_ms": ms(EARCON + tgt_pcm),
                    "mt_ms": mt_ms,
                    "tts_ms": tts_ms,
                }
            )
            print(f"with {side}: {src} -> {tgt}  (mt {mt_ms} ms, tts {tts_ms} ms)")
    finally:
        await tts.aclose()
        await mt.aclose()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {"generated_with": "Sarvam Mayura + Bulbul v3", "without": without, "with": with_},
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )
    print("wrote", OUT)


if __name__ == "__main__":
    asyncio.run(main())

"""Generate the landing page's call audio with the real pipeline: each speaker's line in
their own voice, then Convox's interpretation (Sarvam Mayura MT + Bulbul TTS, the
listener's language, gender-matched, after the earcon), exactly what a caller hears.

    uv run python tools/landing_voices.py          # needs SARVAM_API_KEY in .env

Writes convox/console/static/landing/scenes.json and audio/<scene>-<n>-{src,tgt}.m4a
(AAC via macOS afconvert; .wav if afconvert is missing). The speakers are synthetic
voices; the translations and the interpreter voice are Convox's own output, with the MT
and TTS time-to-first-byte measured while generating.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from convox.audio.earcon import EARCON  # noqa: E402
from convox.audio.frames import SAMPLE_RATE  # noqa: E402
from convox.config.settings import Settings  # noqa: E402
from convox.providers.sarvam_mt import SarvamMT  # noqa: E402
from convox.providers.sarvam_tts import BulbulTTS  # noqa: E402

OUT = ROOT / "convox" / "console" / "static" / "landing"
INTERPRETER = {"f": "ishita", "m": "shubh"}  # the call's default voices (sarvam_tts.py)

# Speakers' own lines. Marathi, Gujarati and Kannada need a native speaker's check (PQ5).
# ruff: noqa: E501 (one speaker per line reads better than wrapped dicts)
SCENES = {
    "delivery": {
        "caption": "A Marathi-speaking customer and a Hindi-speaking delivery rider.",
        "a": {"name": "Sunita", "lang": "mr-IN", "native": "मराठी", "g": "f", "voice": "rupali"},
        "b": {"name": "Ravi", "lang": "hi-IN", "native": "हिन्दी", "g": "m", "voice": "rahul"},
        "lines": [
            ("a", "मी घरी नाही, पार्सल वॉचमनकडे द्या."),
            ("b", "ठीक है, ओटीपी बता दीजिए।"),
            ("a", "ओटीपी 47296 आहे."),
        ],
    },
    "clinic": {
        "caption": "A Gujarati-speaking patient and an English-speaking doctor.",
        "a": {"name": "Hansaben", "lang": "gu-IN", "native": "ગુજરાતી", "g": "f", "voice": "roopa"},
        "b": {"name": "Dr. Mehta", "lang": "en-IN", "native": "English", "g": "m", "voice": "anand"},
        "lines": [
            ("a", "મને ત્રણ દિવસથી તાવ છે."),
            ("b", "Take one tablet twice a day, after food."),
            ("a", "બરાબર, આભાર ડૉક્ટર."),
        ],
    },
    "rent": {
        "caption": "A Kannada-speaking tenant and a Hindi-speaking landlord.",
        "a": {"name": "Kiran", "lang": "kn-IN", "native": "ಕನ್ನಡ", "g": "m", "voice": "vijay"},
        "b": {"name": "Sharma ji", "lang": "hi-IN", "native": "हिन्दी", "g": "m", "voice": "ashutosh"},
        "lines": [
            ("a", "ಎರಡು ದಿನದಿಂದ ನೀರು ಬರುತ್ತಿಲ್ಲ."),
            ("b", "आज शाम 6 बजे प्लंबर भेजता हूँ।"),
            ("a", "ಸರಿ, ಧನ್ಯವಾದಗಳು."),
        ],
    },
}  # fmt: skip


async def synth(tts: BulbulTTS, text: str, voice: str, lang: str) -> tuple[bytes, int]:
    t0 = time.monotonic()
    ttfb = None
    pcm = b""
    async for chunk in tts.synth(text, f"bulbul:{voice}", lang):
        if ttfb is None:
            ttfb = round((time.monotonic() - t0) * 1000)
        pcm += chunk
    return pcm, ttfb or 0


def save(pcm: bytes, path: Path) -> str:
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    if shutil.which("afconvert"):
        m4a = path.with_suffix(".m4a")
        subprocess.run(
            ["afconvert", "-f", "m4af", "-d", "aac", "-b", "48000", str(wav), str(m4a)],
            check=True,
        )
        wav.unlink()
        return m4a.name
    return wav.name


def ms(pcm: bytes) -> int:
    return round(len(pcm) / 2 / SAMPLE_RATE * 1000)


async def main() -> None:
    settings = Settings()
    if not settings.sarvam_api_key:
        sys.exit("SARVAM_API_KEY is not set")
    mt = SarvamMT(settings.sarvam_api_key)
    tts = BulbulTTS(settings.sarvam_api_key)
    (OUT / "audio").mkdir(parents=True, exist_ok=True)
    out: dict = {"generated_with": "Sarvam Mayura MT + Bulbul v3 TTS", "scenes": {}}
    try:
        for key, scene in SCENES.items():
            lines = []
            for n, (side, src) in enumerate(scene["lines"]):
                me, other = scene[side], scene["b" if side == "a" else "a"]
                t0 = time.monotonic()
                tgt = await mt.translate(src, me["lang"], other["lang"])
                mt_ms = round((time.monotonic() - t0) * 1000)
                src_pcm, _ = await synth(tts, src, me["voice"], me["lang"])
                tgt_pcm, tts_ms = await synth(tts, tgt, INTERPRETER[me["g"]], other["lang"])
                base = OUT / "audio" / f"{key}-{n}"
                lines.append(
                    {
                        "from": side,
                        "src": src,
                        "tgt": tgt,
                        "src_audio": "audio/" + save(src_pcm, base.with_name(base.name + "-src")),
                        "tgt_audio": "audio/"
                        + save(EARCON + tgt_pcm, base.with_name(base.name + "-tgt")),
                        "src_ms": ms(src_pcm),
                        "tgt_ms": ms(EARCON + tgt_pcm),
                        "mt_ms": mt_ms,
                        "tts_ttfb_ms": tts_ms,
                    }
                )
                print(f"{key} {n} {side}: {src} -> {tgt}  (mt {mt_ms} ms, tts {tts_ms} ms)")
            people = {s: {k: scene[s][k] for k in ("name", "lang", "native")} for s in "ab"}
            out["scenes"][key] = {"caption": scene["caption"], **people, "lines": lines}
    finally:
        await tts.aclose()
        await mt.aclose()
    (OUT / "scenes.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print("wrote", OUT / "scenes.json")


if __name__ == "__main__":
    asyncio.run(main())

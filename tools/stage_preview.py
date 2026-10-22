"""Generate the live stage's sample call (the Auto story) (/demo/preview) from the real pipeline: each scripted
line translated by Sarvam Mayura (speaker gender passed), the Bulbul voice's time to first
byte measured, and the outcome card written by Claude over the speakers' own words.

    uv run python tools/stage_preview.py      # needs SARVAM_API_KEY and ANTHROPIC_API_KEY

Writes convox/console/static/stage/preview.json. The end-of-turn wait and recognition
times are the live medians from our logged calls (65 turns): eou 300 ms, stt 60 ms.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402

from convox.config.settings import Settings  # noqa: E402
from convox.demo.outcome import summarise  # noqa: E402
from convox.demo.stories import STORIES  # noqa: E402
from convox.providers.sarvam_mt import SarvamMT  # noqa: E402
from convox.providers.sarvam_tts import BulbulTTS  # noqa: E402

OUT = ROOT / "convox" / "console" / "static" / "stage" / "preview.json"
EOU_MS, STT_MS = 300, 60  # live medians (out/live, 65 turns)
VOICE = {"f": "ishita", "m": "shubh"}
GENDER = {"a": "m", "b": "f"}  # Basavaraj, Neha

# ruff: noqa: E501
LINES = [
    ("a", "ಹಲೋ ಮೇಡಂ, ನೀವು ಎಲ್ಲಿದ್ದೀರಾ?"),
    ("b", "मैं मेट्रो स्टेशन के गेट नंबर 2 पर खड़ी हूँ।"),
    ("a", "ಸರಿ, ಎರಡು ನಿಮಿಷದಲ್ಲಿ ಅಲ್ಲಿಗೆ ಬರ್ತೀನಿ. ಬಿಳಿ ಆಟೋ, ನಂಬರ್ 4417."),
    ("b", "ठीक है, मैंने नीली ड्रेस पहनी है।"),
    ("a", "ಕಾಣಿಸ್ತು, ನಾನು ನಿಮ್ಮ ಎದುರಿಗೇ ಇದ್ದೀನಿ."),
    ("b", "हाँ, दिख गया। आ रही हूँ।"),
]


async def ttfb(tts: BulbulTTS, text: str, voice: str, lang: str) -> int:
    t0 = time.monotonic()
    first = None
    async for _ in tts.synth(text, f"bulbul:{voice}", lang):
        if first is None:
            first = round((time.monotonic() - t0) * 1000)
    return first or 0


async def main() -> None:
    s = Settings()
    if not s.sarvam_api_key or not s.anthropic_api_key:
        sys.exit("SARVAM_API_KEY and ANTHROPIC_API_KEY are needed")
    story = STORIES["auto"]
    langs = {k: story[k]["lang"] for k in ("a", "b")}
    mt, tts = SarvamMT(s.sarvam_api_key), BulbulTTS(s.sarvam_api_key)
    turns = []
    try:
        for side, src in LINES:
            other = "b" if side == "a" else "a"
            t0 = time.monotonic()
            tgt = await mt.translate(src, langs[side], langs[other], gender=GENDER[side])
            mt_ms = round((time.monotonic() - t0) * 1000)
            tts_ms = await ttfb(tts, tgt, VOICE[GENDER[side]], langs[other])
            turns.append(
                {
                    "side": side,
                    "src_text": src,
                    "tgt_text": tgt,
                    "st": [EOU_MS, STT_MS, mt_ms, tts_ms, 20],
                }
            )
            print(f"{side}: {src} -> {tgt}  (mt {mt_ms} ms, tts {tts_ms} ms)")
    finally:
        await tts.aclose()
        await mt.aclose()
    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
    outcome = await summarise(client, s.anthropic_model, turns, story, langs)
    print("outcome:", json.dumps(outcome, ensure_ascii=False, indent=1))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated_with": f"Sarvam Mayura + Bulbul v3; outcome by {s.anthropic_model}",
                "story": "auto",
                "turns": turns,
                "outcome": outcome,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )
    print("wrote", OUT)


if __name__ == "__main__":
    asyncio.run(main())

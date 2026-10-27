"""Generate the landing page's sample call (stories.PREVIEW: a Gujarati delivery agent and
a Hindi-speaking customer) for /demo/preview, from the real pipeline: each line translated
by Sarvam Mayura (speaker gender passed), spoken by Bulbul in the speaker's voice and then
in the interpreter's (as tools/landing_voices.py), the voice's time to first byte
measured, and the outcome card written by Claude over the speakers' own words.

    uv run python tools/stage_preview.py      # needs SARVAM_API_KEY and ANTHROPIC_API_KEY

Writes convox/console/static/stage/preview.json and stage/audio/<n>-{src,tgt}.m4a. The
end-of-turn wait and recognition times are the live medians from our logged calls (65
turns): eou 300 ms, stt 60 ms.
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

from convox.audio.earcon import EARCON  # noqa: E402
from convox.config.settings import Settings  # noqa: E402
from convox.demo.outcome import summarise  # noqa: E402
from convox.demo.stories import PREVIEW  # noqa: E402
from convox.interp.numbers import readback  # noqa: E402
from convox.providers.sarvam_mt import SarvamMT  # noqa: E402
from convox.providers.sarvam_tts import BulbulTTS  # noqa: E402
from tools.landing_voices import ms, save, synth  # noqa: E402

OUT = ROOT / "convox" / "console" / "static" / "stage" / "preview.json"
AUDIO = OUT.parent / "audio"
EOU_MS, STT_MS = 300, 60  # live medians (out/live, 65 turns)
VOICE = {"f": "ishita", "m": "shubh"}
GENDER = {"a": "m", "b": "f"}  # Jignesh, Neha
SPEAKER = {"a": "rahul", "b": "priya"}  # the speakers' own (synthetic) voices

# ruff: noqa: E501
LINES = [
    ("a", "નમસ્તે, હું તમારું પાર્સલ લઈને આવ્યો છું. હું સોસાયટીના ગેટ પર છું."),
    ("b", "मैं बी विंग में हूँ, फ़्लैट नंबर 304।"),
    ("a", "બરાબર, ઉપર આવું છું. ઓટીપી કહો."),
    ("b", "ओटीपी 5823 है।"),
    ("a", "સારું, હું બે મિનિટમાં ઉપર આવું છું."),
    ("b", "ठीक है, मैं दरवाज़े पर हूँ।"),
]


async def main() -> None:
    s = Settings()
    if not s.sarvam_api_key or not s.anthropic_api_key:
        sys.exit("SARVAM_API_KEY and ANTHROPIC_API_KEY are needed")
    story = PREVIEW
    langs = {k: story[k]["lang"] for k in ("a", "b")}
    mt, tts = SarvamMT(s.sarvam_api_key), BulbulTTS(s.sarvam_api_key)
    AUDIO.mkdir(parents=True, exist_ok=True)
    turns = []
    try:
        for n, (side, src) in enumerate(LINES):
            other = "b" if side == "a" else "a"
            t0 = time.monotonic()
            tgt = await mt.translate(src, langs[side], langs[other], gender=GENDER[side])
            tgt = readback(src, tgt, langs[other])[0]  # the delivery pack's number check
            mt_ms = round((time.monotonic() - t0) * 1000)
            src_pcm, _ = await synth(tts, src, SPEAKER[side], langs[side])
            tgt_pcm, tts_ms = await synth(tts, tgt, VOICE[GENDER[side]], langs[other])
            turns.append(
                {
                    "side": side,
                    "src_text": src,
                    "tgt_text": tgt,
                    "st": [EOU_MS, STT_MS, mt_ms, tts_ms, 20],
                    "src_audio": "audio/" + save(src_pcm, AUDIO / f"{n}-src"),
                    "tgt_audio": "audio/" + save(EARCON + tgt_pcm, AUDIO / f"{n}-tgt"),
                    "src_ms": ms(src_pcm),
                    "tgt_ms": ms(EARCON + tgt_pcm),
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
                "story": "preview",
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

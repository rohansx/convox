"""Live pivot trial for open question L1: is speech->English + en->kn faster than
hi STT + hi->kn MT?

    uv run python tools/pivot_probe.py                    # 10 sentences, both routes
    uv run python tools/pivot_probe.py --n 3 --fast       # quick look, audio not paced
    uv run python tools/pivot_probe.py --translate-model saaras:v4

Route A: Sarvam realtime STT hi-IN (transcribe)       -> Sarvam MT hi-IN -> kn-IN
Route B: Sarvam realtime STT hi-IN (mode=translate)   -> Sarvam MT en-IN -> kn-IN
Also:    Sarvam MT hi-IN -> en-IN on route A's transcript (is hi->kn ~ hi->en + en->kn?)

The Hindi clips are synthesised once with Bulbul and cached in out/pivot/. Audio is paced
in real time (like a caller) unless --fast. STT latency is end() (speech_end) -> final.
MT is a warm shared httpx client. Routes alternate which goes first per sentence.
Each sentence lists the English words a faithful translation must keep (names, numbers);
route B's English intermediate is checked for them, since the Kannada outputs can't be
rated without a native speaker. Writes out/pivot/results.json. Needs SARVAM_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from convox.audio.frames import FRAME_BYTES, FRAME_MS  # noqa: E402
from convox.audio.wav import read_wav, write_wav  # noqa: E402

OUT = ROOT / "out" / "pivot"
HI, KN, EN = "hi-IN", "kn-IN", "en-IN"
HI_VOICE = "bulbul:hi-female-1"
# Fixed costs from the L1 budget, added to STT + MT for a first-audio estimate.
EOU_MS, TTS_TTFB_MS, PLAYOUT_MS = 550, 250, 200


@dataclass(frozen=True)
class Sentence:
    hi: str
    keeps: tuple[str, ...]  # English a faithful translation keeps; "a|b" = either


SENTENCES: tuple[Sentence, ...] = (
    Sentence("भैया, मैं गेट नंबर दो के बाहर खड़ा हूँ, आप नीचे आ जाइए।", ("gate", "2|two")),
    Sentence(
        "शर्मा जी का पार्सल है, फ्लैट नंबर तीन सौ दो, बी ब्लॉक।",
        ("Sharma", "302|three hundred two|three hundred and two", "B"),
    ),
    Sentence(
        "मैडम, कैश ऑन डिलीवरी है, चार सौ पचास रुपये देने होंगे।",
        ("cash", "450|four hundred fifty|four hundred and fifty"),
    ),
    Sentence("डॉक्टर मेहता आज शाम छह बजे तक ही बैठेंगे।", ("Mehta", "6|six")),
    Sentence("दवाई दिन में दो बार खाना खाने के बाद लेनी है।", ("twice|two times|2 times", "after")),
    Sentence("आपका ओटीपी बता दीजिए, चार अंकों वाला।", ("OTP", "4|four")),
    Sentence("रिसेप्शन पर प्रिया को अपना नाम और टोकन नंबर बताइए।", ("Priya", "token")),
    Sentence("बुखार कितने दिन से है, और कोई दवा ली क्या?", ("fever", "days")),
    Sentence("मैं पाँच मिनट में पहुँच रहा हूँ, ट्रैफिक बहुत है।", ("5|five", "traffic")),
    Sentence(
        "रिपोर्ट कल सुबह दस बजे के बाद मिलेगी, पर्ची साथ लाना।",
        ("report", "10|ten", "tomorrow"),
    ),
)


def pct(xs: list[int], p: float) -> int | None:
    """Nearest-rank percentile (with n=10, p95 is the maximum)."""
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def missing_keeps(text: str, keeps: tuple[str, ...]) -> list[str]:
    low = text.lower()
    out = []
    for k in keeps:
        alts = [a.strip().lower() for a in k.split("|")]
        if not any(re.search(rf"(?<!\w){re.escape(a)}(?!\w)", low) for a in alts):
            out.append(k)
    return out


@dataclass
class Row:
    hi: str
    a_text: str = ""
    a_kn: str = ""
    b_en: str = ""
    b_kn: str = ""
    hi_en: str = ""
    a_stt_ms: int = 0
    a_mt_ms: int = 0
    b_stt_ms: int = 0
    b_mt_ms: int = 0
    hi_en_ms: int = 0
    b_missing: list[str] = field(default_factory=list)
    hi_en_missing: list[str] = field(default_factory=list)

    @property
    def a_total_ms(self) -> int:
        return self.a_stt_ms + self.a_mt_ms

    @property
    def b_total_ms(self) -> int:
        return self.b_stt_ms + self.b_mt_ms


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


async def _push(stream: Any, pcm: bytes, realtime: bool) -> None:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    for i, off in enumerate(range(0, len(pcm), FRAME_BYTES)):
        await stream.push(pcm[off : off + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00"))
        if realtime:
            delay = t0 + (i + 1) * FRAME_MS / 1000 - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)


async def _stt(stt: Any, pcm: bytes, realtime: bool) -> tuple[str, int]:
    stream = await stt.open(HI)
    try:
        await _push(stream, pcm, realtime)
        t0 = time.monotonic()
        text = await stream.end()
        return text, _ms(t0)
    finally:
        await stream.aclose()


async def _mt(mt: Any, text: str, src: str, tgt: str) -> tuple[str, int]:
    t0 = time.monotonic()
    out = await mt.translate(text, src, tgt)
    return out, _ms(t0)


async def run_sentence(
    s: Sentence, pcm: bytes, *, stt_a: Any, stt_b: Any, mt: Any, realtime: bool, b_first=False
) -> Row:
    row = Row(hi=s.hi)

    async def route_a() -> None:
        row.a_text, row.a_stt_ms = await _stt(stt_a, pcm, realtime)
        row.a_kn, row.a_mt_ms = await _mt(mt, row.a_text, HI, KN)

    async def route_b() -> None:
        row.b_en, row.b_stt_ms = await _stt(stt_b, pcm, realtime)
        row.b_kn, row.b_mt_ms = await _mt(mt, row.b_en, EN, KN)

    for step in (route_b, route_a) if b_first else (route_a, route_b):
        await step()
    row.hi_en, row.hi_en_ms = await _mt(mt, row.a_text, HI, EN)
    row.b_missing = missing_keeps(row.b_en, s.keeps)
    row.hi_en_missing = missing_keeps(row.hi_en, s.keeps)
    return row


# --- live -----------------------------------------------------------------------------


async def _synth_clips(key: str, sentences: tuple[Sentence, ...]) -> list[bytes]:
    from convox.providers.sarvam_tts import BulbulTTS

    OUT.mkdir(parents=True, exist_ok=True)
    tts = BulbulTTS(key)
    clips = []
    try:
        for i, s in enumerate(sentences):
            path = OUT / f"hi_{i:02d}.wav"
            if not path.exists():
                pcm = b"".join([c async for c in tts.synth(s.hi, HI_VOICE, HI)])
                # 300 ms tail, roughly what Silero's EOU wait would have pushed
                write_wav(path, pcm + b"\x00" * (FRAME_BYTES * 15))
            clips.append(read_wav(path))
    finally:
        await tts.aclose()
    return clips


def _summary(rows: list[Row]) -> dict[str, dict[str, int | None]]:
    cols = {
        "A stt (hi)": [r.a_stt_ms for r in rows],
        "A mt hi->kn": [r.a_mt_ms for r in rows],
        "A stt+mt": [r.a_total_ms for r in rows],
        "B stt (->en)": [r.b_stt_ms for r in rows],
        "B mt en->kn": [r.b_mt_ms for r in rows],
        "B stt+mt": [r.b_total_ms for r in rows],
        "mt hi->en": [r.hi_en_ms for r in rows],
    }
    return {k: {"p50": pct(v, 50), "p95": pct(v, 95)} for k, v in cols.items()}


def _print(rows: list[Row], summary: dict[str, dict[str, int | None]]) -> None:
    for i, r in enumerate(rows):
        print(f"\n#{i} {r.hi}")
        print(f"  A  hi : {r.a_text}")
        print(f"  A  kn : {r.a_kn}   [stt {r.a_stt_ms} + mt {r.a_mt_ms} = {r.a_total_ms}]")
        print(f"  B  en : {r.b_en}")
        print(f"  B  kn : {r.b_kn}   [stt {r.b_stt_ms} + mt {r.b_mt_ms} = {r.b_total_ms}]")
        print(f"  hi>en : {r.hi_en}   [{r.hi_en_ms}]")
        if r.b_missing:
            print(f"  FLAG B english lost: {r.b_missing}")
        if r.hi_en_missing:
            print(f"  flag hi>en MT lost: {r.hi_en_missing}")
    print(f"\n{'stage':<14} {'p50':>6} {'p95':>6}")
    for k, v in summary.items():
        print(f"{k:<14} {v['p50']!s:>6} {v['p95']!s:>6}")
    fixed = EOU_MS + TTS_TTFB_MS + PLAYOUT_MS
    for route in ("A", "B"):
        v = summary[f"{route} stt+mt"]
        p50, p95 = (x + fixed if x is not None else None for x in (v["p50"], v["p95"]))
        print(f"first-audio est. route {route}: p50 {p50}  p95 {p95}  (+{fixed} fixed)")


async def main_async(opts: argparse.Namespace) -> int:
    from convox.config.settings import Settings
    from convox.providers.sarvam_mt import SarvamMT
    from convox.providers.sarvam_stt import SarvamSTT
    from convox.providers.sarvam_stt_translate import SarvamSTTTranslate

    key = Settings(_env_file=ROOT / ".env").sarvam_api_key  # type: ignore[call-arg]
    if not key:
        print("SARVAM_API_KEY not set", file=sys.stderr)
        return 2
    sentences = SENTENCES[: opts.n]
    clips = await _synth_clips(key, sentences)
    stt_a = SarvamSTT(key)
    stt_b = SarvamSTTTranslate(key, model=opts.translate_model)
    mt = SarvamMT(key)
    rows: list[Row] = []
    try:
        await asyncio.gather(stt_a.prewarm(HI), stt_b.prewarm(HI))
        await mt.translate("नमस्ते", HI, KN)  # warm the HTTP connection
        for i, (s, pcm) in enumerate(zip(sentences, clips, strict=True)):
            rows.append(
                await run_sentence(
                    s, pcm, stt_a=stt_a, stt_b=stt_b, mt=mt,
                    realtime=not opts.fast, b_first=bool(i % 2),
                )
            )  # fmt: skip
    finally:
        await stt_a.aclose()
        await stt_b.aclose()
        await mt.aclose()
    summary = _summary(rows)
    _print(rows, summary)
    OUT.mkdir(parents=True, exist_ok=True)
    out = {
        "translate_model": opts.translate_model,
        "realtime": not opts.fast,
        "summary": summary,
        "rows": [asdict(r) | {"a_total_ms": r.a_total_ms, "b_total_ms": r.b_total_ms}
                 for r in rows],
    }  # fmt: skip
    (OUT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nwrote {OUT / 'results.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--n", type=int, default=len(SENTENCES), help="sentences to run (max 10)")
    p.add_argument("--fast", action="store_true", help="push audio unpaced")
    p.add_argument("--translate-model", default="saaras:v3-realtime")
    return asyncio.run(main_async(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())

"""Live language-ID accuracy probe for lang "auto" (open question L8).

    uv run python tools/langid_probe.py                      # full run (cached clips)
    uv run python tools/langid_probe.py --langs hi-IN mr-IN --n 3 --no-realtime
    uv run python tools/langid_probe.py --models saaras:v3 saaras:v4 --conds pstn
    uv run python tools/langid_probe.py --replay out/langid/results.json \
        out/langid/results-v4.json --models saaras:v3 saaras:v4 --tag all   # no calls

Corpus: 10 short, hard sentences per supported language (CORPUS), parallel across
languages (OTP / lift / flat / parcel / doctor / plumber / cash on delivery), so Hindi,
Marathi and Gujarati share loanwords and near-identical words. Each is synthesised with
Bulbul in a male and a female voice (cached in out/langid/clips/), leading silence trimmed
to 100 ms as our VAD's pre-roll would. Two channels: `clean` (16 kHz) and `pstn`
(16k -> 8k -> G.711 mu-law -> 8k -> 16k, i.e. what a phone leg gives us). Most of these
sentences are under 2 s, so a "long" set (sentences 0+1, 2+3, ... joined with 200 ms of
silence, idx 100+) shows how detection changes with more speech (--no-long skips it).

Engines measured:
  rest      SarvamLID.detect(): REST STT, language_code=unknown, on the first 1.0 s,
            the first 2.0 s and the whole utterance (one call per window, per --models).
  realtime  realtime socket with language_code=auto (transcribe and mode=translate):
            detected_lang + language_confidence on the final of the whole utterance.
  text      Sarvam text-lid on the REST full-utterance transcript (second opinion).

Candidates are SUPPORTED_LANGS. A detection outside them counts as a reject. Writes
out/langid/results[-TAG].json (every row) and out/langid/summary[-TAG].md (per group:
accuracy, mr/hi and gu/hi confusions, latency, confusion matrix, threshold sweep).
Needs SARVAM_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dubhash.audio.codec import ulaw_decode, ulaw_encode  # noqa: E402
from dubhash.audio.frames import FRAME_BYTES, ms_to_bytes  # noqa: E402
from dubhash.audio.resample import Resampler  # noqa: E402
from dubhash.audio.wav import read_wav, write_wav  # noqa: E402
from dubhash.interp.langid import SUPPORTED_LANGS, pick  # noqa: E402

OUT = ROOT / "out" / "langid"
VOICES = {"male": "bulbul:shubh", "female": "bulbul:ishita"}  # v3, all languages
WINDOWS: tuple[int | None, ...] = (1000, 2000, None)  # None = whole utterance
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)
NONE = "none"

CORPUS: dict[str, tuple[str, ...]] = {
    "hi-IN": (
        "ओटीपी आया क्या?",
        "लिफ्ट बंद है।",
        "फ्लैट नंबर तीन सौ दो।",
        "हाँ, ठीक है।",
        "आप अभी कहाँ हो?",
        "मेरा पार्सल गेट पर छोड़ दो।",
        "डॉक्टर साहब आज आएंगे क्या?",
        "पानी नहीं आ रहा है, प्लंबर भेजो।",
        "मैं पाँच मिनट में पहुँच रहा हूँ।",
        "कैश ऑन डिलीवरी है, चार सौ रुपये।",
    ),
    "mr-IN": (
        "ओटीपी आला का?",
        "लिफ्ट बंद आहे.",
        "फ्लॅट नंबर तीनशे दोन.",
        "हो, ठीक आहे.",
        "तुम्ही आता कुठे आहात?",
        "माझं पार्सल गेटवर ठेवा.",
        "डॉक्टर आज येणार आहेत का?",
        "पाणी येत नाहीये, प्लंबर पाठवा.",
        "मी पाच मिनिटांत पोहोचतो.",
        "कॅश ऑन डिलिव्हरी आहे, चारशे रुपये.",
    ),
    "gu-IN": (
        "ઓટીપી આવ્યો?",
        "લિફ્ટ બંધ છે.",
        "ફ્લેટ નંબર ત્રણસો બે.",
        "હા, બરાબર છે.",
        "તમે અત્યારે ક્યાં છો?",
        "મારું પાર્સલ ગેટ પર મૂકી દો.",
        "ડોક્ટર આજે આવશે?",
        "પાણી નથી આવતું, પ્લમ્બર મોકલો.",
        "હું પાંચ મિનિટમાં પહોંચું છું.",
        "કેશ ઓન ડિલિવરી છે, ચારસો રૂપિયા.",
    ),
    "kn-IN": (
        "ಓಟಿಪಿ ಬಂತಾ?",
        "ಲಿಫ್ಟ್ ಬಂದ್ ಆಗಿದೆ.",
        "ಫ್ಲಾಟ್ ನಂಬರ್ ಮುನ್ನೂರ ಎರಡು.",
        "ಹೌದು, ಸರಿ.",
        "ನೀವು ಈಗ ಎಲ್ಲಿದ್ದೀರಾ?",
        "ನನ್ನ ಪಾರ್ಸಲ್ ಗೇಟ್ ಹತ್ರ ಇಡಿ.",
        "ಡಾಕ್ಟರ್ ಇವತ್ತು ಬರ್ತಾರಾ?",
        "ನೀರು ಬರ್ತಿಲ್ಲ, ಪ್ಲಂಬರ್ ಕಳಿಸಿ.",
        "ನಾನು ಐದು ನಿಮಿಷದಲ್ಲಿ ಬರ್ತೀನಿ.",
        "ಕ್ಯಾಶ್ ಆನ್ ಡೆಲಿವರಿ, ನಾನೂರು ರೂಪಾಯಿ.",
    ),
    "ta-IN": (
        "ஓடிபி வந்துச்சா?",
        "லிஃப்ட் வேலை செய்யல.",
        "ஃப்ளாட் நம்பர் முன்னூற்று இரண்டு.",
        "ஆமா, சரி.",
        "நீங்க இப்போ எங்க இருக்கீங்க?",
        "என் பார்சலை கேட்ல வச்சிடுங்க.",
        "டாக்டர் இன்னைக்கு வருவாரா?",
        "தண்ணி வரல, பிளம்பர் அனுப்புங்க.",
        "நான் அஞ்சு நிமிஷத்துல வந்துடுவேன்.",
        "கேஷ் ஆன் டெலிவரி, நானூறு ரூபாய்.",
    ),
    "en-IN": (
        "OTP came?",
        "Lift is not working.",
        "Flat number three zero two.",
        "Yes, okay.",
        "Where are you now?",
        "Please leave the parcel at the gate.",
        "Is the doctor coming today?",
        "No water is coming, send the plumber.",
        "I will reach in five minutes.",
        "Cash on delivery, four hundred rupees.",
    ),
}


# --- pure helpers (unit-tested) --------------------------------------------------------


def pstn(pcm16_16k: bytes) -> bytes:
    """Phone channel: 16k -> 8k -> G.711 mu-law -> 8k -> 16k."""
    down = Resampler(16000, 8000)
    pcm8 = down.process(pcm16_16k) + down.flush()
    pcm8 = ulaw_decode(ulaw_encode(pcm8))
    up = Resampler(8000, 16000)
    return up.process(pcm8) + up.flush()


def window(pcm: bytes, ms: int | None) -> bytes:
    return pcm if ms is None else pcm[: ms_to_bytes(ms)]


def trim_lead(pcm: bytes, *, threshold: float = 328.0, keep_ms: int = 100) -> bytes:
    """Drop leading silence, keeping `keep_ms` before the first 20 ms frame above
    `threshold` RMS (-40 dBFS), like a VAD with pre-roll."""
    x = np.frombuffer(pcm, np.int16).astype(np.float64)
    step = FRAME_BYTES // 2
    for i in range(0, len(x) - step + 1, step):
        if math.sqrt(float(np.mean(x[i : i + step] ** 2))) > threshold:
            start = max(0, i - ms_to_bytes(keep_ms) // 2)
            return pcm[start * 2 :]
    return pcm


LONG_IDX = 100  # idx of joined clips: 100 + pair number


def join_pairs(clips: dict[tuple, bytes], gap_ms: int = 200) -> dict[tuple, bytes]:
    """Long utterances: sentences (0,1), (2,3), ... of one language and voice joined with
    `gap_ms` of silence, keyed (lang, LONG_IDX + pair, voice)."""
    out = {}
    for (lang, i, voice), a in clips.items():
        b = clips.get((lang, i + 1, voice))
        if i % 2 == 0 and i < LONG_IDX and b is not None:
            out[(lang, LONG_IDX + i // 2, voice)] = a + b"\x00" * ms_to_bytes(gap_ms) + b
    return out


def pct(xs: Sequence[int], p: float) -> int | None:
    """Nearest-rank percentile."""
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def confusion(rows: Iterable[tuple[str, str | None]]) -> dict[str, dict[str, int]]:
    """truth -> predicted (or "none") -> count."""
    m: dict[str, dict[str, int]] = {}
    for truth, pred in rows:
        cell = m.setdefault(truth, {})
        key = pred or NONE
        cell[key] = cell.get(key, 0) + 1
    return m


def sweep(
    dets: Sequence[tuple[str, str | None, float]],
    candidates: Sequence[str],
    thresholds: Sequence[float] = THRESHOLDS,
) -> list[dict[str, Any]]:
    """For each threshold: how many detections are accepted (via langid.pick) and how
    many of those are wrong."""
    out = []
    for t in thresholds:
        acc = wrong = 0
        for truth, raw, conf in dets:
            g = pick(raw, conf, candidates, threshold=t)
            if g.lang is not None:
                acc += 1
                wrong += g.lang != truth
        n = len(dets)
        out.append(
            {
                "threshold": t,
                "accepted": acc,
                "wrong": wrong,
                "coverage": acc / n if n else 0.0,
                "error_rate": wrong / acc if acc else 0.0,
                "correct_rate": (acc - wrong) / n if n else 0.0,
            }
        )
    return out


def _count(dets: Sequence[tuple[str, str | None, float]], truth: str, pred: str) -> int:
    return sum(1 for t, p, _ in dets if t == truth and p == pred)


def vote_rows(rows: list[dict[str, Any]], primary: str, second: str) -> list[dict[str, Any]]:
    """SarvamLID(model=primary, second_model=second) on the same clips, replayed offline
    with its own decide(): engine "vote", raw = the decided language (None = rejected),
    ms = the slower of the two parallel calls."""
    from dubhash.providers.sarvam_lid import Detection, decide

    def key(r: dict[str, Any]) -> tuple:
        return (r["lang"], r["idx"], r["voice"], r["cond"], r["window_ms"])

    seconds = {key(r): r for r in rows if r["engine"] == "rest" and r["model"] == second}
    out = []
    for p in rows:
        s = seconds.get(key(p))
        if p["engine"] != "rest" or p["model"] != primary or s is None:
            continue
        g = decide(
            Detection(p.get("raw"), float(p.get("conf") or 0.0), "", 0),
            Detection(s.get("raw"), float(s.get("conf") or 0.0), "", 0),
            SUPPORTED_LANGS,
        )
        ms = [m for m in (p.get("ms"), s.get("ms")) if m is not None]
        out.append(p | {"engine": "vote", "model": f"{primary}+{second}", "raw": g.lang,
                        "conf": g.confidence, "ms": max(ms) if ms else None})  # fmt: skip
    return out


def summarise(
    dets: Sequence[tuple[str, str | None, float]], latencies: Sequence[int]
) -> dict[str, Any]:
    n = len(dets)
    right = sum(1 for t, p, _ in dets if p == t)
    return {
        "n": n,
        "accuracy": right / n if n else 0.0,
        "mr_as_hi": _count(dets, "mr-IN", "hi-IN"),
        "hi_as_mr": _count(dets, "hi-IN", "mr-IN"),
        "gu_as_hi": _count(dets, "gu-IN", "hi-IN"),
        "hi_as_gu": _count(dets, "hi-IN", "gu-IN"),
        "min_conf_correct": min((c for t, p, c in dets if p == t), default=None),
        "max_conf_wrong": max((c for t, p, c in dets if p != t), default=None),
        "latency_ms": {"p50": pct(latencies, 50), "p95": pct(latencies, 95)},
    }


# --- live ------------------------------------------------------------------------------


def _clip_path(lang: str, i: int, voice: str) -> Path:
    return OUT / "clips" / f"{lang}_{i:02d}_{voice}.wav"


async def synth_corpus(key: str, langs: Sequence[str], n: int) -> dict[tuple, bytes]:
    from dubhash.providers.sarvam_tts import BulbulTTS

    (OUT / "clips").mkdir(parents=True, exist_ok=True)
    tts = BulbulTTS(key)
    clips: dict[tuple, bytes] = {}
    try:
        for lang in langs:
            for i, text in enumerate(CORPUS[lang][:n]):
                for voice, tmpl in VOICES.items():
                    path = _clip_path(lang, i, voice)
                    if not path.exists():
                        pcm = await _retry(_synth_one, tts, text, tmpl, lang)
                        write_wav(path, trim_lead(pcm))
                    clips[(lang, i, voice)] = read_wav(path)
    finally:
        await tts.aclose()
    return clips


async def _synth_one(tts: Any, text: str, voice: str, lang: str) -> bytes:
    return b"".join([c async for c in tts.synth(text, voice, lang)])


async def _retry(fn: Any, *args: Any, tries: int = 5) -> Any:
    delay = 1.0
    for attempt in range(tries):
        try:
            return await fn(*args)
        except Exception:
            if attempt == tries - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


async def _detect(lid: Any, pcm: bytes) -> Any:
    from dubhash.providers import sarvam_lid

    try:
        return await lid.detect(pcm)
    except sarvam_lid._Failed as e:  # 429s on the starter tier: retried by _retry
        raise RuntimeError("lid failed") from e


async def run_rest(
    key: str, clips: dict[tuple, bytes], conds: Sequence[str], models: Sequence[str]
) -> list[dict[str, Any]]:
    from dubhash.providers.sarvam_lid import SarvamLID

    rows = []
    for model in models:
        lid = SarvamLID(key, model=model, window_ms=None)
        try:
            await lid.prewarm()
            for (lang, i, voice), clean in clips.items():
                for cond in conds:
                    pcm = clean if cond == "clean" else pstn(clean)
                    for w in WINDOWS:
                        row: dict[str, Any] = {
                            "engine": "rest", "model": model, "lang": lang, "idx": i,
                            "voice": voice, "cond": cond, "window_ms": w,
                            "dur_ms": len(pcm) // 32,
                        }  # fmt: skip
                        try:
                            d = await _retry(_detect, lid, window(pcm, w))
                            row |= {
                                "raw": d.lang, "conf": d.confidence,
                                "transcript": d.transcript, "ms": d.latency_ms,
                            }  # fmt: skip
                        except Exception as e:  # noqa: BLE001
                            row |= {"raw": None, "conf": 0.0, "error": repr(e), "ms": None}
                        rows.append(row)
                        _progress(row)
        finally:
            await lid.aclose()
    return rows


async def run_realtime(
    key: str, clips: dict[tuple, bytes], conds: Sequence[str]
) -> list[dict[str, Any]]:
    from dubhash.providers.sarvam_stt import SarvamSTT
    from dubhash.providers.sarvam_stt_translate import SarvamSTTTranslate

    rows = []
    for name, stt in (
        ("realtime", SarvamSTT(key)),
        ("realtime-translate", SarvamSTTTranslate(key)),
    ):
        try:
            await stt.prewarm("auto")
            for (lang, i, voice), clean in clips.items():
                for cond in conds:
                    pcm = clean if cond == "clean" else pstn(clean)
                    row: dict[str, Any] = {
                        "engine": name, "model": "saaras:v3-realtime", "lang": lang, "idx": i,
                        "voice": voice, "cond": cond, "window_ms": None,
                        "dur_ms": len(pcm) // 32,
                    }  # fmt: skip
                    stream = await stt.open("auto")
                    try:
                        for off in range(0, len(pcm), FRAME_BYTES):
                            await stream.push(
                                pcm[off : off + FRAME_BYTES].ljust(FRAME_BYTES, b"\0")
                            )
                        t0 = time.monotonic()
                        text = await stream.end()
                        row |= {
                            "raw": stream.detected_lang,
                            "conf": stream.detected_confidence or 0.0,
                            "transcript": text,
                            "ms": int((time.monotonic() - t0) * 1000),
                        }
                    finally:
                        await stream.aclose()
                    rows.append(row)
                    _progress(row)
        finally:
            await stt.aclose()
    return rows


async def fixed_lang_check(key: str, pcm: bytes) -> dict[str, Any]:
    """Does a fixed-language socket report a language on its final? (crosstalk gate)"""
    from dubhash.providers.sarvam_stt import SarvamSTT

    stt = SarvamSTT(key)
    try:
        stream = await stt.open("hi-IN")
        for off in range(0, len(pcm), FRAME_BYTES):
            await stream.push(pcm[off : off + FRAME_BYTES].ljust(FRAME_BYTES, b"\0"))
        text = await stream.end()
        out = {"text": text, "detected_lang": stream.detected_lang}
        await stream.aclose()
        return out
    finally:
        await stt.aclose()


async def run_text(key: str, rest_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from dubhash.providers.sarvam_lid import SarvamLID

    lid = SarvamLID(key)
    rows = []
    try:
        for r in rest_rows:
            if r["window_ms"] is not None or not r.get("transcript"):
                continue
            row = {k: r[k] for k in ("model", "lang", "idx", "voice", "cond", "window_ms")}
            row |= {"engine": "text", "speech_raw": r["raw"], "conf": 1.0}
            t0 = time.monotonic()
            try:
                row["raw"] = await _retry(lid.text_lid, r["transcript"])
            except Exception as e:  # noqa: BLE001
                row |= {"raw": None, "error": repr(e)}
            row["ms"] = int((time.monotonic() - t0) * 1000)
            rows.append(row)
            _progress(row)
    finally:
        await lid.aclose()
    return rows


def _progress(row: dict[str, Any]) -> None:
    ok = "ok " if row.get("raw") == row["lang"] else "XX "
    print(
        f"{ok}{row['engine']:<18} {row.get('model', ''):<18} {row['cond']:<5} "
        f"{row['window_ms']!s:>5} {row['lang']} #{row['idx']} {row['voice']:<6} -> "
        f"{row.get('raw')} {row.get('conf', 0):.3f} {row.get('ms')}ms",
        flush=True,
    )


def _groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    g: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        w = "full" if r["window_ms"] is None else f"{r['window_ms'] / 1000:.1f}s"
        kind = "long" if r["idx"] >= LONG_IDX else "short"
        key = f"{r['engine']}|{r.get('model', '')}|{kind}|{r['cond']}|{w}"
        g.setdefault(key, []).append(r)
    return g


def analyse(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for key, rs in _groups(rows).items():
        dets = [(r["lang"], r.get("raw"), float(r.get("conf") or 0.0)) for r in rs]
        lats = [r["ms"] for r in rs if r.get("ms") is not None]
        s = summarise(dets, lats)
        s["confusion"] = confusion((t, p) for t, p, _ in dets)
        s["sweep"] = sweep(dets, SUPPORTED_LANGS)
        s["errors"] = sum(1 for r in rs if r.get("error"))
        out[key] = s
    return out


def _md(analysis: dict[str, Any], extra: dict[str, Any]) -> str:
    langs = list(SUPPORTED_LANGS)
    lines = ["# langid probe", ""]
    lines.append(
        "| group | n | acc | mr→hi | hi→mr | gu→hi | hi→gu "
        "| min conf ok | max conf wrong | p50 ms | p95 ms |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for k, s in analysis.items():
        lines.append(
            f"| {k} | {s['n']} | {s['accuracy']:.3f} | {s['mr_as_hi']} | {s['hi_as_mr']} | "
            f"{s['gu_as_hi']} | {s['hi_as_gu']} | {s['min_conf_correct']} | "
            f"{s['max_conf_wrong']} | {s['latency_ms']['p50']} | {s['latency_ms']['p95']} |"
        )
    for k, s in analysis.items():
        lines += ["", f"## {k}", "", "| truth \\ pred | " + " | ".join([*langs, "other"]) + " |"]
        lines.append("|---" * (len(langs) + 2) + "|")
        for t in langs:
            cell = s["confusion"].get(t, {})
            other = sum(v for p, v in cell.items() if p not in langs)
            lines.append(
                f"| {t} | " + " | ".join(str(cell.get(p, 0)) for p in langs) + f" | {other} |"
            )
        lines += [
            "",
            "| threshold | coverage | wrong accepted | correct overall |",
            "|---|---|---|---|",
        ]
        for sw in s["sweep"]:
            lines.append(
                f"| {sw['threshold']} | {sw['coverage']:.3f} | {sw['wrong']} "
                f"| {sw['correct_rate']:.3f} |"
            )
    lines += ["", "```", json.dumps(extra, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


async def main_async(opts: argparse.Namespace) -> int:
    from dubhash.config.settings import Settings

    key = Settings(_env_file=ROOT / ".env").sarvam_api_key  # type: ignore[call-arg]
    if not key:
        print("SARVAM_API_KEY not set", file=sys.stderr)
        return 2
    clips = await synth_corpus(key, opts.langs, opts.n)
    durs = sorted(len(p) // 32 for p in clips.values())
    extra: dict[str, Any] = {
        "clips": len(clips),
        "clip_ms": {"min": durs[0], "p50": pct(durs, 50), "max": durs[-1]},
        "clips_under_2s": sum(d < 2000 for d in durs),
    }
    if opts.long:
        longs = join_pairs(clips)
        ldurs = sorted(len(p) // 32 for p in longs.values())
        extra["long_clips"] = len(longs)
        extra["long_clip_ms"] = {"min": ldurs[0], "p50": pct(ldurs, 50), "max": ldurs[-1]}
        clips |= longs
    rows = await run_rest(key, clips, opts.conds, opts.models)
    if len(opts.models) >= 2:  # the engine's two-model vote, from the same answers
        rows += vote_rows(rows, opts.models[0], opts.models[1])
    if opts.realtime:
        rows += await run_realtime(key, clips, opts.rt_conds)
        hi = clips.get(("hi-IN", 5, "female"))
        if hi is not None:
            extra["fixed_lang_final"] = await fixed_lang_check(key, pstn(hi))
    if opts.text:
        rows += await run_text(
            key, [r for r in rows if r["engine"] == "rest" and r["cond"] == "pstn"]
        )
    _write(rows, extra, opts.tag)
    return 0


def replay(paths: Sequence[str], models: Sequence[str], tag: str) -> int:
    """Re-analyse saved results files (no vendor calls), adding the two-model vote."""
    rows: list[dict[str, Any]] = []
    extra: dict[str, Any] = {"replayed": list(paths)}
    for p in paths:
        saved = json.loads(Path(p).read_text())
        rows += [r for r in saved["rows"] if r["engine"] != "vote"]
        extra |= {k: v for k, v in saved.get("extra", {}).items() if k not in extra}
    if len(models) >= 2:
        rows += vote_rows(rows, models[0], models[1])
    _write(rows, extra, tag)
    return 0


def _write(rows: list[dict[str, Any]], extra: dict[str, Any], tag: str) -> None:
    analysis = analyse(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"-{tag}" if tag else ""
    res, summ = OUT / f"results{tag}.json", OUT / f"summary{tag}.md"
    res.write_text(
        json.dumps(
            {"extra": extra, "analysis": analysis, "rows": rows}, ensure_ascii=False, indent=1
        )
    )
    md = _md(analysis, extra)
    summ.write_text(md)
    print(md.split("\n## ")[0])
    print(f"wrote {res} and {summ}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--langs", nargs="+", default=list(SUPPORTED_LANGS))
    p.add_argument("--n", type=int, default=10, help="sentences per language (max 10)")
    p.add_argument("--models", nargs="+", default=["saaras:v3"])
    p.add_argument("--conds", nargs="+", default=["clean", "pstn"], choices=["clean", "pstn"])
    p.add_argument("--rt-conds", nargs="+", default=["pstn"], choices=["clean", "pstn"])
    p.add_argument("--no-realtime", dest="realtime", action="store_false")
    p.add_argument("--no-text", dest="text", action="store_false")
    p.add_argument("--no-long", dest="long", action="store_false", help="skip joined pairs")
    p.add_argument("--tag", default="", help="suffix for results/summary file names")
    p.add_argument("--replay", nargs="+", metavar="RESULTS", help="re-analyse saved results")
    opts = p.parse_args(argv)
    if opts.replay:
        return replay(opts.replay, opts.models, opts.tag)
    return asyncio.run(main_async(opts))


if __name__ == "__main__":
    raise SystemExit(main())

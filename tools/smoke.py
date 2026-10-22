"""Live smoke test of the real vendors, using the keys in .env.

    uv run python tools/smoke.py                     # MT, TTS, STT with every key present
    uv run python tools/smoke.py --only mt,tts       # subset
    uv run python tools/smoke.py --eou               # + open question 2 (Sarvam VAD EOU timing)
    uv run python tools/smoke.py --idle 5,30,70      # + open question 4 (idle socket lifetime)

Checks, each skipped with a message when its key is missing:

  sarvam   mt hi→kn #1..3    Mayura on three Hindi sentences; latency; notes an English pivot
  bulbul   tts kn #1, #2     Bulbul on the Kannada MT output; ttfb + total (#2 = warm socket)
  eleven   tts hi #1, #2     ElevenLabs Flash v2.5 on the Hindi source; ttfb + total
  sarvam   stt kn/hi #n      Sarvam realtime STT on each TTS wav; final latency after
                             speech_end, transcript, similarity to the text that was spoken
  deepgram stt hi #n         Nova on the Hindi wavs (DEEPGRAM_API_KEY is expected to be missing)

Wavs go to out/smoke/. Audio is paced in real time for STT unless --fast. Prints a summary
table. Exit 0 if nothing failed, 1 on any failure, 2 if nothing ran (no keys).
Q3 (TTS per language) is the ttfb columns plus a listen to the wavs; Q2 and Q4 need the
opt-in experiments above.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from convox.audio.frames import FRAME_BYTES, FRAME_MS  # noqa: E402
from convox.audio.wav import read_wav, write_wav  # noqa: E402

CHECKS = ("mt", "tts", "stt")
HI_SENTENCES = (
    "मैं गेट नंबर दो के बाहर खड़ा हूँ।",
    "आपका फ्लैट किस ब्लॉक में है और कौन सी मंज़िल पर?",
    "मैं पाँच मिनट में पहुँच जाऊँगा।",
)
# Used for Bulbul when MT did not run or failed, so TTS is still measured.
KN_FALLBACK = (
    "ನಾನು ಗೇಟ್ ನಂಬರ್ ಎರಡರ ಹೊರಗೆ ನಿಂತಿದ್ದೇನೆ.",
    "ನಾನು ಐದು ನಿಮಿಷದಲ್ಲಿ ಬರುತ್ತೇನೆ.",
)
TTS_N = 2
BULBUL_VOICE = "bulbul:kn-female-1"
ELEVEN_VOICE = "eleven:hi-female-1"
DETAIL_MAX = 70
EOU_WAIT_MS = 2000  # silence pushed after speech while waiting for vad.speech_end

Status = Literal["ok", "fail", "skip", "info"]


@dataclass(slots=True)
class Result:
    vendor: str
    check: str
    status: Status
    latency_ms: int | None = None
    ttfb_ms: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Keys:
    sarvam: str
    eleven: str
    deepgram: str


@dataclass(slots=True)
class Factories:
    """Build a provider from an API key. Tests swap in fakes."""

    mt: Callable[..., Any]
    bulbul: Callable[..., Any]
    eleven: Callable[..., Any]
    sarvam_stt: Callable[..., Any]
    deepgram_stt: Callable[..., Any]


def real_factories() -> Factories:
    from convox.providers.deepgram_stt import DeepgramSTT
    from convox.providers.eleven_tts import ElevenTTS
    from convox.providers.sarvam_mt import SarvamMT
    from convox.providers.sarvam_stt import SarvamSTT
    from convox.providers.sarvam_tts import BulbulTTS

    return Factories(
        mt=SarvamMT,
        bulbul=BulbulTTS,
        eleven=ElevenTTS,
        sarvam_stt=SarvamSTT,
        deepgram_stt=DeepgramSTT,
    )


def load_keys(env_file: Path) -> Keys:
    from convox.config.settings import Settings

    s = Settings(_env_file=env_file if Path(env_file).exists() else None)  # type: ignore[call-arg]
    return Keys(sarvam=s.sarvam_api_key, eleven=s.elevenlabs_api_key, deepgram=s.deepgram_api_key)


# --- arguments ------------------------------------------------------------------------


def _checks(value: str) -> set[str]:
    got = {v.strip() for v in value.split(",") if v.strip()}
    bad = got - set(CHECKS)
    if bad or not got:
        raise argparse.ArgumentTypeError(f"choose from {','.join(CHECKS)}; got {value!r}")
    return got


def _seconds(value: str) -> list[int]:
    try:
        out = [int(v) for v in value.split(",") if v.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected seconds like 5,30,70; got {value!r}") from None
    if any(v < 0 for v in out):
        raise argparse.ArgumentTypeError("idle seconds must be >= 0")
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live smoke test of Sarvam, ElevenLabs and Deepgram using .env keys."
    )
    p.add_argument("--only", type=_checks, default=set(CHECKS), help="comma list of mt,tts,stt")
    p.add_argument("--out", type=Path, default=Path("out/smoke"), help="where wavs go")
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    p.add_argument("--fast", action="store_true", help="push STT audio faster than real time")
    p.add_argument("--timeout", type=float, default=20.0, help="seconds per check")
    p.add_argument("--eou", action="store_true", help="Q2: time Sarvam's vad.speech_end")
    p.add_argument(
        "--idle", type=_seconds, default=[], help="Q4: idle gaps in seconds, e.g. 5,30,70"
    )
    p.add_argument("--json", type=Path, default=None, help="also write results as JSON")
    return p.parse_args(argv)


# --- formatting -----------------------------------------------------------------------


def _ms(v: int | None) -> str:
    return "-" if v is None else f"{v} ms"


def format_summary(results: list[Result]) -> str:
    def cut(s: str) -> str:
        s = " ".join(s.split())
        return s if len(s) <= DETAIL_MAX else s[: DETAIL_MAX - 1] + "…"

    rows = [("vendor", "check", "status", "ttfb", "total", "detail")]
    for r in results:
        status = "FAIL" if r.status == "fail" else r.status
        rows.append((r.vendor, r.check, status, _ms(r.ttfb_ms), _ms(r.latency_ms), cut(r.detail)))
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    lines = []
    for row in rows:
        cols = [c.ljust(w) for c, w in zip(row[:5], widths, strict=True)]
        lines.append("  ".join([*cols, row[5]]))
    lines.insert(1, "-" * (sum(widths) + 2 * 5 + len("detail")))
    n = {s: sum(r.status == s for r in results) for s in ("ok", "fail", "skip")}
    lines.append(f"{n['ok']} ok, {n['fail']} failed, {n['skip']} skipped")
    return "\n".join(lines)


def exit_code(results: list[Result]) -> int:
    if any(r.status == "fail" for r in results):
        return 1
    if not any(r.status in ("ok", "info") for r in results):
        return 2
    return 0


# --- checks ---------------------------------------------------------------------------


def _err(e: BaseException) -> str:
    if isinstance(e, TimeoutError):
        return "timeout"
    return f"{type(e).__name__}: {e}"


async def _timed(
    vendor: str, check: str, limit_s: float, fn: Callable[[], Awaitable[Result]]
) -> Result:
    try:
        return await asyncio.wait_for(fn(), limit_s)
    except Exception as e:  # noqa: BLE001 - every failure becomes a row
        return Result(vendor, check, "fail", detail=_err(e))


async def _synth(tts: Any, text: str, voice: str, lang: str) -> tuple[bytes, int, int]:
    t0 = time.monotonic()
    ttfb = None
    chunks = []
    async for c in tts.synth(text, voice, lang):
        if ttfb is None and c:
            ttfb = int((time.monotonic() - t0) * 1000)
        chunks.append(c)
    total = int((time.monotonic() - t0) * 1000)
    return b"".join(chunks), (ttfb if ttfb is not None else total), total


async def _push_audio(stream: Any, pcm: bytes, fast: bool) -> None:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    for i, off in enumerate(range(0, len(pcm), FRAME_BYTES)):
        frame = pcm[off : off + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00")
        await stream.push(frame)
        if not fast:
            delay = t0 + (i + 1) * FRAME_MS / 1000 - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)


def _similarity(a: str, b: str) -> float:
    norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


async def _aclose(*objs: Any) -> None:
    for o in objs:
        close = getattr(o, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001,S110 - best-effort cleanup
                pass


@dataclass(slots=True)
class _Clip:
    path: Path
    lang: str
    tag: str  # "kn #1"
    ref: str  # text that was synthesised, "" if unknown


async def run_smoke(keys: Keys, opts: argparse.Namespace, f: Factories) -> list[Result]:
    out: Path = opts.out
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    rs: list[Result] = []
    t = opts.timeout

    # 1. MT -------------------------------------------------------------------------
    kn_texts: list[str] = []
    if "mt" in opts.only:
        if not keys.sarvam:
            rs.append(Result("sarvam", "mt hi→kn", "skip", detail="SARVAM_API_KEY not set"))
        else:
            mt = f.mt(keys.sarvam)
            for i, text in enumerate(HI_SENTENCES, 1):

                async def one(text: str = text, i: int = i) -> Result:
                    t0 = time.monotonic()
                    got = await mt.translate(text, "hi-IN", "kn-IN")
                    ms = int((time.monotonic() - t0) * 1000)
                    if not got.strip():
                        return Result("sarvam", f"mt hi→kn #{i}", "fail", ms, detail="empty")
                    kn_texts.append(got)
                    return Result("sarvam", f"mt hi→kn #{i}", "ok", ms, detail=got)

                rs.append(await _timed("sarvam", f"mt hi→kn #{i}", t, one))
            if ("hi-IN", "kn-IN") in getattr(mt, "_pivot_pairs", set()):
                rs.append(
                    Result("sarvam", "mt hi→kn route", "info", detail="direct rejected; en pivot")
                )
            await _aclose(mt)

    # 2. TTS ------------------------------------------------------------------------
    clips: list[_Clip] = []
    if "tts" in opts.only:
        kn = (kn_texts + list(KN_FALLBACK))[:TTS_N]
        plans = [
            ("bulbul", keys.sarvam, "SARVAM_API_KEY", f.bulbul, BULBUL_VOICE, "kn-IN", kn),
            ("eleven", keys.eleven, "ELEVENLABS_API_KEY", f.eleven, ELEVEN_VOICE, "hi-IN",
             list(HI_SENTENCES[:TTS_N])),
        ]  # fmt: skip
        for vendor, key, env, factory, voice, lang, texts in plans:
            short = lang.split("-")[0]
            if not key:
                rs.append(Result(vendor, f"tts {short}", "skip", detail=f"{env} not set"))
                continue
            tts = factory(key)
            for i, text in enumerate(texts, 1):
                path = out / f"{vendor}_{short}_{i}.wav"

                async def one(
                    tts: Any = tts, text: str = text, voice: str = voice, lang: str = lang,
                    path: Path = path, vendor: str = vendor, tag: str = f"{short} #{i}",
                ) -> Result:  # fmt: skip
                    pcm, ttfb, total = await _synth(tts, text, voice, lang)
                    if not pcm:
                        return Result(vendor, f"tts {tag}", "fail", total, ttfb, "no audio")
                    write_wav(path, pcm)
                    clips.append(_Clip(path, lang, tag, text))
                    secs = len(pcm) / 32000
                    return Result(vendor, f"tts {tag}", "ok", total, ttfb, f"{secs:.1f}s {path}")

                rs.append(await _timed(vendor, f"tts {short} #{i}", t, one))
            await _aclose(tts)

    # 3. STT ------------------------------------------------------------------------
    if "stt" in opts.only:
        if "tts" not in opts.only:
            clips = _existing_clips(out)
        rs += await _stt_checks(keys, opts, f, clips)

    # 4. opt-in experiments ---------------------------------------------------------
    if keys.sarvam and (opts.eou or opts.idle):
        clip = next(iter(clips or _existing_clips(out)), None)
        if clip is None:
            rs.append(Result("sarvam", "q2/q4", "skip", detail="no TTS audio; run with tts"))
        else:
            if opts.eou:
                rs.append(await _timed("sarvam", "q2 vad eou", t + 5, lambda: _eou(keys, f, clip)))
            for gap in opts.idle:
                for ping in (False, True):
                    name = f"q4 idle {gap}s {'ping' if ping else 'no ping'}"
                    rs.append(
                        await _timed(
                            "sarvam", name, t * 2 + gap,
                            lambda gap=gap, ping=ping, name=name: _idle(
                                keys, f, clip, gap, ping, name, opts.fast
                            ),
                        )
                    )  # fmt: skip
    return rs


def _existing_clips(out: Path) -> list[_Clip]:
    clips = []
    for pattern, lang in (("bulbul_kn_*.wav", "kn-IN"), ("eleven_hi_*.wav", "hi-IN")):
        for p in sorted(out.glob(pattern)):
            clips.append(_Clip(p, lang, f"{lang[:2]} #{p.stem.rsplit('_', 1)[-1]}", ""))
    return clips


async def _stt_checks(
    keys: Keys, opts: argparse.Namespace, f: Factories, clips: list[_Clip]
) -> list[Result]:
    rs: list[Result] = []
    plans = [
        ("sarvam", keys.sarvam, "SARVAM_API_KEY not set", f.sarvam_stt, None),
        ("deepgram", keys.deepgram,
         "DEEPGRAM_API_KEY not set (expected; Sarvam STT covers every leg)", f.deepgram_stt,
         "hi-IN"),
    ]  # fmt: skip
    for vendor, key, why, factory, only_lang in plans:
        if not key:
            rs.append(Result(vendor, "stt", "skip", detail=why))
            continue
        mine = [c for c in clips if only_lang is None or c.lang == only_lang]
        if not mine:
            rs.append(Result(vendor, "stt", "skip", detail="no TTS audio to transcribe"))
            continue
        stt = factory(key)
        for clip in mine:

            async def one(stt: Any = stt, clip: _Clip = clip, vendor: str = vendor) -> Result:
                pcm = read_wav(clip.path)
                stream = await stt.open(clip.lang)
                try:
                    t_open = time.monotonic()
                    await _push_audio(stream, pcm, opts.fast)
                    t_end = time.monotonic()
                    text = await stream.end()
                    t_final = time.monotonic()
                finally:
                    await stream.aclose()
                final_ms = int((t_final - t_end) * 1000)
                total_ms = int((t_final - t_open) * 1000)
                name = f"stt {clip.tag}"
                if not text.strip():
                    return Result(vendor, name, "fail", total_ms, final_ms, "empty transcript")
                sim = f" (sim {_similarity(text, clip.ref):.2f})" if clip.ref else ""
                return Result(vendor, name, "ok", total_ms, final_ms, f"{text}{sim}")

            rs.append(await _timed(vendor, f"stt {clip.tag}", opts.timeout, one))
        await _aclose(stt)
    return rs


async def _eou(keys: Keys, f: Factories, clip: _Clip) -> Result:
    """Q2: push the clip then real-time silence with Sarvam endpointing=vad; report how long
    after the last speech frame vad.speech_end (eou_signal) fired."""
    stt = f.sarvam_stt(keys.sarvam, endpointing="vad")
    try:
        stream = await stt.open(clip.lang)
        try:
            await _push_audio(stream, read_wav(clip.path), fast=False)
            t_speech_end = time.monotonic()
            silence = b"\x00" * FRAME_BYTES
            signal: asyncio.Event | None = getattr(stream, "eou_signal", None)
            for _ in range(EOU_WAIT_MS // FRAME_MS):
                if signal is not None and signal.is_set():
                    break
                await _push_audio(stream, silence, fast=False)
            fired = signal is not None and signal.is_set()
            ms = int((time.monotonic() - t_speech_end) * 1000)
            text = await stream.end()
        finally:
            await stream.aclose()
    finally:
        await _aclose(stt)
    if not fired:
        return Result(
            "sarvam", "q2 vad eou", "info", detail=f"no vad.speech_end within {EOU_WAIT_MS} ms"
        )
    return Result("sarvam", "q2 vad eou", "info", ms, detail=f"speech_end after {ms} ms; {text}")


def idle_socket_alive(stt: Any) -> bool | None:
    """Whether the STT's warm pool still holds a live idle socket (None if it has no pool).
    Reads the pool's private state on purpose: this is a diagnostic."""
    pool = getattr(stt, "_idle", None)
    if pool is None:
        return None
    return any(not c.dead.is_set() for conns in pool.values() for c in conns)


async def _idle(
    keys: Keys, f: Factories, clip: _Clip, gap: int, ping: bool, name: str, fast: bool
) -> Result:
    """Q4: one utterance, then `gap` s idle (pinged or not), then: is the socket alive?"""
    stt = f.sarvam_stt(keys.sarvam, keepalive_s=5.0 if ping else 1e9)
    try:
        pcm = read_wav(clip.path)
        stream = await stt.open(clip.lang)
        await _push_audio(stream, pcm, fast)
        await stream.end()
        await asyncio.sleep(gap)
        alive = idle_socket_alive(stt)
        stream = await stt.open(clip.lang)
        await _push_audio(stream, pcm, fast)
        text = await stream.end()
        await stream.aclose()
    finally:
        await _aclose(stt)
    state = {True: "socket alive", False: "socket closed by server", None: "unknown"}[alive]
    return Result("sarvam", name, "info", detail=f"{state}; reuse ok: {bool(text.strip())}")


# --- entry point ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    keys = load_keys(opts.env_file)
    missing = [n for n, v in (("SARVAM", keys.sarvam), ("ELEVENLABS", keys.eleven)) if not v]
    if missing:
        print(f"note: {', '.join(m + '_API_KEY' for m in missing)} not set; those checks skip")
    results = asyncio.run(run_smoke(keys, opts, real_factories()))
    print(format_summary(results))
    if opts.json is not None:
        opts.json.parent.mkdir(parents=True, exist_ok=True)
        opts.json.write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2) + "\n"
        )
    code = exit_code(results)
    if code == 2:
        print("nothing ran: add SARVAM_API_KEY / ELEVENLABS_API_KEY to .env")
    return code


if __name__ == "__main__":
    sys.exit(main())

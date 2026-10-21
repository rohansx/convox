"""Offline pipeline bench: two FakeLegs play wav files (or synthetic speech) through a real
CallSession (floor, mixer, interpreters) with fake or real providers, then write what each
side heard and a JSON report with per-stage p50/p95 and the turn log.

    uv run python -m tools.bench                       # all fakes, synthetic audio, fast
    uv run python -m tools.bench --a-wav a.wav --b-wav b.wav --stt real --mt real --tts real
    uv run python -m tools.bench --no-spec --fake-mt-ms 1200 --fake-stt-final-ms 290 \
        --fake-tts-ttfb-ms 250                         # fakes with the live latency budget

--spec/--no-spec overrides the pair's spec_mt (speculative MT during the EOU wait); the
report's "spec" block gives the hit rate. --early-final-ms overrides the pair's
early_final_ms (finalise STT after that much silence, before EOU); the "early_final" block
counts early finals started, used and restarted (speech resumed). --fake-*-ms delay the
fake providers on the bench clock. The fake STT counts only voiced audio, so its last
partial equals its final (a fake speculation always hits; only a live run measures the
real hit rate).

With only fakes the bench runs faster than real time on a FakeClock (lockstep, one 20 ms
frame per step on both legs). With any real provider it runs at real time on the wall
clock, since vendor latency is what is being measured. Real providers read keys from .env.

Writes <out>/<call>_a.wav (what A heard), <out>/<call>_b.wav and <out>/<call>.json.
Source is "bench": numbers here are not PSTN numbers (docs/engineering/latency-engineering.md).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from dubhash.audio.frames import FRAME_BYTES, FRAME_MS, SAMPLE_RATE
from dubhash.audio.wav import read_wav, write_wav
from dubhash.clock import Clock, FakeClock, MonotonicClock
from dubhash.config.pairs import PairConfig, load_pairs
from dubhash.config.settings import Settings
from dubhash.events import EventBus
from dubhash.interp.mt_cache import CachedMT
from dubhash.legs.fake import FakeLeg
from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from dubhash.session.call_session import CallSession
from dubhash.session.providers import Providers, build_providers
from dubhash.session.registry import new_call_id
from dubhash.store.writer import percentile
from dubhash.types import Side, Turn

SILENT_FRAME = b"\x00" * FRAME_BYTES

# stage name -> (end field, start field), as differences of turn timestamps
STAGES: dict[str, tuple[str, str]] = {
    "eou": ("t_eou", "t_speech_end"),
    "stt_final": ("t_stt_final", "t_eou"),
    "mt": ("t_mt", "t_stt_final"),
    "tts_ttfb": ("t_tts_ttfb", "t_mt"),
    "first_audio": ("t_play_start", "t_speech_end"),
}


# --- lockstep driver (also used by tests/test_e2e_fake.py) ---------------------------------


def _split(pcm: bytes) -> list[bytes]:
    out = [pcm[i : i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]
    if out and len(out[-1]) < FRAME_BYTES:
        out[-1] = out[-1].ljust(FRAME_BYTES, b"\x00")
    return out


async def _settle(rounds: int) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


async def drive_lockstep(
    session: CallSession,
    legs: dict[Side, FakeLeg],
    clock: FakeClock,
    inbound: dict[Side, bytes],
    *,
    on_frame: Callable[[int], None] | None = None,
    settle_rounds: int = 20,
    idle_frames: int = 5,
    max_tail_ms: int = 30_000,
) -> None:
    """Feed both legs one frame per step on a shared FakeClock: advance 20 ms, feed a frame
    to each leg, fire due marks, let the loop settle, tick the floor. After the inbound
    audio runs out, keep feeding silence until the session is idle (or max_tail_ms), then
    end both legs' input, which ends the call. Legs must be FakeLegs on `clock`,
    already attached to `session`."""
    framed = {s: _split(inbound.get(s, b"")) for s in legs}
    n = max((len(f) for f in framed.values()), default=0)
    i = quiet = 0
    while True:
        clock.advance(FRAME_MS)
        for s, leg in legs.items():
            f = framed[s]
            leg.feed(f[i] if i < len(f) else SILENT_FRAME)
        for leg in legs.values():
            leg.poll()
        await _settle(settle_rounds)
        if on_frame is not None:
            on_frame(i)
        await session.tick()
        await _settle(settle_rounds)
        i += 1
        if i >= n:
            quiet = quiet + 1 if session.idle else 0
            if quiet >= idle_frames or (i - n) * FRAME_MS >= max_tail_ms:
                break
    for leg in legs.values():
        leg.end_input()


# --- synthetic audio -----------------------------------------------------------------------


def _speech(ms: int, f0: float) -> bytes:
    n = SAMPLE_RATE * ms // 1000
    t = np.arange(n) / SAMPLE_RATE
    x = sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, 9))
    x = x / (np.max(np.abs(x)) or 1.0)
    env = 0.65 + 0.35 * np.sin(2 * np.pi * 5 * t)
    fade = min(SAMPLE_RATE // 50, n // 2)
    ramp = np.linspace(0.0, 1.0, fade)
    env[:fade] *= ramp
    env[n - fade :] *= ramp[::-1]
    return (x * env * 8000).astype(np.int16).tobytes()


def _silence(ms: int) -> bytes:
    return b"\x00\x00" * (SAMPLE_RATE * ms // 1000)


def synthetic_inbound() -> dict[Side, bytes]:
    """A asks, B answers, A follows up: three translated turns."""
    a = _silence(500) + _speech(1500, 140) + _silence(5000) + _speech(1200, 140) + _silence(4000)
    b = _silence(4500) + _speech(1400, 210) + _silence(6300)
    return {"a": a, "b": b}


# --- report --------------------------------------------------------------------------------


def stage_stats(turns: Sequence[Turn]) -> dict[str, dict[str, int | None]]:
    out: dict[str, dict[str, int | None]] = {}
    spoken = [t for t in turns if not t.backchannel]
    for name, (end, start) in STAGES.items():
        vals = [
            getattr(t, end) - getattr(t, start)
            for t in spoken
            if getattr(t, end) is not None and getattr(t, start) is not None
        ]
        out[name] = {"n": len(vals), "p50": percentile(vals, 50), "p95": percentile(vals, 95)}
    return out


# --- run -----------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m tools.bench", description=__doc__.split("\n")[0])
    p.add_argument("--a-wav", type=Path, help="side A's inbound audio (default: synthetic)")
    p.add_argument("--b-wav", type=Path, help="side B's inbound audio (default: synthetic)")
    p.add_argument("--pair", default="hi-kn", help="pair key from config/pairs.yaml")
    for stage in ("stt", "mt", "tts"):
        p.add_argument(f"--{stage}", choices=("fake", "real"), default="fake")
    p.add_argument(
        "--vad",
        choices=("auto", "energy"),
        default="auto",
        help="auto: Silero if it loads (EnergyVAD for synthetic audio)",
    )
    p.add_argument("--out", type=Path, default=Path("out"))
    p.add_argument("--call-id", default=None)
    p.add_argument("--tail-ms", type=int, default=5000, help="silence after the audio (real time)")
    p.add_argument("--no-opening", action="store_true", help="skip the opening line")
    p.add_argument(
        "--spec",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="speculative MT on STT partials during the EOU wait (default: the pair's spec_mt)",
    )
    p.add_argument(
        "--spec-after-ms", type=int, default=None, help="override the pair's spec_after_ms"
    )
    p.add_argument(
        "--early-final-ms",
        type=int,
        default=None,
        help="override the pair's early_final_ms (finalise STT after this silence; 0 = off)",
    )
    p.add_argument("--fake-mt-ms", type=int, default=0, help="fake MT delay")
    p.add_argument("--fake-stt-final-ms", type=int, default=0, help="fake STT final delay")
    p.add_argument("--fake-tts-ttfb-ms", type=int, default=0, help="fake TTS first-byte delay")
    return p.parse_args(argv)


def _use_fakes(providers: Providers, args: argparse.Namespace, clock: Clock) -> None:
    """Swap in fakes that sleep on the bench clock with the requested delays."""
    if args.stt == "fake":
        providers.stt = {
            s: FakeSTT(final_delay_ms=args.fake_stt_final_ms, ignore_silence=True, clock=clock)
            for s in ("a", "b")
        }
    if args.mt == "fake":
        providers.mt = CachedMT(FakeMT(delay_ms=args.fake_mt_ms, clock=clock))
    if args.tts == "fake":
        providers.tts = {s: FakeTTS(ttfb_ms=args.fake_tts_ttfb_ms, clock=clock) for s in ("a", "b")}


def spec_stats(session: CallSession, pair: PairConfig) -> dict[str, Any]:
    interps = list(session.interpreters.values())
    out: dict[str, Any] = {"enabled": pair.spec_mt, "after_ms": pair.spec_after_ms}
    for k in ("eligible", "started", "cancelled", "hits", "used"):
        out[k] = sum(getattr(i, f"spec_{k}") for i in interps)
    out["hit_rate"] = round(out["hits"] / out["eligible"], 3) if out["eligible"] else None
    return out


def early_stats(session: CallSession, pair: PairConfig) -> dict[str, Any]:
    interps = list(session.interpreters.values())
    out: dict[str, Any] = {"early_final_ms": pair.early_final_ms}
    for k in ("started", "restarts", "used"):
        out[k] = sum(getattr(i, f"early_{k}") for i in interps)
    out["restart_rate"] = round(out["restarts"] / out["started"], 3) if out["started"] else None
    return out


async def run_bench(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings(stt_impl=args.stt, mt_impl=args.mt, tts_impl=args.tts)
    pairs = load_pairs(settings.pairs_file)
    if args.pair not in pairs:
        raise SystemExit(f"unknown pair {args.pair!r}; choose from {sorted(pairs)}")
    pair = pairs[args.pair]
    if args.spec is not None:
        pair = dataclasses.replace(pair, spec_mt=args.spec)
    if args.spec_after_ms is not None:
        pair = dataclasses.replace(pair, spec_after_ms=args.spec_after_ms)
    if args.early_final_ms is not None:
        pair = dataclasses.replace(pair, early_final_ms=args.early_final_ms)
    providers = build_providers(pair, settings)

    inbound = synthetic_inbound()
    if args.a_wav:
        inbound["a"] = read_wav(args.a_wav)
    if args.b_wav:
        inbound["b"] = read_wav(args.b_wav)
    synthetic = not (args.a_wav or args.b_wav)
    if args.vad == "energy" or (args.vad == "auto" and synthetic):
        providers.vad_factory = EnergyVAD  # Silero is not trained on synthetic harmonics

    realtime = "real" in (args.stt, args.mt, args.tts)
    call_id = args.call_id or new_call_id()
    clock: Clock = MonotonicClock() if realtime else FakeClock()
    _use_fakes(providers, args, clock)
    legs: dict[Side, FakeLeg] = {
        s: FakeLeg(s, clock=clock, realtime=realtime, playout=True) for s in ("a", "b")
    }
    session = CallSession(
        call_id,
        pair,
        providers,
        bus=EventBus(),
        clock=clock,
        opening_line=not args.no_opening,
    )
    await session.attach_leg("a", legs["a"])
    await session.attach_leg("b", legs["b"])
    if realtime:
        longest = max(len(v) for v in inbound.values())
        for s, leg in legs.items():
            leg.feed(inbound[s].ljust(longest, b"\x00") + _silence(args.tail_ms))
            leg.end_input()
    else:
        assert isinstance(clock, FakeClock)
        await drive_lockstep(session, legs, clock, inbound)
    await session.wait_closed()

    args.out.mkdir(parents=True, exist_ok=True)
    write_wav(args.out / f"{call_id}_a.wav", legs["a"].outbound)
    write_wav(args.out / f"{call_id}_b.wav", legs["b"].outbound)
    turns = session.turn_log.turns
    report: dict[str, Any] = {
        "call_id": call_id,
        "pair": pair.key,
        "source": "bench",
        "clock": "realtime" if realtime else "lockstep",
        "providers": {
            "stt": {s: providers.stt[s].name for s in ("a", "b")},
            "mt": providers.mt.name,
            "tts": {s: providers.tts[s].name for s in ("a", "b")},
        },
        "stages": stage_stats(turns),
        "spec": spec_stats(session, pair),
        "early_final": early_stats(session, pair),
        "summary": session.summary(),
        "turns": [dataclasses.asdict(t) for t in turns],
        "audio_s": {s: round(len(inbound[s]) / (2 * SAMPLE_RATE), 2) for s in ("a", "b")},
    }
    (args.out / f"{call_id}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def _fmt(v: int | None) -> str:
    return "-" if v is None else str(v)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(run_bench(args))
    print(f"call {report['call_id']} ({report['clock']}, pair {report['pair']})")
    for name, st in report["stages"].items():
        print(f"  {name:<12} n={st['n']:<3} p50={_fmt(st['p50'])} ms  p95={_fmt(st['p95'])} ms")
    sp = report["spec"]
    if sp["enabled"]:
        print(
            f"  spec         after={sp['after_ms']}ms hits={sp['hits']}/{sp['eligible']} "
            f"used={sp['used']} "
            f"started={sp['started']} cancelled={sp['cancelled']}"
        )
    else:
        print("  spec         off")
    ef = report["early_final"]
    if ef["early_final_ms"] > 0:
        print(
            f"  early_final  after={ef['early_final_ms']}ms started={ef['started']} "
            f"used={ef['used']} restarts={ef['restarts']} (rate {ef['restart_rate']})"
        )
    else:
        print("  early_final  off")
    print(f"  turns={report['summary']['turns']}  out={args.out}/{report['call_id']}_*.wav,.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

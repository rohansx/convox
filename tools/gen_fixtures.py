"""Generate deterministic synthetic test wavs into tests/fixtures/.

Each fixture is speech-like audio: voiced harmonic bursts (a gliding pitch contour with a
syllable-rate envelope) separated by silence, at the native rate (8 kHz phone quality or
16 kHz internal), with an optional white-noise floor. A manifest (synth_manifest.json)
lists every speech/silence segment with its start/end ms, so tests can use the files as
ground truth for VAD and endpointing.

    uv run python tools/gen_fixtures.py            # (re)write tests/fixtures/
    uv run python tools/gen_fixtures.py --check    # exit 1 if committed files drifted

Output is a pure function of SPECS (seeded RNG, no clock), so re-running is a no-op.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dubhash.audio.resample import Resampler  # noqa: E402

DEFAULT_OUT = ROOT / "tests" / "fixtures"
MANIFEST = "synth_manifest.json"

# Two utterances with a short backchannel-length burst between them.
_SCRIPT: tuple[tuple[str, int], ...] = (
    ("silence", 300),
    ("speech", 1200),
    ("silence", 700),
    ("speech", 400),
    ("silence", 700),
    ("speech", 800),
    ("silence", 300),
)


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    name: str
    rate: int
    f0: float  # mean pitch in Hz (male-ish ~120-150, female-ish ~200-240)
    noise: int = 0  # peak of the uniform white-noise floor, int16 units; 0 = digital silence
    amp: int = 9000
    seed: int = 0
    segments: tuple[tuple[str, int], ...] = _SCRIPT


SPECS: tuple[FixtureSpec, ...] = (
    FixtureSpec("synth_16k.wav", 16_000, f0=140.0, seed=1),
    FixtureSpec("synth_8k.wav", 8_000, f0=210.0, seed=2),
    FixtureSpec("synth_8k_noisy.wav", 8_000, f0=140.0, noise=600, seed=3),
)


def _n(ms: int, rate: int) -> int:
    return rate * ms // 1000


def _voiced(n: int, rate: int, f0: float, rng: np.random.Generator) -> np.ndarray:
    """A float burst in [-1, 1]: harmonics of a gliding f0, syllable envelope, 20 ms fades."""
    if n == 0:
        return np.zeros(0)
    t = np.arange(n) / rate
    # pitch glides +-15% over the burst with a little vibrato: not a steady tone
    glide = 1.0 + 0.15 * np.sin(2 * np.pi * t / max(t[-1], 1e-3) * 0.5 + rng.uniform(0, np.pi))
    inst_f0 = f0 * glide * (1.0 + 0.01 * np.sin(2 * np.pi * 5.0 * t))
    phase = 2 * np.pi * np.cumsum(inst_f0) / rate
    x = np.zeros(n)
    for k in range(1, 30):
        if k * f0 * 1.2 >= rate * 0.45:
            break
        x += np.sin(k * phase) / k
    x /= np.max(np.abs(x))
    syll_hz = rng.uniform(4.0, 5.5)
    env = 0.6 + 0.4 * np.sin(2 * np.pi * syll_hz * t) ** 2
    fade = min(_n(20, rate), n // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade)
        env[:fade] *= ramp
        env[n - fade :] *= ramp[::-1]
    return x * env


def render(spec: FixtureSpec) -> np.ndarray:
    """The fixture's samples as int16. Deterministic for a given spec."""
    rng = np.random.default_rng(spec.seed)
    parts = []
    for kind, ms in spec.segments:
        n = _n(ms, spec.rate)
        if kind == "speech":
            parts.append(_voiced(n, spec.rate, spec.f0, rng) * spec.amp)
        elif kind == "silence":
            parts.append(np.zeros(n))
        else:
            raise ValueError(f"unknown segment kind {kind!r}")
    x = np.concatenate(parts) if parts else np.zeros(0)
    if spec.noise > 0:
        x = x + rng.integers(-spec.noise, spec.noise + 1, len(x))
    return np.clip(np.rint(x), -32767, 32767).astype(np.int16)


def segment_bounds(spec: FixtureSpec) -> list[tuple[str, int, int]]:
    """[(kind, start_ms, end_ms), ...] for the spec's script."""
    out, t = [], 0
    for kind, ms in spec.segments:
        out.append((kind, t, t + ms))
        t += ms
    return out


def to_16k(x: np.ndarray, rate: int) -> np.ndarray:
    """Resample int16 samples to 16 kHz with the app's soxr resampler."""
    if rate == 16_000:
        return x
    r = Resampler(rate, 16_000)
    pcm = r.process(x.tobytes()) + r.flush()
    return np.frombuffer(pcm, dtype=np.int16)


def _write_wav(path: Path, x: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.astype("<i2").tobytes())


def _manifest() -> list[dict]:
    return [
        {
            "name": s.name,
            "rate": s.rate,
            "f0": s.f0,
            "noise": s.noise,
            "segments": [{"kind": k, "start_ms": a, "end_ms": b} for k, a, b in segment_bounds(s)],
        }
        for s in SPECS
    ]


def write_all(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in SPECS:
        path = out / spec.name
        _write_wav(path, render(spec), spec.rate)
        paths.append(path)
    man = out / MANIFEST
    man.write_text(json.dumps(_manifest(), indent=2) + "\n")
    paths.append(man)
    return paths


def check(out: Path) -> list[str]:
    """Names of fixtures that are missing or differ from the generator by more than 1 LSB."""
    bad = []
    for spec in SPECS:
        path = out / spec.name
        try:
            with wave.open(str(path), "rb") as w:
                rate = w.getframerate()
                x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        except (OSError, wave.Error, EOFError):
            bad.append(spec.name)
            continue
        want = render(spec)
        if rate != spec.rate or len(x) != len(want):
            bad.append(spec.name)
        elif int(np.max(np.abs(x.astype(np.int32) - want.astype(np.int32)))) > 1:
            bad.append(spec.name)
    return bad


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output dir (tests/fixtures)")
    p.add_argument("--check", action="store_true", help="verify instead of writing")
    args = p.parse_args(argv)
    if args.check:
        bad = check(args.out)
        for name in bad:
            print(f"drifted or missing: {args.out / name}")
        print("fixtures ok" if not bad else "run: uv run python tools/gen_fixtures.py")
        return 1 if bad else 0
    for path in write_all(args.out):
        print(f"wrote {path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

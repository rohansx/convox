"""Record phone-quality utterances for the bench corpus (30 per language) from the mic.

    uv run --with sounddevice python tools/record_fixtures.py prompts/hi.txt --lang hi
    uv run --with sounddevice python tools/record_fixtures.py prompts.csv --out out/corpus

Prompts in:
  .txt  one reference sentence per line (blank lines and "# ..." skipped); needs --lang;
        ids are <lang>_001, <lang>_002, ...
  .csv  header id,lang,text (or reference_text); --lang filters to one language.

Out (default out/corpus/, which is gitignored; the corpus is not committed until the
team decides git vs bucket, see docs/engineering/evaluation-and-metrics.md):
  <out>/<lang>/<id>.wav   16-bit mono, 8 kHz by default (phone quality; --rate 16000 ok)
  <out>/manifest.csv      id, lang, reference_text, path (path relative to <out>)

Per prompt: Enter starts, Enter stops; then Enter keeps, r redoes, q quits. s skips.
Re-running resumes: prompts already in the manifest with a wav on disk are skipped.

`sounddevice` is optional and imported lazily (it needs PortAudio). Without it the tool
prints `sox` commands to record the same files by hand; --manifest-only then writes the
manifest. Everyone recorded must consent in writing; no customer audio.
"""

from __future__ import annotations

import argparse
import csv
import sys
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

MANIFEST = "manifest.csv"
FIELDS = ("id", "lang", "reference_text", "path")
QUIET_PEAK = 1000  # int16 peak below this: probably too far from the mic
CLIP_PEAK = 32000


@dataclass(frozen=True, slots=True)
class Prompt:
    id: str
    lang: str
    text: str


def load_prompts(path: Path, lang: str | None) -> list[Prompt]:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        prompts = [Prompt(r["id"].strip(), r["lang"].strip(), _text(r).strip()) for r in rows]
        if lang:
            prompts = [p for p in prompts if p.lang == lang]
    else:
        if not lang:
            raise ValueError("a .txt prompt list needs --lang")
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
        texts = [ln for ln in lines if ln and not ln.startswith("#")]
        prompts = [Prompt(f"{lang}_{i:03d}", lang, t) for i, t in enumerate(texts, 1)]
    seen: set[str] = set()
    for p in prompts:
        if p.id in seen:
            raise ValueError(f"duplicate prompt id {p.id!r}")
        seen.add(p.id)
    return prompts


def _text(row: dict[str, str]) -> str:
    return row.get("text") or row.get("reference_text") or ""


def rel_path(p: Prompt) -> str:
    return f"{p.lang}/{p.id}.wav"


def read_manifest(out: Path) -> list[dict[str, str]]:
    path = out / MANIFEST
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def append_manifest(out: Path, p: Prompt) -> None:
    path = out / MANIFEST
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({"id": p.id, "lang": p.lang, "reference_text": p.text, "path": rel_path(p)})


def write_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def level_warning(pcm: bytes) -> str | None:
    if len(pcm) < 2:
        return "empty recording"
    peak = int(np.max(np.abs(np.frombuffer(pcm[: len(pcm) // 2 * 2], np.int16).astype(np.int32))))
    if peak < QUIET_PEAK:
        return f"very quiet (peak {peak}); move closer to the mic"
    if peak >= CLIP_PEAK:
        return "clipping; move back or lower the input gain"
    return None


def run_session(
    prompts: list[Prompt],
    out: Path,
    rate: int,
    ask: Callable[[str], str],
    record: Callable[[], bytes],
    say: Callable[..., None] = print,
) -> int:
    """Record each prompt not yet in the manifest. Returns how many were kept."""
    out.mkdir(parents=True, exist_ok=True)
    done = {r["id"] for r in read_manifest(out) if (out / r["path"]).exists()}
    todo = [p for p in prompts if p.id not in done]
    say(f"{len(done)} already recorded, {len(todo)} to go -> {out}")
    kept = 0
    for n, p in enumerate(todo, 1):
        say(f"\n[{n}/{len(todo)}] {p.id} ({p.lang})\n    {p.text}")
        while True:
            a = ask("Enter = record, s = skip, q = quit: ").strip().lower()
            if a == "q":
                return kept
            if a == "s":
                break
            pcm = record()
            warning = level_warning(pcm)
            say(f"    {len(pcm) / (2 * rate):.1f} s" + (f"  ! {warning}" if warning else ""))
            a = ask("Enter = keep, r = redo, q = quit: ").strip().lower()
            if a == "q":
                return kept
            if a == "r":
                continue
            write_wav(out / rel_path(p), pcm, rate)
            append_manifest(out, p)
            kept += 1
            break
    say(f"\nkept {kept}; manifest: {out / MANIFEST}")
    return kept


def _import_sounddevice() -> Any:
    try:
        import sounddevice  # type: ignore[import-not-found]
    except (ImportError, OSError):  # OSError: PortAudio missing
        return None
    return sounddevice


def make_recorder(sd: Any, rate: int, max_s: float, ask: Callable[[str], str]) -> Callable:
    def record() -> bytes:
        chunks: list[bytes] = []

        def cb(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            chunks.append(bytes(indata))

        with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", callback=cb):
            ask("    recording... Enter = stop ")
        return b"".join(chunks)[: int(rate * max_s) * 2]

    return record


def instructions(prompts: list[Prompt], out: Path, rate: int) -> str:
    lines = [
        "sounddevice is not installed (or PortAudio is missing), so the mic can't be used here.",
        "Either run with it:   uv run --with sounddevice python tools/record_fixtures.py ...",
        "(macOS: brew install portaudio; Debian/Ubuntu: apt install libportaudio2)",
        "or record each prompt with sox (Ctrl+C stops), then run with --manifest-only:",
        "",
    ]
    for p in prompts:
        lines.append(f"rec -r {rate} -c 1 -b 16 {out / rel_path(p)}   # {p.id}: {p.text}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record bench-corpus utterances from the mic.")
    ap.add_argument("prompts", type=Path, help=".txt (one per line) or .csv (id,lang,text)")
    ap.add_argument("--lang", default=None, help="language code, e.g. hi (needed for .txt)")
    ap.add_argument("--out", type=Path, default=Path("out/corpus"))
    ap.add_argument("--rate", type=int, default=8000, choices=(8000, 16000))
    ap.add_argument("--max-seconds", type=float, default=10.0)
    ap.add_argument(
        "--manifest-only", action="store_true", help="write manifest.csv for all prompts"
    )
    args = ap.parse_args(argv)

    try:
        prompts = load_prompts(args.prompts, args.lang)
    except ValueError as e:
        ap.error(str(e))
    if args.manifest_only:
        args.out.mkdir(parents=True, exist_ok=True)
        have = {r["id"] for r in read_manifest(args.out)}
        for p in prompts:
            if p.id not in have:
                append_manifest(args.out, p)
        print(f"wrote {args.out / MANIFEST} ({len(prompts)} prompts)")
        return 0
    sd = _import_sounddevice()
    if sd is None:
        print(instructions(prompts, args.out, args.rate))
        return 2
    rec = make_recorder(sd, args.rate, args.max_seconds, input)
    run_session(prompts, args.out, args.rate, ask=input, record=rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())

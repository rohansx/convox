"""Per-turn timeline of a recorded call (CALL_RECORDING, dubhash/store/recorder.py).

    uv run python tools/call_report.py c_ab12cd34ef           # finds out/calls/*/c_ab12cd34ef
    uv run python tools/call_report.py out/calls/2026-10-21/c_ab12cd34ef
    uv run python tools/call_report.py c_ab12cd34ef --json    # machine-readable

For each turn: who spoke (a->b means A spoke, B listened), speech start-end, EOU, STT
final, MT done, TTS first byte, play start on the server, the first play start the phone
reported (page clock corrected by the ping offset, when the page sends telemetry), and
the listener leg's mark ack. Times are seconds from call start. Under each row the gaps
between consecutive stages in ms; a gap over its budget is marked with "!".

Then the warning patterns (store/timeline.py): cross_talk (the listener's phone mic made
a turn while the other side was speaking: the phones hear each other), echo (a mic made a
turn while a translation was playing to it), queue_wait (> 500 ms between TTS ready and
playback), opening_hold (that wait was the opening line still playing), long_utterance
(> 8 s: EOU did not trigger), early_final_restart, wrong_language (text in the wrong
script, or Hindi/Marathi marker words of the other language). Last, "where the time went":
the mean ms per stage over spoken turns, plus the client-side playout.

Open the wavs in Audacity (File > Import > Audio, all four): they share one timeline from
call start, so a_in under b_out shows A's speech and what B was played for it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dubhash.store.timeline import STAGES, analyze  # noqa: E402

DEFAULT_DIR = ROOT / "out" / "calls"
# a gap over this many ms is highlighted (rough per-stage budget for ~1.5 s first audio)
BUDGET_MS = {"eou_wait": 700, "stt": 500, "mt": 400, "tts": 600, "queue": 500, "client": 400}
COLS = ("speech", "eou", "stt", "mt", "tts", "play", "client", "ack", "first", "heard")


def find_call(arg: str, base: Path) -> Path:
    p = Path(arg)
    if p.is_dir() and (p / "events.jsonl").exists():
        return p
    hits = sorted(base.glob(f"*/{arg}"), reverse=True)
    if not hits:
        raise SystemExit(f"no recorded call {arg!r} under {base} (is CALL_RECORDING on?)")
    return hits[0]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a line cut by a crash mid-write
    return out


def load(call_dir: Path) -> dict[str, Any]:
    events = read_jsonl(call_dir / "events.jsonl")
    client = read_jsonl(call_dir / "client.jsonl")
    result = analyze(events, client)
    meta = result["meta"]
    end = next(
        (e for e in reversed(events) if e.get("kind") == "call" and "reason" in e["payload"]),
        None,
    )
    duration = max((e.get("ms") or 0 for e in events), default=0)
    return {
        "call_id": meta.get("call_id") or call_dir.name,
        "path": str(call_dir),
        "pair": meta.get("pair"),
        "mode": meta.get("mode"),
        "sides": meta.get("sides"),
        "duration_ms": duration,
        "end_reason": end["payload"].get("reason") if end else None,
        "n_events": len(events),
        "n_client": len(client),
        **result,
    }


def _s(ms: int | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.2f}"


def _gap(name: str, ms: int | None) -> str:
    if ms is None:
        return f"{name} -"
    flag = "!" if ms > BUDGET_MS.get(name, 10**9) else ""
    return f"{name} {ms}{flag}"


def render(doc: dict[str, Any]) -> str:
    out: list[str] = []
    sides = doc.get("sides") or {}
    langs = " / ".join(f"{s}={(sides.get(s) or {}).get('lang', '?')}" for s in ("a", "b"))
    out.append(
        f"call {doc['call_id']}  pair {doc['pair']}  mode {doc['mode']}  {langs}  "
        f"duration {_s(doc['duration_ms'])} s  end {doc['end_reason']}"
    )
    out.append(f"  {doc['path']}")
    clock = doc.get("clock") or {}
    for s in ("a", "b"):
        c = clock.get(s)
        if c:
            out.append(
                f"  page clock {s}: offset {c['offset_ms']} ms, rtt {c['rtt_ms']} ms (n={c['n']})"
            )
        elif doc["n_client"]:
            out.append(f"  page clock {s}: no pong, client times are server receive times")
    out.append("")
    out.append("TIMELINE (s from call start; gaps in ms, ! = over budget)")
    head = f"{'#':>3} {'dir':<4} " + " ".join(
        f"{c:>11}" if c == "speech" else f"{c:>6}" for c in COLS
    )
    out.append(head)
    for r in doc["turns"]:
        speech = f"{_s(r['speech_start'])}-{_s(r['speech_end'])}"
        cells = [
            f"{speech:>11}",
            *(
                f"{_s(r[k]):>6}"
                for k in (
                    "eou",
                    "stt_final",
                    "mt",
                    "tts_ttfb",
                    "play_start",
                    "client_play_start",
                    "mark_ack",
                )
            ),
            f"{r['first_audio_ms'] if r['first_audio_ms'] is not None else '-':>6}",
            f"{r['heard_ms'] if r['heard_ms'] is not None else '-':>6}",
        ]
        kind = " backchannel" if r["backchannel"] else ""
        clip = f" clipped {r['clipped_ms']} ms" if r["clipped_ms"] else ""
        out.append(f"{r['seq']:>3} {r['side']}>{r['listener']:<2} " + " ".join(cells) + kind + clip)
        if not r["backchannel"]:
            out.append("         " + " | ".join(_gap(n, r["gaps"][n]) for n in STAGES))
        if r.get("src_text") or r.get("tgt_text"):
            out.append(
                f"         {(r.get('src_text') or '')[:60]!r} -> {(r.get('tgt_text') or '')[:60]!r}"
            )
    out.append("")
    warnings = doc["warnings"]
    out.append(f"WARNINGS ({len(warnings)})")
    if not warnings:
        out.append("  none")
    for w in warnings:
        who = f"{w.get('side')}#{w.get('seq')}" if w.get("seq") is not None else w.get("side")
        out.append(f"  {w['code']:<20} {who:<6} {w['detail']}")
    out.append("")
    st = doc["stages"]
    fa, heard = doc["first_audio"], doc["heard"]
    out.append("WHERE THE TIME WENT (mean ms per spoken turn)")
    labels = {
        "eou_wait": "speech end -> EOU (VAD wait)",
        "stt": "EOU -> STT final",
        "mt": "STT final -> MT",
        "tts": "MT -> TTS first byte",
        "queue": "TTS ready -> play start (queue/hold)",
        "client": "server play -> phone play (network + buffer)",
    }
    for name in STAGES:
        v = st.get(name)
        out.append(f"  {labels[name]:<46} {'-' if v is None else v:>6}")
    out.append(
        f"  {'first audio, server (speech end -> play start)':<46} {_fmt(st.get('first_audio')):>6}"
        f"   p50 {_fmt(fa['p50'])} p95 {_fmt(fa['p95'])} n={fa['n']}"
    )
    out.append(
        f"  {'heard on the phone (speech end -> phone play)':<46} {_fmt(st.get('heard')):>6}"
        f"   p50 {_fmt(heard['p50'])} p95 {_fmt(heard['p95'])} n={heard['n']}"
    )
    out.append("  (speech end is the end of the speaker's voiced audio as the VAD saw it;")
    out.append("   the speaker's own sense of 'I stopped' adds their trailing silence)")
    return "\n".join(out)


def _fmt(v: int | None) -> str:
    return "-" if v is None else str(v)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("call", help="call id (c_...) or a recorded call directory")
    p.add_argument("--dir", type=Path, default=None, help=f"recordings dir (default {DEFAULT_DIR})")
    p.add_argument("--json", action="store_true", help="print the analysis as JSON")
    a = p.parse_args(argv)
    base = a.dir
    if base is None:
        try:
            from dubhash.config.settings import Settings

            base = Path(Settings(_env_file=ROOT / ".env").call_recording_dir)  # type: ignore[call-arg]
        except Exception:
            base = DEFAULT_DIR
        if not base.is_absolute():
            base = ROOT / base
    doc = load(find_call(a.call, base))
    if a.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        print(render(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

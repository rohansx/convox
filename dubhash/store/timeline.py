"""Timeline of a recorded call (dubhash/store/recorder.py): per-turn rows, the gaps
between stages, warning patterns and "where the time went". Pure functions over the
recorder's events.jsonl and client.jsonl lines; the recorder's summary.json and
tools/call_report.py both use them.

Event lines are {"ts", "ms", "kind", "payload"}: `ms` is call-relative (the session
clock, the same clock as every t_* field). Besides the CallEvent kinds the recorder adds
"rec.meta" (pair, mode, per-side lang and STT output lang), "leg.mark" / "leg.mark_ack"
(a mark sent to a leg / the leg reported it played), "leg.clear", "leg.dtmf" and
"rec.dropped". Client lines are the phone's telemetry plus the server's receive time
`rx_ms` and the `side` (legs/webrtc.py documents the message shape).

Stages, each the difference of two consecutive points, so they add up to what the
listener heard (a negative `stt` means an early final, L3):
  eou_wait  speech_end -> eou          the VAD's end-of-utterance wait
  stt       eou -> stt_final
  mt        stt_final -> mt
  tts       mt -> tts_ttfb
  queue     tts_ttfb -> play_start     floor queue / opening-line hold
  client    play_start -> client play_start (page clock corrected by the ping offset)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from dubhash.store.writer import percentile

STAGES = ("eou_wait", "stt", "mt", "tts", "queue", "client")
_POINTS = ("t_speech_end", "t_eou", "t_stt_final", "t_mt", "t_tts_ttfb", "t_play_start")

QUEUE_WAIT_MS = 500
LONG_UTTERANCE_MS = 8000
OVERLAP_MS = 300  # speech overlapping another side's speech or playback by this much
CLIENT_MATCH_EARLY_MS = 300  # a page play_start this much before the server's still counts
PROMPT_TAGS = frozenset({"opening"})
MIXED_MIN_SHARE = 0.2  # of an Indic text's letters in its own script

# Unicode script per language (BCP-47 primary subtag)
SCRIPT_OF_LANG = {
    "hi": "deva", "mr": "deva", "gu": "gujr", "en": "latn", "kn": "knda", "ta": "taml",
    "te": "telu", "ml": "mlym", "bn": "beng", "pa": "guru", "od": "orya", "or": "orya",
}  # fmt: skip
_SCRIPT_RANGES = (
    ("deva", 0x0900, 0x097F), ("beng", 0x0980, 0x09FF), ("guru", 0x0A00, 0x0A7F),
    ("gujr", 0x0A80, 0x0AFF), ("orya", 0x0B00, 0x0B7F), ("taml", 0x0B80, 0x0BFF),
    ("telu", 0x0C00, 0x0C7F), ("knda", 0x0C80, 0x0CFF), ("mlym", 0x0D00, 0x0D7F),
)  # fmt: skip
# Frequent function words that tell Hindi from Marathi (both Devanagari).
_HI_WORDS = frozenset("है हैं नहीं क्या मुझे मैं आप और के की का में रहा रही हूँ हूं था थी चाहिए को से".split())
_MR_WORDS = frozenset("आहे आहेत नाही काय मला मी तुम्ही आणि च्या ला मध्ये होते हवे पाहिजे आम्ही तुमचा".split())
_WORD = re.compile(r"[^\s.,!?;:।॥\"'()]+")


def utterances(
    turns: Iterable[Mapping[str, Any]], play_key: str = "t_play_start"
) -> list[dict[str, Any]]:
    """What the listener felt, per utterance (L9). An utterance is every played, spoken turn
    of one (side, utterance_id): one turn unchunked, one per chunk with chunked
    interpretation. A turn without utterance_id (recorded before L9) is an utterance alone.

      speech_start_to_first_heard  utterance speech start -> its first translation heard
      ear_voice_span               mean over its chunks of (chunk heard - chunk speech
                                   start), weighted by each chunk's speech span: how far
                                   the listener trails the speaker (approximated per chunk)
      first_audio                  utterance speech end -> its last chunk heard (the wait
                                   after the speaker stops, as before L9)

    `play_key` names the "heard" time in each turn (server t_play_start by default)."""
    groups: dict[tuple[str, Any], list[Mapping[str, Any]]] = {}
    for i, t in enumerate(turns):
        if t.get("backchannel") or t.get(play_key) is None or t.get("t_speech_start") is None:
            continue
        uid = t.get("utterance_id")
        key = (str(t.get("side")), uid if uid is not None else ("turn", i))
        groups.setdefault(key, []).append(t)
    out: list[dict[str, Any]] = []
    for (side, uid), ts in groups.items():
        ts = sorted(ts, key=lambda x: (x.get("chunk") or 0, x["t_speech_start"]))
        start = min(
            [x["t_utt_start"] for x in ts if x.get("t_utt_start") is not None]
            or [x["t_speech_start"] for x in ts]
        )
        first = min(x[play_key] for x in ts)
        last = ts[-1]
        weights = [max(20, (x.get("t_speech_end") or x["t_speech_start"]) - x["t_speech_start"])
                   for x in ts]  # fmt: skip
        lags = [x[play_key] - x["t_speech_start"] for x in ts]
        evs = sum(w * lag for w, lag in zip(weights, lags, strict=True)) / sum(weights)
        end = last.get("t_speech_end")
        out.append(
            {
                "side": side,
                "utterance_id": None if isinstance(uid, tuple) else uid,
                "chunks": len(ts),
                "t_start": start,
                "t_end": end,
                "speech_start_to_first_heard": first - start,
                "ear_voice_span": round(evs),
                "first_audio": last[play_key] - end if end is not None else None,
            }
        )
    out.sort(key=lambda u: u["t_start"])
    return out


HEARD_METRICS = ("speech_start_to_first_heard", "ear_voice_span", "first_audio")


def utterance_metrics(
    turns: Iterable[Mapping[str, Any]], play_key: str = "t_play_start"
) -> dict[str, dict[str, int | None]]:
    """n / p50 / p95 over utterances of each HEARD_METRICS metric."""
    us = utterances(turns, play_key)
    out: dict[str, dict[str, int | None]] = {}
    for k in HEARD_METRICS:
        vals = [u[k] for u in us if u[k] is not None]
        out[k] = {"n": len(vals), "p50": percentile(vals, 50), "p95": percentile(vals, 95)}
    return out


def script_counts(text: str | None) -> dict[str, int]:
    """Letters per script ("deva", "latn", ...) in `text`."""
    counts: dict[str, int] = {}
    for ch in text or "":
        if not ch.isalpha():
            continue
        cp = ord(ch)
        name = "latn" if cp < 0x0250 else None
        for script, lo, hi in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                name = script
                break
        if name is not None:
            counts[name] = counts.get(name, 0) + 1
    return counts


def script_of(text: str | None) -> str | None:
    """The dominant script of `text`, None if it has no letters."""
    counts = script_counts(text)
    return max(counts, key=lambda k: counts[k]) if counts else None


def language_mismatch(text: str | None, lang: str | None) -> str | None:
    """Why `text` does not look like `lang` (None if it does or we cannot tell). Indian
    translations keep English nouns in Latin script ("मेरा parcel कहाँ है?"), so an Indic
    target only needs MIXED_MIN_SHARE of its letters in its own script."""
    if not text or not lang:
        return None
    short = lang.split("-")[0]
    want = SCRIPT_OF_LANG.get(short)
    counts = script_counts(text)
    total = sum(counts.values())
    if want is None or not total:
        return None
    share = counts.get(want, 0) / total
    if share < (0.5 if want == "latn" else MIXED_MIN_SHARE):
        got = max(counts, key=lambda k: counts[k])
        return f"{got} script ({share:.0%} {want}), expected {want} for {lang}"
    if short in ("hi", "mr"):
        words = _WORD.findall(text)
        hi = sum(w in _HI_WORDS for w in words)
        mr = sum(w in _MR_WORDS for w in words)
        if short == "hi" and mr > hi:
            return f"looks Marathi ({mr} vs {hi} marker words), expected {lang}"
        if short == "mr" and hi > mr:
            return f"looks Hindi ({hi} vs {mr} marker words), expected {lang}"
    return None


def clock_offset(client: Iterable[dict[str, Any]], side: str) -> dict[str, int] | None:
    """Page clock minus server clock for `side`, from the pong with the smallest RTT:
    offset = client_ms - (server_ms + rtt / 2). None without a usable pong."""
    best: tuple[int, int] | None = None
    n = 0
    for c in client:
        if c.get("side") != side or c.get("kind") != "pong":
            continue
        try:
            server_ms, client_ms, rx = int(c["server_ms"]), float(c["client_ms"]), int(c["rx_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        rtt = rx - server_ms
        if rtt < 0:
            continue
        n += 1
        if best is None or rtt < best[0]:
            best = (rtt, round(client_ms - (server_ms + rtt / 2)))
    if best is None:
        return None
    return {"offset_ms": best[1], "rtt_ms": best[0], "n": n}


def _other(side: str) -> str:
    return "b" if side == "a" else "a"


def _diff(a: int | None, b: int | None) -> int | None:
    return b - a if a is not None and b is not None else None


def _overlap(a0: int | None, a1: int | None, b0: int | None, b1: int | None) -> int:
    if None in (a0, a1, b0, b1):
        return 0
    return max(0, min(a1, b1) - max(a0, b0))  # type: ignore[type-var,operator]


def _mean(xs: list[int]) -> int | None:
    return round(sum(xs) / len(xs)) if xs else None


def _client_server_ms(c: dict[str, Any], offsets: dict[str, dict[str, int] | None]) -> int | None:
    off = offsets.get(str(c.get("side")))
    if off is not None and c.get("client_ms") is not None:
        try:
            return round(float(c["client_ms"]) - off["offset_ms"])
        except (TypeError, ValueError):
            pass
    rx = c.get("rx_ms")
    return int(rx) if rx is not None else None


def analyze(
    events: list[dict[str, Any]], client: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    client = client or []
    meta: dict[str, Any] = {}
    turns: list[dict[str, Any]] = []
    marks: dict[str, list[dict[str, Any]]] = {"a": [], "b": []}
    acks: dict[str, list[dict[str, Any]]] = {"a": [], "b": []}
    for e in events:
        kind, p, ms = e.get("kind"), e.get("payload") or {}, e.get("ms")
        if kind == "rec.meta":
            meta = p
        elif kind == "turn":
            t = p.get("turn", p)
            if isinstance(t, dict):
                turns.append(t)
        elif kind == "leg.mark" and p.get("side") in marks:
            marks[p["side"]].append({"tag": p.get("tag"), "ms": ms, "used": False})
        elif kind == "leg.mark_ack" and p.get("side") in acks:
            acks[p["side"]].append({"tag": p.get("tag"), "ms": ms})
    sides = meta.get("sides") or {}
    offsets = {s: clock_offset(client, s) for s in ("a", "b")}
    plays = [c for c in client if c.get("kind") == "play_start"]

    turns.sort(key=lambda t: (t.get("t_speech_start") or 0, t.get("seq") or 0))
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    played_to: dict[str, list[tuple[int, int]]] = {"a": [], "b": []}
    for t in turns:
        side = str(t.get("side"))
        if t.get("t_play_start") is not None:
            end = t.get("t_play_end") or t["t_play_start"]
            played_to[_other(side)].append((t["t_play_start"], end))

    # page play_starts, in order per listener, consumed as turns claim them
    client_plays: dict[str, list[dict[str, Any]]] = {"a": [], "b": []}
    for c in plays:
        s = str(c.get("side"))
        if s in client_plays:
            client_plays[s].append({**c, "server": _client_server_ms(c, offsets), "used": False})

    prompt_acks = {
        s: [a["ms"] for a in acks[s] if a["tag"] in PROMPT_TAGS and a["ms"] is not None]
        for s in acks
    }
    for t in turns:
        side = str(t.get("side"))
        listener = _other(side)
        ps = t.get("t_play_start")
        row: dict[str, Any] = {
            "side": side,
            "listener": listener,
            "seq": t.get("seq"),
            "backchannel": bool(t.get("backchannel")),
            "speech_start": t.get("t_speech_start"),
            "speech_end": t.get("t_speech_end"),
            "eou": t.get("t_eou"),
            "stt_final": t.get("t_stt_final"),
            "mt": t.get("t_mt"),
            "tts_ttfb": t.get("t_tts_ttfb"),
            "play_start": ps,
            "play_end": t.get("t_play_end"),
            "clipped_ms": t.get("clipped_ms"),
            # not Turn fields today; read if the interpreter's timings ever carry them
            "early_final_restarts": t.get("early_final_restarts"),
            "utterance_id": t.get("utterance_id"),
            "chunk": t.get("chunk"),
            "tag": None,
            "mark_ack": None,
            "client_play_start": None,
            "src_text": t.get("src_text"),
            "tgt_text": t.get("tgt_text"),
        }
        if ps is not None:
            for m in marks[listener]:
                tag = str(m["tag"] or "")
                if not m["used"] and tag.startswith(f"{side}:") and (m["ms"] or 0) >= ps:
                    m["used"] = True
                    row["tag"] = tag
                    ack = next(
                        (a for a in acks[listener] if a["tag"] == tag and a["ms"] >= m["ms"]),
                        None,
                    )
                    row["mark_ack"] = ack["ms"] if ack else None
                    break
            row["client_play_start"] = _claim_client_play(client_plays[listener], row)
        points = [row[k.removeprefix("t_")] for k in _POINTS] + [row["client_play_start"]]
        row["gaps"] = {name: _diff(points[i], points[i + 1]) for i, name in enumerate(STAGES)}
        row["first_audio_ms"] = _diff(row["speech_end"], ps)
        row["heard_ms"] = _diff(row["speech_end"], row["client_play_start"])
        rows.append(row)
        warnings.extend(_turn_warnings(row, rows, played_to, prompt_acks, sides))

    heard_turns = [
        {**t, "t_client_play": r["client_play_start"]} for t, r in zip(turns, rows, strict=True)
    ]
    per_utt = utterance_metrics(turns)
    spoken = [r for r in rows if not r["backchannel"]]
    fa = [r["first_audio_ms"] for r in spoken if r["first_audio_ms"] is not None]
    heard = [r["heard_ms"] for r in spoken if r["heard_ms"] is not None]
    stages: dict[str, int | None] = {
        s: _mean([r["gaps"][s] for r in spoken if r["gaps"][s] is not None]) for s in STAGES
    }
    stages["first_audio"] = _mean(fa)
    stages["heard"] = _mean(heard)
    return {
        "meta": meta,
        "turns": rows,
        "warnings": warnings,
        "stages": stages,
        "first_audio": {"n": len(fa), "p50": percentile(fa, 50), "p95": percentile(fa, 95)},
        "heard": {"n": len(heard), "p50": percentile(heard, 50), "p95": percentile(heard, 95)},
        # L9, per utterance: server play start, and heard_from_start on the phone's clock
        "speech_start_to_first_heard": per_utt["speech_start_to_first_heard"],
        "ear_voice_span": per_utt["ear_voice_span"],
        "utterance_first_audio": per_utt["first_audio"],
        "heard_from_start": utterance_metrics(heard_turns, "t_client_play")[
            "speech_start_to_first_heard"
        ],
        "clock": offsets,
    }


def _claim_client_play(plays: list[dict[str, Any]], row: dict[str, Any]) -> int | None:
    tag, ps = row["tag"], row["play_start"]
    if tag is not None:
        for c in plays:
            if not c["used"] and c.get("tag") == tag:
                c["used"] = True
                return c["server"]
    for c in plays:
        if c["used"] or c.get("tag") not in (None, tag) or c["server"] is None:
            continue
        if c["server"] >= ps - CLIENT_MATCH_EARLY_MS:
            end = row["mark_ack"] or row["play_end"]
            if end is not None and c["server"] > end:
                return None
            c["used"] = True
            return c["server"]
    return None


def _warn(code: str, row: dict[str, Any], detail: str, **kw: Any) -> dict[str, Any]:
    return {"code": code, "side": row["side"], "seq": row["seq"], "detail": detail, **kw}


def _turn_warnings(
    row: dict[str, Any],
    rows: list[dict[str, Any]],
    played_to: dict[str, list[tuple[int, int]]],
    prompt_acks: dict[str, list[int]],
    sides: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    side, s0, s1 = row["side"], row["speech_start"], row["speech_end"]
    for prev in rows[:-1]:
        if prev["side"] == side or (prev["speech_start"] or 0) > (s0 or 0):
            continue
        ov = _overlap(s0, s1, prev["speech_start"], prev["speech_end"])
        if ov >= OVERLAP_MS:
            out.append(
                _warn(
                    "cross_talk",
                    row,
                    f"{side}'s mic made a turn while {prev['side']} was speaking "
                    f"({ov} ms overlap with {prev['side']}#{prev['seq']}): the other "
                    "person's voice reached this phone",
                    ms_value=ov,
                )
            )
            break
    for p0, p1 in played_to.get(side, []):
        ov = _overlap(s0, s1, p0, p1)
        if ov >= OVERLAP_MS:
            out.append(
                _warn(
                    "echo",
                    row,
                    f"{side}'s mic made a turn while a translation was playing to {side} "
                    f"({ov} ms overlap): the speaker's output reached the mic",
                    ms_value=ov,
                )
            )
            break
    if s0 is not None and s1 is not None and s1 - s0 > LONG_UTTERANCE_MS:
        out.append(
            _warn(
                "long_utterance",
                row,
                f"utterance {s1 - s0} ms > {LONG_UTTERANCE_MS} ms: EOU did not trigger "
                "(noise, music or the other phone's playback kept the VAD open?)",
                ms_value=s1 - s0,
            )
        )
    restarts = row.get("early_final_restarts")
    sf = row["stt_final"]
    if (restarts or 0) > 0 or (sf is not None and s1 is not None and sf < s1):
        out.append(
            _warn(
                "early_final_restart",
                row,
                "early final discarded and STT restarted"
                if restarts
                else f"STT final {s1 - sf} ms before speech ended: an early final was cut "
                "off by resumed speech",
            )
        )
    wait = _diff(row["tts_ttfb"], row["play_start"])
    if wait is not None and wait > QUEUE_WAIT_MS:
        held = any(
            row["tts_ttfb"] < ack <= row["play_start"] + 50
            for ack in prompt_acks.get(row["listener"], [])
        )
        code = "opening_hold" if held else "queue_wait"
        why = (
            "the listener's opening line was still playing"
            if held
            else "the floor was busy (other playback, listener talking, or queue)"
        )
        out.append(
            _warn(code, row, f"audio ready {wait} ms before it played: {why}", ms_value=wait)
        )
    spec = sides.get(side) or {}
    lis = sides.get(row["listener"]) or {}
    why = language_mismatch(row["src_text"], spec.get("stt_lang") or spec.get("lang"))
    if why is None and not row["backchannel"]:
        why = language_mismatch(row["tgt_text"], lis.get("lang"))
        if why is not None:
            why = f"translation {why}"
    elif why is not None:
        why = f"source text {why}"
    if why is not None:
        out.append(_warn("wrong_language", row, why))
    return out

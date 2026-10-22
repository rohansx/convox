"""Place one real interpreted phone call through a running convox app and watch it.

    uv run python tools/call.py 9172522532 mr-IN <your number> hi-IN
    uv run python tools/call.py A_NUMBER A_LANG B_NUMBER B_LANG --base http://127.0.0.1:8000

Both phones ring (leg a first). Each turn prints as it lands: side, what was said, what
the other side heard, and first-audio latency. Uses EXOTEL_EXOPHONE as caller id and the
first PLATFORM_API_KEYS key. On a trial Exotel account both numbers must be verified.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from convox.config.settings import Settings  # noqa: E402

ENDED = {"completed", "failed", "ended", "no_answer", "busy"}


def e164(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(f"not an Indian 10-digit number: {number!r}")
    return "+91" + digits


def platform_key(keys: str) -> str:
    first = keys.split(",")[0]
    return first.split(":", 1)[1] if ":" in first else first


async def place_call(
    client: httpx.AsyncClient,
    *,
    key: str,
    a: tuple[str, str],
    b: tuple[str, str],
    caller_id: str,
    provider: str,
) -> str:
    body = {
        "a": {"number": e164(a[0]), "lang": a[1]},
        "b": {"number": e164(b[0]), "lang": b[1]},
        "caller_id": e164(caller_id),
        "provider": provider,
        "mode": "interpret",
        "record": True,
    }
    r = await client.post("/v1/calls", json=body, headers={"authorization": f"Bearer {key}"})
    if r.status_code != 201:
        raise RuntimeError(f"POST /v1/calls -> {r.status_code}: {r.text[:300]}")
    return r.json()["call_id"]


def _turn_line(t: dict[str, Any]) -> str:
    fa = None
    if t.get("t_speech_end") is not None and t.get("t_play_start") is not None:
        fa = t["t_play_start"] - t["t_speech_end"]
    lat = f"{fa} ms" if fa is not None else "-"
    return (
        f"  [{t.get('side')}] {t.get('src_text') or ''!s}  ->  {t.get('tgt_text') or ''!s}  ({lat})"
    )


async def watch(
    client: httpx.AsyncClient,
    call_id: str,
    *,
    key: str,
    interval_s: float = 1.0,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    headers = {"authorization": f"Bearer {key}"}
    seen: set[int] = set()
    last_status = None
    while True:
        r = await client.get(f"/v1/calls/{call_id}", headers=headers)
        call = r.json() if r.status_code == 200 else {"status": f"http {r.status_code}"}
        if call.get("status") != last_status:
            last_status = call.get("status")
            out(f"status: {last_status}")
        tr = await client.get(f"/v1/calls/{call_id}/turns", headers=headers)
        if tr.status_code == 200:
            for t in tr.json().get("turns", []):
                if t.get("seq") not in seen and not t.get("backchannel"):
                    seen.add(t.get("seq"))
                    out(_turn_line(t))
        if last_status in ENDED or r.status_code == 404:
            return call
        await asyncio.sleep(interval_s)


async def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("a_number")
    ap.add_argument("a_lang")
    ap.add_argument("b_number")
    ap.add_argument("b_lang")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--provider", default="exotel")
    args = ap.parse_args(argv)
    s = Settings()
    key = platform_key(s.platform_api_keys)
    async with httpx.AsyncClient(base_url=args.base, timeout=30) as c:
        call_id = await place_call(
            c,
            key=key,
            a=(args.a_number, args.a_lang),
            b=(args.b_number, args.b_lang),
            caller_id=s.exotel_exophone,
            provider=args.provider,
        )
        print(f"call {call_id}: ringing {args.a_lang} and {args.b_lang}")
        final = await watch(c, call_id, key=key)
    print(
        {k: final.get(k) for k in ("status", "turns", "p50_first_audio_ms", "p95_first_audio_ms")}
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

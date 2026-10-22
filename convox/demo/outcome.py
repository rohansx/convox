"""The live demo's outcome card: what the two people agreed, in each one's language.

One Claude call per call, on demand (POST /demo/{call_id}/outcome), over the speakers' own
words (src_text), never the machine translations, so a translation slip can't become a
"fact". Returns {"a": {"headline", "points"}, "b": {...}}, each in that side's language, or
None when there is nothing to summarise or Claude fails; the card then shows only numbers.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from convox.join.routes import english

log = logging.getLogger(__name__)

SYSTEM = (
    "You summarise a phone call that a live interpreter carried between two people who "
    "don't share a language. Say what they agreed or decided, plainly, like a note one of "
    "them would write down. Keep every number, time, amount and code exactly as said. If "
    "nothing was agreed, say what is still open. Never invent anything that wasn't said."
)


def _roles(story: dict[str, Any] | None) -> dict[str, str]:
    if story is None:
        return {"a": "Person A", "b": "Person B"}
    return {"a": story["a"]["role"], "b": story["b"]["role"]}


def build_prompt(
    turns: list[dict[str, Any]], story: dict[str, Any] | None, langs: dict[str, str]
) -> str:
    roles = _roles(story)
    lines = [
        f"{roles[t['side']]}: {t['src_text'].strip()}"
        for t in turns
        if not t.get("backchannel") and (t.get("src_text") or "").strip()
    ]
    who = "\n".join(f"- {roles[s]} speaks {english(langs[s])}" for s in ("a", "b"))
    context = f"Situation: {story['headline']}. {story['stakes']}\n" if story else ""
    return (
        f"{context}On the call:\n{who}\n\nWhat each person said, in their own words:\n"
        + "\n".join(lines)
        + "\n\nReply with JSON only, in this shape:\n"
        '{"a": {"headline": "...", "points": ["...", "..."]}, '
        '"b": {"headline": "...", "points": ["...", "..."]}}\n'
        f'"a" is written in {english(langs["a"])} for the {roles["a"]}, "b" in '
        f'{english(langs["b"])} for the {roles["b"]}. The headline is one short line; '
        "at most three points."
    )


def parse_outcome(raw: str) -> dict[str, Any] | None:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    out: dict[str, Any] = {}
    for side in ("a", "b"):
        part = data.get(side) if isinstance(data, dict) else None
        if not isinstance(part, dict) or not isinstance(part.get("headline"), str):
            return None
        points = part.get("points") or []
        if not isinstance(points, list):
            return None
        out[side] = {
            "headline": part["headline"].strip(),
            "points": [str(p).strip() for p in points if str(p).strip()][:3],
        }
    return out


async def summarise(
    client: Any,
    model: str,
    turns: list[dict[str, Any]],
    story: dict[str, Any] | None,
    langs: dict[str, str],
) -> dict[str, Any] | None:
    spoken = [t for t in turns if not t.get("backchannel") and (t.get("src_text") or "").strip()]
    if not spoken:
        return None
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=800,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(spoken, story, langs)}],
        )
    except Exception as e:  # any failure: the card falls back to the numbers
        log.warning("demo.outcome_failed", extra={"error": repr(e)})
        return None
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return parse_outcome(text)

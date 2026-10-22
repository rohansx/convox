"""Meaning-rating tool for bilingual raters: a tiny local web page.

    uv run python tools/label.py out/turns.json --rater asha            # opens on :8765
    uv run python tools/label.py out/turns.json --summary               # print % kept only

The rater sees one src/tgt pair at a time and marks it kept / partly / lost
(docs/engineering/evaluation-and-metrics.md), optionally flags whether the entities in it
(numbers, names, addresses, times) survived, and adds a note. Every click appends a row to
ratings.csv, so closing the tab loses nothing and re-running resumes at the first unrated
turn. Re-rating a turn appends a new row; the summary uses the last row per turn_id.

Keyboard: 1/k kept, 2/p partly, 3/l lost, e cycles entities (n/a, ok, lost),
n focuses the note (Esc leaves it), s or Right skips, u or Left goes back.

Turn-log schema (input)
-----------------------
A UTF-8 JSON file: either a list of turn objects, or an object with a "turns" list (as
written by tools/bench.py). Per turn:

    required  src_text   str   what the speaker said (STT final)
              tgt_text   str   what the listener heard (MT output)
              side       "a" | "b"   the speaking side
              pair       str   pair key from config/pairs.yaml, e.g. "hi-kn"
    optional  turn_id    str   stable id; default "<call_id>:<side>:<seq>" when both exist,
                               else "t<index in file>"
              call_id    str
              seq        int
              src_lang   str   BCP-47, e.g. "hi-IN"
              tgt_lang   str
              backchannel bool  true turns are skipped (they are not translated)

Turns with empty src_text or tgt_text are skipped.

ratings.csv (output)
--------------------
turn_id, pair, side, rating (kept|partly|lost), entities (ok|lost|empty = none in turn),
notes, rater, rated_at (UTC ISO 8601), src_text, tgt_text.

The summary reports % kept / partly / lost and entity accuracy per pair, and calls a
number final only at n >= 300 rated turns ("preliminary, n=..." otherwise).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

FINAL_MIN_N = 300
RATINGS = ("kept", "partly", "lost")
ENTITY_MARKS = ("", "ok", "lost")
CSV_FIELDS = (
    "turn_id",
    "pair",
    "side",
    "rating",
    "entities",
    "notes",
    "rater",
    "rated_at",
    "src_text",
    "tgt_text",
)


@dataclass(frozen=True, slots=True)
class LabelTurn:
    turn_id: str
    pair: str
    side: str
    src_text: str
    tgt_text: str
    src_lang: str = ""
    tgt_lang: str = ""


@dataclass(frozen=True, slots=True)
class Rating:
    turn_id: str
    pair: str
    side: str
    rating: str
    entities: str = ""
    notes: str = ""
    rater: str = ""
    rated_at: str = ""

    def __post_init__(self) -> None:
        if self.rating not in RATINGS:
            raise ValueError(f"rating must be one of {RATINGS}, got {self.rating!r}")
        if self.entities not in ENTITY_MARKS:
            raise ValueError(f"entities must be one of {ENTITY_MARKS}, got {self.entities!r}")


@dataclass(frozen=True, slots=True)
class PairSummary:
    pair: str
    n: int
    kept: int
    partly: int
    lost: int
    entity_n: int
    entity_ok: int

    @staticmethod
    def _pct(k: int, n: int) -> float:
        return round(100.0 * k / n, 1) if n else 0.0

    @property
    def pct_kept(self) -> float:
        return self._pct(self.kept, self.n)

    @property
    def pct_partly(self) -> float:
        return self._pct(self.partly, self.n)

    @property
    def pct_lost(self) -> float:
        return self._pct(self.lost, self.n)

    @property
    def pct_entities(self) -> float | None:
        return self._pct(self.entity_ok, self.entity_n) if self.entity_n else None

    @property
    def final(self) -> bool:
        return self.n >= FINAL_MIN_N

    @property
    def label(self) -> str:
        return f"{'final' if self.final else 'preliminary'}, n={self.n}"

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(
            pct_kept=self.pct_kept,
            pct_partly=self.pct_partly,
            pct_lost=self.pct_lost,
            pct_entities=self.pct_entities,
            final=self.final,
            label=self.label,
        )
        return d


# --- summary --------------------------------------------------------------------------


def summarize(ratings: list[Rating]) -> dict[str, PairSummary]:
    """Per-pair counts, last rating per turn_id wins. Pairs in first-seen order."""
    latest = {r.turn_id: r for r in ratings}
    by_pair: dict[str, list[Rating]] = {r.pair: [] for r in ratings}
    for r in latest.values():
        by_pair[r.pair].append(r)
    out = {}
    for pair, rs in by_pair.items():
        if not rs:
            continue
        marked = [r for r in rs if r.entities]
        out[pair] = PairSummary(
            pair=pair,
            n=len(rs),
            kept=sum(r.rating == "kept" for r in rs),
            partly=sum(r.rating == "partly" for r in rs),
            lost=sum(r.rating == "lost" for r in rs),
            entity_n=len(marked),
            entity_ok=sum(r.entities == "ok" for r in marked),
        )
    return out


def format_summary(summaries: dict[str, PairSummary]) -> str:
    if not summaries:
        return "no ratings yet"
    head = f"{'pair':<10} {'n':>5} {'kept':>7} {'partly':>7} {'lost':>7} {'entities':>9}  status"
    lines = [head, "-" * len(head)]
    for s in summaries.values():
        ent = "-" if s.pct_entities is None else f"{s.pct_entities:.1f}%"
        lines.append(
            f"{s.pair:<10} {s.n:>5} {s.pct_kept:>6.1f}% {s.pct_partly:>6.1f}% "
            f"{s.pct_lost:>6.1f}% {ent:>9}  {s.label}"
        )
    if any(not s.final for s in summaries.values()):
        lines.append(f"(a pair stays preliminary until it has {FINAL_MIN_N} rated turns)")
    return "\n".join(lines)


# --- io -------------------------------------------------------------------------------


def load_turns(path: Path) -> list[LabelTurn]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data["turns"] if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of turns or {{'turns': [...]}}")
    out = []
    for i, t in enumerate(raw):
        for key in ("src_text", "tgt_text", "side", "pair"):
            if key not in t:
                raise ValueError(f"{path}: turn {i} has no {key!r}")
        if t.get("backchannel") or not str(t["src_text"]).strip():
            continue
        if not str(t["tgt_text"]).strip():
            continue
        if t.get("turn_id"):
            tid = str(t["turn_id"])
        elif t.get("call_id") and t.get("seq") is not None:
            tid = f"{t['call_id']}:{t['side']}:{t['seq']}"
        else:
            tid = f"t{i}"
        out.append(
            LabelTurn(
                turn_id=tid,
                pair=str(t["pair"]),
                side=str(t["side"]),
                src_text=str(t["src_text"]),
                tgt_text=str(t["tgt_text"]),
                src_lang=str(t.get("src_lang", "")),
                tgt_lang=str(t.get("tgt_lang", "")),
            )
        )
    return out


def read_ratings(path: Path) -> list[Rating]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [
            Rating(
                turn_id=row["turn_id"],
                pair=row["pair"],
                side=row["side"],
                rating=row["rating"],
                entities=row.get("entities", "") or "",
                notes=row.get("notes", "") or "",
                rater=row.get("rater", "") or "",
                rated_at=row.get("rated_at", "") or "",
            )
            for row in csv.DictReader(fh)
        ]


def append_rating(path: Path, rating: Rating, turn: LabelTurn | None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    row = asdict(rating)
    row["rated_at"] = rating.rated_at or datetime.now(UTC).isoformat(timespec="seconds")
    row["src_text"] = turn.src_text if turn else ""
    row["tgt_text"] = turn.tgt_text if turn else ""
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


# --- web app --------------------------------------------------------------------------


class RateIn(BaseModel):
    turn_id: str
    rating: Literal["kept", "partly", "lost"]
    entities: Literal["", "ok", "lost"] = ""
    notes: str = ""


def create_app(turns: list[LabelTurn], ratings_path: Path, rater: str = "") -> FastAPI:
    app = FastAPI(title="convox label")
    index = {t.turn_id: i for i, t in enumerate(turns)}
    lock = threading.Lock()
    ratings = read_ratings(ratings_path)

    def latest() -> dict[str, Rating]:
        return {r.turn_id: r for r in ratings}

    def next_unrated(after: int = -1) -> int | None:
        done = latest()
        order = list(range(after + 1, len(turns))) + list(range(0, after + 1))
        return next((i for i in order if turns[i].turn_id not in done), None)

    def summary() -> dict[str, Any]:
        mine = [r for r in ratings if r.turn_id in index]
        return {p: s.as_dict() for p, s in summarize(mine).items()}

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        done = latest()
        return {
            "total": len(turns),
            "rated": sum(t.turn_id in done for t in turns),
            "next_index": next_unrated(),
        }

    @app.get("/api/turn/{i}")
    def turn(i: int) -> dict[str, Any]:
        if not 0 <= i < len(turns):
            raise HTTPException(404, "no such turn")
        t = turns[i]
        r = latest().get(t.turn_id)
        return {
            **asdict(t),
            "index": i,
            "total": len(turns),
            "rating": r.rating if r else None,
            "entities": r.entities if r else "",
            "notes": r.notes if r else "",
        }

    @app.post("/api/rate")
    def rate(body: RateIn) -> dict[str, Any]:
        i = index.get(body.turn_id)
        if i is None:
            raise HTTPException(404, "unknown turn_id")
        t = turns[i]
        r = Rating(t.turn_id, t.pair, t.side, body.rating, body.entities, body.notes, rater)
        with lock:
            append_rating(ratings_path, r, t)
            ratings.append(r)
        return {"next_index": next_unrated(i), "summary": summary()}

    @app.get("/api/summary")
    def get_summary() -> dict[str, Any]:
        return summary()

    return app


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>convox label</title>
<style>
:root { --bg:#fafaf8; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#e3e3de;
  --kept:#2f7d4f; --partly:#b07a12; --lost:#b23b3b; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --fg:#ecece8; --muted:#9a9a94;
  --card:#20201e; --line:#33332f; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:16px/1.5 system-ui, -apple-system, "Noto Sans", sans-serif; }
main { max-width:760px; margin:0 auto; padding:24px 16px 48px; }
header { display:flex; justify-content:space-between; align-items:baseline; gap:12px; }
h1 { font-size:18px; margin:0; } .muted { color:var(--muted); font-size:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:18px; margin:16px 0; }
.lbl { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
.txt { font-size:22px; margin:4px 0 14px; white-space:pre-wrap; }
.row { display:flex; gap:10px; flex-wrap:wrap; }
button { font:inherit; padding:10px 16px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; min-width:96px; }
button.kept { border-color:var(--kept); } button.partly { border-color:var(--partly); }
button.lost { border-color:var(--lost); }
button.on { color:#fff; } button.kept.on { background:var(--kept); }
button.partly.on { background:var(--partly); } button.lost.on { background:var(--lost); }
kbd { font-size:12px; border:1px solid var(--line); border-radius:4px; padding:0 4px; }
textarea { width:100%; font:inherit; padding:8px; border-radius:8px; min-height:60px;
  border:1px solid var(--line); background:var(--bg); color:var(--fg); }
table { border-collapse:collapse; width:100%; font-size:14px; }
td, th { text-align:left; padding:4px 8px; border-bottom:1px solid var(--line); }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
</style></head><body><main>
<header><h1>convox meaning rating</h1><span class="muted" id="progress"></span></header>
<div class="card" id="card">
  <div class="lbl" id="srclbl">source</div><div class="txt" id="src"></div>
  <div class="lbl" id="tgtlbl">heard</div><div class="txt" id="tgt"></div>
  <div class="row">
    <button class="kept" data-r="kept"><kbd>1</kbd> kept</button>
    <button class="partly" data-r="partly"><kbd>2</kbd> partly</button>
    <button class="lost" data-r="lost"><kbd>3</kbd> lost</button>
    <button id="ent"><kbd>e</kbd> entities: n/a</button>
  </div>
  <p class="lbl" style="margin-top:14px">note <kbd>n</kbd></p>
  <textarea id="notes" placeholder="what went wrong (optional)"></textarea>
  <p class="muted"><kbd>1</kbd>/<kbd>k</kbd> kept &middot; <kbd>2</kbd>/<kbd>p</kbd> partly
  &middot; <kbd>3</kbd>/<kbd>l</kbd> lost &middot; <kbd>e</kbd> entities &middot;
  <kbd>s</kbd>/<kbd>&rarr;</kbd> skip &middot; <kbd>u</kbd>/<kbd>&larr;</kbd> back
  &middot; <kbd>Esc</kbd> leave note</p>
  <p class="muted">kept: a listener would act correctly. partly: gist survives, a detail is
  wrong or missing. lost: wrong or misleading.</p>
</div>
<div class="card"><div class="lbl">summary</div><div id="summary" class="muted">-</div></div>
</main><script>
const ENT = ["", "ok", "lost"]; const ENT_LABEL = {"": "n/a", ok: "ok", lost: "lost"};
let cur = null, ent = "", history = [];
const $ = (id) => document.getElementById(id);
async function j(url, opts) { const r = await fetch(url, opts); return r.json(); }
function setEnt(v) { ent = v; $("ent").lastChild.textContent = " entities: " + ENT_LABEL[v]; }
async function show(i) {
  if (i === null || i === undefined) { $("src").textContent = "All turns rated.";
    $("tgt").textContent = ""; cur = null; await refresh(); return; }
  cur = await j("/api/turn/" + i);
  $("src").textContent = cur.src_text; $("tgt").textContent = cur.tgt_text;
  $("srclbl").textContent = "source (" + (cur.src_lang || "side " + cur.side) + ")";
  $("tgtlbl").textContent = "heard (" + (cur.tgt_lang || cur.pair) + ")";
  $("notes").value = cur.notes || ""; setEnt(cur.entities || "");
  document.querySelectorAll("button[data-r]").forEach(b =>
    b.classList.toggle("on", b.dataset.r === cur.rating));
  await refresh();
}
async function refresh() {
  const s = await j("/api/state");
  $("progress").textContent = s.rated + " / " + s.total + " rated" +
    (cur ? " \\u00b7 turn " + (cur.index + 1) : "");
  renderSummary(await j("/api/summary"));
}
function renderSummary(sum) {
  const pairs = Object.values(sum);
  if (!pairs.length) { $("summary").textContent = "no ratings yet"; return; }
  const t = document.createElement("table");
  t.innerHTML = "<tr><th>pair</th><th class=num>kept</th><th class=num>partly</th>" +
    "<th class=num>lost</th><th class=num>entities</th><th>status</th></tr>";
  for (const p of pairs) {
    const tr = document.createElement("tr");
    const cells = [p.pair, p.pct_kept + "%", p.pct_partly + "%", p.pct_lost + "%",
      p.pct_entities === null ? "-" : p.pct_entities + "%", p.label];
    cells.forEach((c, k) => { const td = document.createElement("td");
      td.textContent = c; if (k > 0 && k < 5) td.className = "num"; tr.append(td); });
    t.append(tr);
  }
  $("summary").replaceChildren(t);
}
async function rate(r) {
  if (!cur) return;
  const res = await j("/api/rate", {method: "POST", headers: {"Content-Type":
    "application/json"}, body: JSON.stringify({turn_id: cur.turn_id, rating: r,
    entities: ent, notes: $("notes").value})});
  history.push(cur.index); await show(res.next_index);
}
function skip() { if (cur) { history.push(cur.index);
  show(cur.index + 1 < cur.total ? cur.index + 1 : 0); } }
function back() { if (history.length) show(history.pop());
  else if (cur && cur.index > 0) show(cur.index - 1); }
document.querySelectorAll("button[data-r]").forEach(b =>
  b.addEventListener("click", () => rate(b.dataset.r)));
$("ent").addEventListener("click", () => setEnt(ENT[(ENT.indexOf(ent) + 1) % 3]));
document.addEventListener("keydown", (e) => {
  if (e.target === $("notes")) { if (e.key === "Escape") $("notes").blur(); return; }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key;
  if (k === "1" || k === "k") rate("kept");
  else if (k === "2" || k === "p") rate("partly");
  else if (k === "3" || k === "l") rate("lost");
  else if (k === "e") setEnt(ENT[(ENT.indexOf(ent) + 1) % 3]);
  else if (k === "n") { e.preventDefault(); $("notes").focus(); }
  else if (k === "s" || k === "ArrowRight") skip();
  else if (k === "u" || k === "ArrowLeft") back();
});
j("/api/state").then(s => show(s.next_index));
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Rate src/tgt turns as kept / partly / lost.")
    p.add_argument("turns", type=Path, help="turn-log JSON (see module docstring)")
    p.add_argument("--ratings", type=Path, default=Path("ratings.csv"), help="output CSV")
    p.add_argument("--rater", default="", help="your name, stored with each rating")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--summary", action="store_true", help="print the summary and exit")
    args = p.parse_args(argv)

    turns = load_turns(args.turns)
    if args.summary:
        ids = {t.turn_id for t in turns}
        print(
            format_summary(summarize([r for r in read_ratings(args.ratings) if r.turn_id in ids]))
        )
        return 0

    import uvicorn

    print(f"{len(turns)} turns from {args.turns}; ratings -> {args.ratings}")
    print(f"open http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    uvicorn.run(create_app(turns, args.ratings, args.rater), host=args.host, port=args.port)
    ids = {t.turn_id for t in turns}
    print(format_summary(summarize([r for r in read_ratings(args.ratings) if r.turn_id in ids])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

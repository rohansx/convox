// Convox live demo stage (templates/stage.html). Live: the call's console SSE
// (/console/events?call=...). Preview (data-preview): a scripted Rent call from
// stage/preview.json (real Sarvam translations, tools/stage_preview.py) through the same
// handlers. One row per line: what was said on the speaker's side, what was heard on the
// other, and between them the quiet before the listener heard it, counted live.
(function () {
  "use strict";

  const root = document.getElementById("stage");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const OTHER = { a: "b", b: "a" };
  const ROLE = { a: root.dataset.aRole, b: root.dataset.bRole };
  const NATIVE = { a: root.dataset.aNative, b: root.dataset.bNative };
  const has = (v) => v !== null && v !== undefined;
  const secs = (ms) => (ms / 1000).toFixed(1) + " s";
  const mmss = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (has(text)) n.textContent = text;
    return n;
  }

  // --- numbers: any script's digits; "15,000" is one number --------------------------
  const D = "0-9०-९૦-૯೦-೯௦-௯";
  const NUM = new RegExp(`[${D}](?:[${D},]*[${D}])?`, "g");
  const ascii = (s) => s.replace(/[०-९૦-૯೦-೯௦-௯]/g,
    (c) => String((c.charCodeAt(0) & 0xf) % 10));
  const numbers = (s) => (s.match(NUM) || []).map((n) => ascii(n).replace(/,/g, "")).sort();
  function marked(text) {
    const p = el("p");
    let last = 0;
    for (const m of text.matchAll(NUM)) {
      p.append(text.slice(last, m.index), el("mark", null, m[0]));
      last = m.index + m[0].length;
    }
    p.append(text.slice(last));
    return p;
  }

  // --- state -------------------------------------------------------------------------
  const legs = { a: false, b: false };
  const turns = new Map();
  let floor = "IDLE";
  let pending = null; // {side, row, tEnd, timer}
  let tStart = null;
  let ended = false;
  let numsIntact = 0;
  const rows = $("rows");

  const firstAudio = (t) =>
    has(t.t_play_start) && has(t.t_speech_end) ? t.t_play_start - t.t_speech_end : null;
  const median = (v) => {
    if (!v.length) return null;
    const s = [...v].sort((x, y) => x - y);
    return s[Math.floor((s.length - 1) / 2)];
  };

  // --- rows --------------------------------------------------------------------------
  function makeRow(side) {
    const row = el("div", `row from-${side}`);
    const cells = {};
    for (const s of ["a", "b"]) {
      cells[s] = el("div", `cell ${s} ${s === side ? "said" : "heard"}`);
      cells[s].append(el("p"), el("div", "tag"));
    }
    const mid = el("div", "mid");
    mid.append(el("span", "arrow", side === "a" ? "→" : "←"), el("span", "q"), el("span", "q-l"));
    row.append(cells.a, mid, cells.b);
    row._cells = cells;
    row._mid = mid;
    return row;
  }
  const q = (row) => row._mid.querySelector(".q");
  const ql = (row) => row._mid.querySelector(".q-l");

  function clearEmpty() {
    const e = rows.querySelector(".empty-rows");
    if (e) e.remove();
  }
  function scroll() { rows.scrollTop = rows.scrollHeight; }

  function startPending(side) {
    if (pending && pending.side === side) return;
    dropPending();
    clearEmpty();
    const row = makeRow(side);
    row.classList.add("pending");
    row._cells[OTHER[side]].querySelector("p").textContent = "…";
    ql(row).textContent = "listening";
    rows.append(row);
    pending = { side, row, tEnd: null, timer: null };
    scroll();
  }
  function dropPending() {
    if (!pending) return;
    clearInterval(pending.timer);
    if (!pending.row._cells[pending.side].querySelector("p").textContent) pending.row.remove();
    pending = null;
  }
  function speechEnded(side) {
    if (!pending || pending.side !== side || has(pending.tEnd)) return;
    pending.tEnd = performance.now();
    ql(pending.row).textContent = "quiet · translating";
    const p = pending;
    p.timer = setInterval(() => { q(p.row).textContent = secs(performance.now() - p.tEnd); }, 100);
  }
  function heardStarts(listener) {
    const side = OTHER[listener];
    if (!pending || pending.side !== side) return;
    clearInterval(pending.timer);
    ql(pending.row).textContent = `${ROLE[listener]} hears it`;
  }

  function onPartial(side, text) {
    if (!text) return;
    startPending(side);
    pending.row._cells[side].querySelector("p").textContent = text;
    scroll();
  }

  function onTurn(p) {
    const t = Object.assign({}, p.turn || p);
    if (!t || t.backchannel || !t.side) return;
    const key = String(t.seq);
    let row = turns.has(key) ? turns.get(key)._row : null;
    if (!row && pending && pending.side === t.side) {
      row = pending.row;
      clearInterval(pending.timer);
      pending = null;
    }
    if (!row) {
      clearEmpty();
      row = makeRow(t.side);
      rows.append(row);
    }
    row.classList.remove("pending");
    const said = row._cells[t.side];
    const heard = row._cells[OTHER[t.side]];
    said.querySelector("p").replaceWith(marked(t.src_text || "…"));
    heard.querySelector("p").replaceWith(marked(t.tgt_text || "…"));
    const tag = heard.querySelector(".tag");
    tag.replaceChildren(el("span", null, `Convox · in ${NATIVE[OTHER[t.side]]}`));
    const ns = numbers(t.src_text || "");
    const intact = ns.length > 0 && ns.join(" ") === numbers(t.tgt_text || "").join(" ");
    if (intact) tag.append(el("span", "ok", `✓ ${ns.join(", ")} came through`));
    const fa = firstAudio(t);
    const later = has(t.chunk) && t.chunk > 0;
    q(row).textContent = later ? "+" : has(fa) ? secs(fa) : "";
    q(row).className = "q" + (has(fa) && !later ? (fa < 1500 ? " fast" : fa > 2500 ? " slow" : "") : "");
    ql(row).textContent = later ? "next part, while still talking" : "quiet before it was heard";
    const prev = turns.get(key);
    if (!(prev && prev._intact) && intact) numsIntact += ns.length;
    t._row = row;
    t._intact = intact || (prev && prev._intact);
    turns.set(key, t);
    scroll();
    refresh();
  }

  function refresh() {
    const all = [...turns.values()];
    const quiet = median(all.filter((t) => !(t.chunk > 0)).map(firstAudio).filter(has));
    $("s-turns").textContent = String(all.length);
    $("s-quiet").textContent = has(quiet) ? secs(quiet) : "—";
    $("s-nums").textContent = String(numsIntact);
    $("wrap").disabled = all.length < 2;
  }

  // --- floor, legs, call -------------------------------------------------------------
  const PILL = {
    IDLE: () => "Live · listening",
    A_SPEAKING: () => `${ROLE.a} is speaking`,
    B_SPEAKING: () => `${ROLE.b} is speaking`,
    PLAYING_AB: () => `${ROLE.b} hears it in ${NATIVE.b}`,
    PLAYING_BA: () => `${ROLE.a} hears it in ${NATIVE.a}`,
  };
  function setFloor(to) {
    const prev = floor;
    floor = to;
    if (!ended && (legs.a && legs.b)) {
      const pill = $("state");
      pill.dataset.state = to === "IDLE" ? "LIVE" : to;
      pill.textContent = (PILL[to] || (() => to))();
    }
    $("person-a").classList.toggle("speaking", to === "A_SPEAKING");
    $("person-b").classList.toggle("speaking", to === "B_SPEAKING");
    for (const s of ["a", "b"]) {
      const speaking = s === "a" ? "A_SPEAKING" : "B_SPEAKING";
      if (to === speaking) startPending(s);
      if (prev === speaking && to !== speaking) speechEnded(s);
    }
    if (to === "PLAYING_AB") heardStarts("b");
    if (to === "PLAYING_BA") heardStarts("a");
  }

  function goLive() {
    if (root.classList.contains("live")) return;
    root.classList.add("live");
    tStart = performance.now();
    const pill = $("state");
    pill.dataset.state = "LIVE";
    pill.textContent = PILL.IDLE();
    if (!turns.size) {
      const e = el("div", "empty-rows");
      e.append(el("b", null, "Both phones are on. Start talking."),
        el("span", null, "Every line shows up here: on the speaker's side as said, on the other side as heard."));
      rows.append(e);
    }
  }

  function setLeg(side, status) {
    if (!side) return;
    legs[side] = status === "attached";
    const n = $("leg-" + side);
    n.dataset.status = status || "";
    n.textContent = status === "attached" ? "on the call" : status === "closed" ? "left" : "waiting";
    const j = root.querySelector(".join-" + side);
    if (j) j.classList.toggle("joined", legs[side]);
    if (legs.a && legs.b) goLive();
  }

  function setEnded() {
    if (ended) return;
    ended = true;
    dropPending();
    const pill = $("state");
    pill.dataset.state = "ENDED";
    pill.textContent = "Call ended";
    if (turns.size) showOutcome();
  }

  setInterval(() => {
    if (tStart !== null && !ended) $("clock").textContent = mmss(performance.now() - tStart);
  }, 250);

  // --- outcome -----------------------------------------------------------------------
  let preview = null;
  async function showOutcome() {
    $("o-turns").textContent = $("s-turns").textContent;
    $("o-quiet").textContent = $("s-quiet").textContent;
    $("o-nums").textContent = $("s-nums").textContent;
    $("o-time").textContent = $("clock").textContent;
    $("oc-wait").hidden = false;
    $("oc-wait").textContent = "Writing up what they agreed…";
    $("oc-title").textContent = "What they agreed";
    $("outcome").hidden = false;
    let out = null;
    try {
      if (preview) out = preview.outcome;
      else if (root.dataset.outcome) {
        const r = await fetch(root.dataset.outcome, { method: "POST" });
        out = r.ok ? (await r.json()).outcome : null;
      }
    } catch (err) { out = null; }
    for (const side of ["a", "b"]) {
      const box = root.querySelector(`.oc-${side}`);
      const part = out && out[side];
      box.querySelector("h3").textContent = part ? part.headline : "";
      box.querySelector("ul").replaceChildren(...(part ? part.points : []).map((x) => el("li", null, x)));
      box.hidden = !part;
    }
    if (out) $("oc-wait").hidden = true;
    else {
      $("oc-title").textContent = "The call, in numbers";
      $("oc-wait").textContent = "No written summary this time: Claude isn't set up or didn't answer.";
    }
  }
  $("wrap").addEventListener("click", showOutcome);
  $("oc-close").addEventListener("click", () => { $("outcome").hidden = true; });

  // --- events ------------------------------------------------------------------------
  const handlers = {
    turn: onTurn,
    state: (p) => { if (p.to || p.state) setFloor(p.to || p.state); },
    leg: (p) => setLeg(p.side, p.status),
    call: (p) => { if (["ended", "completed", "failed"].includes(p.status)) setEnded(); },
    partial: (p) => onPartial(p.side, p.text || ""),
  };
  function dispatch(kind, p) {
    const fn = handlers[kind];
    if (!fn) return;
    try { fn(p); } catch (err) { console.error("stage: bad event", kind, err); }
  }

  // --- boot --------------------------------------------------------------------------
  let bootLegs = {};
  try { bootLegs = JSON.parse(root.dataset.legs || "{}") || {}; } catch (err) { bootLegs = {}; }
  for (const s of ["a", "b"]) {
    const st = typeof bootLegs[s] === "string" ? bootLegs[s] : bootLegs[s] && bootLegs[s].status;
    if (st) setLeg(s, st);
  }
  const earlier = JSON.parse(root.dataset.turns || "[]");
  if (earlier.length) goLive();
  for (const t of earlier) onTurn(t);
  if (["ended", "completed", "failed"].includes(root.dataset.status)) setEnded();

  if (root.dataset.events) {
    const source = new EventSource(root.dataset.events);
    for (const kind of Object.keys(handlers)) {
      source.addEventListener(kind, (e) => {
        let p;
        try { p = JSON.parse(e.data); } catch (err) { return; }
        dispatch(kind, p);
      });
    }
  } else if (root.dataset.preview) {
    fetch("/static/console/stage/preview.json")
      .then((r) => r.json())
      .then((data) => { preview = data; play(data); })
      .catch((err) => console.error("stage: preview", err));
  }

  // --- preview: the scripted call, on the measured timings ----------------------------
  function play(data) {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const emit = (kind, p) => dispatch(kind, p);
    (async () => {
      await wait(900);
      emit("leg", { side: "a", status: "attached" });
      await wait(700);
      emit("leg", { side: "b", status: "attached" });
      await wait(900);
      let seq = 0;
      for (const t of data.turns) {
        const side = t.side;
        emit("state", { to: side === "a" ? "A_SPEAKING" : "B_SPEAKING" });
        const tSpeech = performance.now();
        const words = t.src_text.split(" ");
        for (let i = 0; i < words.length; i++) {
          emit("partial", { side, text: words.slice(0, i + 1).join(" ") });
          await wait(300);
        }
        const tEnd = performance.now();
        emit("state", { to: "IDLE" });
        const quiet = t.st.reduce((a, b) => a + b, 0);
        await wait(quiet);
        emit("state", { to: side === "a" ? "PLAYING_AB" : "PLAYING_BA" });
        seq += 1;
        emit("turn", { turn: { side, seq, src_text: t.src_text, tgt_text: t.tgt_text,
          t_speech_start: tSpeech, t_speech_end: tEnd, t_play_start: tEnd + quiet } });
        await wait(Math.max(1800, t.tgt_text.length * 60));
        emit("state", { to: "IDLE" });
        await wait(700);
      }
      await wait(1000);
      emit("call", { status: "ended" });
      await wait(14000);
      location.reload();
    })();
  }
})();

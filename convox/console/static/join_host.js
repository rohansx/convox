// convox stage demo, host view (templates/join_host.html): the call's live transcript
// from the console SSE (/console/events?call=...), both columns, per-turn latency and a
// state badge. Joined phones shrink the QR codes to give the transcript the screen.
(function () {
  "use strict";

  const root = document.getElementById("call");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const P50 = Number(root.dataset.p50) || 1500;
  const P95 = Number(root.dataset.p95) || 2000;
  const NATIVE = { a: root.dataset.aNative, b: root.dataset.bNative };
  const timeline = $("timeline");
  const totals = new Map(); // seq -> first-audio ms
  const legs = { a: false, b: false };

  const has = (v) => v !== null && v !== undefined;
  const secs = (ms) => (has(ms) ? (ms / 1000).toFixed(2) + " s" : "—");
  const grade = (ms) => (ms < P50 ? "good" : ms < P95 ? "warn" : "bad");
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (has(text)) n.textContent = text;
    return n;
  }
  function percentile(values, p) {
    if (!values.length) return null;
    const v = [...values].sort((x, y) => x - y);
    return v[Math.max(1, Math.ceil((p / 100) * v.length)) - 1];
  }

  const STATE_TEXT = {
    IDLE: () => "Listening",
    A_SPEAKING: () => `${NATIVE.a} speaking`,
    B_SPEAKING: () => `${NATIVE.b} speaking`,
    PLAYING_AB: () => `Interpreting → ${NATIVE.b}`,
    PLAYING_BA: () => `Interpreting → ${NATIVE.a}`,
  };
  function setState(state) {
    const badge = $("state");
    badge.dataset.state = state;
    badge.textContent = (STATE_TEXT[state] || (() => state))();
  }

  function setLeg(side, status) {
    if (!side) return;
    legs[side] = status === "attached";
    const n = $("leg-" + side);
    n.dataset.status = status || "";
    n.textContent = status === "attached" ? "joined" : status === "closed" ? "left" : "waiting for phone";
    document.body.classList.toggle("joined", legs.a && legs.b);
  }

  function setEnded() {
    document.body.classList.add("call-ended");
    document.body.classList.remove("joined");
    $("ended").hidden = false;
    setState("ENDED");
    $("state").textContent = "Call ended";
  }

  function refreshStats() {
    $("n-turns").textContent = timeline.querySelectorAll(".turn").length;
    $("p50").textContent = secs(percentile([...totals.values()], 50));
  }

  function renderTurn(t) {
    const a = el("article", `turn col-${t.side}`);
    a.dataset.seq = t.seq;
    a.append(el("p", "src", t.src_text || "…"));
    const tgt = el("p", "tgt");
    tgt.append(el("span", "arrow", "→ "), t.tgt_text || "…");
    a.append(tgt);
    const total = has(t.t_play_start) && has(t.t_speech_end) ? t.t_play_start - t.t_speech_end : null;
    if (has(total)) {
      a.append(el("span", `lat lat-${grade(total)}`, secs(total)));
      totals.set(String(t.seq), total);
    }
    if (has(t.clipped_ms)) a.append(el("span", "lat lat-warn", `clipped at ${t.clipped_ms} ms`));
    return a;
  }

  function onTurn(p) {
    const t = p.turn || p;
    if (!t || t.backchannel) return;
    const fresh = renderTurn(t);
    const old = timeline.querySelector(`.turn[data-seq="${CSS.escape(String(t.seq))}"]`);
    if (old) old.replaceWith(fresh);
    else timeline.append(fresh);
    $("empty").hidden = true;
    const partial = $("partial-" + t.side);
    if (partial) partial.hidden = true;
    fresh.scrollIntoView({ block: "end", behavior: "smooth" });
    refreshStats();
  }

  for (const t of JSON.parse(root.dataset.turns || "[]")) onTurn(t);
  for (const s of ["a", "b"]) {
    const st = $("leg-" + s).dataset.status;
    if (st) setLeg(s, st);
  }
  setState($("state").dataset.state || "IDLE");
  if (root.dataset.status === "ringing") $("state").textContent = "Waiting for phones";
  if (["ended", "completed", "failed"].includes(root.dataset.status)) setEnded();

  const handlers = {
    turn: onTurn,
    state: (p) => { if (p.to || p.state) setState(p.to || p.state); },
    leg: (p) => setLeg(p.side, p.status),
    call: (p) => {
      if (p.status === "active") setState($("state").dataset.state || "IDLE");
      if (["ended", "completed", "failed"].includes(p.status)) setEnded();
    },
    partial: (p) => {
      const n = $("partial-" + p.side);
      if (!n) return;
      n.textContent = p.text ? "… " + p.text : "";
      n.hidden = !p.text;
    },
  };
  const source = new EventSource(root.dataset.events);
  for (const [kind, fn] of Object.entries(handlers)) {
    source.addEventListener(kind, (e) => {
      try { fn(JSON.parse(e.data)); } catch (err) { console.error("join: bad event", kind, err); }
    });
  }
})();

// dubhash console: live updates for the per-call view, and the connection dot on every
// page. The call list itself refreshes through htmx (sse:* triggers on #calls-body).
// Turn and marker markup mirrors templates/_macros.html; keep the two in sync.
(function () {
  "use strict";

  const STAGES = [
    ["eou", "t_speech_end", "t_eou"],
    ["stt", "t_eou", "t_stt_final"],
    ["mt", "t_stt_final", "t_mt"],
    ["tts", "t_mt", "t_tts_ttfb"],
    ["play", "t_tts_ttfb", "t_play_start"],
  ];

  const live = document.getElementById("live");
  function setLive(state, text) {
    if (!live) return;
    live.dataset.state = state;
    live.querySelector("span").textContent = text;
  }

  const callEl = document.getElementById("call");
  if (!callEl) {
    // Calls list: htmx owns the stream; mirror its connection state on the dot.
    document.body.addEventListener("htmx:sseOpen", () => setLive("on", "live"));
    document.body.addEventListener("htmx:sseError", () => setLive("off", "reconnecting"));
    return;
  }

  const P50 = Number(callEl.dataset.p50) || 1500;
  const P95 = Number(callEl.dataset.p95) || 2000;
  const totals = new Map(); // String(seq) -> first-audio ms
  for (const [seq, ms] of Object.entries(JSON.parse(callEl.dataset.totals || "{}"))) {
    totals.set(String(seq), ms);
  }

  const $ = (id) => document.getElementById(id);
  const timeline = $("timeline");

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  const grade = (ms) => (ms < P50 ? "good" : ms < P95 ? "warn" : "bad");
  const secs = (ms) => (ms === null || ms === undefined ? "—" : (ms / 1000).toFixed(2) + " s");
  const has = (v) => v !== null && v !== undefined;

  function percentile(values, p) {
    if (!values.length) return null;
    const v = [...values].sort((a, b) => a - b);
    return v[Math.max(1, Math.ceil((p / 100) * v.length)) - 1];
  }

  function refreshStats() {
    const vals = [...totals.values()];
    $("p50").textContent = secs(percentile(vals, 50));
    $("p95").textContent = secs(percentile(vals, 95));
    const n = timeline.querySelectorAll(".turn:not(.bc)").length;
    $("n-turns").textContent = n;
  }

  function nearBottom() {
    return window.innerHeight + window.scrollY >= document.body.scrollHeight - 120;
  }

  function append(row) {
    const stick = nearBottom();
    $("empty").hidden = true;
    timeline.insertBefore(row, $("empty"));
    if (stick) window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  }

  function renderTurn(t) {
    const row = el("div", "row");
    row.dataset.seq = t.seq;
    if (t.backchannel) {
      const a = el("article", `item turn bc col-${t.side}`);
      a.append(el("span", "src", t.src_text || "—"), " · backchannel · not translated");
      row.append(a);
      return row;
    }
    const a = el("article", `item turn col-${t.side}` + (has(t.clipped_ms) ? " clipped" : ""));
    a.dataset.seq = t.seq;
    const h = el("header");
    h.append(el("span", "seq", "#" + t.seq));
    if (has(t.clipped_ms)) h.append(el("span", "tag tag-warn", `clipped at ${t.clipped_ms} ms`));
    a.append(h, el("p", "src", t.src_text || "—"));
    const tgt = el("p", "tgt");
    tgt.append(el("span", "arrow", "→"), " " + (t.tgt_text || "—"));
    a.append(tgt);

    const stages = STAGES.filter(([, x, y]) => has(t[x]) && has(t[y]));
    const total = has(t.t_play_start) && has(t.t_speech_end) ? t.t_play_start - t.t_speech_end : null;
    if (stages.length || has(total)) {
      const lat = el("div", "lat");
      for (const [name, x, y] of stages) {
        const s = el("span", "stage");
        s.append(el("em", null, name), " " + (t[y] - t[x]));
        lat.append(s);
      }
      if (has(total)) lat.append(el("b", `total lat-${grade(total)}`, secs(total)));
      a.append(lat);
    }
    if (t.stt_provider || t.mt_provider || t.tts_provider || t.voice) {
      const d = el("details", "prov");
      d.append(el("summary", null, "providers"));
      d.append(
        `stt ${t.stt_provider || "—"} · mt ${t.mt_provider || "—"} · ` +
          `tts ${t.tts_provider || "—"} · voice ${t.voice || "—"}`
      );
      a.append(d);
    }
    row.append(a);
    if (has(total)) totals.set(String(t.seq), total);
    return row;
  }

  function renderMarker(kind, p) {
    const side = p.side || p.listener || "a";
    const row = el("div", "row");
    let text = kind;
    if (kind === "dtmf") {
      text = `DTMF ${p.digit}` + (p.digit === "1" ? " · replay" : p.digit === "0" ? " · bypass" : "");
    } else if (kind === "barge_in") {
      text = "⌁ barge-in" + (has(p.clipped_ms) ? ` · clipped ${p.clipped_ms} ms` : "");
    } else if (kind === "bypass") {
      text = "escape pressed · interpretation off";
    } else if (kind === "gap") {
      text = `audio gap ${p.gap_ms} ms`;
    } else if (kind === "provider_error") {
      text = `${p.provider} ${p.stage} error` + (p.error ? `: ${p.error}` : "");
    }
    row.append(el("div", `item marker col-${side} marker-${kind}`, text));
    return row;
  }

  function onTurn(p) {
    const t = p.turn || p;
    const fresh = renderTurn(t);
    const old = timeline.querySelector(`.row[data-seq="${CSS.escape(String(t.seq))}"]`);
    if (old) old.replaceWith(fresh);
    else append(fresh);
    const partial = $("partial-" + t.side);
    if (partial) partial.hidden = true;
    refreshStats();
  }

  function onState(p) {
    const state = p.to || p.state;
    const badge = $("state");
    if (state) {
      badge.textContent = state;
      badge.className = "badge state-" + state;
    }
    if (has(p.queue_depth)) $("queue-depth").textContent = p.queue_depth;
    const q = Number($("queue-depth").textContent) || 0;
    $("queued").hidden = !(q > 0 && String(badge.textContent).startsWith("PLAYING"));
  }

  function onCall(p) {
    if (p.status) {
      const s = $("call-status");
      s.textContent = p.status.replace("_", " ");
      s.className = "status status-" + p.status;
    }
    if (has(p.duration_s)) {
      $("summary").hidden = false;
      $("sum-duration").textContent = p.duration_s + " s";
      if (has(p.interpreted_s)) $("sum-interpreted").textContent = p.interpreted_s + " s";
      if (has(p.turns)) $("sum-turns").textContent = p.turns;
      $("sum-lat").textContent = `${secs(p.p50_first_audio_ms)} / ${secs(p.p95_first_audio_ms)}`;
      if (has(p.escape_used)) $("sum-escape").textContent = p.escape_used ? "yes" : "no";
    }
  }

  function setEscape() {
    const e = $("escape");
    e.textContent = "used";
    e.className = "flag";
    $("sum-escape").textContent = "yes";
  }

  const handlers = {
    turn: onTurn,
    state: onState,
    call: onCall,
    partial(p) {
      const n = $("partial-" + p.side);
      if (!n) return;
      n.textContent = p.text ? "… " + p.text : "";
      n.hidden = !p.text;
    },
    leg(p) {
      const n = $("leg-" + p.side);
      if (n) n.textContent = p.status || "";
    },
    bypass(p) {
      setEscape();
      append(renderMarker("bypass", p));
    },
  };
  for (const kind of ["dtmf", "barge_in", "gap", "provider_error"]) {
    handlers[kind] = (p) => append(renderMarker(kind, p));
  }

  const source = new EventSource(callEl.dataset.events);
  source.onopen = () => setLive("on", "live");
  source.onerror = () => setLive("off", "reconnecting");
  for (const [kind, fn] of Object.entries(handlers)) {
    source.addEventListener(kind, (e) => {
      try {
        fn(JSON.parse(e.data));
      } catch (err) {
        console.error("console: bad event", kind, err);
      }
    });
  }
  refreshStats();
})();

// convox stage dashboard (templates/demo.html). Live mode reads the call's console SSE
// (/console/events?call=...); preview mode (data-preview) plays a scripted sample call
// through the same handlers. Shows the conversation in both languages, per-turn latency,
// the pipeline split of the last turn, who has the floor, and the events underneath.
(function () {
  "use strict";

  const root = document.getElementById("demo");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const P50 = Number(root.dataset.p50) || 1500;
  const P95 = Number(root.dataset.p95) || 2000;
  const NATIVE = { a: root.dataset.aNative, b: root.dataset.bNative };
  const OTHER = { a: "b", b: "a" };
  const STAGES = [
    ["eou", "t_speech_end", "t_eou", "end of turn"],
    ["stt", "t_eou", "t_stt_final", "recognise"],
    ["mt", "t_stt_final", "t_mt", "translate"],
    ["tts", "t_mt", "t_tts_ttfb", "voice"],
    ["play", "t_tts_ttfb", "t_play_start", "playout"],
  ];
  const LANE_WINDOW_MS = 60000;
  const LANG_NAMES = {
    "mr-IN": "मराठी", "hi-IN": "हिन्दी", "gu-IN": "ગુજરાતી", "en-IN": "English",
    "kn-IN": "ಕನ್ನಡ", "ta-IN": "தமிழ்",
  };

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
  const mmss = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };

  // --- state -------------------------------------------------------------------------
  const turns = new Map(); // seq -> turn
  const legs = { a: false, b: false };
  const numberFixes = { a: 0, b: 0 }; // waiting to be pinned on the side's next turn
  let numbersChecked = 0;
  let floor = "IDLE";
  let tStart = null; // performance.now() when both phones joined
  const lane = []; // {state, t0, t1}

  // --- floor + sides -----------------------------------------------------------------
  const STATE_TEXT = {
    IDLE: () => "Listening",
    A_SPEAKING: () => `${NATIVE.a} speaking`,
    B_SPEAKING: () => `${NATIVE.b} speaking`,
    PLAYING_AB: () => `Interpreting → ${NATIVE.b}`,
    PLAYING_BA: () => `Interpreting → ${NATIVE.a}`,
  };

  function sideMode(side, state) {
    if (state === (side === "a" ? "A_SPEAKING" : "B_SPEAKING")) return "speaking";
    if (state === (side === "a" ? "PLAYING_BA" : "PLAYING_AB")) return "hearing";
    return "idle";
  }

  function paintSides() {
    for (const side of ["a", "b"]) {
      const panel = $("side-" + side);
      const mode = legs[side] ? sideMode(side, floor) : "off";
      panel.classList.toggle("speaking", mode === "speaking");
      panel.classList.toggle("hearing", mode === "hearing");
      $("live-" + side).textContent =
        mode === "off" ? (panel.classList.contains("left") ? "Phone left" : "Waiting for the phone")
        : mode === "speaking" ? "Speaking"
        : mode === "hearing" ? `Hearing ${NATIVE[OTHER[side]]}, in ${NATIVE[side]}`
        : floor === "IDLE" ? "Listening"
        : floor === (side === "a" ? "PLAYING_AB" : "PLAYING_BA") ? `Being interpreted into ${NATIVE[OTHER[side]]}`
        : "Waiting";
    }
  }

  function setFloor(state) {
    const now = performance.now();
    const last = lane[lane.length - 1];
    if (last && !has(last.t1)) last.t1 = now;
    if (state !== "IDLE") lane.push({ state, t0: now, t1: null });
    floor = state;
    const pill = $("state");
    pill.dataset.state = state;
    pill.textContent = (STATE_TEXT[state] || (() => state))();
    paintSides();
  }

  function setLeg(side, status) {
    if (!side) return;
    legs[side] = status === "attached";
    const n = $("leg-" + side);
    n.dataset.status = status || "";
    n.textContent = status === "attached" ? "joined" : status === "closed" ? "left" : "waiting";
    const panel = $("side-" + side);
    panel.classList.toggle("joined", legs[side]);
    panel.classList.toggle("left", status === "closed");
    if (legs.a && legs.b && tStart === null) {
      tStart = performance.now();
      if (floor === "IDLE") setFloor("IDLE");
    }
    paintSides();
  }

  function setEnded() {
    const last = lane[lane.length - 1];
    if (last && !has(last.t1)) last.t1 = performance.now();
    floor = "ENDED";
    $("state").dataset.state = "ENDED";
    $("state").textContent = "Call ended";
    $("ended").hidden = false;
    legs.a = legs.b = false;
    paintSides();
  }

  // --- ticker ------------------------------------------------------------------------
  function tick(icon, text, kind, tsMs) {
    const li = el("li", kind ? "k-" + kind : "");
    const at = has(tsMs) ? tsMs : tStart !== null ? performance.now() - tStart : 0;
    li.append(el("time", null, mmss(at)), el("span", "ico", icon), el("span", null, text));
    const list = $("ticker");
    list.prepend(li);
    while (list.children.length > 40) list.lastElementChild.remove();
  }

  // --- turns -------------------------------------------------------------------------
  function firstAudio(t) {
    return has(t.t_play_start) && has(t.t_speech_end) ? t.t_play_start - t.t_speech_end : null;
  }
  function heard(t) {
    if (has(t.chunk) && t.chunk > 0) return null;
    const start = has(t.t_utt_start) ? t.t_utt_start : t.t_speech_start;
    return has(t.t_play_start) && has(start) ? t.t_play_start - start : null;
  }

  function renderTurn(t) {
    const card = el("article", `turn from-${t.side}`);
    card.dataset.seq = t.seq;
    card.append(el("p", "src", t.src_text || "…"), el("p", "tgt", t.tgt_text || "…"));
    const meta = el("div", "meta");
    const fa = firstAudio(t);
    if (has(fa)) meta.append(el("span", `chip ${grade(fa)}`, secs(fa)));
    if (has(t.chunk)) meta.append(el("span", "chip", `clause ${t.chunk + 1}`));
    if (t._numbers) meta.append(el("span", "chip num", "✓ numbers checked"));
    if (has(t.clipped_ms)) meta.append(el("span", "chip warn", `cut in after ${secs(t.clipped_ms)}`));
    card.append(meta);
    return card;
  }

  function onTurn(p) {
    const t = Object.assign({}, p.turn || p);
    if (!t || t.backchannel) return;
    const prev = turns.get(String(t.seq));
    if (prev && prev._numbers) t._numbers = true;
    if (!prev && numberFixes[t.side] > 0) {
      t._numbers = true;
      numberFixes[t.side] = 0;
    }
    turns.set(String(t.seq), t);
    const fresh = renderTurn(t);
    const timeline = $("timeline");
    const old = timeline.querySelector(`.turn[data-seq="${CSS.escape(String(t.seq))}"]`);
    if (old) old.replaceWith(fresh);
    else timeline.append(fresh);
    $("empty").hidden = true;
    $("partial-" + t.side).textContent = "";
    timeline.scrollTop = timeline.scrollHeight;
    refreshMetrics();
    paintPipeline(t);
  }

  function setMetric(id, value, cls) {
    const n = $(id);
    if (n.textContent === value) return;
    n.textContent = value;
    n.className = cls || "";
    void n.offsetWidth;
    n.classList.add("bump");
  }

  function refreshMetrics() {
    const all = [...turns.values()];
    const fa = all.map(firstAudio).filter(has);
    const hr = all.map(heard).filter(has);
    const p50 = percentile(fa, 50);
    setMetric("m-p50", secs(p50), has(p50) ? grade(p50) : "");
    const best = fa.length ? Math.min(...fa) : null;
    setMetric("m-best", secs(best), has(best) ? grade(best) : "");
    setMetric("m-heard", secs(percentile(hr, 50)));
    setMetric("m-turns", String(all.length));
    const na = all.filter((t) => t.side === "a").length;
    $("m-split").textContent = `${NATIVE.a} ${na} · ${NATIVE.b} ${all.length - na}`;
    setMetric("m-nums", String(numbersChecked));
    $("n-turns").textContent = `${all.length} turn${all.length === 1 ? "" : "s"}`;
  }

  // --- pipeline ----------------------------------------------------------------------
  function stagesOf(t) {
    return STAGES.map(([k, a, b, label]) => ({
      k, label, ms: has(t[a]) && has(t[b]) ? Math.max(0, t[b] - t[a]) : null,
    })).filter((s) => has(s.ms));
  }

  function paintPipeline(t) {
    const st = stagesOf(t);
    if (!st.length) return;
    const bar = $("pipe-bar");
    bar.replaceChildren();
    for (const s of st) {
      const seg = el("span", `pipe-seg k-${s.k}`);
      seg.style.flexGrow = String(Math.max(s.ms, 1));
      seg.title = `${s.label}: ${s.ms} ms`;
      seg.append(el("span", null, s.label), el("small", null, `${s.ms} ms`));
      bar.append(seg);
    }
    const fa = firstAudio(t);
    $("pipe-total").textContent = has(fa) ? `${secs(fa)} · ${NATIVE[t.side]} → ${NATIVE[OTHER[t.side]]}` : "";
    paintHistory();
  }

  function paintHistory() {
    const hist = $("pipe-hist");
    const recent = [...turns.values()].filter((t) => stagesOf(t).length).slice(-14);
    const max = Math.max(P95, ...recent.map((t) => stagesOf(t).reduce((a, s) => a + s.ms, 0)));
    hist.replaceChildren();
    const target = el("div", "target");
    target.style.bottom = `${(P50 / max) * 100}%`;
    target.append(el("span", null, `${(P50 / 1000).toFixed(1)} s target`));
    hist.append(target);
    for (const t of recent) {
      const col = el("div", "col");
      const total = stagesOf(t).reduce((a, s) => a + s.ms, 0);
      col.style.height = `${(total / max) * 100}%`;
      col.title = `${NATIVE[t.side]}: ${secs(total)}`;
      for (const s of stagesOf(t)) {
        const i = el("i", `k-${s.k}`);
        i.style.flexGrow = String(s.ms);
        col.append(i);
      }
      col.style.display = "flex";
      hist.append(col);
    }
  }

  // --- floor lane --------------------------------------------------------------------
  const LANE_OF = {
    A_SPEAKING: ["a", "speak"], B_SPEAKING: ["b", "speak"],
    PLAYING_AB: ["b", "hear"], PLAYING_BA: ["a", "hear"],
  };
  function paintLane() {
    const now = performance.now();
    const from = now - LANE_WINDOW_MS;
    const tracks = { a: $("lane-a"), b: $("lane-b") };
    tracks.a.replaceChildren();
    tracks.b.replaceChildren();
    while (lane.length && has(lane[0].t1) && lane[0].t1 < from) lane.shift();
    for (const seg of lane) {
      const where = LANE_OF[seg.state];
      if (!where) continue;
      const t0 = Math.max(seg.t0, from);
      const t1 = has(seg.t1) ? seg.t1 : now;
      const i = el("i", where[1]);
      i.style.left = `${((t0 - from) / LANE_WINDOW_MS) * 100}%`;
      i.style.width = `${Math.max(0.3, ((t1 - t0) / LANE_WINDOW_MS) * 100)}%`;
      tracks[where[0]].append(i);
    }
  }
  setInterval(() => {
    paintLane();
    if (tStart !== null && floor !== "ENDED") $("clock").textContent = mmss(performance.now() - tStart);
  }, 200);

  // --- events ------------------------------------------------------------------------
  const DTMF = { "1": "replay the last line", "0": "interpreting off" };
  const handlers = {
    turn: onTurn,
    state: (p) => { if (p.to || p.state) setFloor(p.to || p.state); },
    leg: (p) => {
      setLeg(p.side, p.status);
      if (p.status === "attached") tick("●", `Phone ${p.side.toUpperCase()} joined (${NATIVE[p.side]})`, "", p.ts_ms);
      if (p.status === "closed") tick("○", `Phone ${p.side.toUpperCase()} left`, "warn", p.ts_ms);
    },
    call: (p) => {
      if (["ended", "completed", "failed"].includes(p.status)) {
        setEnded();
        tick("■", "Call ended", "", p.ts_ms);
      }
    },
    partial: (p) => {
      const n = $("partial-" + p.side);
      if (n) n.textContent = p.text || "";
    },
    lang: (p) => {
      const pct = has(p.confidence) ? ` · ${Math.round(p.confidence * 100)}%` : "";
      const name = LANG_NAMES[p.lang] || p.lang;
      tick("文", `Side ${p.side.toUpperCase()} is speaking ${name} (${String(p.state).toLowerCase()}${pct})`, "", p.ts_ms);
      if (p.state === "CONFIRMED" && LANG_NAMES[p.lang]) {
        const panel = $("side-" + p.side).querySelector('[data-role="native"]');
        if (panel) panel.textContent = name;
        NATIVE[p.side] = name;
      }
    },
    number_fix: (p) => {
      numbersChecked += 1;
      numberFixes[p.side] = (numberFixes[p.side] || 0) + 1;
      const what = [p.missing, p.replaced, p.appended].flat().filter(Boolean).join(", ");
      tick("✓", `Number read back and fixed${what ? `: ${what}` : ""}`, "num", p.ts_ms);
      refreshMetrics();
    },
    crosstalk: (p) => tick("≈", `Ignored cross-talk on ${p.side.toUpperCase()} (${p.reason})`, "warn", p.ts_ms),
    barge_in: (p) => tick("↯", `${NATIVE[p.side]} cut in; the translation stopped after ${secs(p.clipped_ms)}`, "warn", p.ts_ms),
    dtmf: (p) => tick("#", `Phone ${p.side.toUpperCase()} pressed ${p.digit}${DTMF[p.digit] ? ` (${DTMF[p.digit]})` : ""}`, "", p.ts_ms),
    bypass: (p) => tick("⏸", `Interpreting turned off by ${p.side.toUpperCase()}`, "warn", p.ts_ms),
    line_check: (p) => tick("?", "Long silence: asked if both people are still on the line", "", p.ts_ms),
    prompt_cut: (p) => tick("✂", "Opening line cut short: the first translation was ready", "", p.ts_ms),
    hold_released: (p) => tick("…", `Clause hold on ${p.side.toUpperCase()} released (${p.reason})`, "", p.ts_ms),
    provider_error: (p) => tick("!", `${p.stage || p.provider} error: ${p.error}`, "bad", p.ts_ms),
  };
  function dispatch(kind, p) {
    const fn = handlers[kind];
    if (!fn) return;
    try { fn(p); } catch (err) { console.error("demo: bad event", kind, err); }
  }

  // --- boot --------------------------------------------------------------------------
  for (const t of JSON.parse(root.dataset.turns || "[]")) onTurn(t);
  for (const s of ["a", "b"]) {
    const st = $("leg-" + s).dataset.status;
    if (st) setLeg(s, st);
  }
  paintSides();
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
    preview();
  }

  // --- preview: a scripted sample call through the same handlers ----------------------
  // Marathi lines need a native speaker's check (PQ5).
  function preview() {
    const LINES = [
      { side: "a", src: "नमस्कार, मी सुनीता बोलतेय. पार्सल कुठपर्यंत आलं?", tgt: "नमस्ते, मैं सुनीता बोल रही हूँ। पार्सल कहाँ तक पहुँचा?", st: [550, 230, 340, 250, 40] },
      { side: "b", src: "मैं आपकी सोसाइटी के गेट पर खड़ा हूँ।", tgt: "मी तुमच्या सोसायटीच्या गेटवर उभा आहे.", st: [550, 210, 300, 220, 40], crosstalk: true },
      { side: "a", chunks: [
          { src: "मी आत्ता घरी नाही,", tgt: "मैं अभी घर पर नहीं हूँ,", st: [300, 200, 290, 230, 30] },
          { src: "पार्सल वॉचमनकडे द्या.", tgt: "पार्सल वॉचमैन को दे दीजिए।", st: [550, 190, 280, 210, 40] },
        ] },
      { side: "b", src: "ठीक है, ओटीपी बता दीजिए।", tgt: "ठीक आहे, ओटीपी सांगा.", st: [550, 180, 260, 200, 30] },
      { side: "a", src: "ओटीपी 47296 आहे.", tgt: "ओटीपी 47296 है।", st: [550, 240, 360, 230, 40], number: "47296" },
      { side: "b", src: "धन्यवाद, पार्सल वॉचमैन को दे दिया है।", tgt: "धन्यवाद, पार्सल वॉचमनकडे दिलं आहे.", st: [550, 220, 320, 240, 40] },
    ];
    const T0 = performance.now();
    const now = () => Math.round(performance.now() - T0);
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const emit = (kind, p) => dispatch(kind, Object.assign({ ts_ms: now() }, p));
    let seq = 0;

    async function say(side, src, tgt, st, extra) {
      const tSpeech = now();
      for (const [i, w] of src.split(" ").entries()) {
        emit("partial", { side, text: src.split(" ").slice(0, i + 1).join(" ") });
        await wait(260);
      }
      const tEnd = now();
      let t = tEnd;
      const marks = {};
      ["t_eou", "t_stt_final", "t_mt", "t_tts_ttfb", "t_play_start"].forEach((k, i) => {
        t += st[i];
        marks[k] = t;
      });
      await wait(st.reduce((a, b) => a + b, 0));
      if (extra.number) emit("number_fix", { side, missing: [], replaced: [extra.number], appended: [] });
      emit("state", { to: side === "a" ? "PLAYING_AB" : "PLAYING_BA" });
      seq += 1;
      emit("turn", { turn: Object.assign({ side, seq, src_text: src, tgt_text: tgt,
        t_speech_start: tSpeech, t_speech_end: tEnd, t_utt_start: extra.uttStart ?? tSpeech }, marks,
        has(extra.chunk) ? { chunk: extra.chunk, utterance_id: extra.utt } : {}) });
      await wait(Math.max(1400, tgt.length * 55));
    }

    (async () => {
      await wait(600);
      emit("leg", { side: "a", status: "attached" });
      await wait(700);
      emit("leg", { side: "b", status: "attached" });
      emit("call", { status: "active" });
      await wait(900);
      for (const [n, line] of LINES.entries()) {
        const speaking = line.side === "a" ? "A_SPEAKING" : "B_SPEAKING";
        emit("state", { to: speaking });
        if (n === 0) {
          await wait(500);
          emit("lang", { side: "a", lang: "mr-IN", state: "CONFIRMED", confidence: 0.94, source: "lid" });
        }
        if (line.chunks) {
          const uttStart = now();
          for (const [i, c] of line.chunks.entries()) {
            await say(line.side, c.src, c.tgt, c.st, { chunk: i, utt: n, uttStart });
            if (i < line.chunks.length - 1) emit("state", { to: speaking });
          }
        } else {
          await say(line.side, line.src, line.tgt, line.st, line);
        }
        emit("state", { to: "IDLE" });
        if (line.crosstalk) emit("crosstalk", { side: "a", reason: "echo", stage: "frame" });
        await wait(800);
      }
      await wait(1200);
      emit("call", { status: "ended" });
      await wait(9000);
      location.reload();
    })();
  }
})();

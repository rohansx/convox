// convox stage demo: one phone, one side of a loopback call (templates/join_phone.html).
// Tap to start (mic permission and AudioContext unlock need a user gesture on iOS and
// Android), then mic -> 16 kHz PCM16 over the websocket, and translations played back.
// The protocol is WebRtcLeg's (legs/webrtc.py): binary audio both ways, {"type":"mark"}
// acked when playout reaches it, {"type":"clear"} flushes the queue.
//
// Telemetry (L7), on the same socket, so a test call shows what the phone really did:
//   {"type":"telemetry","kind":"play_start","tag","client_ms","audio_ctx_ms"}  first
//       sample of a translation (or the opening line) played; tag = the server's mark
//   {"type":"telemetry","kind":"play_end", ...same fields}  playout reached its mark
//       (plus "cleared": true when a clear cut it)
//   {"type":"telemetry","kind":"mic_level","rms","client_ms"}  every 500 ms, mean RMS of
//       the mic (0..1, before the half-duplex gate; plus "gated" while it was gated)
//   {"type":"telemetry","kind":"pong","server_ms","client_ms"}  answers {"type":"ping"}
// client_ms is epoch ms (performance.timeOrigin + performance.now()). For play_* it is
// when the sample left the speaker: the audio context time is mapped through
// getOutputTimestamp(), which includes the device's output latency.
(function () {
  "use strict";

  const root = document.getElementById("phone");
  const $ = (id) => document.getElementById(id);
  const side = root.dataset.side;
  const lang = root.dataset.lang;
  const ui = JSON.parse(root.dataset.ui);
  const qs = new URLSearchParams(location.search);
  const DUPLEX = qs.get("duplex") === "1"; // off: the mic is gated while we play
  const GATE_TAIL_MS = 300; // room echo after the last played sample
  const MAX_LINES = 4;
  const TALK_PEAK = 0.06;
  const MIC_LEVEL_FRAMES = 25; // 500 ms of 20 ms mic frames

  const tap = $("tap"), orb = $("orb"), orbText = $("orb-text"), leave = $("leave");
  const statusEl = $("status"), linesEl = $("lines");

  let ctx = null, stream = null, capture = null, player = null, ws = null, wakeLock = null;
  let started = false, ended = false, callActive = false, playing = false, talking = false;
  let gateTimer = null, talkUntil = 0;
  let micSumsq = 0, micFrames = 0, micGated = false;

  function nowMs() { return performance.timeOrigin + performance.now(); }

  function sendJson(msg) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
  }

  function telemetry(kind, fields) {
    sendJson(Object.assign({ type: "telemetry", kind }, fields));
  }

  // audio context time (ms) of a rendered sample -> epoch ms when it is heard
  function heardAt(ctxMs) {
    const ts = ctx && ctx.getOutputTimestamp ? ctx.getOutputTimestamp() : null;
    if (ts && ts.contextTime > 0 && ts.performanceTime > 0) {
      return performance.timeOrigin + ts.performanceTime + (ctxMs - ts.contextTime * 1000);
    }
    const lat = ctx ? (ctx.outputLatency || ctx.baseLatency || 0) * 1000 : 0;
    return nowMs() + (ctxMs - (ctx ? ctx.currentTime * 1000 : ctxMs)) + lat;
  }

  function onMicFrame(rms, gated) {
    const now = nowMs();
    micSumsq += rms * rms;
    micFrames++;
    micGated = micGated || gated;
    if (micFrames >= MIC_LEVEL_FRAMES) {
      const fields = { rms: Math.sqrt(micSumsq / micFrames), client_ms: now };
      if (micGated) fields.gated = true;
      telemetry("mic_level", fields);
      micSumsq = 0; micFrames = 0; micGated = false;
    }
  }

  function setStatus(text) { statusEl.textContent = text || ""; }

  function render() {
    let state = "idle";
    if (ended) state = "ended";
    else if (!started) state = "idle";
    else if (!ws || ws.readyState !== 1) state = "connecting";
    else if (ctx && ctx.state !== "running") state = "paused"; // iOS interruption, lock screen
    else if (playing) state = "speaking";
    else if (!callActive) state = "waiting";
    else state = talking ? "talking" : "listening";
    document.body.dataset.state = state;
    const text = {
      ended: ui.ended, connecting: ui.connecting, waiting: ui.waiting,
      speaking: ui.speaking, listening: ui.listening, talking: ui.listening,
      paused: ui.resume, idle: "",
    }[state];
    orbText.textContent = text;
    tap.hidden = !(state === "idle" || state === "paused");
    orb.hidden = state === "idle" || state === "paused";
    leave.hidden = !started || ended;
    if (state === "paused") tap.querySelector(".tap-native").textContent = ui.resume;
  }

  // --- conversation lines (from the call's SSE) -------------------------------------
  const lines = new Map(); // seq -> element

  function onTurn(p) {
    const t = p.turn || p;
    if (!t || t.backchannel) return;
    const mine = t.side === side;
    const text = mine ? t.src_text : t.tgt_text;
    if (!text) return;
    let el = lines.get(String(t.seq));
    if (!el) {
      el = document.createElement("p");
      el.className = "line " + (mine ? "mine" : "theirs");
      lines.set(String(t.seq), el);
      linesEl.append(el);
    }
    el.textContent = text;
    el.dataset.who = mine ? ui.you : "";
    while (linesEl.children.length > MAX_LINES) {
      const first = linesEl.firstElementChild;
      for (const [k, v] of lines) if (v === first) lines.delete(k);
      first.remove();
    }
    const partial = $("partial");
    if (partial && mine) partial.remove();
  }

  function onPartial(p) {
    if (p.side !== side) return;
    let el = $("partial");
    if (!p.text) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement("p");
      el.id = "partial";
      el.className = "line mine partial";
      linesEl.append(el);
    }
    el.textContent = p.text;
  }

  function onCall(p) {
    if (p.status === "active") { callActive = true; render(); }
    if (["ended", "completed", "failed"].includes(p.status)) finish();
  }

  // lang "auto": the page shows "speak in your language" until the call confirms one
  const names = JSON.parse(root.dataset.names || "{}");
  function onLang(p) {
    if (!p || p.state !== "CONFIRMED" || !names[p.lang]) return;
    const [en, nat] = names[p.lang];
    const short = p.lang.slice(0, 2);
    if (p.side === side) {
      const h = document.getElementById("me-native");
      if (h) { h.textContent = nat; h.lang = short; }
      const e = document.getElementById("me-english");
      if (e) e.textContent = en === nat ? "" : en;
      root.dataset.lang = p.lang;
    } else {
      const o = document.getElementById("other-native");
      if (o) { o.textContent = nat; o.lang = short; }
    }
  }

  const source = new EventSource(root.dataset.events);
  source.addEventListener("lang", (e) => onLang(JSON.parse(e.data)));
  source.addEventListener("turn", (e) => onTurn(JSON.parse(e.data)));
  source.addEventListener("partial", (e) => onPartial(JSON.parse(e.data)));
  source.addEventListener("call", (e) => onCall(JSON.parse(e.data)));
  source.addEventListener("state", () => { if (started && !callActive) { callActive = true; render(); } });
  source.onerror = () => { if (source.readyState === 2 && started) finish(); };

  // --- audio ------------------------------------------------------------------------

  function unlockAudio() {
    // Must run synchronously inside the tap: iOS only unlocks output from a gesture.
    const AC = window.AudioContext || window.webkitAudioContext;
    ctx = new AC({ latencyHint: "interactive" });
    const buf = ctx.createBuffer(1, 1, ctx.sampleRate);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start(0);
    ctx.resume();
    ctx.onstatechange = render;
  }

  function setGate(on) {
    if (DUPLEX || !capture) return;
    clearTimeout(gateTimer);
    if (on) capture.port.postMessage({ type: "gate", on: true });
    else gateTimer = setTimeout(() => capture && capture.port.postMessage({ type: "gate", on: false }), GATE_TAIL_MS);
  }

  async function start() {
    if (started) { if (ctx) ctx.resume(); return; }
    started = true;
    unlockAudio();
    render();
    if (!window.isSecureContext || !navigator.mediaDevices) {
      setStatus("The microphone needs https. Open the link from the QR code.");
      return fail();
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      await ctx.audioWorklet.addModule(root.dataset.worklet);
      capture = new AudioWorkletNode(ctx, "capture", { numberOfInputs: 1, numberOfOutputs: 1 });
      player = new AudioWorkletNode(ctx, "player", { numberOfInputs: 0, outputChannelCount: [1] });
      const sink = ctx.createGain();
      sink.gain.value = 0; // keeps the capture node pulled by the graph in every browser
      ctx.createMediaStreamSource(stream).connect(capture).connect(sink).connect(ctx.destination);
      player.connect(ctx.destination);
      await ctx.resume();
    } catch (err) {
      setStatus("Microphone unavailable: " + (err && err.message ? err.message : err));
      return fail();
    }
    keepAwake();
    connect();
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + root.dataset.ws);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "hello", side, lang }));
      if (!root.dataset.solo) setStatus(`${ctx.sampleRate / 1000} kHz → 16 kHz`);
      render();
    };
    ws.onmessage = (e) => {
      if (typeof e.data !== "string") {
        const i16 = new Int16Array(e.data, 0, e.data.byteLength >> 1);
        const f32 = new Float32Array(i16.length);
        for (let k = 0; k < i16.length; k++) f32[k] = i16[k] / 32768;
        player.port.postMessage({ type: "push", pcm: f32.buffer }, [f32.buffer]);
        return;
      }
      let m;
      try { m = JSON.parse(e.data); } catch { return; }
      if (m.type === "clear") player.port.postMessage({ type: "clear" });
      else if (m.type === "mark") player.port.postMessage({ type: "mark", name: m.name });
      else if (m.type === "ping") telemetry("pong", { server_ms: m.server_ms, client_ms: nowMs() });
    };
    ws.onclose = (e) => {
      if (e.code === 4401) setStatus("This link has expired. Scan the code again.");
      else if (e.code === 4409) setStatus("Another phone already joined this side.");
      finish();
    };
    capture.port.onmessage = (e) => {
      if (e.data.type !== "frame") return;
      if (ws && ws.readyState === 1) ws.send(e.data.pcm); // one 20 ms frame, sent at once
      if (typeof e.data.rms === "number") onMicFrame(e.data.rms, e.data.gated);
      const now = performance.now();
      if (!e.data.gated && e.data.peak > TALK_PEAK) talkUntil = now + 400;
      const t = now < talkUntil;
      document.body.style.setProperty("--level", Math.min(1, e.data.gated ? 0 : e.data.peak * 4).toFixed(2));
      if (t !== talking) { talking = t; render(); }
    };
    player.port.onmessage = (e) => {
      if (e.data.type === "mark") {
        sendJson({ type: "mark", name: e.data.name });
      } else if (e.data.type === "play_start" || e.data.type === "play_end") {
        const d = e.data;
        const fields = { tag: d.name, client_ms: heardAt(d.ctx_ms), audio_ctx_ms: d.ctx_ms };
        if (d.cleared) fields.cleared = true;
        telemetry(d.type, fields);
      } else if (e.data.type === "playing") {
        playing = e.data.on;
        setGate(playing);
        render();
      }
    };
  }

  async function keepAwake() {
    try { if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen"); }
    catch { /* not granted: the phone may dim, keep tapping the screen */ }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && started && !ended) {
      keepAwake();
      if (ctx && ctx.state !== "running") ctx.resume().then(render, render);
    }
  });

  function teardown(keepEvents) {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (ctx && ctx.state !== "closed") ctx.close();
    if (ws && ws.readyState <= 1) ws.close(1000);
    if (wakeLock) wakeLock.release().catch(() => {});
    if (!keepEvents) source.close();
    stream = capture = player = wakeLock = null;
  }

  function fail() {
    started = false;
    teardown(true);
    ws = null;
    ctx = null;
    render();
  }

  function finish() {
    if (ended) return;
    ended = true;
    teardown();
    render();
  }

  tap.addEventListener("click", start);
  leave.addEventListener("click", () => {
    if (ws && ws.readyState <= 1) ws.close(1000);
    finish();
  });
  render();
})();

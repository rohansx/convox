// The solo demo's call page (templates/try_call.html): the visitor's phone and the AI's
// phone, driven by the live call.
//   mic -> /ws/loopback (WebRtcLeg protocol, as join_phone.js): 16 kHz PCM16 up, the
//          interpretation for the visitor down, marks acked when they have played
//   /ws/try/{id}/monitor: the AI's phone for "hear both phones": b"S"+PCM what it says,
//          b"H"+PCM what it hears, played on a second player
//   /join/{id}/{side}/events (SSE): floor state, STT partials and turns, which draw the
//          bubbles on both phones, the delay card, the transcript and the numbers
// The mic is gated while either player plays (and 300 ms after), so the visitor's own
// speaker is not heard as them talking.
(function () {
  "use strict";

  const root = document.getElementById("call");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const body = document.body;
  const ME = root.dataset.side, AG = root.dataset.agentSide;
  const OTHER = { a: "b", b: "a" };
  const people = JSON.parse(root.dataset.people);
  const GATE_TAIL_MS = 300;
  const TALK_PEAK = 0.06;
  const has = (v) => v !== null && v !== undefined;
  const secs = (ms) => (ms / 1000).toFixed(1) + " s";
  const mmss = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };
  const who = (side) => (side === ME ? "You" : people[side].name);
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (has(text)) n.textContent = text;
    return n;
  }

  // numbers in any script; "15,000" is one number (as stage.js)
  const D = "0-9०-९૦-૯೦-೯௦-௯";
  const NUM = new RegExp(`[${D}](?:[${D},]*[${D}])?`, "g");
  const ascii = (s) => s.replace(/[०-९૦-૯೦-೯௦-௯]/g, (c) => String((c.charCodeAt(0) & 0xf) % 10));
  const numbers = (s) => (s.match(NUM) || []).map((n) => ascii(n).replace(/,/g, "")).sort();

  // --- phones ------------------------------------------------------------------------
  const P = {};
  for (const side of ["a", "b"]) {
    const r = root.querySelector(`.iphone[data-side="${side}"]`);
    P[side] = { root: r, convo: r.querySelector('[data-role="convo"]'),
                status: r.querySelector('[data-role="status"]'), timer: r.querySelector('[data-role="timer"]') };
  }
  const link = $("link");
  const linkDir = link.querySelector('[data-role="dir"]');
  const linkTiming = link.querySelector('[data-role="timing"]');

  function bubble(side, kind, text) {
    const b = el("div", `bubble ${kind}`);
    const t = el("span", null, text);
    b.append(t);
    P[side].convo.append(b);
    P[side].convo.scrollTop = P[side].convo.scrollHeight;
    return { b, t };
  }
  function setPhone(side, state, text) {
    P[side].root.classList.toggle("speaking", state === "speaking");
    P[side].root.classList.toggle("hearing", state === "hearing");
    P[side].status.textContent = text;
  }
  function setLink(dir, title, timing) {
    if (dir) link.dataset.dir = dir; else delete link.dataset.dir;
    linkDir.textContent = title;
    linkTiming.innerHTML = "";
    linkTiming.textContent = timing || " ";
  }

  // --- call state ------------------------------------------------------------------
  let started = false, live = false, ended = false, tLive = null;
  const pending = {}; // side -> the speaker's typing bubble
  const turns = [];
  let numsIntact = 0;
  let floor = "IDLE";
  let quiet = null; // {dir, t0, timer}

  function pill(text) { $("pill").textContent = text; }
  function setBodyState(s) { body.dataset.state = s; }

  function goLive() {
    if (live || ended) return;
    live = true;
    tLive = performance.now();
    setBodyState("live");
    pill("Live");
    $("leave").hidden = false;
    setPhone(AG, "idle", "Connected");
    setPhone(ME, "idle", "Connected");
    setLink(null, "Listening");
  }

  function startQuiet(from) {
    stopQuiet();
    const to = OTHER[from];
    const dir = from + to;
    const t0 = performance.now();
    setLink(dir, `${people[from].native} → ${people[to].native}`, "quiet 0.0 s");
    quiet = { dir, t0, timer: setInterval(() => { linkTiming.textContent = "quiet " + secs(performance.now() - t0); }, 100) };
    setPhone(from, "idle", "Interpreting…");
    setPhone(to, "idle", "Waiting for Convox…");
  }
  function stopQuiet() {
    if (quiet) clearInterval(quiet.timer);
    quiet = null;
  }

  function onState(to) {
    const prev = floor;
    floor = to;
    if (!live) return;
    if (to === "A_SPEAKING" || to === "B_SPEAKING") {
      const s = to[0].toLowerCase();
      stopQuiet();
      setPhone(s, "speaking", s === ME ? "You're speaking…" : "Speaking…");
      setPhone(OTHER[s], "idle", `${who(s)} ${s === ME ? "are" : "is"} speaking`);
      setLink(null, "Listening", people[s].native);
      return;
    }
    if (to === "PLAYING_AB" || to === "PLAYING_BA") {
      const from = to[8].toLowerCase(), hearer = OTHER[from];
      const q = quiet ? performance.now() - quiet.t0 : null;
      stopQuiet();
      setLink(from + hearer, `${people[from].native} → ${people[hearer].native}`, has(q) ? "quiet " + secs(q) : "");
      setPhone(hearer, "hearing", hearer === ME ? `Hearing ${who(from)} in ${people[ME].native}` : `Hearing you in ${people[hearer].native}`);
      setPhone(from, "idle", "Convox is speaking for " + (from === ME ? "you" : "him"));
      return;
    }
    // IDLE (or anything else): a speaker who just stopped is being interpreted
    if (prev === "A_SPEAKING" || prev === "B_SPEAKING") startQuiet(prev[0].toLowerCase());
    else if (!quiet) {
      setLink(null, "Listening");
      setPhone("a", "idle", "Connected");
      setPhone("b", "idle", "Connected");
    }
  }

  function onPartial(p) {
    if (!live || !p.side || !p.text) return;
    let b = pending[p.side];
    if (!b) {
      b = pending[p.side] = bubble(p.side, "mine partial", "");
      b.t.classList.add("typing");
    }
    b.t.textContent = p.text;
  }

  function onTurn(p) {
    const t = p.turn || p;
    if (!t || t.backchannel || !t.side) return;
    goLive();
    const from = t.side, to = OTHER[from];
    let mine = pending[from];
    delete pending[from];
    if (!mine) mine = bubble(from, "mine", "");
    mine.b.classList.remove("partial");
    mine.t.classList.remove("typing");
    mine.t.textContent = t.src_text || "…";
    const fa = has(t.t_play_start) && has(t.t_speech_end) ? t.t_play_start - t.t_speech_end : null;
    const later = t.chunk > 0;
    const heard = bubble(to, "heard", t.tgt_text || "…");
    const meta = el("div", "meta");
    meta.append(el("span", fa > 2500 ? "slow" : null, later ? `${who(from)} · while still talking` : `${who(from)}${has(fa) ? " · after " + secs(fa) : ""}`));
    const ns = numbers(t.src_text || "");
    const intact = ns.length > 0 && ns.join(" ") === numbers(t.tgt_text || "").join(" ");
    if (intact) { meta.append(el("span", "ok", "✓ " + ns.join(" "))); numsIntact += ns.length; }
    heard.b.append(meta);
    if (has(fa) && !later) linkTiming.textContent = "quiet " + secs(fa);
    turns.push({ fa: later ? null : fa });
    addTranscript(t, fa, later);
    refresh();
  }

  function addTranscript(t, fa, later) {
    const li = el("li");
    const from = people[t.side], to = people[OTHER[t.side]];
    const name = el("b", null, `${t.side === ME ? "You" : from.name}`);
    const said = el("span", "t-said", t.src_text || "…");
    said.lang = from.lang.slice(0, 2);
    const heard = el("span", "t-heard", t.tgt_text || "…");
    heard.lang = to.lang.slice(0, 2);
    li.append(name, said, heard, el("span", "t-q", later ? "+" : has(fa) ? secs(fa) : ""));
    name.append(el("span", "try-muted", ` · ${from.native}`));
    $("transcript").append(li);
  }

  function refresh() {
    const v = turns.map((x) => x.fa).filter(has).sort((x, y) => x - y);
    $("s-turns").textContent = String(turns.length);
    $("s-quiet").textContent = v.length ? secs(v[Math.floor((v.length - 1) / 2)]) : "—";
    $("s-nums").textContent = String(numsIntact);
    $("wrap").disabled = turns.length < 2;
  }

  setInterval(() => {
    if (!live || ended) return;
    const t = mmss(performance.now() - tLive);
    $("clock").textContent = t;
    P.a.timer.textContent = t;
    P.b.timer.textContent = t;
  }, 250);

  // --- events ------------------------------------------------------------------------
  const source = new EventSource(root.dataset.events);
  const on = (kind, fn) => source.addEventListener(kind, (e) => {
    try { fn(JSON.parse(e.data)); } catch (err) { console.error("try: bad event", kind, err); }
  });
  on("state", (p) => onState(p.to || p.state));
  on("partial", onPartial);
  on("turn", onTurn);
  on("leg", (p) => { if (p.side === ME && p.status === "attached") goLive(); });
  on("call", (p) => {
    if (p.status === "active") goLive();
    if (["ended", "completed", "failed"].includes(p.status)) finish("The call has ended.");
  });
  source.onerror = () => { if (source.readyState === 2 && started) finish("The call has ended."); };

  // --- audio ---------------------------------------------------------------------------
  let ctx = null, stream = null, capture = null, player = null, monitor = null;
  let ws = null, mws = null, wakeLock = null;
  let callPlaying = false, monPlaying = false, gateTimer = null, talkUntil = 0, talking = false;
  let both = true;

  const nowMs = () => performance.timeOrigin + performance.now();
  const send = (m) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); };

  function toF32(buf, offset) {
    const i16 = new Int16Array(buf, offset, (buf.byteLength - offset) >> 1);
    const f32 = new Float32Array(i16.length);
    for (let k = 0; k < i16.length; k++) f32[k] = i16[k] / 32768;
    return f32;
  }
  function gate() {
    if (!capture) return;
    clearTimeout(gateTimer);
    if (callPlaying || monPlaying) capture.port.postMessage({ type: "gate", on: true });
    else gateTimer = setTimeout(() => capture && capture.port.postMessage({ type: "gate", on: false }), GATE_TAIL_MS);
  }

  async function start() {
    if (started) return;
    started = true;
    setBodyState("connecting");
    pill("Connecting…");
    // iOS unlocks audio output only inside the tap
    const AC = window.AudioContext || window.webkitAudioContext;
    ctx = new AC({ latencyHint: "interactive" });
    const silent = ctx.createBufferSource();
    silent.buffer = ctx.createBuffer(1, 1, ctx.sampleRate);
    silent.connect(ctx.destination);
    silent.start(0);
    ctx.resume();
    if (!window.isSecureContext || !navigator.mediaDevices) return fail("The microphone needs https.");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      await ctx.audioWorklet.addModule(root.dataset.worklet);
      capture = new AudioWorkletNode(ctx, "capture", { numberOfInputs: 1, numberOfOutputs: 1 });
      player = new AudioWorkletNode(ctx, "player", { numberOfInputs: 0, outputChannelCount: [1] });
      monitor = new AudioWorkletNode(ctx, "player", { numberOfInputs: 0, outputChannelCount: [1] });
      const sink = ctx.createGain();
      sink.gain.value = 0; // keeps the capture node pulled in every browser
      ctx.createMediaStreamSource(stream).connect(capture).connect(sink).connect(ctx.destination);
      player.connect(ctx.destination);
      monitor.connect(ctx.destination);
      await ctx.resume();
    } catch (err) {
      return fail("Microphone blocked. Allow it in your browser, then reload this page.");
    }
    keepAwake();
    connect();
    connectMonitor();
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + root.dataset.ws);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { ws.send(JSON.stringify({ type: "hello", side: ME, lang: root.dataset.lang })); pill("Connected · waiting for " + people[AG].name); };
    ws.onmessage = (e) => {
      if (typeof e.data !== "string") {
        const f = toF32(e.data, 0);
        player.port.postMessage({ type: "push", pcm: f.buffer }, [f.buffer]);
        return;
      }
      let m;
      try { m = JSON.parse(e.data); } catch { return; }
      if (m.type === "clear") player.port.postMessage({ type: "clear" });
      else if (m.type === "mark") player.port.postMessage({ type: "mark", name: m.name });
      else if (m.type === "ping") send({ type: "telemetry", kind: "pong", server_ms: m.server_ms, client_ms: nowMs() });
    };
    ws.onclose = (e) => {
      if (e.code === 4401) finish("This link has expired. Start a new call.");
      else if (e.code === 4409) finish("This call is already open in another tab.");
      else finish("The call has ended.");
    };
    capture.port.onmessage = (e) => {
      if (e.data.type !== "frame") return;
      if (ws && ws.readyState === 1) ws.send(e.data.pcm);
      const now = performance.now();
      if (!e.data.gated && e.data.peak > TALK_PEAK) talkUntil = now + 400;
      const t = now < talkUntil;
      if (t !== talking) {
        talking = t;
        if (live && floor === "IDLE" && !quiet) setPhone(ME, t ? "speaking" : "idle", t ? "You're speaking…" : "Connected");
      }
    };
    player.port.onmessage = (e) => {
      const d = e.data;
      if (d.type === "mark") send({ type: "mark", name: d.name });
      else if (d.type === "play_start" || d.type === "play_end") {
        send({ type: "telemetry", kind: d.type, tag: d.name, client_ms: nowMs(), audio_ctx_ms: d.ctx_ms });
      } else if (d.type === "playing") { callPlaying = d.on; gate(); }
    };
    monitor.port.onmessage = (e) => { if (e.data.type === "playing") { monPlaying = e.data.on; gate(); } };
  }

  function connectMonitor() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    mws = new WebSocket(proto + "//" + location.host + root.dataset.monitor);
    mws.binaryType = "arraybuffer";
    mws.onmessage = (e) => {
      if (typeof e.data === "string") {
        if (e.data.includes('"clear"')) monitor.port.postMessage({ type: "clear" });
        return;
      }
      if (!both || e.data.byteLength < 3) return;
      const f = toF32(e.data.slice(1), 0);
      monitor.port.postMessage({ type: "push", pcm: f.buffer }, [f.buffer]);
    };
  }

  function setBoth(v) {
    both = v;
    const b = $("both");
    b.setAttribute("aria-pressed", String(v));
    b.querySelector(".sound-label").textContent = v ? "Hearing both phones" : "Hearing only your phone";
    if (!v && monitor) monitor.port.postMessage({ type: "clear" });
  }

  async function keepAwake() {
    try { if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen"); } catch { /* fine */ }
  }

  function teardown() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (ws && ws.readyState <= 1) ws.close(1000);
    if (mws && mws.readyState <= 1) mws.close(1000);
    if (wakeLock) wakeLock.release().catch(() => {});
    setTimeout(() => { if (ctx && ctx.state !== "closed") ctx.close(); }, 1500);
    stream = capture = wakeLock = null;
  }

  function fail(text) {
    started = false;
    teardown();
    setBodyState("idle");
    pill(text);
  }

  function finish(text) {
    if (ended) return;
    ended = true;
    stopQuiet();
    teardown();
    source.close();
    setBodyState("ended");
    pill(text);
    $("leave").hidden = true;
    setPhone("a", "idle", "Call ended");
    setPhone("b", "idle", "Call ended");
    setLink(null, "Call ended");
    if (turns.length) showOutcome();
  }

  // --- outcome -----------------------------------------------------------------------
  async function showOutcome() {
    $("outcome").hidden = false;
    $("oc-wait").hidden = false;
    $("oc-wait").textContent = "Writing up what you agreed…";
    let out = null;
    try {
      const r = await fetch(root.dataset.outcome, { method: "POST" });
      out = r.ok ? (await r.json()).outcome : null;
    } catch { out = null; }
    for (const side of ["a", "b"]) {
      const box = document.querySelector(`.try-oc-side[data-side="${side}"]`);
      const part = out && out[side];
      box.querySelector("h3").textContent = part ? part.headline : "";
      box.querySelector("ul").replaceChildren(...(part ? part.points : []).map((x) => el("li", null, x)));
      box.hidden = !part;
    }
    if (out) $("oc-wait").hidden = true;
    else $("oc-wait").textContent = `${turns.length} lines interpreted. No written summary this time.`;
  }

  $("start").addEventListener("click", start);
  $("both").addEventListener("click", () => setBoth(!both));
  $("leave").addEventListener("click", () => { if (ws && ws.readyState <= 1) ws.close(1000); finish("You ended the call."); });
  $("wrap").addEventListener("click", showOutcome);
  $("oc-close").addEventListener("click", () => { $("outcome").hidden = true; });
})();

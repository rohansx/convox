// Convox landing page (templates/landing.html). The two phones play an interpreted call
// from landing/scenes.json: Convox's real output made by tools/landing_voices.py. Sound
// is off until the visitor turns it on (browsers block autoplay with sound); with sound
// off the call plays silently on the same timings. The call pauses when scrolled away.
(function () {
  "use strict";

  const BASE = "/static/console/landing/";
  const ORDER = ["auto", "delivery", "rent", "clinic"];
  const MAX_BUBBLES = 4;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const nav = document.querySelector(".nav");
  const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // --- the dashboard iframe: load near the viewport, scale a 1440 px page to fit -----
  const view = document.getElementById("dash-view");
  if (view) {
    const frame = view.querySelector("iframe");
    const fit = () => { frame.style.transform = `scale(${view.clientWidth / 1440})`; };
    window.addEventListener("resize", fit);
    fit();
    new IntersectionObserver((es, io) => {
      if (!es.some((e) => e.isIntersecting)) return;
      frame.src = frame.dataset.src;
      io.disconnect();
    }, { rootMargin: "400px" }).observe(view);
  }
  document.querySelectorAll(".num-card").forEach((n) =>
    new IntersectionObserver((es, io) => {
      if (es.some((e) => e.isIntersecting)) { n.classList.add("seen"); io.disconnect(); }
    }, { threshold: 0.4 }).observe(n)
  );

  // --- the call ------------------------------------------------------------------------
  const stage = document.getElementById("stage");
  if (!stage) return;
  const link = document.getElementById("link");
  const caption = document.getElementById("stage-caption");
  const toggles = document.querySelectorAll("[data-sound-toggle]");
  const q = (root, r) => root.querySelector(`[data-role="${r}"]`);
  const P = {};
  for (const side of ["a", "b"]) {
    const root = stage.querySelector(`.iphone[data-side="${side}"]`);
    P[side] = { root, convo: q(root, "convo"), status: q(root, "status"), timer: q(root, "timer"),
                name: q(root, "name"), avatar: q(root, "avatar"), lang: q(root, "lang") };
  }

  let SCENES = null;
  let scene = "auto";
  let auto = true;
  let soundOn = false;
  let visible = true;
  let run = 0;
  let t0 = performance.now();
  let current = null; // the playing Audio
  const clips = new Map();

  const first = (name) => name.split(" ")[0];
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  const stale = (id) => id !== run;
  const sleep = (ms, id) => new Promise((res, rej) =>
    setTimeout(() => (stale(id) ? rej("stale") : res()), ms));

  function clip(url) {
    if (!clips.has(url)) {
      const a = new Audio(BASE + url);
      a.preload = "auto";
      clips.set(url, a);
    }
    return clips.get(url);
  }
  function stopAudio() {
    if (current) { current.pause(); current = null; }
  }
  // plays a clip with sound on, else waits its length; resolves when it ends
  function play(url, ms, id) {
    if (!soundOn) return sleep(ms, id);
    const a = clip(url);
    a.currentTime = 0;
    current = a;
    return new Promise((res, rej) => {
      let done = false;
      const end = (ok) => { if (done) return; done = true; clearInterval(watch); ok ? res() : rej("stale"); };
      const watch = setInterval(() => { if (stale(id)) { a.pause(); end(false); } }, 100);
      a.onended = () => end(true);
      setTimeout(() => end(!stale(id)), ms + 800); // never hang on a stalled clip
      a.play().catch(() => sleep(ms, id).then(() => end(true), () => end(false)));
    });
  }

  function bubble(p, kind, text) {
    const b = el("div", `bubble ${kind}`);
    const body = el("span", null, text);
    b.append(body);
    p.convo.append(b);
    while (p.convo.children.length > MAX_BUBBLES) p.convo.firstElementChild.remove();
    return { b, body };
  }

  function setState(side, state, text) {
    const p = P[side];
    p.root.classList.toggle("speaking", state === "speaking");
    p.root.classList.toggle("hearing", state === "hearing");
    p.status.textContent = text;
  }

  function setLink(dir, title, timing) {
    if (dir) link.dataset.dir = dir; else delete link.dataset.dir;
    q(link, "dir").textContent = title;
    q(link, "timing").innerHTML = timing || "&nbsp;";
  }

  function setScene(key) {
    const s = SCENES[key];
    scene = key;
    for (const side of ["a", "b"]) {
      const p = P[side], who = s[side];
      p.name.textContent = who.name;
      p.avatar.textContent = who.name.replace("Dr. ", "")[0];
      p.lang.textContent = who.native;
      p.convo.replaceChildren();
      setState(side, "idle", "Connected");
    }
    caption.textContent = s.caption;
    setLink(null, "Listening");
    for (const tab of stage.querySelectorAll(".tab"))
      tab.setAttribute("aria-selected", String(tab.dataset.scene === key));
    t0 = performance.now();
  }

  const digits = (s) => (s.match(/\d+/g) || []).join(" ");

  async function line(s, l, id) {
    const from = l.from, to = from === "a" ? "b" : "a";
    const me = s[from], other = s[to];
    // the speaker talks: words appear as they are recognised
    setState(from, "speaking", "Speaking…");
    setState(to, "idle", `${first(me.name)} is speaking`);
    setLink(null, "Listening", me.native);
    const mine = bubble(P[from], "mine", "");
    mine.body.classList.add("typing");
    const words = l.src.split(" ");
    const said = play(l.src_audio, l.src_ms, id);
    const per = Math.max(120, (l.src_ms - 250) / words.length);
    for (let i = 0; i < words.length; i++) {
      mine.body.textContent = words.slice(0, i + 1).join(" ");
      await sleep(per, id);
    }
    await said;
    mine.body.classList.remove("typing");
    // Convox interprets. The quiet is honest: the live median end-of-turn wait and
    // recognition (300 + 60 ms) plus this line's MT and TTS time, measured when generated.
    const quiet = 300 + 60 + l.mt_ms + l.tts_ttfb_ms;
    setState(from, "idle", "Interpreting…");
    setState(to, "idle", "Waiting for Convox…");
    const t0q = performance.now();
    const counting = setInterval(() => {
      q(link, "timing").textContent = `quiet ${((performance.now() - t0q) / 1000).toFixed(1)} s`;
    }, 100);
    setLink(from + to, `${me.native} → ${other.native}`, "quiet 0.0 s");
    try { await sleep(quiet, id); } finally { clearInterval(counting); }
    setLink(from + to, `${me.native} → ${other.native}`,
      `quiet ${(quiet / 1000).toFixed(1)} s<br>translate ${(l.mt_ms / 1000).toFixed(1)} · voice ${(l.tts_ttfb_ms / 1000).toFixed(1)}`);
    // the listener hears it in their language
    setState(to, "hearing", `Hearing ${first(me.name)} in ${other.native}`);
    const heard = bubble(P[to], "heard", l.tgt);
    const meta = el("div", "meta");
    meta.append(el("span", null, `${first(me.name)} · after ${(quiet / 1000).toFixed(1)} s`));
    const d = digits(l.src);
    if (d && d === digits(l.tgt)) meta.append(el("span", "ok", `✓ ${d}`));
    heard.b.append(meta);
    await play(l.tgt_audio, l.tgt_ms, id);
    setState(to, "idle", "Connected");
    setLink(null, "Listening");
    await sleep(500, id);
  }

  async function start(key) {
    const id = ++run;
    stopAudio();
    setScene(key);
    if (!visible) return;
    try {
      await sleep(600, id);
      for (const l of SCENES[key].lines) await line(SCENES[key], l, id);
      await sleep(2200, id);
      start(auto ? ORDER[(ORDER.indexOf(key) + 1) % ORDER.length] : key);
    } catch (e) {
      if (e !== "stale") throw e;
    }
  }

  function staticScene(key) {
    run++;
    setScene(key);
    for (const l of SCENES[key].lines) {
      bubble(P[l.from], "mine", l.src);
      bubble(P[l.from === "a" ? "b" : "a"], "heard", l.tgt);
    }
  }

  function setSound(on) {
    soundOn = on;
    for (const t of toggles) {
      if (!t.classList.contains("sound")) continue;
      t.setAttribute("aria-pressed", String(on));
      t.querySelector(".sound-label").textContent = on ? "Sound on" : "Turn sound on to hear the call";
    }
    if (!on) stopAudio();
    if (SCENES && !reduced) start(scene); // restart the call so the audio starts with a line
  }

  toggles.forEach((t) => t.addEventListener("click", () => {
    if (t.hasAttribute("data-scroll")) {
      stage.scrollIntoView({ behavior: "smooth", block: "center" });
      if (!soundOn) setSound(true);
      return;
    }
    setSound(!soundOn);
  }));
  stage.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    auto = false;
    if (reduced) staticScene(tab.dataset.scene); else start(tab.dataset.scene);
  }));

  setInterval(() => {
    const s = Math.floor((performance.now() - t0) / 1000);
    const txt = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    P.a.timer.textContent = txt;
    P.b.timer.textContent = txt;
  }, 500);

  new IntersectionObserver((es) => {
    const now = es.some((e) => e.isIntersecting);
    if (now === visible) return;
    visible = now;
    if (!SCENES || reduced) return;
    if (visible) start(scene);
    else { run++; stopAudio(); }
  }, { threshold: 0.25 }).observe(stage);

  fetch(BASE + "scenes.json")
    .then((r) => r.json())
    .then((data) => {
      SCENES = data.scenes;
      if (reduced) staticScene(scene); else start(scene);
    })
    .catch((err) => console.error("convox: scenes", err));
})();

// Ward mode: push-to-talk interpreter on one phone. Protocol: dubhash/ward/routes.py.
(() => {
  'use strict';

  const PREROLL_FRAMES = 8; // 160 ms before the press, so the first syllable is not cut
  const TAIL_MS = 250; // keep sending after release, people let go mid-word
  const MIN_PRESS_MS = 350;

  const $ = (id) => document.getElementById(id);
  const app = $('app');
  const token = app.dataset.wsToken || '';
  const pads = { a: $('pad-a'), b: $('pad-b') };
  const selects = { a: $('lang-a'), b: $('lang-b') };
  const transcriptEl = $('transcript');

  let ws = null, ctx = null, capture = null, player = null, stream = null;
  let ready = false, wantOpen = false, retry = 0;
  let active = null; // { side, t0, sending }
  let releaseTimer = null;
  const preroll = [];
  const pending = []; // turns awaiting audio, in order: { el, tEnd, gotAudio }
  let lastTurn = null;
  let lastEndAt = 0; // performance.now() when the last 'end' was sent
  let hintTimer = null;

  // ---------------------------------------------------------------- labels

  function langParts(side) {
    const opt = selects[side].selectedOptions[0];
    const [native, english] = opt.textContent.split(' · ');
    return { code: opt.value, native: native.trim(), english: (english || '').trim() };
  }

  function renderPadLabels() {
    for (const side of ['a', 'b']) {
      const l = langParts(side);
      const el = $('pad-lang-' + side);
      el.textContent = l.native;
      if (l.english) {
        const small = document.createElement('small');
        small.textContent = l.english;
        el.appendChild(small);
      }
    }
  }

  function setPadState(side, text) { $('pad-state-' + side).textContent = text; }

  function hint(text, ms = 3500) {
    const el = $('hint');
    el.textContent = text;
    el.classList.toggle('show', !!text);
    clearTimeout(hintTimer);
    if (text && ms) hintTimer = setTimeout(() => hint(''), ms);
  }

  function setConn(state) { $('conn').className = 'dot ' + state; }

  // ---------------------------------------------------------------- audio

  async function setupAudio() {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    ctx = new AudioContext({ latencyHint: 'interactive' });
    const src = $('ward-worklet').textContent;
    await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
    capture = new AudioWorkletNode(ctx, 'ward-capture', { numberOfInputs: 1, numberOfOutputs: 1 });
    player = new AudioWorkletNode(ctx, 'ward-player', { numberOfInputs: 0, outputChannelCount: [1] });
    const sink = ctx.createGain();
    sink.gain.value = 0; // keeps the capture node pulled by the graph in every browser
    ctx.createMediaStreamSource(stream).connect(capture).connect(sink).connect(ctx.destination);
    player.connect(ctx.destination);
    await ctx.resume();

    capture.port.onmessage = (e) => {
      if (e.data.type !== 'frame') return;
      if (active && active.sending) {
        $('meter-' + active.side).style.width = Math.min(100, e.data.peak * 160) + '%';
        if (ws && ws.readyState === 1) ws.send(e.data.pcm);
      } else {
        preroll.push(e.data.pcm);
        if (preroll.length > PREROLL_FRAMES) preroll.shift();
      }
    };
    player.port.onmessage = (e) => {
      if (e.data.type === 'started') app.classList.add('speaking');
      else if (e.data.type === 'idle') app.classList.remove('speaking');
    };
  }

  function playPcm16(buf) {
    if (!player) return;
    const i16 = new Int16Array(buf, 0, buf.byteLength >> 1);
    const f32 = new Float32Array(i16.length);
    for (let k = 0; k < i16.length; k++) f32[k] = i16[k] / 32768;
    player.port.postMessage({ type: 'push', pcm: f32.buffer }, [f32.buffer]);
  }

  function stopPlayback() {
    if (player) player.port.postMessage({ type: 'clear' });
    app.classList.remove('speaking');
  }

  // ---------------------------------------------------------------- socket

  function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

  function sendConfig() { send({ type: 'config', a: selects.a.value, b: selects.b.value }); }

  function connect() {
    wantOpen = true;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/ward?token=${encodeURIComponent(token)}`);
    ws.binaryType = 'arraybuffer';
    setConn('wait');
    ws.onopen = () => { retry = 0; sendConfig(); };
    ws.onmessage = (e) => (typeof e.data === 'string' ? onJson(JSON.parse(e.data)) : onAudio(e.data));
    ws.onclose = (e) => {
      ready = false;
      setConn('off');
      pending.length = 0;
      for (const s of ['a', 'b']) setPadState(s, 'Hold to talk');
      if (e.code === 1008) { hint('Session expired. Reload the page.', 0); return; }
      if (!wantOpen) return;
      hint('Reconnecting…', 0);
      setTimeout(connect, Math.min(5000, 500 * 2 ** retry++));
    };
  }

  function onJson(m) {
    switch (m.type) {
      case 'ready':
        ready = true; setConn('on'); hint(''); renderPadLabels();
        break;
      case 'turn': {
        const el = addBubble(m);
        pending.push({ el, tEnd: lastEndAt, gotAudio: false });
        setPadState(m.side, 'Hold to talk');
        break;
      }
      case 'audio_end': {
        const p = pending.shift();
        if (p) setLatency(p.el, p.gotAudio ? p.firstMs : null, m.timings);
        break;
      }
      case 'empty':
        setPadState(m.side, 'Hold to talk');
        hint("Didn't catch that. Hold the button while you speak.");
        break;
      case 'transcript':
        download(m);
        break;
      case 'error':
        for (const s of ['a', 'b']) if (!active || active.side !== s) setPadState(s, 'Hold to talk');
        hint(m.message);
        break;
    }
  }

  function onAudio(buf) {
    const p = pending[0];
    if (p && !p.gotAudio) {
      p.gotAudio = true;
      p.firstMs = Math.round(performance.now() - p.tEnd);
    }
    if (!active) playPcm16(buf); // a new press silences whatever was still to come
  }

  // ---------------------------------------------------------------- push to talk

  function pressStart(side) {
    if (!ready) { hint('Connecting…'); return; }
    if (active) return;
    clearTimeout(releaseTimer);
    stopPlayback();
    active = { side, t0: performance.now(), sending: true };
    send({ type: 'start', side });
    for (const f of preroll.splice(0)) ws.send(f);
    pads[side].classList.add('rec');
    app.classList.add('recording');
    setPadState(side, 'Listening… release to translate');
    if (navigator.vibrate) navigator.vibrate(15);
  }

  function pressEnd(side) {
    if (!active || active.side !== side || !active.sending) return;
    const held = performance.now() - active.t0;
    pads[side].classList.remove('rec');
    app.classList.remove('recording');
    $('meter-' + side).style.width = '0';
    if (held < MIN_PRESS_MS) {
      active = null;
      send({ type: 'cancel' });
      setPadState(side, 'Hold to talk');
      hint('Hold the button while you speak, then release.');
      return;
    }
    setPadState(side, 'Translating…');
    releaseTimer = setTimeout(() => {
      active = null;
      lastEndAt = performance.now();
      send({ type: 'end' });
    }, TAIL_MS);
  }

  for (const side of ['a', 'b']) {
    const pad = pads[side];
    pad.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      if (pad.setPointerCapture) pad.setPointerCapture(e.pointerId);
      pressStart(side);
    });
    for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      pad.addEventListener(ev, () => pressEnd(side));
    }
    pad.addEventListener('keydown', (e) => {
      if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); pressStart(side); }
    });
    pad.addEventListener('keyup', (e) => {
      if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); pressEnd(side); }
    });
    pad.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  // ---------------------------------------------------------------- transcript

  const NUM = /\p{Nd}+(?:[.,:/]\p{Nd}+)*/gu;

  function withNumbers(el, text) {
    let last = 0;
    for (const m of text.matchAll(NUM)) {
      el.append(text.slice(last, m.index));
      const mark = document.createElement('mark');
      mark.textContent = m[0];
      el.append(mark);
      last = m.index + m[0].length;
    }
    el.append(text.slice(last));
    return el;
  }

  function node(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function addBubble(m) {
    $('empty-state').hidden = true;
    const src = langParts(m.side), tgt = langParts(m.side === 'a' ? 'b' : 'a');
    const b = node('article', `bubble from-${m.side}` + (m.warning ? ' warn' : ''));
    b.append(node('div', 'who', `${src.native} → ${tgt.native}`));
    b.append(withNumbers(node('p', 'src'), m.src_text));
    const t = withNumbers(node('p', 'tgt'), m.tgt_text);
    t.lang = (m.tgt_lang || '').split('-')[0];
    b.append(t);
    if (m.warning) {
      const w = node('p', 'warning');
      w.append(node('strong', '', 'Check numbers. '), m.warning.replace(/^numbers differ: /, 'Differs: '));
      b.append(w);
    }
    b.append(node('div', 'meta', 'playing…'));
    transcriptEl.append(b);
    b.scrollIntoView({ behavior: 'smooth', block: 'end' });
    lastTurn = b;
    return b;
  }

  function setLatency(el, firstMs, timings) {
    const meta = el.querySelector('.meta');
    const parts = [];
    if (timings && timings.stt_ms !== undefined) parts.push(`stt ${timings.stt_ms}`);
    if (timings && timings.mt_ms !== undefined) parts.push(`mt ${timings.mt_ms}`);
    if (timings && timings.tts_ttfb_ms !== undefined) parts.push(`tts ${timings.tts_ttfb_ms}`);
    const ms = firstMs != null ? firstMs : timings && timings.first_audio_ms;
    meta.textContent = (ms != null ? `${(ms / 1000).toFixed(2)} s to voice` : '') +
      (parts.length ? ` · ${parts.join(' · ')} ms` : '');
  }

  // ---------------------------------------------------------------- save / new

  function stamp() {
    const d = new Date(), p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
  }

  function save(name, type, body) {
    const url = URL.createObjectURL(new Blob([body], { type }));
    const a = Object.assign(document.createElement('a'), { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function download(m) {
    if (!m.json.turns.length) { hint('Nothing to save yet.'); return; }
    const base = `ward-${stamp()}`;
    save(`${base}.txt`, 'text/plain;charset=utf-8', m.text);
    save(`${base}.json`, 'application/json', JSON.stringify(m.json, null, 2));
    hint('Transcript saved (.txt and .json).');
  }

  $('save-btn').onclick = () => { if (ready) send({ type: 'export' }); else hint('Not connected.'); };

  $('new-btn').onclick = () => {
    if (lastTurn && !confirm('Start a new patient? The current transcript is cleared. Save it first if needed.')) return;
    send({ type: 'reset' });
    stopPlayback();
    pending.length = 0;
    transcriptEl.querySelectorAll('.bubble').forEach((el) => el.remove());
    $('empty-state').hidden = false;
    lastTurn = null;
  };

  // ---------------------------------------------------------------- languages

  function onLangChange(side) {
    const other = side === 'a' ? 'b' : 'a';
    if (selects.a.value === selects.b.value) {
      selects[other].value = selects[other].dataset.prev || (selects[side].value === 'hi-IN' ? 'mr-IN' : 'hi-IN');
    }
    for (const s of ['a', 'b']) selects[s].dataset.prev = selects[s].value;
    renderPadLabels();
    try { localStorage.setItem('dubhash.ward.langs', JSON.stringify([selects.a.value, selects.b.value])); } catch {}
    if (ready) sendConfig();
  }

  selects.a.onchange = () => onLangChange('a');
  selects.b.onchange = () => onLangChange('b');
  $('swap-btn').onclick = () => {
    const a = selects.a.value;
    selects.a.value = selects.b.value;
    selects.b.value = a;
    onLangChange('a');
  };

  try {
    const saved = JSON.parse(localStorage.getItem('dubhash.ward.langs') || 'null');
    if (Array.isArray(saved) && saved[0] !== saved[1]) {
      selects.a.value = saved[0] || selects.a.value;
      selects.b.value = saved[1] || selects.b.value;
    }
  } catch {}
  for (const s of ['a', 'b']) selects[s].dataset.prev = selects[s].value;
  renderPadLabels();

  // ---------------------------------------------------------------- start

  $('start-btn').onclick = async () => {
    const btn = $('start-btn');
    btn.disabled = true;
    try {
      await setupAudio();
    } catch (err) {
      $('gate-msg').textContent = 'Microphone unavailable: ' + (err && err.message ? err.message : err);
      btn.disabled = false;
      return;
    }
    $('gate').hidden = true;
    connect();
  };

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && ctx && ctx.state === 'suspended') ctx.resume();
  });
})();

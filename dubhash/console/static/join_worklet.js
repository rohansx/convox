// Mic -> 16 kHz PCM16, 20 ms (320-sample) frames. Box-filter decimation from the context
// rate (48 kHz on most phones, 44.1 kHz on some). Copied from legs/static/loopback.html,
// plus a gate: while the phone is playing a translation (and for a short tail after) it
// sends silence, so the phone's own speaker is not heard as the user speaking.
class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.phase = 0; this.acc = 0; this.n = 0;
    this.frame = new Int16Array(320); this.i = 0;
    this.gated = false; this.peak = 0;
    this.port.onmessage = (e) => { if (e.data.type === 'gate') this.gated = e.data.on; };
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let k = 0; k < ch.length; k++) {
      this.acc += ch[k]; this.n++; this.phase += 1;
      if (this.phase >= this.ratio) {
        this.phase -= this.ratio;
        let v = this.acc / this.n;
        this.acc = 0; this.n = 0;
        v = Math.max(-1, Math.min(1, v));
        this.peak = Math.max(this.peak, Math.abs(v));
        if (this.gated) v = 0;
        this.frame[this.i++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        if (this.i === 320) {
          const out = this.frame.buffer;
          this.port.postMessage({ type: 'frame', pcm: out, peak: this.peak, gated: this.gated }, [out]);
          this.frame = new Int16Array(320); this.i = 0; this.peak = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('capture', Capture);

// 16 kHz chunks and marks, played at the context rate with linear interpolation. A mark is
// acked when playout reaches it; clear drops audio and pending marks. Reports when it
// starts and stops playing so the page can gate the mic and show "speaking".
class Player extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = 16000 / sampleRate;
    this.q = []; this.pos = 0; this.playing = false;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === 'push') this.q.push({ s: new Float32Array(m.pcm) });
      else if (m.type === 'mark') this.q.push({ mark: m.name });
      else if (m.type === 'clear') { this.q = []; this.pos = 0; }
    };
  }
  drainMarks() {
    while (this.q.length && this.q[0].mark !== undefined) {
      this.port.postMessage({ type: 'mark', name: this.q.shift().mark });
    }
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    let played = false;
    for (let k = 0; k < out.length; k++) {
      this.drainMarks();
      const head = this.q[0];
      if (!head) { out[k] = 0; continue; }
      played = true;
      const s = head.s, i = Math.floor(this.pos), f = this.pos - i;
      const a = s[i], b = i + 1 < s.length ? s[i + 1] : a;
      out[k] = a + (b - a) * f;
      this.pos += this.step;
      if (this.pos >= s.length) { this.pos -= s.length; this.q.shift(); }
    }
    this.drainMarks();
    if (played !== this.playing) {
      this.playing = played;
      this.port.postMessage({ type: 'playing', on: played });
    }
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);
    return true;
  }
}
registerProcessor('player', Player);

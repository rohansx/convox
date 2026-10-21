// Runs join_phone.js under node with stand-ins for the DOM, Web Audio, getUserMedia,
// WebSocket and EventSource. Taps "start", opens the socket, then plays a short script:
// 25 mic frames (500 ms), a server ping, a translation's play_start/play_end/mark from the
// player worklet. Prints what the page sent and the getUserMedia constraints.
const fs = require("fs");
const el = () => ({
  dataset: {}, style: { setProperty() {} }, hidden: false, textContent: "", children: [],
  listeners: {}, addEventListener(k, f) { this.listeners[k] = f; }, append() {},
  querySelector() { return el(); }, remove() {},
});
const els = {};
const root = el();
root.dataset = {
  side: "a", lang: "mr-IN", ui: "{}", ws: "/ws/loopback?call=c_x&side=a&t=t",
  worklet: "/w.js", events: "/e",
};
els.phone = root;
globalThis.document = {
  getElementById: (id) => els[id] || (els[id] = el()),
  body: { dataset: {}, style: { setProperty() {} } },
  addEventListener() {}, visibilityState: "visible", createElement: el,
};
globalThis.window = globalThis;
globalThis.isSecureContext = true;
globalThis.location = { protocol: "https:", host: "x", search: "" };
let now = 1000;
Object.defineProperty(globalThis, "performance", {
  value: { timeOrigin: 1.7e12, now: () => now }, configurable: true,
});
const sent = [];
const nodes = {};
class Port { constructor() { this.posted = []; } postMessage(m) { this.posted.push(m); } }
globalThis.AudioWorkletNode = class {
  constructor(ctx, name) { this.port = new Port(); nodes[name] = this; }
  connect(x) { return x || this; }
};
globalThis.AudioContext = class {
  constructor() {
    this.sampleRate = 48000; this.state = "running"; this.currentTime = 2.0;
    this.audioWorklet = { addModule: async () => {} }; this.destination = {};
  }
  createBuffer() { return {}; }
  createBufferSource() { return { connect() {}, start() {} }; }
  createGain() { return { gain: {}, connect(x) { return x; } }; }
  createMediaStreamSource() { return { connect(x) { return x; } }; }
  getOutputTimestamp() { return { contextTime: 2.0, performanceTime: 900 }; }
  resume() { return Promise.resolve(); }
  close() {}
};
let constraints = null;
Object.defineProperty(globalThis, "navigator", {
  value: {
    mediaDevices: { getUserMedia: async (c) => { constraints = c; return { getTracks: () => [] }; } },
  },
  configurable: true,
});
let ws = null;
globalThis.WebSocket = class {
  constructor(url) { this.url = url; this.readyState = 1; ws = this; }
  send(m) { sent.push(m); }
  close() {}
};
globalThis.EventSource = class { constructor() {} addEventListener() {} close() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  await els.tap.listeners.click();
  ws.onopen();
  for (let i = 0; i < 25; i++) {
    now += 20;
    nodes.capture.port.onmessage({
      data: { type: "frame", pcm: new ArrayBuffer(640), peak: 0.2, rms: 0.1, gated: false },
    });
  }
  ws.onmessage({ data: JSON.stringify({ type: "ping", server_ms: 4242 }) });
  nodes.player.port.onmessage({ data: { type: "play_start", name: "b:0", ctx_ms: 2100 } });
  nodes.player.port.onmessage({ data: { type: "play_end", name: "b:0", ctx_ms: 3100 } });
  nodes.player.port.onmessage({ data: { type: "mark", name: "b:0" } });
  console.log(JSON.stringify({
    sent: sent.filter((m) => typeof m === "string").map((m) => JSON.parse(m)),
    binary: sent.filter((m) => typeof m !== "string").length,
    constraints,
  }));
})().catch((e) => { console.error(e); process.exit(1); });

// Runs join_worklet.js's Capture under node at 16 kHz with the gate on: one 20 ms frame
// of a constant 0.5 signal. Prints the frame's rms, gated flag and whether it is zeros.
const fs = require("fs");
const procs = {};
globalThis.sampleRate = 16000;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { posted: [], postMessage(m) { this.posted.push(m); } }; }
};
globalThis.registerProcessor = (name, cls) => { procs[name] = cls; };
eval(fs.readFileSync(process.argv[2], "utf8"));
const c = new procs.capture();
c.port.onmessage({ data: { type: "gate", on: true } });
const ch = new Float32Array(128).fill(0.5);
for (let i = 0; i < 3; i++) c.process([[ch]]);
const f = c.port.posted.find((m) => m.type === "frame");
console.log(JSON.stringify({
  rms: f.rms, gated: f.gated, zero: new Int16Array(f.pcm).every((x) => x === 0),
}));

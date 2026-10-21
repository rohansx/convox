// Runs join_worklet.js's Player under node: argv[2] = worklet path, argv[3] = JSON steps
// [["push", samples] | ["mark", name] | ["clear"] | ["blocks", n]]. Prints the messages
// the processor posted and, per rendered 128-sample block, whether it was non-silent.
const fs = require("fs");
const procs = {};
globalThis.sampleRate = 48000;
globalThis.currentFrame = 0;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { posted: [], postMessage(m) { this.posted.push(m); } }; }
};
globalThis.registerProcessor = (name, cls) => { procs[name] = cls; };
eval(fs.readFileSync(process.argv[2], "utf8"));
const out = [];
const p = new procs.player();
const send = (m) => p.port.onmessage({ data: m });
const block = () => {
  const o = [[new Float32Array(128)]];
  p.process([], o);
  globalThis.currentFrame += 128;
  return o[0][0];
};
const pcm = (n) => new Float32Array(n).fill(0.25).buffer;
for (const s of JSON.parse(process.argv[3])) {
  if (s[0] === "push") send({ type: "push", pcm: pcm(s[1]) });
  else if (s[0] === "mark") send({ type: "mark", name: s[1] });
  else if (s[0] === "clear") send({ type: "clear" });
  else if (s[0] === "blocks") {
    for (let i = 0; i < s[1]; i++) out.push(["block", block().some((x) => x !== 0)]);
  }
}
console.log(JSON.stringify({ posted: p.port.posted, out }));

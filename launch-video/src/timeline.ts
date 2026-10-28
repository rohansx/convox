// The video's clock, built from the real call (call.json, tools/launch_audio.py): every
// line starts when the previous one has finished, and every translation arrives after
// the pipeline's own delay (end of turn + recognition + translation + first audio).
import call from "./call.json";

export const FPS = 30;
export const sec = (s: number) => Math.round(s * FPS);
const msf = (ms: number) => Math.round((ms / 1000) * FPS);

export type Side = "a" | "b";
export const other = (s: Side): Side => (s === "a" ? "b" : "a");

export const PEOPLE = {
  a: { name: "Basavaraj", role: "Auto driver", native: "ಕನ್ನಡ", english: "Kannada" },
  b: { name: "Neha", role: "Passenger", native: "हिन्दी", english: "Hindi" },
} as const;

// the pipeline's live medians (out/live, 65 turns): end of turn 300 ms, recognition 60 ms
const EOU_MS = 300;
const STT_MS = 60;

export type Line = {
  speaker: Side; // who said it
  plays: Side; // whose phone it comes out of: the listener's
  text: string; // what the listener's phone shows
  said: string; // what the speaker said
  en: string;
  audio: string;
  from: number; // frame, relative to the scene
  dur: number;
  kind: "raw" | "src" | "tgt";
  delayMs?: number;
};

const GAP = sec(0.5);

function without() {
  const lines: Line[] = [];
  let t = sec(1.4);
  for (const r of call.without) {
    const s = r.side as Side;
    const dur = msf(r.ms);
    lines.push({ speaker: s, plays: other(s), text: r.text, said: r.text, en: r.en, audio: r.audio, from: t, dur, kind: "raw" });
    t += dur + GAP;
  }
  const fail = t + sec(0.2);
  return { lines, fail, length: fail + sec(3.4) };
}

function withConvox() {
  const lines: Line[] = [];
  let t = sec(1.2);
  for (const r of call.with) {
    const s = r.side as Side;
    const srcDur = msf(r.src_ms);
    const delayMs = EOU_MS + STT_MS + r.mt_ms + r.tts_ms;
    lines.push({ speaker: s, plays: s, text: r.src_text, said: r.src_text, en: r.en, audio: r.src_audio, from: t, dur: srcDur, kind: "src" });
    const at = t + srcDur + msf(delayMs);
    const tgtDur = msf(r.tgt_ms);
    lines.push({ speaker: s, plays: other(s), text: r.tgt_text, said: r.src_text, en: r.en, audio: r.tgt_audio, from: at, dur: tgtDur, kind: "tgt", delayMs });
    t = at + tgtDur + sec(0.6);
  }
  const done = t + sec(0.1);
  return { lines, done, length: done + sec(3.6) };
}

export const WITHOUT = without();
export const WITH = withConvox();

export const SCENES = {
  intro: sec(7),
  without: WITHOUT.length,
  title: sec(3.2),
  with: WITH.length,
  outro: sec(7),
};

export const TOTAL = Object.values(SCENES).reduce((a, b) => a + b, 0);

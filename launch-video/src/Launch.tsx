// The launch video: Basavaraj drives an auto in Bengaluru and speaks Kannada; Neha, his
// passenger, speaks Hindi. The call fails without Convox, then works with it. Every voice,
// translation and delay is from the real pipeline (tools/launch_audio.py).
import React from "react";
import { AbsoluteFill, Audio, interpolate, Sequence, Series, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Bubble, Phone } from "./Phone";
import { FPS, Line, other, PEOPLE, SCENES, sec, Side, WITH, WITHOUT } from "./timeline";
import { C, fade, Logo, Mark, MONO, Pill, SANS, StageArt, useIn } from "./ui";

const useVertical = () => {
  const { width, height } = useVideoConfig();
  return height > width;
};

export const Launch: React.FC = () => (
  <AbsoluteFill style={{ background: C.bg, fontFamily: SANS }}>
    <Series>
      <Series.Sequence durationInFrames={SCENES.intro}>
        <Intro />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.without}>
        <Call mode="without" />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.title}>
        <Title />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.with}>
        <Call mode="with" />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.outro}>
        <Outro />
      </Series.Sequence>
    </Series>
  </AbsoluteFill>
);

// --- 1. who they are ---------------------------------------------------------------------

const LANGS = ["ಕನ್ನಡ", "हिन्दी", "मराठी", "ગુજરાતી", "தமிழ்", "తెలుగు", "English"];

const Intro: React.FC = () => {
  const frame = useCurrentFrame();
  const v = useVertical();
  const head = useIn(sec(0.9));
  const out = interpolate(frame, [SCENES.intro - 10, SCENES.intro], [1, 0], { extrapolateLeft: "clamp" });
  return (
    <AbsoluteFill style={{ background: C.bg, padding: v ? "0 80px" : "0 140px", justifyContent: "center", opacity: out }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: v ? 18 : 26, fontSize: v ? 34 : 30, color: C.muted, marginBottom: 48 }}>
        {LANGS.map((l, i) => (
          <span key={l} style={{ opacity: fade(frame, 4 + i * 4, 10) }}>
            {l}
            {i < LANGS.length - 1 && <span style={{ color: C.faint, marginLeft: v ? 18 : 26 }}>·</span>}
          </span>
        ))}
      </div>
      <h1
        style={{
          margin: 0,
          fontWeight: 400,
          fontSize: v ? 84 : 92,
          lineHeight: 1.04,
          letterSpacing: "-.045em",
          color: C.ink,
          maxWidth: v ? 920 : 1400,
          opacity: head,
          transform: `translateY(${(1 - head) * 24}px)`,
        }}
      >
        In Bengaluru, the driver and the passenger
        <span style={{ color: C.faint }}> often don't share a language.</span>
      </h1>
      <div style={{ display: "flex", flexDirection: v ? "column" : "row", gap: 28, marginTop: 64 }}>
        <Person side="a" from={sec(3)} />
        <Person side="b" from={sec(3.6)} />
      </div>
    </AbsoluteFill>
  );
};

const AV = { a: ["#ffa06b", "#e2552a"], b: ["#8fa1ff", "#4152d6"] };
const ABOUT = { a: "Drives an auto, born and raised here", b: "Moved from Delhi for work last month" };

const Person: React.FC<{ side: Side; from: number }> = ({ side, from }) => {
  const k = useIn(from);
  const p = PEOPLE[side];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 22, padding: "22px 30px 22px 22px", borderRadius: 22, background: "#fff", border: `1px solid ${C.line}`, boxShadow: "0 1px 2px rgba(31,28,23,.06), 0 14px 34px -18px rgba(31,28,23,.28)", opacity: k, transform: `translateY(${(1 - k) * 20}px)`, minWidth: 560 }}>
      <div style={{ width: 76, height: 76, borderRadius: "50%", display: "grid", placeItems: "center", color: "#fff", fontSize: 34, fontWeight: 500, background: `linear-gradient(160deg, ${AV[side][0]}, ${AV[side][1]})` }}>{p.name[0]}</div>
      <div>
        <div style={{ fontSize: 34, fontWeight: 500, letterSpacing: "-.02em", color: C.ink }}>
          {p.name} <span style={{ color: C.muted, fontWeight: 400, fontSize: 26 }}>· {p.role}</span>
        </div>
        <div style={{ fontSize: 24, color: C.ink2, marginTop: 2 }}>
          {ABOUT[side]}. Speaks only <b style={{ fontWeight: 500, color: C.accent }}>{p.native}</b>.
        </div>
      </div>
    </div>
  );
};

// --- 2 & 4. the call -----------------------------------------------------------------------

type Mode = "without" | "with";

function phoneState(lines: Line[], side: Side, frame: number, mode: Mode) {
  const bubbles: Bubble[] = [];
  type Now = { mode: "speaking" | "hearing"; audio: { src: string; from: number } };
  let now = null as Now | null;
  let status: React.ReactNode = mode === "with" ? "Convox is on the call" : "Connected";
  const them = PEOPLE[other(side)];
  lines.forEach((l, i) => {
    if (l.from > frame) return;
    const playing = frame < l.from + l.dur;
    if (l.kind === "raw") {
      if (l.speaker === side) {
        bubbles.push({ key: `r${i}`, text: l.said, mine: true, from: l.from });
        if (playing) now = { mode: "speaking", audio: { src: l.audio, from: l.from } };
      } else {
        bubbles.push({ key: `r${i}`, text: l.said, mine: false, from: l.from, meta: <span style={{ color: "#fca5a5" }}>✕ can't understand</span> });
        if (playing) status = `Hearing ${them.english}…`;
      }
    } else if (l.kind === "src" && l.speaker === side) {
      bubbles.push({ key: `s${i}`, text: l.said, mine: true, from: l.from });
      if (playing) now = { mode: "speaking", audio: { src: l.audio, from: l.from } };
      else status = `${them.name} hears you in ${them.native}`;
    } else if (l.kind === "src" && l.speaker !== side) {
      status = `${them.name} is speaking…`;
    } else if (l.kind === "tgt" && l.plays === side) {
      bubbles.push({
        key: `t${i}`,
        text: l.text,
        mine: false,
        from: l.from,
        meta: (
          <>
            <span style={{ color: "#4ade80" }}>✓</span>
            <span>{((l.delayMs ?? 0) / 1000).toFixed(1)} s</span>
          </>
        ),
      });
      if (playing) {
        now = { mode: "hearing", audio: { src: l.audio, from: l.from } };
        status = `Convox · in ${PEOPLE[side].native}`;
      } else status = "Convox is on the call";
    }
  });
  return { bubbles: bubbles.slice(-4), now: now as Now | null, status };
}

const Call: React.FC<{ mode: Mode }> = ({ mode }) => {
  const frame = useCurrentFrame();
  const v = useVertical();
  const T = mode === "without" ? WITHOUT : WITH;
  const lines = T.lines;
  const end = mode === "without" ? WITHOUT.fail : WITH.done;
  const pw = v ? 400 : 360;
  const enter = useIn(0, 20);
  const exit = interpolate(frame, [T.length - 10, T.length], [1, 0], { extrapolateLeft: "clamp" });
  const cold = mode === "without" ? interpolate(frame, [end, end + 20], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 0;
  const state = (s: Side) => phoneState(lines, s, frame, mode);
  const a = state("a");
  const b = state("b");
  const over = frame >= end;
  const seconds = frame / FPS + (mode === "without" ? 2 : 4);
  return (
    <AbsoluteFill style={{ opacity: exit }}>
      <StageArt cold={cold} />
      {lines.map((l, i) => (
        <Sequence key={i} from={l.from} durationInFrames={l.dur + 2}>
          <Audio src={staticFile(l.audio)} />
        </Sequence>
      ))}
      <AbsoluteFill style={{ alignItems: "center", paddingTop: v ? 120 : 44 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 18, opacity: enter }}>
          {mode === "with" ? (
            <Pill dot={C.good}>With Convox</Pill>
          ) : (
            <Pill dot={C.bad}>Without Convox</Pill>
          )}
        </div>
      </AbsoluteFill>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", paddingBottom: v ? 40 : 30 }}>
        <div style={{ display: "flex", alignItems: "center", transform: `translateY(${(1 - enter) * 40}px) scale(${0.97 + 0.03 * enter})`, opacity: enter }}>
          <Phone side="a" pw={pw} name={PEOPLE.a.name} native={PEOPLE.a.native} tag="Driver's phone" bubbles={a.bubbles} mode={over ? null : a.now?.mode ?? null} audio={a.now?.audio} status={over ? (mode === "without" ? "Call ended" : "Pickup confirmed") : a.status} seconds={seconds} />
          <Link mode={mode} lines={lines} frame={frame} width={v ? 180 : 250} over={over} />
          <Phone side="b" pw={pw} name={PEOPLE.b.name} native={PEOPLE.b.native} tag="Passenger's phone" bubbles={b.bubbles} mode={over ? null : b.now?.mode ?? null} audio={b.now?.audio} status={over ? (mode === "without" ? "Call ended" : "Waiting at gate 2") : b.status} seconds={seconds} />
        </div>
      </AbsoluteFill>
      <Caption lines={lines} frame={frame} over={over} mode={mode} />
      {over && <Verdict mode={mode} from={end} />}
    </AbsoluteFill>
  );
};

/** Between the phones: nothing joins them without Convox; with it, the interpreter. */
const Link: React.FC<{ mode: Mode; lines: Line[]; frame: number; width: number; over: boolean }> = ({ mode, lines, frame, width, over }) => {
  const dashed = (o: number) => ({ flex: 1, height: 2, background: `repeating-linear-gradient(90deg, rgba(255,255,255,${o}) 0 5px, transparent 5px 10px)`, position: "relative" as const });
  if (mode === "without") {
    return (
      <div style={{ width, display: "flex", alignItems: "center" }}>
        <div style={dashed(0.3)} />
        <div style={{ width: 58, height: 58, borderRadius: "50%", display: "grid", placeItems: "center", background: "rgba(255,255,255,.18)", border: "1px solid rgba(255,255,255,.35)", color: "#fff", fontSize: 26 }}>✕</div>
        <div style={dashed(0.3)} />
      </div>
    );
  }
  // which way the words are travelling, and what the card says
  let dir: "ab" | "ba" | null = null;
  let label: React.ReactNode = <>&nbsp;</>;
  let lit = 0;
  for (const l of lines) {
    if (l.kind === "src" && frame >= l.from + l.dur * 0.3) {
      const tgt = lines.find((x) => x.kind === "tgt" && x.said === l.said && x.speaker === l.speaker)!;
      if (frame < tgt.from + tgt.dur) {
        dir = l.speaker === "a" ? "ab" : "ba";
        label = `${PEOPLE[l.speaker].english} → ${PEOPLE[other(l.speaker)].english}`;
        lit = frame < tgt.from ? 1 : 0.6;
      } else label = `${((tgt.delayMs ?? 0) / 1000).toFixed(1)} s`;
    }
  }
  if (over) {
    dir = null;
    label = "4 of 4 lines";
  }
  const pulse = (delay: number) => {
    if (!dir) return null;
    const p = ((frame + delay) % 27) / 27;
    const x = dir === "ab" ? p : 1 - p;
    return <span style={{ position: "absolute", top: -4, left: `calc(${x * 100}% - 5px)`, width: 10, height: 10, borderRadius: "50%", background: "#fff", boxShadow: "0 0 12px #fff", opacity: Math.sin(p * Math.PI) }} />;
  };
  return (
    <div style={{ width, display: "flex", alignItems: "center" }}>
      <div style={dashed(0.55)}>{pulse(0)}</div>
      <div style={{ flex: "none", width: 150, display: "flex", flexDirection: "column", alignItems: "center", gap: 6, padding: "16px 10px", borderRadius: 20, background: "rgba(255,255,255,.92)", color: C.ink, boxShadow: `0 0 0 ${6 * lit}px rgba(255,255,255,.3), 0 14px 34px -14px rgba(60,25,10,.5)`, textAlign: "center" }}>
        <Mark size={34} />
        <b style={{ fontSize: 17, fontWeight: 500 }}>Convox</b>
        <span style={{ fontFamily: MONO, fontSize: 13.5, color: C.muted, whiteSpace: "nowrap" }}>{label}</span>
      </div>
      <div style={dashed(0.55)}>{pulse(13)}</div>
    </div>
  );
};

/** English subtitles: who is speaking, in what, and what they mean. */
const Caption: React.FC<{ lines: Line[]; frame: number; over: boolean; mode: Mode }> = ({ lines, frame, over, mode }) => {
  const v = useVertical();
  if (over) return null;
  const l = [...lines].reverse().find((x) => x.from <= frame && frame < x.from + x.dur + sec(mode === "with" ? 2 : 0.5));
  if (!l) return null;
  const k = fade(frame, l.from, 6);
  const who = PEOPLE[l.speaker];
  const to = PEOPLE[other(l.speaker)];
  const head =
    l.kind === "tgt" ? (
      <>
        <b style={{ color: C.accent, fontWeight: 500 }}>{to.name} hears it in {to.english}</b>, {((l.delayMs ?? 0) / 1000).toFixed(1)} s later
      </>
    ) : (
      <>
        <b style={{ fontWeight: 500 }}>{who.name}</b>, in {who.english}
      </>
    );
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: v ? 150 : 34 }}>
      <div style={{ opacity: k, maxWidth: v ? 960 : 1400, padding: "14px 26px", borderRadius: 16, background: "rgba(20,12,8,.62)", backdropFilter: "blur(8px)", color: "#fff", textAlign: "center", fontSize: v ? 32 : 28, lineHeight: 1.35 }}>
        <div style={{ fontSize: v ? 24 : 20, color: "rgba(255,255,255,.75)", marginBottom: 2 }}>{head}</div>
        “{l.en}”
      </div>
    </AbsoluteFill>
  );
};

const Verdict: React.FC<{ mode: Mode; from: number }> = ({ mode, from }) => {
  const v = useVertical();
  const k = useIn(from + 6, 16);
  const sub = useIn(from + 30, 20);
  const bad = mode === "without";
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: v ? 130 : 40 }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, opacity: k, transform: `translateY(${(1 - k) * 18}px)` }}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 12, padding: "14px 26px", borderRadius: 999, background: "#fff", color: C.ink, fontSize: v ? 36 : 32, fontWeight: 500, letterSpacing: "-.02em", boxShadow: "0 18px 40px -18px rgba(0,0,0,.5)" }}>
          <span style={{ width: 14, height: 14, borderRadius: "50%", background: bad ? C.bad : C.good }} />
          {bad ? "Ride cancelled." : "Picked up at gate 2."}
        </div>
        <div style={{ opacity: sub, color: "#fff", fontSize: v ? 30 : 26, textShadow: "0 1px 12px rgba(0,0,0,.4)", textAlign: "center", maxWidth: 1000 }}>
          {bad ? "Nobody did anything wrong. They just don't share a language." : "Same call, same phones. Each of them spoke only their own language."}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// --- 3. Convox joins ---------------------------------------------------------------------

const Title: React.FC = () => {
  const frame = useCurrentFrame();
  const v = useVertical();
  const grow = interpolate(frame, [4, 26], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const word = useIn(sec(0.6));
  const line = useIn(sec(1.2));
  const out = interpolate(frame, [SCENES.title - 10, SCENES.title], [1, 0], { extrapolateLeft: "clamp" });
  return (
    <AbsoluteFill style={{ background: C.bg, alignItems: "center", justifyContent: "center", opacity: out, textAlign: "center" }}>
      <div style={{ color: C.ink }}>
        <Mark size={v ? 150 : 130} grow={grow} />
      </div>
      <div style={{ marginTop: 18, fontWeight: 600, fontSize: v ? 64 : 58, letterSpacing: "0.08em", color: C.ink, opacity: word, transform: `translateY(${(1 - word) * 14}px)` }}>
        CONVOX<span style={{ color: C.accent, marginLeft: 10 }}>AI</span>
      </div>
      <p style={{ margin: "26px 0 0", fontSize: v ? 44 : 44, letterSpacing: "-.03em", color: C.ink2, opacity: line, padding: "0 60px" }}>
        Now the same call, with a live interpreter on it.
      </p>
    </AbsoluteFill>
  );
};

// --- 5. the ask --------------------------------------------------------------------------

const Outro: React.FC = () => {
  const v = useVertical();
  const h = useIn(4);
  const h2 = useIn(sec(0.6));
  const sub = useIn(sec(1.3));
  const cta = useIn(sec(2), 14);
  const foot = useIn(sec(2.8));
  return (
    <AbsoluteFill style={{ background: C.bg, padding: v ? "0 80px" : "0 140px", justifyContent: "center" }}>
      <div style={{ position: "absolute", top: v ? 120 : 70, left: v ? 80 : 140 }}>
        <Logo size={v ? 46 : 40} />
      </div>
      <h1 style={{ margin: 0, fontWeight: 400, fontSize: v ? 104 : 120, lineHeight: 1.02, letterSpacing: "-.05em", color: C.ink }}>
        <span style={{ display: "block", opacity: h, transform: `translateY(${(1 - h) * 22}px)` }}>Speak your language.</span>
        <span style={{ display: "block", color: C.faint, opacity: h2, transform: `translateY(${(1 - h2) * 22}px)` }}>Be heard in theirs.</span>
      </h1>
      <p style={{ margin: "40px 0 0", fontSize: v ? 34 : 32, lineHeight: 1.45, color: C.ink2, maxWidth: 1200, opacity: sub }}>
        A live interpreter for rider and delivery calls. Convox joins the masked call your platform already routes through Exotel or another CPaaS. No app, any phone.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 24, marginTop: 48, opacity: cta, transform: `scale(${0.96 + 0.04 * cta})`, transformOrigin: "left center" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 14, height: 76, padding: "0 34px", borderRadius: 999, background: C.ink, color: "#fff", fontSize: 32, fontWeight: 500 }}>
          Try a live call <span style={{ color: C.accent }}>→</span>
        </span>
        <span style={{ fontFamily: MONO, fontSize: 32, color: C.ink }}>convox.ai/try</span>
      </div>
      <p style={{ position: "absolute", bottom: v ? 110 : 56, left: v ? 80 : 140, right: v ? 80 : 140, margin: 0, fontSize: v ? 22 : 20, color: C.muted, opacity: foot }}>
        The call is real Convox output: Sarvam translation and synthetic voices, with the pipeline's measured delays. The people are illustrative.
      </p>
    </AbsoluteFill>
  );
};

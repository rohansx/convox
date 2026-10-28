// The landing page's iPhone (landing.css .iphone), as a component sized by `pw`.
import React from "react";
import { useAudioData, visualizeAudio } from "@remotion/media-utils";
import { interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { C, Mark, MONO, SANS } from "./ui";

export type Bubble = { key: string; text: string; mine: boolean; from: number; meta?: React.ReactNode };

const TINT = {
  a: { tint: "#6b3a22", av1: "#ffa06b", av2: "#e2552a" },
  b: { tint: "#2d3768", av1: "#8fa1ff", av2: "#4152d6" },
};

type Props = {
  side: "a" | "b";
  pw: number;
  name: string;
  native: string;
  tag: string;
  bubbles: Bubble[];
  mode: "speaking" | "hearing" | null;
  audio?: { src: string; from: number } | null; // the voice now playing, for the wave
  status: React.ReactNode;
  seconds: number;
};

export const Phone: React.FC<Props> = ({ side, pw, name, native, tag, bubbles, mode, audio, status, seconds }) => {
  const t = TINT[side];
  const r = pw * 0.17;
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const islandW = spring({ frame: mode === "hearing" ? frame - (audio?.from ?? 0) : 0, fps, config: { damping: 14 } });
  const hearing = mode === "hearing" ? islandW : 0;
  const mmss = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  return (
    <div
      style={{
        position: "relative",
        flex: "none",
        width: pw,
        height: (pw * 19.4) / 9,
        borderRadius: r,
        background: "linear-gradient(140deg, #8d8a85 0%, #3b3936 18%, #1d1c1b 45%, #2e2d2b 75%, #77736d 100%)",
        boxShadow: "inset 0 0 0 .6px rgba(255,255,255,.35), 0 46px 80px -36px rgba(40,15,5,.6), 0 14px 30px -14px rgba(40,15,5,.4)",
        fontFamily: SANS,
      }}
    >
      <div style={{ position: "absolute", inset: pw * 0.016, borderRadius: r - pw * 0.016, background: "#050505" }} />
      <div
        style={{
          position: "absolute",
          inset: pw * 0.046,
          borderRadius: r - pw * 0.046,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          color: "#fff",
          background: `radial-gradient(120% 55% at 50% 0%, ${t.tint} 0%, transparent 70%), linear-gradient(180deg, #24252a 0%, #141518 60%, #0d0d0f 100%)`,
        }}
      >
        {/* dynamic island: widens with the Convox mark while the interpreter speaks */}
        <div
          style={{
            position: "absolute",
            top: pw * 0.028,
            left: "50%",
            transform: "translateX(-50%)",
            width: `${30 + hearing * 12}%`,
            height: pw * 0.095,
            background: "#000",
            borderRadius: 999,
            zIndex: 3,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: `0 ${pw * 0.035}px`,
          }}
        >
          <div style={{ opacity: hearing, color: "#fff", display: "flex" }}>
            <Mark size={pw * 0.06} />
          </div>
          <div style={{ opacity: hearing, display: "flex", gap: 2, alignItems: "center", height: "40%" }}>
            {[0, 1, 2, 3, 4].map((i) => (
              <i key={i} style={{ width: 2.5, height: `${30 + 60 * Math.abs(Math.sin(frame / 4 + i))}%`, borderRadius: 2, background: C.accent }} />
            ))}
          </div>
        </div>
        <div style={{ height: pw * 0.156, display: "flex", justifyContent: "space-between", alignItems: "center", padding: `0 ${pw * 0.075}px 0 ${pw * 0.105}px`, fontSize: pw * 0.048, fontWeight: 600, position: "relative", zIndex: 2 }}>
          <span>8:41</span>
          <span style={{ width: pw * 0.078, height: pw * 0.038, border: "1px solid rgba(255,255,255,.45)", borderRadius: pw * 0.013, padding: 1.5, display: "flex" }}>
            <i style={{ width: "78%", background: "#fff", borderRadius: 1.5 }} />
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: `${pw * 0.06}px 12px ${pw * 0.02}px`, lineHeight: 1.2 }}>
          <div style={{ width: pw * 0.22, height: pw * 0.22, borderRadius: "50%", display: "grid", placeItems: "center", fontSize: pw * 0.094, fontWeight: 500, background: `linear-gradient(160deg, ${t.av1}, ${t.av2})`, marginBottom: pw * 0.04 }}>
            {name[0]}
          </div>
          <b style={{ fontSize: pw * 0.085, fontWeight: 500, letterSpacing: "-.025em" }}>{name}</b>
          <span style={{ fontSize: pw * 0.045, color: "rgba(255,255,255,.6)", marginTop: 4, fontVariantNumeric: "tabular-nums" }}>
            {native} · {mmss}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, marginTop: pw * 0.035, padding: "3px 10px 3px 7px", borderRadius: 999, background: "rgba(255,255,255,.1)", fontSize: pw * 0.036, color: "rgba(255,255,255,.78)" }}>
            {tag}
          </span>
        </div>
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            justifyContent: "flex-end",
            gap: pw * 0.026,
            padding: `6px ${pw * 0.055}px 4px`,
            overflow: "hidden",
            WebkitMaskImage: "linear-gradient(180deg, transparent, #000 26%)",
          }}
        >
          {bubbles.map((b) => (
            <BubbleView key={b.key} b={b} pw={pw} />
          ))}
        </div>
        <div style={{ padding: `4px ${pw * 0.07}px 0` }}>
          {audio && mode ? <LiveWave pw={pw} src={audio.src} from={audio.from} color={mode === "hearing" ? C.accent : "#fff"} /> : <IdleWave pw={pw} />}
          <p style={{ textAlign: "center", fontSize: pw * 0.042, color: "rgba(255,255,255,.7)", margin: `6px 0 ${pw * 0.05}px`, minHeight: "1.4em", whiteSpace: "nowrap" }}>{status}</p>
        </div>
        <div style={{ height: pw * 0.016, width: "36%", margin: `0 auto ${pw * 0.024}px`, borderRadius: 999, background: "rgba(255,255,255,.75)" }} />
      </div>
    </div>
  );
};

const BubbleView: React.FC<{ b: Bubble; pw: number }> = ({ b, pw }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const k = spring({ frame: frame - b.from, fps, config: { damping: 13, mass: 0.6 } });
  return (
    <div
      style={{
        alignSelf: b.mine ? "flex-end" : "flex-start",
        maxWidth: "88%",
        padding: `${pw * 0.024}px ${pw * 0.038}px`,
        borderRadius: pw * 0.058,
        [b.mine ? "borderBottomRightRadius" : "borderBottomLeftRadius"]: pw * 0.018,
        background: b.mine ? "#0a84ff" : "rgba(255,255,255,.14)",
        fontSize: pw * 0.054,
        lineHeight: 1.38,
        opacity: k,
        transform: `translateY(${(1 - k) * 10}px) scale(${0.96 + 0.04 * k})`,
      }}
    >
      {b.text}
      {b.meta && <div style={{ display: "flex", gap: 6, marginTop: 5, fontSize: pw * 0.036, fontFamily: MONO, color: "rgba(255,255,255,.62)" }}>{b.meta}</div>}
    </div>
  );
};

const BARS = 28;

const IdleWave: React.FC<{ pw: number }> = ({ pw }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 3, height: pw * 0.085 }}>
    {Array.from({ length: BARS }, (_, i) => (
      <i key={i} style={{ width: 3, height: "10%", borderRadius: 2, background: "rgba(255,255,255,.25)" }} />
    ))}
  </div>
);

/** The wave follows the voice actually playing, sampled from its audio file. */
const LiveWave: React.FC<{ pw: number; src: string; from: number; color: string }> = ({ pw, src, from, color }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const data = useAudioData(staticFile(src));
  if (!data) return <IdleWave pw={pw} />;
  const v = visualizeAudio({ fps, frame: frame - from, audioData: data, numberOfSamples: 32, smoothing: true });
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 3, height: pw * 0.085 }}>
      {Array.from({ length: BARS }, (_, i) => {
        const a = v[Math.min(v.length - 1, Math.abs(i - BARS / 2) + 1)] ?? 0;
        const h = interpolate(Math.sqrt(a), [0, 0.3], [10, 100], { extrapolateRight: "clamp" });
        return <i key={i} style={{ width: 3, height: `${h}%`, borderRadius: 2, background: color }} />;
      })}
    </div>
  );
};

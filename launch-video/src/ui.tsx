// Shared pieces in the landing page's style (convox/console/static/landing.css): Geist,
// warm paper and ink, the orange half of the mark, and the painted dusk behind the phones.
import React from "react";
import { loadFont as geist } from "@remotion/google-fonts/Geist";
import { loadFont as geistMono } from "@remotion/google-fonts/GeistMono";
import { loadFont as kannada } from "@remotion/google-fonts/NotoSansKannada";
import { loadFont as devanagari } from "@remotion/google-fonts/NotoSansDevanagari";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

const g = geist("normal", { weights: ["300", "400", "500", "600"], subsets: ["latin"] });
const gm = geistMono("normal", { weights: ["400", "500"], subsets: ["latin"] });
const kn = kannada("normal", { weights: ["400", "500"], subsets: ["kannada"] });
const hi = devanagari("normal", { weights: ["400", "500"], subsets: ["devanagari"] });

export const SANS = `${g.fontFamily}, ${kn.fontFamily}, ${hi.fontFamily}, sans-serif`;
export const MONO = `${gm.fontFamily}, monospace`;

export const C = {
  bg: "#fbfaf7",
  ink: "#1f1c17",
  ink2: "#48443c",
  muted: "#7a746a",
  faint: "#aaa398",
  line: "#e9e5dc",
  accent: "#ff5b14",
  good: "#16a34a",
  bad: "#dc2626",
};

/** 0 → 1 with the page's soft overshoot, starting at `from` frames into the scene. */
export function useIn(from = 0, damping = 18) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - from, fps, config: { damping, mass: 0.7 } });
}

export function fade(frame: number, from: number, len = 12) {
  return interpolate(frame, [from, from + len], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
}

/** One waveform that turns from ink to orange halfway: one voice, another language. */
export const Mark: React.FC<{ size: number; color?: string; grow?: number }> = ({ size, color = "currentColor", grow = 1 }) => {
  const bars = [
    [3.2, 11, 10],
    [7.6, 7, 18],
    [12, 3, 26],
    [16.4, 3, 26],
    [20.8, 7, 18],
    [25.2, 11, 10],
  ];
  return (
    <svg width={size} height={size} viewBox="0 0 32 32">
      {bars.map(([x, y, h], i) => {
        const k = Math.max(0.08, Math.min(1, grow * 6 - i * 0.6));
        const hh = h * k;
        return <rect key={i} x={x} y={16 - hh / 2} width={2.8} height={hh} rx={1.4} fill={i < 3 ? color : C.accent} />;
      })}
    </svg>
  );
};

export const Logo: React.FC<{ size?: number; color?: string }> = ({ size = 34, color = C.ink }) => (
  <div style={{ display: "flex", alignItems: "center", gap: size * 0.3, color }}>
    <Mark size={size} />
    <span style={{ fontFamily: SANS, fontWeight: 600, fontSize: size * 0.62, letterSpacing: "0.08em" }}>
      CONVOX<em style={{ fontStyle: "normal", color: C.accent, marginLeft: size * 0.12 }}>AI</em>
    </span>
  </div>
);

const GRAIN =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 .55 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

/** The dusk behind the phones; `cold` drains it toward grey for the failed call. */
export const StageArt: React.FC<{ cold?: number }> = ({ cold = 0 }) => (
  <AbsoluteFill>
    <AbsoluteFill
      style={{
        background:
          "radial-gradient(120% 70% at 50% 108%, rgba(255,150,80,.55), transparent 60%), radial-gradient(60% 50% at 82% 18%, rgba(255,226,190,.55), transparent 70%), linear-gradient(180deg, #e9d9c8 0%, #e8b993 30%, #d9865c 56%, #9c513f 80%, #4a2c2c 100%)",
        filter: `saturate(${1 - cold * 0.85}) brightness(${1 - cold * 0.25})`,
      }}
    />
    <Skyline />
    <AbsoluteFill style={{ backgroundImage: GRAIN, opacity: 0.34, mixBlendMode: "overlay" }} />
  </AbsoluteFill>
);

// Bengaluru at dusk, very roughly: low blocks, a metro viaduct, a tower or two
const Skyline: React.FC = () => (
  <svg viewBox="0 0 1600 220" preserveAspectRatio="none" style={{ position: "absolute", left: 0, right: 0, bottom: 0, width: "100%", height: "22%", fill: "#3a2324", opacity: 0.42 }}>
    <path d="M0 220V150h60v-30h50v40h40v-70h70v60h30v-20h60v40h50v-90h24v-20h12v20h24v90h60v-40h80v50h40v-30h90v40h50v-110h60v110h40v-50h70v30h50v-60h40v-30h30v30h40v80h60v-40h70v50h50v-70h80v70h40v-20h60v40h40v-60h60v80z" />
    <rect x="0" y="168" width="1600" height="10" />
    {Array.from({ length: 16 }, (_, i) => (
      <rect key={i} x={40 + i * 100} y="168" width="12" height="52" />
    ))}
  </svg>
);

export const Pill: React.FC<{ children: React.ReactNode; dot?: string; scale?: number }> = ({ children, dot, scale = 1 }) => (
  <div
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 12 * scale,
      height: 52 * scale,
      padding: `0 ${22 * scale}px`,
      borderRadius: 999,
      background: "rgba(255,255,255,.24)",
      border: "1px solid rgba(255,255,255,.34)",
      color: "#2b170e",
      fontFamily: SANS,
      fontSize: 22 * scale,
      fontWeight: 500,
      backdropFilter: "blur(10px)",
    }}
  >
    {dot && <span style={{ width: 11 * scale, height: 11 * scale, borderRadius: "50%", background: dot, boxShadow: `0 0 0 ${4 * scale}px ${dot}40` }} />}
    {children}
  </div>
);

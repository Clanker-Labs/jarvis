import type { JarvisState } from "@jarvis/protocol";

export const DEFAULT_COLORS: Record<JarvisState, string> = {
  IDLE: "#0EA5E9",
  WAKE: "#F59E0B",
  LISTENING: "#F59E0B",
  THINKING: "#8B5CF6",
  TOOL: "#8B5CF6",
  SPEAKING: "#14B8A6",
  FOLLOW_UP: "#F59E0B",
  ERROR: "#EF4444",
};

export interface StateParams {
  energy: number; // surface agitation
  swirl: number; // internal flow speed
  glow: number; // halo intensity
  particles: number; // orbiting particle opacity
  ring: number; // tool ring opacity
  breathe: number; // idle breathing amplitude
  audio: "mic" | "tts" | null; // which level drives displacement
  pulse: number; // ERROR heartbeat amplitude
}

export const STATE_PARAMS: Record<JarvisState, StateParams> = {
  IDLE: { energy: 0.12, swirl: 0.15, glow: 0.55, particles: 0, ring: 0, breathe: 1, audio: null, pulse: 0 },
  WAKE: { energy: 0.6, swirl: 0.8, glow: 1.0, particles: 0, ring: 0, breathe: 0, audio: null, pulse: 0 },
  LISTENING: { energy: 0.32, swirl: 0.5, glow: 0.85, particles: 0, ring: 0, breathe: 0, audio: "mic", pulse: 0 },
  THINKING: { energy: 0.5, swirl: 1.6, glow: 0.8, particles: 1, ring: 0, breathe: 0, audio: null, pulse: 0 },
  TOOL: { energy: 0.45, swirl: 1.3, glow: 0.8, particles: 0.6, ring: 1, breathe: 0, audio: null, pulse: 0 },
  SPEAKING: { energy: 0.3, swirl: 0.7, glow: 0.9, particles: 0, ring: 0, breathe: 0, audio: "tts", pulse: 0 },
  FOLLOW_UP: { energy: 0.2, swirl: 0.4, glow: 0.65, particles: 0, ring: 0, breathe: 0.6, audio: "mic", pulse: 0 },
  ERROR: { energy: 0.45, swirl: 0.6, glow: 0.9, particles: 0, ring: 0, breathe: 0, audio: null, pulse: 1 },
};

/** Live audio levels, written by the socket handler and read every frame (no React churn). */
export const levels = { mic: 0, tts: 0, micAt: 0, ttsAt: 0 };

export function setLevel(kind: "mic" | "tts", v: number) {
  levels[kind] = Math.max(0, Math.min(1, v));
  levels[kind === "mic" ? "micAt" : "ttsAt"] = performance.now();
}

/** Exponential approach factor for a time constant (seconds). */
export const approach = (dt: number, tau: number) => 1 - Math.exp(-dt / tau);

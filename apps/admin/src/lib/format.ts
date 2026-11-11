/** Formatting helpers (pure, unit-tested). */
import type { Stage } from "./types";

/** Seconds → "840 ms" / "1.24 s" / "—". */
export function fmtLatency(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 0) return "—";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 10) return `${s.toFixed(2)} s`;
  if (s < 100) return `${s.toFixed(1)} s`;
  return `${Math.round(s)} s`;
}

/** USD → "$0.0042" (small amounts keep precision), "$1.23", "—" for null, "$0" for 0. */
export function fmtCost(usd: number | null | undefined): string {
  if (usd == null || !Number.isFinite(usd)) return "—";
  if (usd === 0) return "$0";
  if (Math.abs(usd) < 0.01) return `$${usd.toFixed(4)}`;
  if (Math.abs(usd) < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}k`;
  return n.toLocaleString("en-US");
}

/** Uptime seconds → "3d 4h", "2h 05m", "12m", "45s". */
export function fmtDuration(totalS: number): string {
  const s = Math.max(0, Math.floor(totalS));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m`;
  return `${s}s`;
}

/** Unix seconds → local "HH:MM:SS" if today, else "12 Nov, HH:MM". */
export function fmtTime(unix: number | null | undefined, now: Date = new Date()): string {
  if (!unix) return "—";
  const d = new Date(unix * 1000);
  const hms = d.toLocaleTimeString("en-GB", { hour12: false });
  if (d.toDateString() === now.toDateString()) return hms;
  return `${d.toLocaleDateString("en-GB", { month: "short", day: "numeric" })}, ${hms.slice(0, 5)}`;
}

/** Unix seconds → "12 s ago" / "5 min ago" / "3 h ago" / date. */
export function fmtAgo(unix: number | null | undefined, nowMs: number = Date.now()): string {
  if (!unix) return "never";
  const s = Math.round(nowMs / 1000 - unix);
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(unix * 1000).toLocaleDateString("en-GB", { month: "short", day: "numeric" });
}

/** Relative offset in a trace: "+0.000 s". */
export function fmtOffset(s: number): string {
  return `+${Math.max(0, s).toFixed(3)} s`;
}

export const STAGE_LABELS: Record<Stage, string> = {
  wake_to_listen: "Wake → listening",
  stt: "Speech-to-text",
  llm_first_token: "LLM first token",
  tts_first_audio: "TTS first audio",
  end_of_speech_to_audio: "End of speech → audio",
};

/** The pipeline stages shown as bars, in order. */
export const PIPELINE: Stage[] = ["wake_to_listen", "stt", "llm_first_token", "tts_first_audio"];

export function truncate(s: string | null | undefined, n: number): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

/** Compact one-line JSON for tool args / results. */
export function summarize(v: unknown, n = 160): string {
  if (v == null) return "";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return truncate(s, n);
}

/** Width (0–100 %) of a latency bar relative to the largest value shown. */
export function barPct(v: number | null | undefined, max: number): number {
  if (v == null || !(max > 0) || !(v > 0)) return 0;
  return Math.min(100, (v / max) * 100);
}

/** A 0–1 value (mic level is already perceptual, dBFS-mapped by the node) → clamped percent. */
export function pct01(v: number | null | undefined): number {
  if (v == null || !Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(100, v * 100));
}

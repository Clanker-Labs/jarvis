/** Turn-trace helpers: build a timeline from stored events + timing marks (pure, unit-tested). */
import { summarize } from "./format";
import type { TraceEvent, TurnDetail } from "./types";

export interface TimelineItem {
  t: number; // seconds from turn start
  kind: "event" | "mark";
  type: string;
  text: string;
  tone: "neutral" | "good" | "warn" | "bad" | "info" | "violet";
}

const s = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));

export function describeEvent(ev: TraceEvent): string {
  switch (ev.type) {
    case "state.change":
      return `${s(ev.previous)} → ${s(ev.state)}${ev.reason ? ` (${s(ev.reason)})` : ""}`;
    case "wake.detected":
      return `${s(ev.source)} · score ${typeof ev.score === "number" ? ev.score.toFixed(2) : "?"}`;
    case "listen.start":
      return ev.follow_up ? "follow-up" : "listening";
    case "listen.final":
      return `“${s(ev.text)}” via ${s(ev.stt_provider)}`;
    case "agent.thinking":
      return `${s(ev.provider)} / ${s(ev.model)}`;
    case "agent.tool_call":
      return `${s(ev.tool)}(${summarize(ev.args, 120)})`;
    case "agent.tool_result":
      return `${ev.ok ? "ok" : "failed"} · ${summarize(ev.summary, 140)}`;
    case "agent.confirm_request":
      return `${s(ev.tool)}: ${s(ev.prompt)}`;
    case "agent.progress":
      return s(ev.message);
    case "agent.done":
      return `${ev.interrupted ? "interrupted · " : ""}${summarize(ev.text, 140)}`;
    case "tts.start":
      return `${s(ev.provider)} @ ${s(ev.sample_rate)} Hz`;
    case "tts.sentence":
      return summarize(ev.text, 140);
    case "tts.end":
      return ev.interrupted ? "interrupted" : "finished";
    case "barge_in":
      return s(ev.source);
    case "error":
      return `${s(ev.code)}: ${s(ev.message)}`;
    default: {
      const { type: _t, ts: _ts, turn_id: _tid, ...rest } = ev;
      return summarize(rest, 140);
    }
  }
}

function toneFor(type: string, ev?: TraceEvent): TimelineItem["tone"] {
  if (type === "error" || (type === "agent.tool_result" && ev && !ev.ok)) return "bad";
  if (type.startsWith("agent.tool")) return "violet";
  if (type.startsWith("agent.")) return "info";
  if (type.startsWith("tts.")) return "good";
  if (type.startsWith("wake") || type.startsWith("listen") || type === "barge_in") return "warn";
  return "neutral";
}

/** Merge events and timing marks into one list sorted by time, relative to the earliest point. */
export function buildTimeline(turn: Pick<TurnDetail, "latency" | "events" | "started">): TimelineItem[] {
  const events = turn.events?.events ?? [];
  const marks = Object.entries(turn.latency?.timings ?? {});
  const times = [...events.map((e) => e.ts), ...marks.map(([, t]) => t)].filter((t) => Number.isFinite(t));
  const t0 = times.length ? Math.min(...times) : turn.started;
  const items: TimelineItem[] = [
    ...events.map((ev) => ({
      t: ev.ts - t0,
      kind: "event" as const,
      type: ev.type,
      text: describeEvent(ev),
      tone: toneFor(ev.type, ev),
    })),
    ...marks.map(([name, t]) => ({ t: t - t0, kind: "mark" as const, type: name, text: "", tone: "neutral" as const })),
  ];
  // Stable sort: marks after events at the same instant.
  return items.sort((a, b) => a.t - b.t || (a.kind === b.kind ? 0 : a.kind === "event" ? -1 : 1));
}

/** End-to-end latency of a turn row (end of speech → first audio), if recorded. */
export function endToEnd(latency: Partial<Record<string, number>>): number | null {
  return latency.end_of_speech_to_audio ?? null;
}

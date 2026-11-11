import { describe, expect, it } from "vitest";
import { backoffDelay, eventsUrl } from "./events";
import { barPct, fmtAgo, fmtCost, fmtDuration, fmtLatency, fmtOffset, pct01, truncate } from "./format";
import { addUnique, dropFromRoutes, linesToList, move, removeAt, renameInRoutes } from "./reorder";
import { buildTimeline, describeEvent } from "./trace";
import { issuesAt, jsonEqual } from "./useSection";

describe("latency / cost formatting", () => {
  it("formats latencies", () => {
    expect(fmtLatency(null)).toBe("—");
    expect(fmtLatency(undefined)).toBe("—");
    expect(fmtLatency(0.0424)).toBe("42 ms");
    expect(fmtLatency(0.9996)).toBe("1000 ms");
    expect(fmtLatency(1.234)).toBe("1.23 s");
    expect(fmtLatency(12.34)).toBe("12.3 s");
    expect(fmtLatency(150)).toBe("150 s");
    expect(fmtLatency(-1)).toBe("—");
  });
  it("formats costs", () => {
    expect(fmtCost(null)).toBe("—");
    expect(fmtCost(0)).toBe("$0");
    expect(fmtCost(0.00042)).toBe("$0.0004");
    expect(fmtCost(0.123)).toBe("$0.123");
    expect(fmtCost(3.5)).toBe("$3.50");
  });
  it("formats durations and offsets", () => {
    expect(fmtDuration(45)).toBe("45s");
    expect(fmtDuration(12 * 60 + 5)).toBe("12m");
    expect(fmtDuration(2 * 3600 + 5 * 60)).toBe("2h 05m");
    expect(fmtDuration(3 * 86400 + 4 * 3600)).toBe("3d 4h");
    expect(fmtOffset(1.23456)).toBe("+1.235 s");
  });
  it("formats relative times", () => {
    const now = 1_700_000_000_000;
    expect(fmtAgo(null, now)).toBe("never");
    expect(fmtAgo(now / 1000 - 2, now)).toBe("just now");
    expect(fmtAgo(now / 1000 - 30, now)).toBe("30 s ago");
    expect(fmtAgo(now / 1000 - 600, now)).toBe("10 min ago");
  });
  it("scales bars", () => {
    expect(barPct(1, 2)).toBe(50);
    expect(barPct(null, 2)).toBe(0);
    expect(barPct(3, 0)).toBe(0);
    expect(barPct(5, 2)).toBe(100);
    expect(pct01(0.42)).toBeCloseTo(42);
    expect(pct01(2)).toBe(100);
    expect(truncate("abcdef", 4)).toBe("abc…");
  });
});

describe("reordering", () => {
  const xs = ["a", "b", "c"];
  it("moves up and down", () => {
    expect(move(xs, 1, -1)).toEqual(["b", "a", "c"]);
    expect(move(xs, 1, 1)).toEqual(["a", "c", "b"]);
  });
  it("ignores out-of-range moves and never mutates", () => {
    expect(move(xs, 0, -1)).toEqual(xs);
    expect(move(xs, 2, 1)).toEqual(xs);
    expect(move(xs, 5, -1)).toEqual(xs);
    expect(xs).toEqual(["a", "b", "c"]);
  });
  it("removes and adds uniquely", () => {
    expect(removeAt(xs, 1)).toEqual(["a", "c"]);
    expect(addUnique(xs, "b")).toEqual(xs);
    expect(addUnique(xs, "d")).toEqual(["a", "b", "c", "d"]);
  });
  it("renames and drops ids across routes", () => {
    const routes = { chat: ["local", "claude"], tools: ["claude"], task: [] };
    expect(renameInRoutes(routes, "claude", "opus")).toEqual({ chat: ["local", "opus"], tools: ["opus"], task: [] });
    expect(dropFromRoutes(routes, "claude")).toEqual({ chat: ["local"], tools: [], task: [] });
  });
  it("parses keyword textareas", () => {
    expect(linesToList(" timer\n\nweather \n  ")).toEqual(["timer", "weather"]);
  });
});

describe("event socket helpers", () => {
  it("builds the socket URL from the page location", () => {
    expect(eventsUrl({ protocol: "http:", host: "127.0.0.1:9000" })).toBe("ws://127.0.0.1:9000/api/events");
    expect(eventsUrl({ protocol: "https:", host: "jarvis.lan" })).toBe("wss://jarvis.lan/api/events");
  });
  it("backs off exponentially with a cap", () => {
    const mid = () => 0.5; // no jitter
    expect(backoffDelay(0, mid)).toBe(500);
    expect(backoffDelay(1, mid)).toBe(1000);
    expect(backoffDelay(3, mid)).toBe(4000);
    expect(backoffDelay(20, mid)).toBe(15000);
    expect(backoffDelay(0, () => 0)).toBe(400);
    expect(backoffDelay(0, () => 1)).toBe(600);
  });
});

describe("traces", () => {
  it("describes events", () => {
    expect(describeEvent({ type: "state.change", ts: 0, previous: "IDLE", state: "WAKE", reason: "wake" })).toBe("IDLE → WAKE (wake)");
    expect(describeEvent({ type: "agent.tool_call", ts: 0, tool: "timer", args: { minutes: 5 } })).toBe('timer({"minutes":5})');
    expect(describeEvent({ type: "error", ts: 0, code: "stt", message: "boom" })).toBe("stt: boom");
  });
  it("merges events and marks relative to the first point", () => {
    const tl = buildTimeline({
      started: 100,
      latency: { timings: { wake: 100, first_audio: 101.5 } },
      events: {
        events: [
          { type: "listen.final", ts: 100.8, text: "hi", stt_provider: "deepgram" },
          { type: "wake.detected", ts: 100, score: 0.9, source: "voice" },
        ],
      },
    });
    expect(tl.map((i) => [i.kind, i.type, Number(i.t.toFixed(2))])).toEqual([
      ["event", "wake.detected", 0],
      ["mark", "wake", 0],
      ["event", "listen.final", 0.8],
      ["mark", "first_audio", 1.5],
    ]);
  });
  it("copes with empty traces", () => {
    expect(buildTimeline({ started: 1, latency: {}, events: {} })).toEqual([]);
  });
});

describe("section helpers", () => {
  it("compares drafts structurally", () => {
    expect(jsonEqual({ a: [1, 2] }, { a: [1, 2] })).toBe(true);
    expect(jsonEqual({ a: [1, 2] }, { a: [2, 1] })).toBe(false);
  });
  it("selects validation issues by path", () => {
    const issues = [
      { loc: ["providers", 0, "id"], msg: "bad id" },
      { loc: ["providers", 1, "kind"], msg: "bad kind" },
      { loc: ["health_interval_s"], msg: "too small" },
    ];
    expect(issuesAt(issues, "providers", 0)).toEqual(["bad id"]);
    expect(issuesAt(issues, "health_interval_s")).toEqual(["too small"]);
    expect(issuesAt(issues, "routes")).toEqual([]);
  });
});

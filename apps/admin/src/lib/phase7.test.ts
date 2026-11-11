import { describe, expect, it } from "vitest";
import { buildInit } from "./api";
import { communityColor, hitTest, nodeRadius, shapeGraph } from "./graph";
import { chunkLocation, fmtUntil, knowledgeBusy, oauthReturn, skillTemplate, SKILL_NAME, sourceTitle } from "./misc";
import { isAllowed, looksLikeRawSecret, recordToRows, rowsToRecord, stricterOrEqual, toggleAllowed } from "./sideEffects";
import { omitKeys } from "./useSection";

describe("side effects", () => {
  it("only offers the declared level or stricter", () => {
    expect(stricterOrEqual("none")).toEqual(["none", "reversible", "irreversible"]);
    expect(stricterOrEqual("reversible")).toEqual(["reversible", "irreversible"]);
    expect(stricterOrEqual("irreversible")).toEqual(["irreversible"]);
  });
});

describe("MCP allow-list toggling", () => {
  const all = ["shout", "wipe", "read"];
  it("null means all; unticking one switches to an explicit list", () => {
    expect(isAllowed(null, "wipe")).toBe(true);
    expect(toggleAllowed(null, all, "wipe", false)).toEqual(["shout", "read"]);
  });
  it("ticking the last missing tool goes back to null", () => {
    expect(toggleAllowed(["shout", "read"], all, "wipe", true)).toBeNull();
  });
  it("keeps discovery order and unknown names", () => {
    expect(toggleAllowed(["read", "gone"], all, "shout", true)).toEqual(["shout", "read", "gone"]);
    expect(toggleAllowed(["read"], all, "read", false)).toEqual([]);
  });
  it("ticking an already-allowed tool is a no-op", () => {
    expect(toggleAllowed(["read"], all, "read", true)).toEqual(["read"]);
  });
});

describe("key/value rows", () => {
  it("round-trips and drops blank keys", () => {
    expect(recordToRows({ A: "1" })).toEqual([{ key: "A", value: "1" }]);
    expect(rowsToRecord([{ key: " A ", value: "1" }, { key: "", value: "x" }, { key: "B", value: "" }])).toEqual({ A: "1", B: "" });
  });
  it("flags pasted credentials but not ${SECRET} references", () => {
    expect(looksLikeRawSecret("GITHUB_TOKEN", "ghp_abcdefghijklmnop")).toBe(true);
    expect(looksLikeRawSecret("GITHUB_TOKEN", "${GITHUB_TOKEN}")).toBe(false);
    expect(looksLikeRawSecret("Authorization", "Bearer ${TOKEN}")).toBe(false);
    expect(looksLikeRawSecret("LOG_LEVEL", "debug-verbose-mode")).toBe(false);
  });
});

describe("graph shaping", () => {
  const g = {
    nodes: [
      { id: 1, name: "Brain", type: "component", community: 0, mentions: 20 },
      { id: 2, name: "Audio node", type: "component", community: 1, mentions: 4 },
      { id: 3, name: "Kiosk", type: "component", community: null, mentions: 0 },
    ],
    edges: [
      { src: 1, dst: 2, label: "talks to" },
      { src: 2, dst: 1, label: "streams to" }, // duplicate pair
      { src: 1, dst: 1, label: "self" },
      { src: 1, dst: 99, label: "dangling" },
      { src: 3, dst: 1, label: "connects" },
    ],
    communities: [],
  };
  it("drops self, duplicate and dangling edges", () => {
    const { nodes, links } = shapeGraph(g);
    expect(nodes).toHaveLength(3);
    expect(links.map((l) => [l.source, l.target])).toEqual([[1, 2], [3, 1]]);
  });
  it("labels the biggest nodes and colours by community", () => {
    const { nodes } = shapeGraph(g, 1);
    expect(nodes.filter((n) => n.label).map((n) => n.name)).toEqual(["Brain"]);
    expect(nodes[2]!.color).toBe(communityColor(null));
    expect(communityColor(0)).not.toBe(communityColor(1));
    expect(communityColor(10)).toBe(communityColor(0));
  });
  it("sizes nodes by mentions within bounds", () => {
    expect(nodeRadius(0)).toBe(3);
    expect(nodeRadius(4)).toBeGreaterThan(nodeRadius(1));
    expect(nodeRadius(10_000)).toBe(14);
  });
  it("hit-tests the topmost node", () => {
    const { nodes } = shapeGraph(g);
    nodes[0]!.x = 0;
    nodes[0]!.y = 0;
    nodes[1]!.x = 100;
    nodes[1]!.y = 100;
    expect(hitTest(nodes, 1, 1)?.name).toBe("Brain");
    expect(hitTest(nodes, 50, 50)).toBeNull();
  });
});

describe("misc helpers", () => {
  it("validates skill names and builds a template with frontmatter", () => {
    expect(SKILL_NAME.test("morning-briefing")).toBe(true);
    expect(SKILL_NAME.test("Bad_Name")).toBe(false);
    expect(skillTemplate("my-skill")).toMatch(/^---\nname: my-skill\ndescription: .+\ntriggers: \[.*\]\n---\n/);
  });
  it("polls knowledge while anything is in flight", () => {
    expect(knowledgeBusy([{ status: "ready" }, { status: "error" }])).toBe(false);
    expect(knowledgeBusy([{ status: "ready" }, { status: "graph" }])).toBe(true);
    expect(knowledgeBusy([])).toBe(false);
  });
  it("formats chunk locations", () => {
    expect(chunkLocation({ page: 3 })).toBe("p. 3");
    expect(chunkLocation({ pages: [3, 4, 5], section: "Intro" })).toBe("pp. 3–5 · § Intro");
    expect(chunkLocation({})).toBe("");
  });
  it("reads the OAuth return parameters", () => {
    expect(oauthReturn("?connected=google")).toEqual({ connected: "google", error: undefined });
    expect(oauthReturn("?error=access_denied")).toEqual({ connected: undefined, error: "access_denied" });
    expect(oauthReturn("")).toBeNull();
  });
  it("names sources", () => {
    expect(sourceTitle({ title: null, kind: "file", uri: "file:///data/kb/ARCH.md" })).toBe("ARCH.md");
    expect(sourceTitle({ title: "Doc", kind: "url", uri: "https://x" })).toBe("Doc");
  });
  it("formats time until a due date", () => {
    const now = 1_700_000_000_000;
    expect(fmtUntil(now / 1000 - 1, now)).toBe("overdue");
    expect(fmtUntil(now / 1000 + 300, now)).toBe("in 5 min");
    expect(fmtUntil(now / 1000 + 3900, now)).toBe("in 1 h 5 min");
  });
  it("omits fields a form doesn't own", () => {
    expect(omitKeys({ a: 1, overrides: { x: 1 } }, ["overrides"])).toEqual({ a: 1 });
  });
});

describe("raw uploads", () => {
  it("sends Blobs as-is with their content type", () => {
    const blob = new Blob(["PK"], { type: "application/zip" });
    const init = buildInit({ method: "POST", body: blob, contentType: "application/zip" });
    expect(init.body).toBe(blob);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/zip");
    expect((init.headers as Record<string, string>)["X-Jarvis-Admin"]).toBe("1");
  });
});

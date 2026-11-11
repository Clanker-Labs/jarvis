/** Knowledge-graph shaping for the canvas view (pure, unit-tested). */
import type { GraphData } from "./types";

export interface SimNode {
  id: number;
  name: string;
  type: string;
  community: number | null;
  mentions: number;
  r: number;
  color: string;
  label: boolean;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

export interface SimLink {
  source: number | SimNode;
  target: number | SimNode;
  label: string;
}

const PALETTE = ["#0EA5E9", "#F59E0B", "#8B5CF6", "#14B8A6", "#EF4444", "#EC4899", "#84CC16", "#F97316", "#06B6D4", "#A855F7"];

export function communityColor(c: number | null | undefined): string {
  if (c == null || c < 0) return "#64748B";
  return PALETTE[c % PALETTE.length] ?? "#64748B";
}

/** Radius grows with sqrt(mentions), clamped to 3–14 px. */
export function nodeRadius(mentions: number): number {
  return Math.max(3, Math.min(14, 3 + Math.sqrt(Math.max(0, mentions)) * 1.6));
}

/** Nodes + links ready for d3-force: drops dangling/self edges, labels the `labelTop` biggest nodes. */
export function shapeGraph(g: GraphData, labelTop = 18): { nodes: SimNode[]; links: SimLink[] } {
  const ids = new Set(g.nodes.map((n) => n.id));
  const biggest = new Set(
    [...g.nodes].sort((a, b) => b.mentions - a.mentions).slice(0, labelTop).map((n) => n.id),
  );
  const nodes: SimNode[] = g.nodes.map((n) => ({
    ...n,
    r: nodeRadius(n.mentions),
    color: communityColor(n.community),
    label: biggest.has(n.id),
  }));
  const seen = new Set<string>();
  const links: SimLink[] = [];
  for (const e of g.edges) {
    if (!ids.has(e.src) || !ids.has(e.dst) || e.src === e.dst) continue;
    const key = e.src < e.dst ? `${e.src}-${e.dst}` : `${e.dst}-${e.src}`;
    if (seen.has(key)) continue;
    seen.add(key);
    links.push({ source: e.src, target: e.dst, label: e.label });
  }
  return { nodes, links };
}

/** Topmost node under a point (graph coordinates), or null. */
export function hitTest(nodes: SimNode[], x: number, y: number, slop = 3): SimNode | null {
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i]!;
    if (n.x == null || n.y == null) continue;
    const dx = n.x - x;
    const dy = n.y - y;
    if (dx * dx + dy * dy <= (n.r + slop) ** 2) return n;
  }
  return null;
}

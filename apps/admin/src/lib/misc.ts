/** Small pure helpers for the skills, knowledge and connectors pages (unit-tested). */
import type { KnowledgeChunk, KnowledgeSource } from "./types";

export const SKILL_NAME = /^[a-z0-9][a-z0-9-]{0,40}$/;

export function skillTemplate(name: string): string {
  return `---
name: ${name}
description: One sentence saying what this skill does and when to use it.
triggers: [example phrase, another phrase]
---
Instructions for Jarvis when this skill is active.

1. Say which tools to call and in what order.
2. Keep spoken answers short.
`;
}

/** Keep polling while any source is still being processed. */
export function knowledgeBusy(sources: Pick<KnowledgeSource, "status">[]): boolean {
  return sources.some((s) => s.status !== "ready" && s.status !== "error");
}

/** "p. 3" / "pp. 3–5" / "§ Intro" for a chunk's location. */
export function chunkLocation(meta: KnowledgeChunk["meta"]): string {
  const parts: string[] = [];
  if (Array.isArray(meta.pages) && meta.pages.length > 1) parts.push(`pp. ${meta.pages[0]}–${meta.pages[meta.pages.length - 1]}`);
  else if (typeof meta.pages === "string" && meta.pages) parts.push(`pp. ${meta.pages}`);
  else if (meta.page != null) parts.push(`p. ${meta.page}`);
  if (meta.section) parts.push(`§ ${meta.section}`);
  return parts.join(" · ");
}

/** Read `?connected=` / `?error=` left by the OAuth callback redirect. */
export function oauthReturn(search: string): { connected?: string; error?: string } | null {
  const q = new URLSearchParams(search);
  const connected = q.get("connected") ?? undefined;
  const error = q.get("error") ?? undefined;
  return connected || error ? { connected, error } : null;
}

/** Display name for a knowledge source. */
export function sourceTitle(s: Pick<KnowledgeSource, "title" | "uri" | "kind">): string {
  if (s.title) return s.title;
  if (s.kind === "file") return s.uri.replace(/^file:\/\//, "").split("/").pop() ?? s.uri;
  return s.uri;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} kB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Seconds until a unix time → "in 4 min" / "in 2 h 5 min" / "overdue". */
export function fmtUntil(unix: number, nowMs: number = Date.now()): string {
  const s = Math.round(unix - nowMs / 1000);
  if (s <= 0) return "overdue";
  if (s < 60) return `in ${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `in ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `in ${h} h${m % 60 ? ` ${m % 60} min` : ""}`;
  return `in ${Math.floor(h / 24)} days`;
}

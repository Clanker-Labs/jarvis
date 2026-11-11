/** Side-effect levels and MCP allow-list helpers (pure, unit-tested). */
import { SIDE_EFFECTS, type SideEffect } from "./types";

export function rank(level: SideEffect): number {
  return SIDE_EFFECTS.indexOf(level);
}

/** Levels a user may pick: the declared one or anything stricter (the backend only raises). */
export function stricterOrEqual(declared: SideEffect): SideEffect[] {
  return SIDE_EFFECTS.filter((l) => rank(l) >= rank(declared));
}

export const SIDE_EFFECT_TONE: Record<SideEffect, "good" | "warn" | "bad"> = {
  none: "good",
  reversible: "warn",
  irreversible: "bad",
};

export const SIDE_EFFECT_HINT: Record<SideEffect, string> = {
  none: "Read-only",
  reversible: "Changes something that can be undone",
  irreversible: "Asks for spoken confirmation",
};

/**
 * Toggle one tool in an MCP allow-list. `null` means "all discovered tools"; unticking one
 * switches to an explicit list, and ticking the last missing one goes back to `null`.
 */
export function toggleAllowed(
  allowed: string[] | null,
  all: string[],
  name: string,
  on: boolean,
): string[] | null {
  const current = allowed ?? all;
  const next = on ? (current.includes(name) ? current : [...current, name]) : current.filter((n) => n !== name);
  if (all.length > 0 && all.every((n) => next.includes(n))) return null;
  // Keep discovery order where we can.
  return [...all.filter((n) => next.includes(n)), ...next.filter((n) => !all.includes(n))];
}

export function isAllowed(allowed: string[] | null, name: string): boolean {
  return allowed === null || allowed.includes(name);
}

// ------------------------------------------------------------------ key/value rows

export interface KvRow {
  key: string;
  value: string;
}

export function recordToRows(r: Record<string, string>): KvRow[] {
  return Object.entries(r).map(([key, value]) => ({ key, value }));
}

/** Rows → record; blank keys are dropped, later duplicates win. */
export function rowsToRecord(rows: KvRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const { key, value } of rows) {
    const k = key.trim();
    if (k) out[k] = value;
  }
  return out;
}

/** True if a value looks like a pasted credential rather than a `${SECRET}` reference. */
export function looksLikeRawSecret(key: string, value: string): boolean {
  if (!value || /\$\{[A-Z][A-Z0-9_]*\}/.test(value)) return false;
  return /token|key|secret|password|auth/i.test(key) && value.length >= 12;
}

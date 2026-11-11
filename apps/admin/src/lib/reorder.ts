/** Ordered-list helpers for fallback chains (pure, unit-tested). */

/** Move item at `index` by `delta` (-1 = up, +1 = down). Out-of-range moves return an unchanged copy. */
export function move<T>(list: readonly T[], index: number, delta: number): T[] {
  const out = [...list];
  const to = index + delta;
  if (index < 0 || index >= out.length || to < 0 || to >= out.length) return out;
  const [item] = out.splice(index, 1) as [T];
  out.splice(to, 0, item);
  return out;
}

export function removeAt<T>(list: readonly T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}

/** Append if not already present. */
export function addUnique<T>(list: readonly T[], item: T): T[] {
  return list.includes(item) ? [...list] : [...list, item];
}

/** Rename an id inside every route list (used when a provider id is edited). */
export function renameInRoutes(
  routes: Record<string, string[]>,
  from: string,
  to: string,
): Record<string, string[]> {
  return Object.fromEntries(
    Object.entries(routes).map(([k, ids]) => [k, ids.map((id) => (id === from ? to : id))]),
  );
}

/** Remove an id from every route list (used when a provider is deleted). */
export function dropFromRoutes(
  routes: Record<string, string[]>,
  id: string,
): Record<string, string[]> {
  return Object.fromEntries(
    Object.entries(routes).map(([k, ids]) => [k, ids.filter((x) => x !== id)]),
  );
}

/** Textarea (one per line) → list. Trims and drops blanks. */
export function linesToList(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

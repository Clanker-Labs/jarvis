import { useEffect, useState } from "react";

export function useClock(intervalMs = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** "HH:MM" → minutes after midnight. */
export function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

/** True if `date` falls in [start, end), handling windows that wrap past midnight. */
export function inWindow(date: Date, start: string, end: string): boolean {
  const t = date.getHours() * 60 + date.getMinutes();
  const s = toMinutes(start);
  const e = toMinutes(end);
  return s <= e ? t >= s && t < e : t >= s || t < e;
}

/** Re-render every `ms` (used for fade timers). */
export function useTick(ms: number): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setN((x) => x + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
  return n;
}

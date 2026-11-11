/**
 * Live event socket (`WS /api/events`). One shared connection, open while at least one
 * component is subscribed; reconnects with exponential backoff.
 */
import { parseEvent, type JarvisEvent } from "@jarvis/protocol";
import { useEffect, useRef, useSyncExternalStore } from "react";

export type SocketStatus = "connecting" | "open" | "closed";

/** ws(s)://host/api/events from the page location. */
export function eventsUrl(loc: { protocol: string; host: string }): string {
  return `${loc.protocol === "https:" ? "wss:" : "ws:"}//${loc.host}/api/events`;
}

/** Reconnect delay: 0.5 s doubling up to 15 s, ±20 % jitter (`rand` injectable for tests). */
export function backoffDelay(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(15_000, 500 * 2 ** Math.max(0, attempt));
  return Math.round(base * (0.8 + 0.4 * rand()));
}

type Handler = (ev: JarvisEvent) => void;

class EventSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private statusListeners = new Set<() => void>();
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  status: SocketStatus = "closed";
  /** Called when the server refuses the socket (close code 1008 = not signed in). */
  onAuthRefused: (() => void) | null = null;

  subscribe(fn: Handler): () => void {
    this.handlers.add(fn);
    if (this.handlers.size === 1) this.open();
    return () => {
      this.handlers.delete(fn);
      if (this.handlers.size === 0) this.shutdown();
    };
  }

  watchStatus(fn: () => void): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }

  private setStatus(s: SocketStatus) {
    this.status = s;
    this.statusListeners.forEach((fn) => fn());
  }

  private open() {
    this.timer = null;
    if (this.ws || typeof WebSocket === "undefined") return;
    this.setStatus("connecting");
    const ws = new WebSocket(eventsUrl(location));
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.setStatus("open");
    };
    ws.onmessage = (m) => {
      const ev = typeof m.data === "string" ? parseEvent(m.data) : null;
      if (ev) this.handlers.forEach((fn) => fn(ev));
    };
    ws.onclose = (e) => {
      this.ws = null;
      this.setStatus("closed");
      if (e.code === 1008) this.onAuthRefused?.();
      if (this.handlers.size && !this.timer) {
        this.timer = setTimeout(() => this.open(), backoffDelay(this.attempt++));
      }
    };
  }

  private shutdown() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
    this.attempt = 0;
    this.setStatus("closed");
  }
}

export const eventSocket = new EventSocket();

/** Subscribe to live events for the component's lifetime. The handler may change between renders. */
export function useEvents(handler: Handler): SocketStatus {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => eventSocket.subscribe((ev) => ref.current(ev)), []);
  return useSyncExternalStore(
    (fn) => eventSocket.watchStatus(fn),
    () => eventSocket.status,
  );
}

export * from "./events.gen";
import type { JarvisEvent } from "./events.gen";

export const PROTOCOL_VERSION = 1;
export const MIC_SAMPLE_RATE = 16000;

export type EventType = JarvisEvent["type"];
/** Narrow the event union by its `type` discriminator. */
export type EventOf<T extends EventType> = Extract<JarvisEvent, { type: T }>;

export const STATES = [
  "IDLE",
  "WAKE",
  "LISTENING",
  "THINKING",
  "TOOL",
  "SPEAKING",
  "FOLLOW_UP",
  "ERROR",
] as const;
export type JarvisState = (typeof STATES)[number];

let counter = 0;
/** Build an outgoing event with the envelope fields filled in. */
export function makeEvent<T extends EventType>(
  type: T,
  body: Omit<EventOf<T>, "type" | "id" | "ts" | "turn_id"> & { turn_id?: string | null },
): EventOf<T> {
  counter = (counter + 1) % 1e9;
  return {
    id: `${Date.now().toString(36)}${counter.toString(36)}`,
    ts: Date.now() / 1000,
    turn_id: null,
    ...body,
    type,
  } as unknown as EventOf<T>;
}

/** Parse a JSON frame; returns null for anything that isn't a known-shaped event. */
export function parseEvent(raw: string): JarvisEvent | null {
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" && typeof v.type === "string" ? (v as JarvisEvent) : null;
  } catch {
    return null;
  }
}

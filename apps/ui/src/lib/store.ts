import { create } from "zustand";
import type { ConversationEntry, JarvisEvent, JarvisState, NowCard, UiConfig, Weather } from "@jarvis/protocol";
import { DEFAULT_COLORS, setLevel } from "../core/params";

export interface UiStore {
  connected: boolean;
  state: JarvisState;
  config: UiConfig;
  colors: Record<JarvisState, string>;
  status: { llm: boolean; internet: boolean; mic: boolean; offlineVoice: boolean; muted: boolean };
  weather: Weather | null;
  heard: { text: string; final: boolean; at: number };
  subtitle: { text: string; at: number; done: boolean };
  tool: { label: string; icon: string } | null;
  progress: string | null;
  recent: ConversationEntry[];
  now: NowCard;
  lastInteraction: number;
  apply: (e: JarvisEvent) => void;
  setConnected: (c: boolean) => void;
  touch: () => void;
}

const DEFAULT_CONFIG: UiConfig = {
  colors: {},
  quality: "auto",
  night_start: "23:00",
  night_end: "07:00",
  night_dim: 0.3,
  screen_sleep_min: 10,
  show_cards: true,
};

export const useUi = create<UiStore>((set, get) => ({
  connected: false,
  state: "IDLE",
  config: DEFAULT_CONFIG,
  colors: DEFAULT_COLORS,
  status: { llm: false, internet: false, mic: false, offlineVoice: false, muted: false },
  weather: null,
  heard: { text: "", final: false, at: 0 },
  subtitle: { text: "", at: 0, done: true },
  tool: null,
  progress: null,
  recent: [],
  now: { next_event_title: null, next_event_start: null, next_event_location: null, unread_important: null },
  lastInteraction: Date.now(),

  setConnected: (connected) => set({ connected }),
  touch: () => set({ lastInteraction: Date.now() }),

  apply: (e) => {
    const now = Date.now();
    switch (e.type) {
      case "ui.snapshot":
        set({
          state: e.state,
          config: e.config,
          colors: { ...DEFAULT_COLORS, ...(e.config.colors as Partial<Record<JarvisState, string>>) },
          recent: e.recent,
          now: e.now,
        });
        break;
      case "state.change": {
        const patch: Partial<UiStore> = { state: e.state };
        if (e.state !== "IDLE") patch.lastInteraction = now;
        if (e.state !== "TOOL") patch.tool = null;
        if (e.state === "LISTENING" && e.previous !== "LISTENING") patch.heard = { text: "", final: false, at: now };
        if (e.state === "IDLE") patch.progress = null;
        set(patch);
        break;
      }
      case "status.update":
        set({
          status: {
            llm: e.llm,
            internet: e.internet,
            mic: e.mic,
            offlineVoice: e.offline_voice,
            muted: e.muted,
          },
          weather: e.weather ?? get().weather,
        });
        break;
      case "listen.level":
        setLevel("mic", e.level);
        break;
      case "tts.audio_level":
        setLevel("tts", e.level);
        break;
      case "listen.partial":
        set({ heard: { text: e.text, final: false, at: now } });
        break;
      case "listen.final":
        set({ heard: { text: e.text, final: true, at: now } });
        break;
      case "tts.sentence":
        set({ subtitle: { text: e.text, at: now, done: false } });
        break;
      case "tts.end":
        set({ subtitle: { ...get().subtitle, done: true, at: now } });
        break;
      case "agent.tool_call":
        set({ tool: { label: e.label || e.tool, icon: e.icon } });
        break;
      case "agent.progress":
        set({ progress: e.message });
        break;
      case "agent.done":
        if (e.text) {
          set({
            recent: [
              { user: get().heard.text, assistant: e.text, ts: e.ts },
              ...get().recent,
            ].slice(0, 3),
          });
        }
        break;
      default:
        break;
    }
  },
}));

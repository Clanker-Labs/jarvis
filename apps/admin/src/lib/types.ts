/** Shapes of the admin API responses (docs/ADMIN_API.md). */
import type { JarvisState } from "@jarvis/protocol";

export type Stage =
  | "wake_to_listen"
  | "stt"
  | "llm_first_token"
  | "tts_first_audio"
  | "end_of_speech_to_audio";

export interface AuthState {
  authenticated: boolean;
  totp_enabled: boolean;
  needs_totp_setup: boolean;
}

export interface StatusFlags {
  llm: boolean;
  internet: boolean;
  mic: boolean;
  offline_voice: boolean;
  muted: boolean;
  brain: boolean;
}

export interface StageStat {
  p50: number | null;
  p90: number | null;
  mean: number | null;
  n: number;
}

export interface ProviderHealth {
  id: string;
  healthy: boolean | null;
  checked: number | null;
  model: string;
}

export interface Overview {
  state: JarvisState;
  status: StatusFlags;
  services: { brain: boolean; audio_node: boolean; ui_clients: number };
  providers: ProviderHealth[];
  voice: { stt: string[]; tts: string[] };
  stats: {
    turns: number;
    stages: Partial<Record<Stage, StageStat>>;
    last_24h: { turns: number; cost_usd: number };
  };
  uptime_s: number;
}

export interface TurnRow {
  id: string;
  started: number;
  ended: number | null;
  user_text: string | null;
  reply_text: string | null;
  provider: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  latency: Partial<Record<Stage, number>>;
  error: string | null;
}

/** A protocol event as stored in a trace (no `id`). */
export interface TraceEvent {
  type: string;
  ts: number;
  [key: string]: unknown;
}

export interface ToolCallTrace {
  tool: string;
  args: unknown;
  ok: boolean;
  summary: string;
  duration_s: number | null;
}

export interface TurnDetail extends Omit<TurnRow, "latency"> {
  latency: { timings?: Record<string, number>; stages?: Partial<Record<Stage, number>> };
  events: { events?: TraceEvent[]; tool_calls?: ToolCallTrace[] };
}

export interface LlmCall {
  ts: number;
  provider: string;
  model: string;
  route: string;
  ok: boolean;
  first_token_s: number | null;
  total_s: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  error: string;
}

// ------------------------------------------------------------------ config sections

export const PROVIDER_KINDS = ["anthropic", "openai", "local", "cli"] as const;
export const TOOL_MODES = ["native", "json", "auto"] as const;
export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;
export const CLIS = ["claude", "codex", "cursor-agent"] as const;
export const ROUTES = ["chat", "tools", "task"] as const;
export type RouteName = (typeof ROUTES)[number];

export interface ProviderEntry {
  id: string;
  kind: (typeof PROVIDER_KINDS)[number];
  model: string;
  base_url: string;
  api_key_secret: string;
  enabled: boolean;
  tool_mode: (typeof TOOL_MODES)[number];
  effort: (typeof EFFORTS)[number] | null;
  input_price: number | null;
  output_price: number | null;
  cli: (typeof CLIS)[number] | null;
}

export interface ProvidersConfig {
  providers: ProviderEntry[];
  routes: Record<string, string[]>;
  tool_keywords: string[];
  task_keywords: string[];
  health_interval_s: number;
}

export interface VoiceConfig {
  stt_chain: string[];
  tts_chain: string[];
  language: string;
  deepgram_model: string;
  openai_stt_model: string;
  whisper_remote_url: string;
  whisper_remote_model: string;
  whisper_local_model: string;
  openai_tts_model: string;
  openai_voice: string;
  openai_tts_instructions: string;
  elevenlabs_voice_id: string;
  elevenlabs_model: string;
  piper_voice: string;
  follow_up_s: number;
  vad_end_silence_ms: number;
  max_utterance_s: number;
  chime: boolean;
  wake_threshold: number;
}

export interface AppearanceConfig {
  colors: Record<string, string>;
  quality: "auto" | "high" | "medium" | "low";
  night_start: string;
  night_end: string;
  night_dim: number;
  screen_sleep_min: number;
  show_cards: boolean;
}

export interface LocationConfig {
  name: string;
  latitude: number | null;
  longitude: number | null;
  timezone: string;
  units: "metric" | "imperial";
}

export interface AgentConfig {
  max_steps: number;
  history_turns: number;
  user_name: string;
  session_timeout_min: number;
}

export interface SectionMap {
  providers: ProvidersConfig;
  voice: VoiceConfig;
  appearance: AppearanceConfig;
  location: LocationConfig;
  agent: AgentConfig;
}
export type SectionName = keyof SectionMap;

export interface Engine {
  id: string;
  available: boolean;
  local: boolean;
  streaming?: boolean;
}

export interface VoiceInfo {
  stt: Engine[];
  tts: Engine[];
  config: VoiceConfig;
  node_connected: boolean;
  monitor: boolean;
}

export interface SecretInfo {
  name: string;
  description: string;
  source: "db" | "env" | null;
  updated: number | null;
}

export interface ProviderTestResult {
  ok: boolean;
  text?: string;
  first_token_s?: number | null;
  total_s?: number;
  error?: string;
}

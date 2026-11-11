/* eslint-disable */
/** AUTO-GENERATED from packages/protocol (pydantic). Do not edit — run `make protocol`. */

export type JarvisEvent =
  | Hello
  | StatusUpdate
  | StateChange
  | ErrorEvent
  | WakeDetected
  | ListenStart
  | ListenLevel
  | ListenEnd
  | ListenPartial
  | ListenFinal
  | AgentThinking
  | AgentToolCall
  | AgentToolResult
  | AgentConfirmRequest
  | AgentProgress
  | AgentToken
  | AgentDone
  | TtsStart
  | TtsSentence
  | TtsAudioLevel
  | TtsEnd
  | PlaybackDone
  | BargeIn
  | NodeCommand
  | NodeConfig
  | WakeScore
  | UiCommand
  | UiSnapshot;
export type State =
  "IDLE" | "WAKE" | "LISTENING" | "THINKING" | "TOOL" | "SPEAKING" | "FOLLOW_UP" | "ERROR";
export type SideEffect = "none" | "reversible" | "irreversible";

/**
 * First message a client sends after connecting.
 */
export interface Hello {
  client_version: string;
  /**
   * Unique event id
   */
  id: string;
  protocol: number;
  role: "ui" | "audio-node" | "admin";
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "hello";
}
/**
 * Health of the moving parts, shown as connection dots on the UI.
 */
export interface StatusUpdate {
  brain: boolean;
  /**
   * Unique event id
   */
  id: string;
  internet: boolean;
  llm: boolean;
  mic: boolean;
  muted: boolean;
  /**
   * STT/TTS fell back to local engines
   */
  offline_voice: boolean;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "status.update";
  weather: Weather | null;
}
export interface Weather {
  condition: string;
  /**
   * Short icon key, e.g. 'clear', 'cloud', 'rain', 'snow', 'storm'
   */
  icon: string;
  location: string;
  temperature_c: number;
}
export interface StateChange {
  /**
   * Unique event id
   */
  id: string;
  previous: State;
  reason: string;
  state: State;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "state.change";
}
export interface ErrorEvent {
  code: string;
  /**
   * Unique event id
   */
  id: string;
  message: string;
  recoverable: boolean;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "error";
}
export interface WakeDetected {
  /**
   * Unique event id
   */
  id: string;
  model: string;
  score: number;
  source: "voice" | "touch";
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "wake.detected";
}
export interface ListenStart {
  follow_up: boolean;
  /**
   * Unique event id
   */
  id: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "listen.start";
}
/**
 * Microphone RMS level, 0..1, ~30 Hz while listening.
 */
export interface ListenLevel {
  /**
   * Unique event id
   */
  id: string;
  level: number;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "listen.level";
}
/**
 * Audio node finished capturing an utterance (endpointing decided).
 */
export interface ListenEnd {
  duration_s: number;
  /**
   * Unique event id
   */
  id: string;
  reason: "silence" | "max_duration" | "timeout" | "cancelled";
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "listen.end";
}
export interface ListenPartial {
  /**
   * Unique event id
   */
  id: string;
  text: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "listen.partial";
}
export interface ListenFinal {
  /**
   * Unique event id
   */
  id: string;
  stt_provider: string;
  text: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "listen.final";
}
export interface AgentThinking {
  /**
   * Unique event id
   */
  id: string;
  model: string;
  provider: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "agent.thinking";
}
export interface AgentToolCall {
  args: {
    [k: string]: unknown;
  };
  call_id: string;
  /**
   * Icon key for the UI chip
   */
  icon: string;
  /**
   * Unique event id
   */
  id: string;
  /**
   * Short human label for the UI chip
   */
  label: string;
  side_effect: SideEffect;
  tool: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "agent.tool_call";
}
export interface AgentToolResult {
  call_id: string;
  /**
   * Unique event id
   */
  id: string;
  ok: boolean;
  summary: string;
  tool: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "agent.tool_result";
}
/**
 * An irreversible tool call is waiting for a spoken yes/no.
 */
export interface AgentConfirmRequest {
  call_id: string;
  /**
   * Unique event id
   */
  id: string;
  prompt: string;
  tool: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "agent.confirm_request";
}
/**
 * Long-running work (agent-CLI providers) reporting progress.
 */
export interface AgentProgress {
  /**
   * Unique event id
   */
  id: string;
  message: string;
  provider: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "agent.progress";
}
export interface AgentToken {
  /**
   * Unique event id
   */
  id: string;
  text: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "agent.token";
}
export interface AgentDone {
  cost_usd: number;
  /**
   * Unique event id
   */
  id: string;
  input_tokens: number;
  interrupted: boolean;
  model: string;
  output_tokens: number;
  provider: string;
  text: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "agent.done";
}
export interface TtsStart {
  /**
   * Unique event id
   */
  id: string;
  provider: string;
  sample_rate: number;
  stream_id: number;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "tts.start";
}
/**
 * Subtitle for the sentence that is about to be spoken.
 */
export interface TtsSentence {
  /**
   * Unique event id
   */
  id: string;
  stream_id: number;
  text: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "tts.sentence";
}
export interface TtsAudioLevel {
  /**
   * Unique event id
   */
  id: string;
  level: number;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "tts.audio_level";
}
/**
 * Brain finished sending audio for a stream.
 */
export interface TtsEnd {
  /**
   * Unique event id
   */
  id: string;
  interrupted: boolean;
  stream_id: number;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "tts.end";
}
/**
 * Audio node finished (or aborted) playing a stream.
 */
export interface PlaybackDone {
  /**
   * Unique event id
   */
  id: string;
  interrupted: boolean;
  stream_id: number;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "playback.done";
}
export interface BargeIn {
  /**
   * Unique event id
   */
  id: string;
  source: "voice" | "touch";
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "barge_in";
}
/**
 * Brain → audio node.
 */
export interface NodeCommand {
  command: "listen.open" | "listen.cancel" | "playback.stop" | "chime" | "mute" | "unmute";
  follow_up: boolean;
  /**
   * Unique event id
   */
  id: string;
  /**
   * listen.open: give up if no speech starts within this window
   */
  timeout_s: number | null;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "node.command";
}
/**
 * Brain → audio node: live-tunable voice settings (from the admin Voice page).
 */
export interface NodeConfig {
  chime: boolean;
  /**
   * Unique event id
   */
  id: string;
  max_utterance_s: number;
  monitor: boolean;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "node.config";
  vad_end_silence_ms: number;
  wake_threshold: number;
}
/**
 * Audio node → brain while monitoring: best wake-word score of the last ~0.5 s.
 */
export interface WakeScore {
  /**
   * Unique event id
   */
  id: string;
  score: number;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "wake.score";
}
/**
 * UI → brain (touch interactions).
 */
export interface UiCommand {
  command: "push_to_talk" | "mute" | "unmute" | "barge_in" | "screen_wake";
  /**
   * Unique event id
   */
  id: string;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "ui.command";
}
/**
 * Everything the UI needs after (re)connecting or when side data changes.
 */
export interface UiSnapshot {
  config: UiConfig;
  /**
   * Unique event id
   */
  id: string;
  now: NowCard;
  recent: ConversationEntry[];
  state: State;
  /**
   * Unix time (seconds) at creation
   */
  ts: number;
  /**
   * Conversation turn this event belongs to
   */
  turn_id: string | null;
  type: "ui.snapshot";
}
/**
 * Appearance settings pushed to the kiosk UI.
 */
export interface UiConfig {
  /**
   * State → #RRGGBB
   */
  colors: {
    [k: string]: string;
  };
  night_dim: number;
  night_end: string;
  night_start: string;
  quality: "auto" | "high" | "medium" | "low";
  screen_sleep_min: number;
  show_cards: boolean;
}
export interface NowCard {
  next_event_location: string | null;
  /**
   * Unix seconds
   */
  next_event_start: number | null;
  next_event_title: string | null;
  unread_important: number | null;
}
export interface ConversationEntry {
  assistant: string;
  ts: number;
  user: string;
}

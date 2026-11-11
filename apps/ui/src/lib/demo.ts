import { makeEvent, type JarvisState } from "@jarvis/protocol";
import { useUi } from "./store";

/** `?demo=1`: cycle through every state with synthetic levels — no brain required. */
export function runDemo(): () => void {
  const script: [JarvisState, number, (() => void)?][] = [
    ["IDLE", 4000],
    ["WAKE", 400],
    ["LISTENING", 3000, () => say("listen.partial", "What's on my calendar")],
    ["THINKING", 2200, () => say("listen.final", "What's on my calendar tomorrow?")],
    ["TOOL", 2000],
    ["SPEAKING", 4200, () => sentence("You have two things tomorrow: the dentist at ten, and dinner with Sam at eight.")],
    ["FOLLOW_UP", 2500],
    ["ERROR", 1800],
  ];
  const ui = useUi.getState();
  ui.setConnected(true);
  let i = 0;
  let prev: JarvisState = "IDLE";
  let timer = 0;
  const step = () => {
    const [state, ms, fx] = script[i % script.length]!;
    useUi.getState().apply(makeEvent("state.change", { state, previous: prev, reason: "demo" }));
    if (state === "TOOL")
      useUi.getState().apply(
        makeEvent("agent.tool_call", { call_id: "d", tool: "calendar.list", args: {}, side_effect: "none", label: "Calendar", icon: "calendar" }),
      );
    if (state === "FOLLOW_UP") useUi.getState().apply(makeEvent("tts.end", { stream_id: 1, interrupted: false }));
    fx?.();
    prev = state;
    i++;
    timer = window.setTimeout(step, ms);
  };
  step();

  // Synthetic mic / TTS levels at 30 Hz.
  const levels = window.setInterval(() => {
    const t = performance.now() / 1000;
    const s = useUi.getState().state;
    if (s === "LISTENING")
      useUi.getState().apply(makeEvent("listen.level", { level: Math.abs(Math.sin(t * 7) * Math.sin(t * 2.3)) * 0.8 }));
    if (s === "SPEAKING")
      useUi.getState().apply(
        makeEvent("tts.audio_level", { level: Math.max(0, Math.sin(t * 11) * 0.5 + Math.sin(t * 3.1) * 0.4) }),
      );
  }, 33);
  return () => {
    clearTimeout(timer);
    clearInterval(levels);
  };
}

function say(type: "listen.partial" | "listen.final", text: string) {
  useUi
    .getState()
    .apply(type === "listen.final" ? makeEvent(type, { text, stt_provider: "demo" }) : makeEvent(type, { text }));
}

function sentence(text: string) {
  useUi.getState().apply(makeEvent("tts.sentence", { stream_id: 1, text }));
}

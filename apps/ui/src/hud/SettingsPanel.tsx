import { useEffect, useRef } from "react";
import { Mic, MicOff, Play, Volume2, X } from "lucide-react";
import { levels } from "../core/params";
import { sendCommand } from "../lib/connection";
import { useUi } from "../lib/store";

const AUTO_CLOSE_MS = 90_000; // a kiosk panel left open would keep the mic meter streaming

/** Mic level bar driven straight from the socket's level buffer (no React re-render per frame). */
function LevelMeter() {
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let raf = 0;
    let shown = 0;
    const tick = () => {
      const fresh = performance.now() - levels.micAt < 400;
      const target = fresh ? levels.mic : 0;
      shown += (target - shown) * (target > shown ? 0.6 : 0.15);
      if (bar.current) bar.current.style.transform = `scaleX(${shown.toFixed(3)})`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  return (
    <div className="h-2.5 w-full overflow-hidden rounded-full bg-white/8">
      <div
        ref={bar}
        className="h-full w-full origin-left rounded-full bg-gradient-to-r from-teal-500 via-teal-300 to-amber-300"
        style={{ transform: "scaleX(0)" }}
      />
    </div>
  );
}

function Row({ label, ok, value }: { label: string; ok: boolean; value: string }) {
  return (
    <div className="flex items-center justify-between py-1.5 text-[14px]">
      <span className="text-white/65">{label}</span>
      <span className="flex items-center gap-2 text-white/80">
        <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-teal-400" : "bg-white/20"}`} />
        {value}
      </span>
    </div>
  );
}

const TEST_LABEL = {
  idle: "Record 3 s and play it back",
  recording: "Recording — say something…",
  playing: "Playing back…",
  done: "Record again",
  failed: "Try again",
} as const;

export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const status = useUi((s) => s.status);
  const connected = useUi((s) => s.connected);
  const test = useUi((s) => s.micTest);
  const wakeScore = useUi((s) => s.wakeScore);
  const lastInteraction = useUi((s) => s.lastInteraction);

  // Stream mic levels while the panel is open (they normally only flow while listening).
  useEffect(() => {
    useUi.setState({ micTest: { phase: "idle", peak: 0, detail: "" }, wakeScore: 0 });
    sendCommand("monitor_on");
    return () => sendCommand("monitor_off");
  }, [connected]);

  useEffect(() => {
    const t = window.setTimeout(onClose, AUTO_CLOSE_MS - (Date.now() - lastInteraction));
    return () => clearTimeout(t);
  }, [lastInteraction, onClose]);

  const micText = !connected
    ? "brain offline"
    : !status.mic
      ? "not connected"
      : status.muted
        ? "muted"
        : "listening";
  const busy = test.phase === "recording" || test.phase === "playing";
  let result = "";
  if (test.phase === "failed") result = test.detail || "The test failed.";
  else if (test.phase === "done")
    result =
      test.peak < 0.15
        ? "Heard almost nothing — check the mic isn't muted or too far away."
        : "Mic works. Did you hear yourself? If not, check the speaker.";

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
      onPointerDown={(e) => {
        e.stopPropagation(); // no swipe / push-to-talk underneath
        useUi.getState().touch();
        if (e.target === e.currentTarget) onClose();
      }}
      onPointerUp={(e) => e.stopPropagation()}
    >
      <section
        role="dialog"
        aria-label="Settings"
        className="max-h-[92vh] w-[min(560px,92vw)] overflow-y-auto rounded-3xl border border-white/10 bg-[#0d1219] p-7 text-white/90 shadow-[0_24px_80px_rgba(0,0,0,0.7)]"
      >
        <header className="mb-5 flex items-center justify-between">
          <h2 className="text-[20px] font-light tracking-wide">Settings</h2>
          <button
            type="button"
            aria-label="Close settings"
            className="-m-2 rounded-full p-2 text-white/65 active:bg-white/10"
            onClick={onClose}
          >
            <X className="h-5 w-5" strokeWidth={1.5} />
          </button>
        </header>

        <h3 className="mb-2 text-[11px] tracking-[0.2em] text-white/55 uppercase">Microphone</h3>
        <div className="mb-4 flex items-center gap-3">
          {status.mic && !status.muted ? (
            <Mic className="h-6 w-6 shrink-0 text-teal-300" strokeWidth={1.5} />
          ) : (
            <MicOff className="h-6 w-6 shrink-0 text-white/55" strokeWidth={1.5} />
          )}
          <div className="min-w-0 flex-1">
            <div className="truncate text-[16px]">{status.micDevice || "No microphone"}</div>
            <div className="text-[13px] text-white/65">
              {status.mic ? micText : "Plug in a USB mic or speakerphone — Jarvis picks it up within a few seconds."}
            </div>
          </div>
          <button
            type="button"
            disabled={!connected || !status.mic}
            className="rounded-full border border-white/12 px-4 py-2 text-[13px] text-white/75 active:bg-white/10 disabled:opacity-30"
            onClick={() => sendCommand(status.muted ? "unmute" : "mute")}
          >
            {status.muted ? "Unmute" : "Mute"}
          </button>
        </div>

        <div className="mb-1 flex justify-between text-[12px] text-white/60">
          <span>Level</span>
          <span className="tabular-nums">Wake word {Math.round(wakeScore * 100)}%</span>
        </div>
        <LevelMeter />
        <p className="mt-2 text-[12px] text-white/55">Say “Hey Jarvis” — the wake-word score should jump.</p>

        <div className="mt-5 flex gap-3">
          <button
            type="button"
            disabled={!connected || !status.mic || busy}
            className="flex flex-1 items-center justify-center gap-2 rounded-2xl bg-teal-400/15 px-4 py-3 text-[14px] text-teal-100 active:bg-teal-400/25 disabled:opacity-40"
            onClick={() => sendCommand("mic_test")}
          >
            <span className={`h-2 w-2 rounded-full ${test.phase === "recording" ? "animate-pulse bg-red-400" : "bg-teal-300"}`} />
            {TEST_LABEL[test.phase]}
          </button>
          <button
            type="button"
            disabled={!connected}
            className="flex items-center gap-2 rounded-2xl border border-white/12 px-4 py-3 text-[14px] text-white/75 active:bg-white/10 disabled:opacity-40"
            onClick={() => sendCommand("speaker_test")}
          >
            <Volume2 className="h-4 w-4" strokeWidth={1.5} />
            Speaker
          </button>
        </div>
        {result && (
          <p className={`mt-3 flex items-center gap-2 text-[13px] ${test.phase === "failed" ? "text-amber-300/85" : "text-white/60"}`}>
            <Play className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
            {result}
          </p>
        )}

        <h3 className="mt-6 mb-1 text-[11px] tracking-[0.2em] text-white/55 uppercase">Connections</h3>
        <Row label="Brain" ok={connected} value={connected ? "connected" : "offline"} />
        <Row label="Language model" ok={connected && status.llm} value={status.llm ? "ready" : "unavailable"} />
        <Row label="Internet" ok={connected && status.internet} value={status.internet ? "online" : "offline"} />

        <p className="mt-5 text-[12px] leading-relaxed text-white/50">
          Jarvis listens through the Pi's own audio hardware, not the browser, so the browser never asks for
          microphone permission. Other settings live in the admin panel on your tailnet.
        </p>
      </section>
    </div>
  );
}

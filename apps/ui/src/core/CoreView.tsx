import { useEffect, useRef, useState } from "react";
import { CoreRenderer, type FrameStats, type Quality } from "./CoreRenderer";
import { useUi } from "../lib/store";
import { sendCommand } from "../lib/connection";

const LONG_PRESS_MS = 650;

interface Props {
  dim: number;
  timeScale: number;
  paused: boolean;
  showFps: boolean;
}

function storedQuality(): Quality | null {
  const q = new URLSearchParams(location.search).get("quality");
  return q === "high" || q === "medium" || q === "low" ? q : null;
}

export function CoreView({ dim, timeScale, paused, showFps }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<CoreRenderer | null>(null);
  const [stats, setStats] = useState<FrameStats | null>(null);
  const state = useUi((s) => s.state);
  const colors = useUi((s) => s.colors);
  const configQuality = useUi((s) => s.config.quality);
  const muted = useUi((s) => s.status.muted);

  // Create once; quality changes are applied in place.
  useEffect(() => {
    const canvas = canvasRef.current!;
    const forced = storedQuality();
    const q: Quality = forced ?? (configQuality === "auto" ? "high" : configQuality);
    const r = new CoreRenderer(canvas, q, !forced && configQuality === "auto");
    r.onStats = setStats;
    rendererRef.current = r;
    r.start();
    const ro = new ResizeObserver(() => r.resize());
    ro.observe(canvas);
    return () => {
      ro.disconnect();
      r.dispose();
      rendererRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (configQuality !== "auto" && !storedQuality()) rendererRef.current?.setQuality(configQuality);
  }, [configQuality]);

  useEffect(() => {
    rendererRef.current?.setState(state, colors[state]);
  }, [state, colors]);

  useEffect(() => {
    rendererRef.current?.setAmbience(paused ? 0 : muted ? dim * 0.35 : dim, timeScale);
  }, [dim, timeScale, muted, paused]);

  // Tap = push-to-talk (or barge-in while speaking). Long-press = toggle mute.
  const press = useRef<{ timer: number; long: boolean } | null>(null);
  const onDown = () => {
    useUi.getState().touch();
    const p = { timer: 0, long: false };
    p.timer = window.setTimeout(() => {
      p.long = true;
      sendCommand(useUi.getState().status.muted ? "unmute" : "mute");
      navigator.vibrate?.(20);
    }, LONG_PRESS_MS);
    press.current = p;
  };
  const onUp = () => {
    const p = press.current;
    press.current = null;
    if (!p) return;
    clearTimeout(p.timer);
    if (p.long) return;
    const s = useUi.getState().state;
    if (s === "SPEAKING" || s === "THINKING" || s === "TOOL") sendCommand("barge_in");
    else if (s === "IDLE" || s === "FOLLOW_UP") sendCommand("push_to_talk");
  };
  const onCancel = () => {
    if (press.current) clearTimeout(press.current.timer);
    press.current = null;
  };

  return (
    <div className="relative flex items-center justify-center" style={{ height: "56vh", width: "56vh" }}>
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      {/* Hit target sized to the sphere itself (≈45% of screen height). */}
      <button
        type="button"
        aria-label={state === "SPEAKING" ? "Interrupt" : "Talk to Jarvis"}
        className="relative z-10 rounded-full outline-none"
        style={{ height: "44vh", width: "44vh", WebkitTapHighlightColor: "transparent" }}
        onPointerDown={onDown}
        onPointerUp={onUp}
        onPointerLeave={onCancel}
        onPointerCancel={onCancel}
        onContextMenu={(e) => e.preventDefault()}
      />
      {showFps && stats && (
        <div className="absolute -bottom-4 font-mono text-[11px] text-white/40 tabular-nums">
          {stats.fps.toFixed(0)} fps · {stats.quality}
        </div>
      )}
    </div>
  );
}

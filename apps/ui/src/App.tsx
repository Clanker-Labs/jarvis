import { useCallback, useEffect, useRef, useState } from "react";
import { CoreView } from "./core/CoreView";
import { SettingsPanel } from "./hud/SettingsPanel";
import { TopBar } from "./hud/TopBar";
import { Transcript } from "./hud/Transcript";
import { NowCard, RecentCard } from "./hud/SideCards";
import { inWindow, useClock } from "./lib/hooks";
import { useUi } from "./lib/store";

const params = new URLSearchParams(location.search);
const SHOW_FPS = params.has("fps");
const SWIPE_PX = 90;
const NIGHT_OVERRIDE = params.get("night"); // "0"/"1" forces night mode off/on (testing)

export function App() {
  const now = useClock(15_000);
  const config = useUi((s) => s.config);
  const state = useUi((s) => s.state);
  const lastInteraction = useUi((s) => s.lastInteraction);
  const connected = useUi((s) => s.connected);
  const [cardsHidden, setCardsHidden] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  const night =
    NIGHT_OVERRIDE != null ? NIGHT_OVERRIDE === "1" : inWindow(now, config.night_start, config.night_end);
  const sleeping =
    config.screen_sleep_min > 0 &&
    state === "IDLE" &&
    Date.now() - lastInteraction > config.screen_sleep_min * 60_000;

  // Horizontal swipe anywhere outside the core toggles the side cards ("pure core" view).
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    useUi.getState().touch();
    swipe.current = { x: e.clientX, y: e.clientY };
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const s = swipe.current;
    swipe.current = null;
    if (!s) return;
    const dx = e.clientX - s.x;
    if (Math.abs(dx) > SWIPE_PX && Math.abs(dx) > 2 * Math.abs(e.clientY - s.y)) setCardsHidden((h) => !h);
  };

  useEffect(() => {
    document.documentElement.style.cursor = params.has("cursor") ? "auto" : "none";
  }, []);

  const showCards = config.show_cards && !cardsHidden;

  return (
    <main
      className="vignette relative flex h-screen w-screen flex-col overflow-hidden select-none"
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
    >
      <TopBar onSettings={() => setSettingsOpen(true)} />
      <div className="relative flex flex-1 items-center justify-center">
        <aside
          className={`absolute left-10 transition-all duration-700 ease-out ${
            showCards ? "translate-x-0 opacity-100" : "-translate-x-8 opacity-0 pointer-events-none"
          }`}
        >
          <NowCard />
        </aside>
        <div className="flex flex-col items-center">
          <CoreView dim={1} timeScale={night ? 0.5 : 1} paused={sleeping} showFps={SHOW_FPS} />
          <Transcript />
        </div>
        <aside
          className={`absolute right-10 transition-all duration-700 ease-out ${
            showCards ? "translate-x-0 opacity-100" : "translate-x-8 opacity-0 pointer-events-none"
          }`}
        >
          <RecentCard />
        </aside>
      </div>
      {!connected && (
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 text-[12px] tracking-[0.2em] text-white/30 uppercase">
          connecting to brain…
        </div>
      )}
      {settingsOpen && <SettingsPanel onClose={closeSettings} />}
      {/* Night dim and screen sleep are a single cheap overlay (no CSS filters on the canvas). */}
      <div
        className="pointer-events-none absolute inset-0 bg-black transition-opacity duration-[1500ms]"
        style={{ opacity: sleeping ? 1 : night ? 1 - config.night_dim : 0 }}
      />
    </main>
  );
}

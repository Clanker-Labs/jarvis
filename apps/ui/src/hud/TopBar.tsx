import { Cloud, CloudLightning, CloudRain, CloudSnow, CloudFog, Sun, MicOff } from "lucide-react";
import { useClock } from "../lib/hooks";
import { useUi } from "../lib/store";

const WEATHER_ICONS = { clear: Sun, cloud: Cloud, rain: CloudRain, snow: CloudSnow, storm: CloudLightning, fog: CloudFog };

function Dot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-1.5" title={label}>
      <span
        className={`h-1.5 w-1.5 rounded-full transition-colors duration-500 ${
          ok ? "bg-teal-400 shadow-[0_0_6px_rgba(45,212,191,0.8)]" : "bg-white/15"
        }`}
      />
      <span className="text-[11px] tracking-wider text-white/35 uppercase">{label}</span>
    </div>
  );
}

export function TopBar() {
  const now = useClock();
  const connected = useUi((s) => s.connected);
  const status = useUi((s) => s.status);
  const weather = useUi((s) => s.weather);
  const Icon = weather ? (WEATHER_ICONS[weather.icon as keyof typeof WEATHER_ICONS] ?? Cloud) : null;

  return (
    <header className="flex items-start justify-between px-10 pt-7">
      <div className="flex items-baseline gap-5">
        <time className="text-[64px] leading-none font-extralight tracking-tight text-white/90 tabular-nums">
          {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}
        </time>
        <div className="flex flex-col">
          <span className="text-[15px] font-light text-white/60">
            {now.toLocaleDateString([], { weekday: "long" })}
          </span>
          <span className="text-[15px] font-light text-white/40">
            {now.toLocaleDateString([], { day: "numeric", month: "long" })}
          </span>
        </div>
      </div>
      <div className="flex flex-col items-end gap-3">
        {weather && Icon && (
          <div className="flex items-center gap-2 text-white/70">
            <Icon className="h-5 w-5" strokeWidth={1.25} />
            <span className="text-[22px] font-light tabular-nums">{Math.round(weather.temperature_c)}°</span>
          </div>
        )}
        <div className="flex items-center gap-4">
          {status.offlineVoice && (
            <span className="rounded-full border border-amber-400/30 px-2 py-0.5 text-[10px] tracking-wider text-amber-300/80 uppercase">
              offline voice
            </span>
          )}
          {status.muted && <MicOff className="h-4 w-4 text-red-400/80" strokeWidth={1.5} />}
          <Dot ok={connected} label="brain" />
          <Dot ok={connected && status.llm} label="llm" />
          <Dot ok={connected && status.internet} label="net" />
          <Dot ok={connected && status.mic && !status.muted} label="mic" />
        </div>
      </div>
    </header>
  );
}

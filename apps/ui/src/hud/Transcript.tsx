import { useTick } from "../lib/hooks";
import { useUi } from "../lib/store";

const HEARD_FADE_MS = 3500;
const SUBTITLE_HOLD_MS = 2500;

/** My words while listening (fade out), then Jarvis's reply as subtitles. */
export function Transcript() {
  useTick(250);
  const state = useUi((s) => s.state);
  const heard = useUi((s) => s.heard);
  const subtitle = useUi((s) => s.subtitle);
  const tool = useUi((s) => s.tool);
  const progress = useUi((s) => s.progress);
  const now = Date.now();

  const showHeard =
    heard.text && (state === "LISTENING" || state === "THINKING" || now - heard.at < HEARD_FADE_MS) && state !== "SPEAKING";
  const showSub = subtitle.text && (!subtitle.done || now - subtitle.at < SUBTITLE_HOLD_MS);

  return (
    <div className="flex h-[124px] w-[860px] flex-col items-center justify-start gap-3 text-center">
      {tool && state === "TOOL" && (
        <div className="glass flex items-center gap-2 rounded-full px-4 py-1.5 text-[13px] tracking-wide text-violet-200/80">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-violet-400" />
          {tool.label}
        </div>
      )}
      {progress && (state === "THINKING" || state === "TOOL") && (
        <p className="text-[15px] font-light text-violet-200/60">{progress}</p>
      )}
      <div className="grid w-full place-items-center">
        <p
          className={`col-start-1 row-start-1 line-clamp-2 text-[22px] leading-snug font-light transition-opacity duration-700 ${
            heard.final ? "text-white/55" : "text-amber-100/80"
          } ${showHeard ? "opacity-100" : "opacity-0"}`}
        >
          {heard.text}
        </p>
        <p
          className={`col-start-1 row-start-1 line-clamp-2 text-[24px] leading-snug font-light text-teal-50/90 transition-opacity duration-500 ${
            showSub ? "opacity-100" : "opacity-0"
          }`}
        >
          {subtitle.text}
        </p>
      </div>
    </div>
  );
}

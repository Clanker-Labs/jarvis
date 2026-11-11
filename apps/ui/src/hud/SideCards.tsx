import { CalendarClock, Mail, MessagesSquare } from "lucide-react";
import { useTick } from "../lib/hooks";
import { useUi } from "../lib/store";

function relTime(unixS: number): string {
  const d = new Date(unixS * 1000);
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  if (d.toDateString() === today.toDateString()) return hm;
  if (d.toDateString() === tomorrow.toDateString()) return `Tomorrow ${hm}`;
  return `${d.toLocaleDateString([], { weekday: "short" })} ${hm}`;
}

function Card({ title, icon, children, side }: { title: string; icon: React.ReactNode; children: React.ReactNode; side: "left" | "right" }) {
  return (
    <section className={`glass w-[270px] rounded-3xl p-5 ${side === "left" ? "" : ""}`}>
      <h2 className="mb-3 flex items-center gap-2 text-[11px] font-medium tracking-[0.18em] text-white/40 uppercase">
        {icon}
        {title}
      </h2>
      {children}
    </section>
  );
}

export function NowCard() {
  useTick(30_000);
  const now = useUi((s) => s.now);
  return (
    <Card title="Now" side="left" icon={<CalendarClock className="h-3.5 w-3.5" strokeWidth={1.5} />}>
      {now.next_event_title ? (
        <div className="space-y-1">
          <p className="text-[17px] leading-tight font-light text-white/85">{now.next_event_title}</p>
          <p className="text-[13px] text-white/45 tabular-nums">
            {now.next_event_start ? relTime(now.next_event_start) : ""}
            {now.next_event_location ? ` · ${now.next_event_location}` : ""}
          </p>
        </div>
      ) : (
        <p className="text-[14px] font-light text-white/35">Nothing scheduled</p>
      )}
      {now.unread_important != null && (
        <div className="mt-4 flex items-center gap-2 border-t border-white/[0.06] pt-3 text-[13px] text-white/55">
          <Mail className="h-3.5 w-3.5" strokeWidth={1.5} />
          <span className="tabular-nums">{now.unread_important}</span> important unread
        </div>
      )}
    </Card>
  );
}

export function RecentCard() {
  const recent = useUi((s) => s.recent);
  return (
    <Card title="Recent" side="right" icon={<MessagesSquare className="h-3.5 w-3.5" strokeWidth={1.5} />}>
      {recent.length === 0 ? (
        <p className="text-[14px] font-light text-white/35">Say “Hey Jarvis”</p>
      ) : (
        <ul className="space-y-3">
          {recent.slice(0, 3).map((r) => (
            <li key={r.ts} className="space-y-0.5">
              <p className="line-clamp-1 text-[13px] text-white/45">{r.user}</p>
              <p className="line-clamp-2 text-[14px] leading-snug font-light text-white/80">{r.assistant}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

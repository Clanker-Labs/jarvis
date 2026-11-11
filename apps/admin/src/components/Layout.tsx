import { useQueryClient } from "@tanstack/react-query";
import { Cpu, KeyRound, LayoutDashboard, LogOut, Mic, Palette, Settings as SettingsIcon } from "lucide-react";
import { NavLink, Outlet } from "react-router";
import { post } from "../lib/api";
import { LiveProvider, useLive } from "../lib/live";
import { Orb, StatePill, STATE_COLORS } from "./widgets";
import { cx } from "./ui";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/providers", label: "Providers", icon: Cpu },
  { to: "/voice", label: "Voice", icon: Mic },
  { to: "/appearance", label: "Appearance", icon: Palette },
  { to: "/secrets", label: "Secrets", icon: KeyRound },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export function Layout() {
  return (
    <LiveProvider>
      <div className="flex min-h-screen">
        <Sidebar />
        <main className="min-w-0 flex-1 px-6 py-5 lg:px-8">
          <div className="mx-auto max-w-[1400px]">
            <Outlet />
          </div>
        </main>
      </div>
    </LiveProvider>
  );
}

function Sidebar() {
  const live = useLive();
  const qc = useQueryClient();
  const signOut = async () => {
    await post("/api/auth/logout").catch(() => {});
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
    await qc.invalidateQueries({ queryKey: ["auth"] });
  };
  return (
    <aside className="sticky top-0 flex h-screen w-52 shrink-0 flex-col border-r border-white/[0.07] bg-black/30">
      <div className="flex items-center gap-2.5 px-4 pt-4 pb-3">
        <Orb color={live.state ? STATE_COLORS[live.state] : "#0EA5E9"} size={26} />
        <div className="leading-tight">
          <div className="text-[13px] font-semibold tracking-[0.18em] text-white">JARVIS</div>
          <div className="text-[11px] text-white/40">Admin</div>
        </div>
      </div>
      <div className="px-4 pb-3">
        <StatePill state={live.state} live={live.socket === "open"} />
        {live.socket !== "open" && (
          <p className="mt-1.5 text-[11px] text-amber-300/80">{live.socket === "connecting" ? "Connecting…" : "Live feed offline — retrying"}</p>
        )}
      </div>
      <nav aria-label="Main" className="flex flex-1 flex-col gap-0.5 px-2">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cx(
                "focus-ring flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13px] transition-colors",
                isActive ? "bg-white/[0.08] font-medium text-white" : "text-white/60 hover:bg-white/[0.04] hover:text-white",
              )
            }
          >
            <Icon className="size-4" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-white/[0.07] p-2">
        <button
          type="button"
          onClick={() => void signOut()}
          className="focus-ring flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-[13px] text-white/55 hover:bg-white/[0.04] hover:text-white"
        >
          <LogOut className="size-4" aria-hidden /> Sign out
        </button>
      </div>
    </aside>
  );
}

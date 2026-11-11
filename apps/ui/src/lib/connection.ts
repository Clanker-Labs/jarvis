import { makeEvent, parseEvent, type EventOf } from "@jarvis/protocol";
import { useUi } from "./store";

interface RuntimeConfig {
  brainUrl: string;
  token: string;
}

let socket: WebSocket | null = null;

async function loadConfig(): Promise<RuntimeConfig> {
  const params = new URLSearchParams(location.search);
  let cfg: RuntimeConfig = { brainUrl: `ws://${location.hostname}:8000/ws/ui`, token: "" };
  try {
    const r = await fetch("/config.json", { cache: "no-store" });
    if (r.ok) cfg = { ...cfg, ...(await r.json()) };
  } catch {
    /* fall back to defaults */
  }
  if (params.get("brain")) cfg.brainUrl = params.get("brain")!;
  return cfg;
}

export async function connect(): Promise<void> {
  const cfg = await loadConfig();
  let delay = 500;

  const open = () => {
    const url = new URL(cfg.brainUrl);
    if (cfg.token) url.searchParams.set("token", cfg.token);
    const ws = new WebSocket(url);
    socket = ws;
    ws.onopen = () => {
      delay = 500;
      useUi.getState().setConnected(true);
    };
    ws.onmessage = (m) => {
      if (typeof m.data !== "string") return;
      const ev = parseEvent(m.data);
      if (ev) useUi.getState().apply(ev);
    };
    ws.onclose = () => {
      socket = null;
      useUi.getState().setConnected(false);
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 8000);
    };
    ws.onerror = () => ws.close();
  };
  open();
}

export function sendCommand(command: EventOf<"ui.command">["command"]): void {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(makeEvent("ui.command", { command })));
  }
}

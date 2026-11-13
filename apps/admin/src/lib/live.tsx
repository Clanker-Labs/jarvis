/** Live state from the event socket, shared by the shell (sidebar pill) and pages. */
import type { JarvisState, StatusUpdate } from "@jarvis/protocol";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";
import { useEvents, type SocketStatus } from "./events";

export interface Live {
  state: JarvisState | undefined;
  status: Omit<StatusUpdate, "id" | "ts" | "turn_id" | "type" | "weather"> | undefined;
  socket: SocketStatus;
}

const Ctx = createContext<Live>({ state: undefined, status: undefined, socket: "closed" });

export function useLive(): Live {
  return useContext(Ctx);
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [state, setState] = useState<JarvisState>();
  const [status, setStatus] = useState<Live["status"]>();
  const socket = useEvents((ev) => {
    switch (ev.type) {
      case "ui.snapshot":
      case "state.change":
        setState(ev.state);
        break;
      case "status.update": {
        const { brain, llm, internet, mic, mic_device, muted, offline_voice } = ev;
        setStatus({ brain, llm, internet, mic, mic_device, muted, offline_voice });
        break;
      }
      case "agent.done":
        // A turn finished: refresh the conversation list and stats shortly after it's saved.
        setTimeout(() => {
          void qc.invalidateQueries({ queryKey: ["turns"] });
          void qc.invalidateQueries({ queryKey: ["overview"] });
          void qc.invalidateQueries({ queryKey: ["llm-calls"] });
        }, 1500);
        break;
    }
  });
  return <Ctx.Provider value={{ state, status, socket }}>{children}</Ctx.Provider>;
}

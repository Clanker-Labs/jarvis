import { CheckCircle2, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "./ui";

interface ToastItem {
  id: number;
  text: string;
  tone: "ok" | "error";
}

interface ToastApi {
  show: (text: string, tone?: "ok" | "error") => void;
}

const Ctx = createContext<ToastApi>({ show: () => {} });

export function useToast(): ToastApi {
  return useContext(Ctx);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const show = useCallback((text: string, tone: "ok" | "error" = "ok") => {
    const id = next.current++;
    setItems((xs) => [...xs.slice(-3), { id, text, tone }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), tone === "ok" ? 2200 : 5000);
  }, []);
  const api = useMemo(() => ({ show }), [show]);
  return (
    <Ctx.Provider value={api}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-50 flex flex-col items-end gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            role="status"
            className={cx(
              "glass flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] shadow-lg shadow-black/40",
              t.tone === "ok" ? "text-teal-200" : "text-red-200",
            )}
          >
            {t.tone === "ok" ? <CheckCircle2 className="size-4" aria-hidden /> : <XCircle className="size-4" aria-hidden />}
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

/** Composite widgets: Drawer, SaveBar, ReorderList, StatePill, Orb, FormIssues. */
import type { JarvisState } from "@jarvis/protocol";
import { ArrowDown, ArrowUp, Plus, RotateCcw, Save, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ValidationIssue } from "../lib/api";
import { formatIssue } from "../lib/api";
import { move, removeAt } from "../lib/reorder";
import { Badge, Button, IconButton, Select, cx } from "./ui";

// ------------------------------------------------------------------ drawer

export function Drawer({
  title,
  onClose,
  children,
  wide,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close.current();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, []);
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={onClose} aria-hidden />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : "Details"}
        tabIndex={-1}
        className={cx(
          "relative flex h-full w-full flex-col border-l border-white/10 bg-[#080b10]/95 shadow-2xl shadow-black outline-none",
          wide ? "max-w-3xl" : "max-w-xl",
        )}
      >
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-white/[0.08] px-4">
          <h2 className="font-semibold text-white">{title}</h2>
          <IconButton label="Close" onClick={onClose}>
            <X className="size-4" />
          </IconButton>
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ save bar

export function SaveBar({
  dirty,
  saving,
  onSave,
  onReset,
  label = "Save",
}: {
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onReset: () => void;
  label?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      {dirty && <Badge tone="warn">Unsaved changes</Badge>}
      <Button variant="ghost" size="sm" onClick={onReset} disabled={!dirty || saving} icon={<RotateCcw className="size-3.5" />}>
        Revert
      </Button>
      <Button variant="primary" size="sm" onClick={onSave} disabled={!dirty} busy={saving} icon={<Save className="size-3.5" />}>
        {label}
      </Button>
    </div>
  );
}

export function FormIssues({ issues }: { issues: ValidationIssue[] }) {
  if (!issues.length) return null;
  return (
    <div role="alert" className="mb-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12.5px] text-red-200">
      <p className="font-medium">Not saved — please fix:</p>
      <ul className="mt-1 list-disc pl-5">
        {issues.map((i, k) => (
          <li key={k}>{formatIssue(i)}</li>
        ))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------ reorderable list

/**
 * Ordered list with up/down/remove, plus an "add" select for the remaining options.
 * Order = priority / fallback order.
 */
export function ReorderList({
  items,
  onChange,
  options,
  render,
  label,
  disabled,
}: {
  items: string[];
  onChange: (next: string[]) => void;
  options: string[];
  render?: (id: string) => ReactNode;
  label: string;
  disabled?: boolean;
}) {
  const [adding, setAdding] = useState("");
  const remaining = options.filter((o) => !items.includes(o));
  return (
    <div>
      <ol className="flex flex-col gap-1" aria-label={label}>
        {items.map((id, i) => (
          <li
            key={id}
            className="flex items-center gap-2 rounded-md border border-white/[0.07] bg-white/[0.025] py-1 pr-1 pl-2"
          >
            <span className="w-4 text-right text-[11px] text-white/35 tabular-nums">{i + 1}</span>
            <div className="flex min-w-0 flex-1 items-center gap-2">{render ? render(id) : <span className="font-medium">{id}</span>}</div>
            <IconButton label={`Move ${id} up`} disabled={disabled || i === 0} onClick={() => onChange(move(items, i, -1))}>
              <ArrowUp className="size-3.5" />
            </IconButton>
            <IconButton
              label={`Move ${id} down`}
              disabled={disabled || i === items.length - 1}
              onClick={() => onChange(move(items, i, 1))}
            >
              <ArrowDown className="size-3.5" />
            </IconButton>
            <IconButton label={`Remove ${id}`} disabled={disabled} onClick={() => onChange(removeAt(items, i))}>
              <X className="size-3.5" />
            </IconButton>
          </li>
        ))}
        {!items.length && <li className="px-2 py-1.5 text-[12px] text-white/40">Empty — nothing will be used.</li>}
      </ol>
      {remaining.length > 0 && (
        <div className="mt-2 flex items-center gap-2">
          <Select
            aria-label={`Add to ${label}`}
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            className="max-w-52"
            disabled={disabled}
          >
            <option value="">Add…</option>
            {remaining.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </Select>
          <Button
            size="sm"
            disabled={!adding || disabled}
            icon={<Plus className="size-3.5" />}
            onClick={() => {
              onChange([...items, adding]);
              setAdding("");
            }}
          >
            Add
          </Button>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ state

export const STATE_COLORS: Record<JarvisState, string> = {
  IDLE: "#0EA5E9",
  WAKE: "#F59E0B",
  LISTENING: "#F59E0B",
  THINKING: "#8B5CF6",
  TOOL: "#8B5CF6",
  SPEAKING: "#14B8A6",
  FOLLOW_UP: "#F59E0B",
  ERROR: "#EF4444",
};

export function StatePill({ state, live }: { state: JarvisState | undefined; live?: boolean }) {
  const color = state ? STATE_COLORS[state] : "#64748b";
  return (
    <span
      className="inline-flex h-6 items-center gap-2 rounded-full border px-2.5 text-[11.5px] font-semibold tracking-wider"
      style={{ borderColor: `${color}55`, background: `${color}1a`, color }}
      aria-live="polite"
    >
      <span
        className={cx("size-1.5 rounded-full", live && "animate-pulse")}
        style={{ background: color, boxShadow: `0 0 8px ${color}` }}
        aria-hidden
      />
      {state ?? "UNKNOWN"}
    </span>
  );
}

/** Soft radial-gradient orb used as the appearance swatch. */
export function Orb({ color, size = 56, dim = 1 }: { color: string; size?: number; dim?: number }) {
  return (
    <div
      aria-hidden
      className="shrink-0 rounded-full"
      style={{
        width: size,
        height: size,
        opacity: dim,
        background: `radial-gradient(circle at 50% 50%, #ffffffcc 0%, ${color} 22%, ${color}66 48%, ${color}00 70%)`,
        filter: "saturate(1.1)",
      }}
    />
  );
}

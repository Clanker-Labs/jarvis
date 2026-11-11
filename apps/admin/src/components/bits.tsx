/** Shared pieces for the tools / MCP / skills / knowledge pages. */
import { Check, Copy, Plus, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { SIDE_EFFECT_HINT, SIDE_EFFECT_TONE, looksLikeRawSecret, recordToRows, rowsToRecord, type KvRow } from "../lib/sideEffects";
import { SIDE_EFFECTS, type SideEffect } from "../lib/types";
import { jsonEqual } from "../lib/useSection";
import { useToast } from "./Toast";
import { Badge, Button, IconButton, Input, Select } from "./ui";

export function SideEffectBadge({ level }: { level: SideEffect }) {
  return (
    <Badge tone={SIDE_EFFECT_TONE[level]} className={level === "irreversible" ? "font-semibold" : undefined}>
      <span title={SIDE_EFFECT_HINT[level]}>{level === "irreversible" ? "irreversible · asks for spoken confirmation" : level}</span>
    </Badge>
  );
}

export function SideEffectSelect({
  value,
  options = [...SIDE_EFFECTS],
  onChange,
  label,
  defaultLabel,
  disabled,
}: {
  value: SideEffect | "";
  options?: SideEffect[];
  onChange: (v: SideEffect | "") => void;
  label: string;
  /** If set, offers an empty "default" choice with this text. */
  defaultLabel?: string;
  disabled?: boolean;
}) {
  return (
    <div className="w-40 shrink-0">
      <Select aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as SideEffect | "")} className="h-7 text-[12px]">
        {defaultLabel && <option value="">{defaultLabel}</option>}
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </Select>
    </div>
  );
}

/** Read-only value with a copy button (falls back to selecting the text on plain-http origins). */
export function CopyField({ value, label }: { value: string; label: string }) {
  const toast = useToast();
  const ref = useRef<HTMLInputElement>(null);
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch {
      ref.current?.select();
      toast.show("Selected — press Ctrl+C to copy");
    }
  };
  return (
    <div className="flex items-center gap-1.5">
      <Input ref={ref} aria-label={label} readOnly value={value} className="font-mono text-[12px]" onFocus={(e) => e.target.select()} />
      <IconButton label={`Copy ${label}`} onClick={() => void copy()}>
        {done ? <Check className="size-3.5 text-teal-300" /> : <Copy className="size-3.5" />}
      </IconButton>
    </div>
  );
}

/** Two-step delete: first click arms, second confirms. */
export function ConfirmButton({
  onConfirm,
  busy,
  label = "Delete",
  confirmLabel = "Confirm delete",
  ariaLabel,
  icon = <Trash2 className="size-3.5" />,
}: {
  onConfirm: () => void;
  busy?: boolean;
  label?: string;
  confirmLabel?: string;
  ariaLabel?: string;
  icon?: ReactNode;
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 5000);
    return () => clearTimeout(t);
  }, [armed]);
  if (armed)
    return (
      <span className="inline-flex gap-1">
        <Button size="sm" variant="danger" busy={busy} onClick={onConfirm}>
          {confirmLabel}
        </Button>
        <IconButton label="Cancel" onClick={() => setArmed(false)}>
          <X className="size-3.5" />
        </IconButton>
      </span>
    );
  return (
    <Button size="sm" variant="ghost" onClick={() => setArmed(true)} icon={icon} aria-label={ariaLabel}>
      {label}
    </Button>
  );
}

/** Editable key/value rows backed by a record. Keeps raw rows so blank keys can be typed. */
export function KvEditor({
  value,
  onChange,
  label,
  keyPlaceholder = "NAME",
  valuePlaceholder = "${SECRET_NAME}",
}: {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  label: string;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const [rows, setRows] = useState<KvRow[]>(() => recordToRows(value));
  useEffect(() => {
    if (!jsonEqual(rowsToRecord(rows), value)) setRows(recordToRows(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  const update = (next: KvRow[]) => {
    setRows(next);
    onChange(rowsToRecord(next));
  };
  return (
    <div className="flex flex-col gap-1.5" role="group" aria-label={label}>
      {rows.map((r, i) => (
        <div key={i} className="flex flex-col gap-0.5">
          <div className="flex items-center gap-1.5">
            <div className="w-48 shrink-0">
              <Input
                aria-label={`${label} name ${i + 1}`}
                value={r.key}
                placeholder={keyPlaceholder}
                className="font-mono text-[12px]"
                onChange={(e) => update(rows.map((x, k) => (k === i ? { ...x, key: e.target.value } : x)))}
              />
            </div>
            <div className="min-w-0 flex-1">
              <Input
                aria-label={`${label} value ${i + 1}`}
                value={r.value}
                placeholder={valuePlaceholder}
                className="font-mono text-[12px]"
                onChange={(e) => update(rows.map((x, k) => (k === i ? { ...x, value: e.target.value } : x)))}
              />
            </div>
            <IconButton label={`Remove ${label} ${r.key || i + 1}`} onClick={() => update(rows.filter((_, k) => k !== i))}>
              <X className="size-3.5" />
            </IconButton>
          </div>
          {looksLikeRawSecret(r.key, r.value) && (
            <p className="text-[11px] text-amber-300/90">Looks like a pasted credential — store it on the Secrets page and use {"${NAME}"} here.</p>
          )}
        </div>
      ))}
      <div>
        <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" />} onClick={() => setRows([...rows, { key: "", value: "" }])}>
          Add
        </Button>
      </div>
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="glass rounded-xl px-3.5 py-2.5" title={hint}>
      <div className="text-[11px] tracking-wide text-white/45 uppercase">{label}</div>
      <div className="truncate text-lg font-semibold text-white tabular-nums">{value}</div>
    </div>
  );
}

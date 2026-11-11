/** Handwritten primitives: Card, Button, Field/Input/Select/Textarea, Toggle, Slider, Badge, Table. */
import { Loader2 } from "lucide-react";
import {
  forwardRef,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ layout

export function Card({
  title,
  actions,
  children,
  className,
  bodyClass,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
}) {
  return (
    <section className={cx("glass rounded-xl", className)}>
      {(title || actions) && (
        <header className="flex min-h-11 items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-2">
          <h2 className="text-[13px] font-semibold tracking-wide text-white/80">{title}</h2>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx("p-4", bodyClass)}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-white">{title}</h1>
        {sub && <p className="mt-0.5 text-white/50">{sub}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ buttons

type Variant = "primary" | "secondary" | "ghost" | "danger";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-cyan/90 text-white hover:bg-cyan border border-cyan/60",
  secondary: "bg-white/[0.06] text-white/85 hover:bg-white/[0.1] border border-white/10",
  ghost: "text-white/70 hover:text-white hover:bg-white/[0.06] border border-transparent",
  danger: "bg-danger/15 text-red-300 hover:bg-danger/25 border border-danger/30",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md";
  busy?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", busy, icon, className, children, disabled, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || busy}
      className={cx(
        "focus-ring inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-45",
        size === "sm" ? "h-7 px-2.5 text-[12px]" : "h-8 px-3 text-[13px]",
        VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {busy ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

export function IconButton({
  label,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cx(
        "focus-ring inline-flex size-7 items-center justify-center rounded-md text-white/60 hover:bg-white/[0.07] hover:text-white",
        "disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

// ------------------------------------------------------------------ form fields

const control =
  "focus-ring w-full rounded-md border border-white/10 bg-black/30 px-2.5 text-[13px] text-white placeholder:text-white/30 " +
  "hover:border-white/20 focus-visible:border-cyan/60 disabled:opacity-50 disabled:cursor-not-allowed";

/** Label + control + hint/errors. `children` receives the generated id. */
export function Field({
  label,
  hint,
  errors,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  errors?: string[];
  children: (id: string) => ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cx("flex flex-col gap-1", className)}>
      <label htmlFor={id} className="text-[12px] font-medium text-white/60">
        {label}
      </label>
      {children(id)}
      {hint && !errors?.length && <p className="text-[11.5px] text-white/40">{hint}</p>}
      {errors?.map((e) => (
        <p key={e} className="text-[11.5px] text-red-300" role="alert">
          {e}
        </p>
      ))}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={cx(control, "h-8", className)} {...rest} />;
  },
);

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(control, "h-8 pr-7", className)} {...rest}>
      {children}
    </select>
  );
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx(control, "scroll-thin py-1.5 leading-relaxed", className)} {...rest} />;
}

/** Number input that maps "" ↔ null. */
export function NumberInput({
  value,
  onValue,
  nullable,
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> & {
  value: number | null;
  onValue: (v: number | null) => void;
  nullable?: boolean;
}) {
  return (
    <Input
      type="number"
      inputMode="decimal"
      value={value ?? ""}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === "") onValue(nullable ? null : 0);
        else if (Number.isFinite(e.target.valueAsNumber)) onValue(e.target.valueAsNumber);
      }}
      {...rest}
    />
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  disabled?: boolean;
  hint?: ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex items-start gap-3">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx(
          "focus-ring relative mt-0.5 h-[18px] w-8 shrink-0 rounded-full border transition-colors disabled:opacity-40",
          checked ? "border-cyan/60 bg-cyan/70" : "border-white/15 bg-white/10",
        )}
      >
        <span
          className={cx(
            "absolute top-[2px] left-0 size-3 rounded-full bg-white transition-transform",
            checked ? "translate-x-[16px]" : "translate-x-[2px]",
          )}
        />
      </button>
      <label htmlFor={id} className="cursor-pointer select-none">
        <span className="text-[13px] text-white/85">{label}</span>
        {hint && <span className="block text-[11.5px] text-white/40">{hint}</span>}
      </label>
    </div>
  );
}

export function Slider({
  value,
  onChange,
  min,
  max,
  step,
  format,
  id,
  disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  format?: (v: number) => string;
  id?: string;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center gap-3">
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.valueAsNumber)}
        className="focus-ring flex-1 cursor-pointer"
      />
      <output htmlFor={id} className="w-14 text-right text-[12.5px] text-white/80 tabular-nums">
        {format ? format(value) : value}
      </output>
    </div>
  );
}

// ------------------------------------------------------------------ status

type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "violet";
const TONES: Record<Tone, string> = {
  neutral: "bg-white/[0.06] text-white/60 border-white/10",
  good: "bg-teal/10 text-teal-300 border-teal/25",
  warn: "bg-amber/10 text-amber-300 border-amber/25",
  bad: "bg-danger/10 text-red-300 border-danger/25",
  info: "bg-cyan/10 text-sky-300 border-cyan/25",
  violet: "bg-violet/10 text-violet-300 border-violet/25",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex h-5 items-center gap-1 rounded border px-1.5 text-[11px] font-medium whitespace-nowrap",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Health dot: true = teal, false = red, null = unknown grey. */
export function Dot({ ok, label }: { ok: boolean | null | undefined; label?: string }) {
  const color = ok == null ? "bg-white/25" : ok ? "bg-teal shadow-[0_0_8px] shadow-teal/60" : "bg-danger shadow-[0_0_8px] shadow-danger/60";
  return (
    <span
      role="img"
      aria-label={label ?? (ok == null ? "unknown" : ok ? "healthy" : "unhealthy")}
      className={cx("inline-block size-2 shrink-0 rounded-full", color)}
    />
  );
}

// ------------------------------------------------------------------ tables

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("scroll-thin overflow-x-auto", className)}>
      <table className="w-full border-collapse text-left text-[12.5px]">{children}</table>
    </div>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      scope="col"
      className={cx(
        "sticky top-0 border-b border-white/[0.08] bg-panel/80 px-3 py-2 text-[11px] font-semibold tracking-wide whitespace-nowrap text-white/45 uppercase",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({ children, className, title }: { children?: ReactNode; className?: string; title?: string }) {
  return (
    <td title={title} className={cx("border-b border-white/[0.05] px-3 py-1.5 align-top", className)}>
      {children}
    </td>
  );
}

// ------------------------------------------------------------------ loading / errors

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-white/50" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-red-200">
      <span>{msg}</span>
      {onRetry && (
        <Button size="sm" variant="ghost" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-white/40">{children}</p>;
}

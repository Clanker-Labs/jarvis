import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FlaskConical, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { ListArea } from "../components/KeywordArea";
import { useToast } from "../components/Toast";
import { Badge, Button, Card, Dot, ErrorBox, Field, IconButton, Input, Loading, NumberInput, PageHeader, Select, Toggle } from "../components/ui";
import { FormIssues, ReorderList, SaveBar } from "../components/widgets";
import { get, post } from "../lib/api";
import { fmtAgo, fmtLatency } from "../lib/format";
import { dropFromRoutes, renameInRoutes } from "../lib/reorder";
import {
  CLIS,
  EFFORTS,
  PROVIDER_KINDS,
  ROUTES,
  TOOL_MODES,
  type Overview,
  type ProviderEntry,
  type ProviderHealth,
  type ProviderTestResult,
  type ProvidersConfig,
  type SecretInfo,
} from "../lib/types";
import { issuesAt, useSection } from "../lib/useSection";

const ROUTE_HINTS: Record<string, string> = {
  chat: "Plain conversation.",
  tools: "Requests that match a tool keyword.",
  task: "Long multi-step tasks (task keywords).",
};

function newEntry(existing: ProviderEntry[]): ProviderEntry {
  let n = 1;
  while (existing.some((p) => p.id === `provider-${n}`)) n++;
  return {
    id: `provider-${n}`,
    kind: "openai",
    model: "",
    base_url: "",
    api_key_secret: "",
    enabled: true,
    tool_mode: "native",
    effort: null,
    input_price: null,
    output_price: null,
    cli: null,
  };
}

export function Providers() {
  const s = useSection("providers");
  const qc = useQueryClient();
  const toast = useToast();
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => get<Overview>("/api/overview"), refetchInterval: 10_000 });
  const secrets = useQuery({ queryKey: ["secrets"], queryFn: () => get<SecretInfo[]>("/api/secrets") });
  const [open, setOpen] = useState<string | null>(null);

  const recheck = useMutation({
    mutationFn: () => post<Record<string, boolean>>("/api/providers/health"),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["overview"] });
      toast.show("Health re-checked");
    },
    onError: (e) => toast.show(e.message, "error"),
  });

  const health = new Map<string, ProviderHealth>((overview.data?.providers ?? []).map((p) => [p.id, p]));
  const d = s.draft;
  const update = (fn: (c: ProvidersConfig) => ProvidersConfig) => s.setDraft((c) => (c ? fn(c) : c));

  const setEntry = (i: number, patch: Partial<ProviderEntry>) => {
    const oldId = d?.providers[i]?.id;
    if (patch.id !== undefined && oldId !== undefined && open === oldId) setOpen(patch.id);
    update((c) => {
      const old = c.providers[i];
      if (!old) return c;
      const providers = c.providers.map((p, k) => (k === i ? { ...p, ...patch } : p));
      const routes = patch.id !== undefined && patch.id !== old.id ? renameInRoutes(c.routes, old.id, patch.id) : c.routes;
      return { ...c, providers, routes };
    });
  };

  const remove = (i: number) =>
    update((c) => {
      const p = c.providers[i];
      if (!p) return c;
      return { ...c, providers: c.providers.filter((_, k) => k !== i), routes: dropFromRoutes(c.routes, p.id) };
    });

  return (
    <>
      <PageHeader
        title="Providers"
        sub="LLM backends and how requests are routed between them."
        actions={d && <SaveBar dirty={s.dirty} saving={s.saving} onSave={s.save} onReset={s.reset} />}
      />
      {s.query.isPending && <Loading />}
      {s.query.isError && <ErrorBox error={s.query.error} onRetry={() => void s.query.refetch()} />}
      {d && (
        <div className="flex flex-col gap-4">
          <FormIssues issues={s.issues} />
          <Card
            title="Providers"
            actions={
              <>
                <Button size="sm" variant="ghost" busy={recheck.isPending} onClick={() => recheck.mutate()} icon={<RefreshCw className="size-3.5" />}>
                  Re-check health
                </Button>
                <Button
                  size="sm"
                  icon={<Plus className="size-3.5" />}
                  onClick={() => {
                    const e = newEntry(d.providers);
                    update((c) => ({ ...c, providers: [...c.providers, e] }));
                    setOpen(e.id);
                  }}
                >
                  Add provider
                </Button>
              </>
            }
            bodyClass="p-2"
          >
            <datalist id="secret-names">
              {(secrets.data ?? []).map((x) => (
                <option key={x.name} value={x.name}>
                  {x.source ? `${x.name} (set)` : `${x.name} (unset)`}
                </option>
              ))}
            </datalist>
            <ul className="flex flex-col gap-1">
              {d.providers.map((p, i) => (
                <ProviderRow
                  key={i}
                  entry={p}
                  health={health.get(p.id)}
                  saved={s.query.data?.providers.find((x) => x.id === p.id)}
                  dirty={s.dirty}
                  duplicate={d.providers.filter((x) => x.id === p.id).length > 1}
                  issues={issuesAt(s.issues, "providers", i)}
                  expanded={open === p.id}
                  onToggle={() => setOpen(open === p.id ? null : p.id)}
                  onChange={(patch) => setEntry(i, patch)}
                  onRemove={() => remove(i)}
                />
              ))}
            </ul>
          </Card>

          <Card title="Routing" bodyClass="p-4">
            <p className="mb-4 text-[12.5px] text-white/50">
              Each route tries providers top to bottom; the next one is used if a provider is unhealthy or fails.
            </p>
            <div className="grid gap-5 md:grid-cols-3">
              {ROUTES.map((r) => (
                <div key={r}>
                  <div className="mb-1 font-medium text-white capitalize">{r}</div>
                  <p className="mb-2 text-[11.5px] text-white/40">{ROUTE_HINTS[r]}</p>
                  <ReorderList
                    label={`${r} route`}
                    items={d.routes[r] ?? []}
                    options={d.providers.map((p) => p.id)}
                    onChange={(ids) => update((c) => ({ ...c, routes: { ...c.routes, [r]: ids } }))}
                    render={(id) => {
                      const p = d.providers.find((x) => x.id === id);
                      return (
                        <>
                          <Dot ok={health.get(id)?.healthy} />
                          <span className="truncate font-medium">{id}</span>
                          {!p && <Badge tone="bad">unknown</Badge>}
                          {p && !p.enabled && <Badge>disabled</Badge>}
                        </>
                      );
                    }}
                  />
                </div>
              ))}
            </div>
          </Card>

          <Card title="Routing keywords">
            <div className="grid gap-4 md:grid-cols-[1fr_1fr_200px]">
              <ListArea
                label="Tool keywords"
                hint="One per line. A match sends the request to the tools route."
                value={d.tool_keywords}
                onChange={(v) => update((c) => ({ ...c, tool_keywords: v }))}
                errors={issuesAt(s.issues, "tool_keywords")}
              />
              <ListArea
                label="Task keywords"
                hint="One per line. A match sends the request to the task route."
                value={d.task_keywords}
                onChange={(v) => update((c) => ({ ...c, task_keywords: v }))}
                errors={issuesAt(s.issues, "task_keywords")}
              />
              <Field label="Health check interval (s)" hint="5–3600" errors={issuesAt(s.issues, "health_interval_s")}>
                {(id) => (
                  <NumberInput id={id} min={5} max={3600} value={d.health_interval_s} onValue={(v) => update((c) => ({ ...c, health_interval_s: v ?? 30 }))} />
                )}
              </Field>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}

function ProviderRow({
  entry: p,
  health,
  saved,
  dirty,
  duplicate,
  issues,
  expanded,
  onToggle,
  onChange,
  onRemove,
}: {
  entry: ProviderEntry;
  health: ProviderHealth | undefined;
  saved: ProviderEntry | undefined;
  dirty: boolean;
  duplicate: boolean;
  issues: string[];
  expanded: boolean;
  onToggle: () => void;
  onChange: (patch: Partial<ProviderEntry>) => void;
  onRemove: () => void;
}) {
  const [result, setResult] = useState<ProviderTestResult | null>(null);
  const test = useMutation({
    mutationFn: () => post<ProviderTestResult>(`/api/providers/${encodeURIComponent(p.id)}/test`),
    onMutate: () => setResult(null),
    onSuccess: setResult,
    onError: (e) => setResult({ ok: false, error: e.message }),
  });
  const testable = !!saved && saved.enabled && !!health;
  const testHint = !saved ? "Save first" : !saved.enabled ? "Enable and save first" : !health ? "Not loaded by the router" : dirty ? "Tests the saved settings" : "";

  return (
    <li className="rounded-lg border border-white/[0.06] bg-white/[0.02]">
      <div className="flex flex-wrap items-center gap-2.5 px-2 py-1.5">
        <IconButton label={expanded ? `Collapse ${p.id}` : `Edit ${p.id}`} onClick={onToggle} aria-expanded={expanded}>
          {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </IconButton>
        <Dot ok={p.enabled ? health?.healthy : null} label={health?.healthy == null ? "health unknown" : health.healthy ? "healthy" : "unhealthy"} />
        <button type="button" onClick={onToggle} className="focus-ring rounded font-medium text-white">
          {p.id || <span className="text-white/40">(no id)</span>}
        </button>
        <Badge tone="info">{p.kind}</Badge>
        <span className="truncate text-white/50">{p.model || (p.kind === "cli" ? p.cli : "") || "default model"}</span>
        {duplicate && <Badge tone="bad">duplicate id</Badge>}
        {issues.length > 0 && <Badge tone="bad">invalid</Badge>}
        <span className="ml-auto text-[11.5px] text-white/35">{health ? `checked ${fmtAgo(health.checked)}` : ""}</span>
        <Toggle checked={p.enabled} onChange={(v) => onChange({ enabled: v })} label={<span className="sr-only">Enabled</span>} />
        <Button size="sm" variant="ghost" busy={test.isPending} disabled={!testable} title={testHint} onClick={() => test.mutate()} icon={<FlaskConical className="size-3.5" />}>
          Test
        </Button>
        <IconButton label={`Remove ${p.id}`} onClick={() => confirm(`Remove provider “${p.id}”? (Takes effect when you save.)`) && onRemove()}>
          <Trash2 className="size-3.5" />
        </IconButton>
      </div>
      {result && (
        <div className={`mx-2 mb-2 rounded-md border px-3 py-2 text-[12.5px] ${result.ok ? "border-teal/25 bg-teal/[0.07]" : "border-danger/30 bg-danger/10 text-red-200"}`} role="status">
          {result.ok ? (
            <>
              <span className="text-white/90">“{result.text}”</span>
              <span className="ml-2 text-white/50 tabular-nums">
                first token {fmtLatency(result.first_token_s)} · total {fmtLatency(result.total_s)}
              </span>
            </>
          ) : (
            <>Test failed: {result.error}</>
          )}
        </div>
      )}
      {expanded && <ProviderForm p={p} issues={issues} onChange={onChange} />}
    </li>
  );
}

function ProviderForm({ p, issues, onChange }: { p: ProviderEntry; issues: string[]; onChange: (patch: Partial<ProviderEntry>) => void }) {
  return (
    <div className="grid gap-3 border-t border-white/[0.06] p-3 sm:grid-cols-2 lg:grid-cols-4">
      {issues.length > 0 && (
        <ul className="col-span-full text-[12px] text-red-300" role="alert">
          {issues.map((m) => <li key={m}>{m}</li>)}
        </ul>
      )}
      <Field label="Id" hint="lowercase, digits, - and _">
        {(id) => <Input id={id} value={p.id} pattern="[a-z0-9][a-z0-9_\-]{0,40}" onChange={(e) => onChange({ id: e.target.value.trim() })} />}
      </Field>
      <Field label="Kind">
        {(id) => (
          <Select id={id} value={p.kind} onChange={(e) => onChange({ kind: e.target.value as ProviderEntry["kind"] })}>
            {PROVIDER_KINDS.map((k) => <option key={k}>{k}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Model" hint="Empty = provider default">
        {(id) => <Input id={id} value={p.model} onChange={(e) => onChange({ model: e.target.value })} />}
      </Field>
      <Field label="Base URL" hint={p.kind === "anthropic" ? "Empty = official API" : "OpenAI-compatible endpoint"}>
        {(id) => <Input id={id} value={p.base_url} placeholder="https://…" onChange={(e) => onChange({ base_url: e.target.value })} />}
      </Field>
      <Field label="API key secret" hint="Name of a secret (see Secrets page)">
        {(id) => (
          <Input
            id={id}
            list="secret-names"
            value={p.api_key_secret}
            placeholder="e.g. OPENAI_API_KEY"
            onChange={(e) => onChange({ api_key_secret: e.target.value.trim() })}
          />
        )}
      </Field>
      <Field label="Tool mode">
        {(id) => (
          <Select id={id} value={p.tool_mode} onChange={(e) => onChange({ tool_mode: e.target.value as ProviderEntry["tool_mode"] })}>
            {TOOL_MODES.map((k) => <option key={k}>{k}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Effort">
        {(id) => (
          <Select id={id} value={p.effort ?? ""} onChange={(e) => onChange({ effort: (e.target.value || null) as ProviderEntry["effort"] })}>
            <option value="">default</option>
            {EFFORTS.map((k) => <option key={k}>{k}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Agent CLI" hint="Only for kind = cli">
        {(id) => (
          <Select id={id} value={p.cli ?? ""} disabled={p.kind !== "cli"} onChange={(e) => onChange({ cli: (e.target.value || null) as ProviderEntry["cli"] })}>
            <option value="">none</option>
            {CLIS.map((k) => <option key={k}>{k}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Input price (USD / 1M tokens)" hint="Empty = built-in table">
        {(id) => <NumberInput id={id} nullable min={0} step="any" value={p.input_price} onValue={(v) => onChange({ input_price: v })} />}
      </Field>
      <Field label="Output price (USD / 1M tokens)" hint="Empty = built-in table">
        {(id) => <NumberInput id={id} nullable min={0} step="any" value={p.output_price} onValue={(v) => onChange({ output_price: v })} />}
      </Field>
    </div>
  );
}

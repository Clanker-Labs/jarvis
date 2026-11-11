import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FlaskConical, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { KvEditor, SideEffectBadge, SideEffectSelect } from "../components/bits";
import { ListArea } from "../components/KeywordArea";
import { useToast } from "../components/Toast";
import { Badge, Button, Card, Dot, Empty, ErrorBox, Field, IconButton, Input, Loading, NumberInput, PageHeader, Select, Toggle } from "../components/ui";
import { FormIssues, SaveBar } from "../components/widgets";
import { get, post } from "../lib/api";
import { isAllowed, toggleAllowed } from "../lib/sideEffects";
import type { McpServerConfig, McpServerInfo, McpStatus, McpTestResult, SideEffect } from "../lib/types";
import { issuesAt, useSection } from "../lib/useSection";

const PYTHON = "/home/jarvis/jarvis/.venv/bin/python";

function newServer(existing: McpServerConfig[]): McpServerConfig {
  let n = 1;
  while (existing.some((s) => s.name === `server-${n}`)) n++;
  return {
    name: `server-${n}`,
    transport: "stdio",
    command: "",
    args: [],
    env: {},
    url: "",
    headers: {},
    enabled: true,
    allowed_tools: null,
    side_effects: {},
    timeout_s: 60,
  };
}

const STATUS_TONE: Record<McpStatus, boolean | null> = { connected: true, error: false, starting: null, stopped: null, disabled: null };

/** One discovered tool, from the live server or from a Test run. */
interface ToolRowData {
  name: string;
  description: string;
  /** Level without the user override (annotation default), when known. */
  defaultLevel: SideEffect | null;
}

export function Mcp() {
  const qc = useQueryClient();
  const toast = useToast();
  const live = useQuery({
    queryKey: ["mcp"],
    queryFn: () => get<McpServerInfo[]>("/api/mcp"),
    refetchInterval: (q) => ((q.state.data ?? []).some((s) => s.status === "starting") ? 2000 : 10_000),
  });
  const s = useSection("mcp", { invalidate: [["mcp"], ["tools"]] });
  const [open, setOpen] = useState<number | null>(null);
  const [tests, setTests] = useState<Record<string, McpTestResult>>({});

  const reload = useMutation({
    mutationFn: () => post<McpServerInfo[]>("/api/mcp/reload"),
    onSuccess: (list) => {
      qc.setQueryData(["mcp"], list);
      void qc.invalidateQueries({ queryKey: ["tools"] });
      toast.show("MCP servers restarted");
    },
    onError: (e) => toast.show(e.message, "error"),
  });

  const d = s.draft;
  const setServer = (i: number, patch: Partial<McpServerConfig>) =>
    s.setDraft((c) => (c ? { ...c, servers: c.servers.map((x, k) => (k === i ? { ...x, ...patch } : x)) } : c));

  return (
    <>
      <PageHeader
        title="MCP servers"
        sub="Model Context Protocol servers add tools from other programs. Changes apply when you save."
        actions={
          <>
            <Button size="sm" variant="ghost" busy={reload.isPending} onClick={() => reload.mutate()} icon={<RefreshCw className="size-3.5" />}>
              Reload all
            </Button>
            {d && <SaveBar dirty={s.dirty} saving={s.saving} onSave={() => void s.save()} onReset={s.reset} />}
          </>
        }
      />
      {(s.query.isPending || live.isPending) && <Loading />}
      {s.query.isError && <ErrorBox error={s.query.error} onRetry={() => void s.query.refetch()} />}
      {live.isError && <ErrorBox error={live.error} onRetry={() => void live.refetch()} />}
      {d && (
        <div className="flex flex-col gap-3">
          <FormIssues issues={s.issues} />
          {d.servers.length === 0 && (
            <Card>
              <Empty>No MCP servers yet.</Empty>
            </Card>
          )}
          {d.servers.map((srv, i) => {
            const info = live.data?.find((x) => x.name === srv.name);
            const test = tests[srv.name];
            const tools: ToolRowData[] =
              test?.ok
                ? test.tools.map((t) => ({ name: t.name, description: t.description, defaultLevel: t.side_effect }))
                : (info?.tools ?? []).map((t) => ({ name: t.name, description: t.description, defaultLevel: srv.side_effects[t.name] ? null : t.side_effect }));
            return (
              <ServerCard
                key={i}
                srv={srv}
                info={info}
                tools={tools}
                test={test}
                issues={issuesAt(s.issues, "servers", i)}
                expanded={open === i}
                onToggle={() => setOpen(open === i ? null : i)}
                onChange={(p) => setServer(i, p)}
                onTested={(r) => setTests((t) => ({ ...t, [srv.name]: r }))}
                onRemove={() => {
                  s.setDraft((c) => (c ? { ...c, servers: c.servers.filter((_, k) => k !== i) } : c));
                  setOpen(null);
                }}
              />
            );
          })}
          <div>
            <Button
              icon={<Plus className="size-3.5" />}
              onClick={() => {
                s.setDraft((c) => (c ? { ...c, servers: [...c.servers, newServer(c.servers)] } : c));
                setOpen(d.servers.length);
              }}
            >
              Add server
            </Button>
          </div>
        </div>
      )}
    </>
  );
}

function ServerCard({
  srv,
  info,
  tools,
  test,
  issues,
  expanded,
  onToggle,
  onChange,
  onTested,
  onRemove,
}: {
  srv: McpServerConfig;
  info: McpServerInfo | undefined;
  tools: ToolRowData[];
  test: McpTestResult | undefined;
  issues: string[];
  expanded: boolean;
  onToggle: () => void;
  onChange: (p: Partial<McpServerConfig>) => void;
  onTested: (r: McpTestResult) => void;
  onRemove: () => void;
}) {
  const runTest = useMutation({
    mutationFn: () => post<McpTestResult>("/api/mcp/test", srv),
    onSuccess: onTested,
    onError: (e) => onTested({ ok: false, error: e.message }),
  });
  const status: McpStatus = info?.status ?? "stopped";
  const allNames = tools.map((t) => t.name);

  return (
    <section className="glass rounded-xl">
      <header className="flex flex-wrap items-center gap-2.5 px-3 py-2">
        <IconButton label={expanded ? `Collapse ${srv.name}` : `Edit ${srv.name}`} onClick={onToggle} aria-expanded={expanded}>
          {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </IconButton>
        <Dot ok={STATUS_TONE[status]} label={status} />
        <button type="button" onClick={onToggle} className="focus-ring rounded font-medium text-white">
          {srv.name}
        </button>
        <Badge tone="info">{srv.transport}</Badge>
        <Badge tone={status === "connected" ? "good" : status === "error" ? "bad" : "neutral"}>{info ? status : "not saved"}</Badge>
        <span className="truncate font-mono text-[11.5px] text-white/40">{srv.transport === "stdio" ? [srv.command, ...srv.args].join(" ") : srv.url}</span>
        {issues.length > 0 && <Badge tone="bad">invalid</Badge>}
        <span className="ml-auto text-[12px] text-white/40">{tools.length ? `${tools.filter((t) => isAllowed(srv.allowed_tools, t.name)).length}/${tools.length} tools` : ""}</span>
        <Toggle checked={srv.enabled} onChange={(v) => onChange({ enabled: v })} label={<span className="sr-only">Enable {srv.name}</span>} />
        <Button size="sm" variant="ghost" busy={runTest.isPending} onClick={() => runTest.mutate()} icon={<FlaskConical className="size-3.5" />}>
          Test
        </Button>
        <IconButton label={`Remove ${srv.name}`} onClick={() => confirm(`Remove MCP server “${srv.name}”? (Takes effect when you save.)`) && onRemove()}>
          <Trash2 className="size-3.5" />
        </IconButton>
      </header>
      {info?.error && <p role="alert" className="mx-3 mb-2 rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 font-mono text-[12px] text-red-200">{info.error}</p>}
      {test && !test.ok && <p role="alert" className="mx-3 mb-2 rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-[12px] text-red-200">Test failed: {test.error}</p>}
      {test?.ok && <p role="status" className="mx-3 mb-2 text-[12px] text-teal-300">Test OK — {test.tools.length} tools discovered (not saved yet).</p>}
      {expanded && <ServerForm srv={srv} issues={issues} onChange={onChange} />}
      {(expanded || tools.length > 0) && (
        <div className="border-t border-white/[0.06] px-3 py-2">
          {tools.length === 0 ? (
            <p className="py-1 text-[12px] text-white/40">No tools discovered yet — save, or press Test.</p>
          ) : (
            <ul className="flex flex-col">
              {tools.map((t) => {
                const allowed = isAllowed(srv.allowed_tools, t.name);
                const override = srv.side_effects[t.name] ?? "";
                return (
                  <li key={t.name} className="flex flex-wrap items-center gap-3 border-b border-white/[0.04] py-1.5 last:border-b-0">
                    <label className="flex min-w-56 items-center gap-2">
                      <input
                        type="checkbox"
                        checked={allowed}
                        onChange={(e) => onChange({ allowed_tools: toggleAllowed(srv.allowed_tools, allNames, t.name, e.target.checked) })}
                        className="focus-ring size-3.5 accent-sky-500"
                      />
                      <span className={`font-mono text-[12px] ${allowed ? "text-white" : "text-white/40 line-through"}`}>{t.name}</span>
                    </label>
                    <span className="min-w-0 flex-1 truncate text-[12px] text-white/45" title={t.description}>
                      {t.description}
                    </span>
                    <SideEffectBadge level={(override || t.defaultLevel || "irreversible") as SideEffect} />
                    <SideEffectSelect
                      label={`Side effect for ${t.name}`}
                      value={override}
                      defaultLabel={t.defaultLevel ? `default (${t.defaultLevel})` : "default"}
                      onChange={(v) => {
                        const next = { ...srv.side_effects };
                        if (v) next[t.name] = v;
                        else delete next[t.name];
                        onChange({ side_effects: next });
                      }}
                    />
                  </li>
                );
              })}
            </ul>
          )}
          {srv.allowed_tools !== null && <p className="mt-1 text-[11px] text-white/35">Explicit allow-list: tools added to this server later stay off until you tick them.</p>}
        </div>
      )}
    </section>
  );
}

function ServerForm({ srv, issues, onChange }: { srv: McpServerConfig; issues: string[]; onChange: (p: Partial<McpServerConfig>) => void }) {
  return (
    <div className="grid gap-3 border-t border-white/[0.06] p-3 sm:grid-cols-2 xl:grid-cols-4">
      {issues.length > 0 && (
        <ul className="col-span-full text-[12px] text-red-300" role="alert">
          {issues.map((m) => <li key={m}>{m}</li>)}
        </ul>
      )}
      <Field label="Name" hint="a-z, 0-9, - and _">
        {(id) => <Input id={id} value={srv.name} onChange={(e) => onChange({ name: e.target.value.trim() })} />}
      </Field>
      <Field label="Transport">
        {(id) => (
          <Select id={id} value={srv.transport} onChange={(e) => onChange({ transport: e.target.value as McpServerConfig["transport"] })}>
            <option value="stdio">stdio (local program)</option>
            <option value="http">http (streamable HTTP)</option>
          </Select>
        )}
      </Field>
      <Field label="Timeout (s)" hint="Per tool call, 1–900">
        {(id) => <NumberInput id={id} min={1} max={900} value={srv.timeout_s} onValue={(v) => onChange({ timeout_s: v ?? 60 })} />}
      </Field>
      <div />
      {srv.transport === "stdio" ? (
        <>
          <Field label="Command" className="sm:col-span-2" hint={`e.g. npx, uvx, or ${PYTHON}`}>
            {(id) => <Input id={id} value={srv.command} className="font-mono text-[12px]" onChange={(e) => onChange({ command: e.target.value })} />}
          </Field>
          <div className="sm:col-span-2">
            <ListArea label="Arguments (one per line)" rows={3} value={srv.args} onChange={(v) => onChange({ args: v })} />
          </div>
          <div className="col-span-full">
            <div className="mb-1 text-[12px] font-medium text-white/60">Environment</div>
            <p className="mb-1.5 text-[11.5px] text-white/40">Reference secrets as {"${SECRET_NAME}"} instead of pasting tokens here.</p>
            <KvEditor label="Environment" value={srv.env} onChange={(v) => onChange({ env: v })} />
          </div>
        </>
      ) : (
        <>
          <Field label="URL" className="sm:col-span-2 xl:col-span-4">
            {(id) => <Input id={id} value={srv.url} placeholder="https://mcp.example.com/mcp" onChange={(e) => onChange({ url: e.target.value })} />}
          </Field>
          <div className="col-span-full">
            <div className="mb-1 text-[12px] font-medium text-white/60">Headers</div>
            <p className="mb-1.5 text-[11.5px] text-white/40">e.g. Authorization → Bearer {"${MY_TOKEN}"}</p>
            <KvEditor label="Headers" value={srv.headers} keyPlaceholder="Header" valuePlaceholder="Bearer ${TOKEN}" onChange={(v) => onChange({ headers: v })} />
          </div>
        </>
      )}
    </div>
  );
}

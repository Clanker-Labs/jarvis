import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ExternalLink, LogIn, Unplug } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { CopyField, SideEffectBadge, SideEffectSelect } from "../components/bits";
import { useToast } from "../components/Toast";
import { Badge, Button, Card, Dot, Empty, ErrorBox, Field, Input, Loading, NumberInput, PageHeader, Select, Table, Td, Th, Toggle } from "../components/ui";
import { FormIssues, SaveBar } from "../components/widgets";
import { get, post, put } from "../lib/api";
import { oauthReturn } from "../lib/misc";
import { stricterOrEqual } from "../lib/sideEffects";
import { WEB_SEARCH_BACKENDS, type ConnectorInfo, type ConnectorsConfig, type ConnectorsResponse, type SideEffect, type ToolInfo, type ToolsConfig } from "../lib/types";
import { useSection } from "../lib/useSection";

export function Connectors() {
  return (
    <>
      <PageHeader title="Tools & connectors" sub="Services Jarvis can reach, and every tool it may call." />
      <OAuthReturnToast />
      <div className="flex flex-col gap-4">
        <ConnectorsCard />
        <ToolsCard />
        <div className="grid gap-4 xl:grid-cols-2">
          <ToolsConfigCard />
          <ConnectorsConfigCard />
        </div>
      </div>
    </>
  );
}

/** Google redirects back to /connectors?connected=<id> or ?error=<msg>. */
function OAuthReturnToast() {
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const qc = useQueryClient();
  useEffect(() => {
    const r = oauthReturn(`?${params.toString()}`);
    if (!r) return;
    if (r.connected) toast.show(`Connected ${r.connected}`);
    if (r.error) toast.show(`Sign-in failed: ${r.error}`, "error");
    void qc.invalidateQueries({ queryKey: ["connectors"] });
    setParams({}, { replace: true });
  }, [params, setParams, toast, qc]);
  return null;
}

// ------------------------------------------------------------------ connectors

function connectorStatus(c: ConnectorInfo): { tone: "good" | "warn" | "bad" | "neutral"; text: string } {
  if (c.error) return { tone: "bad", text: "error" };
  if (!c.enabled) return { tone: "neutral", text: "disabled" };
  if (c.missing_secrets.length) return { tone: "warn", text: "needs secrets" };
  if (c.auth === "oauth2" && !c.connected) return { tone: "warn", text: "not connected" };
  if (c.active) return { tone: "good", text: "active" };
  return { tone: "neutral", text: "inactive" };
}

function ConnectorsCard() {
  const q = useQuery({ queryKey: ["connectors"], queryFn: () => get<ConnectorsResponse>("/api/connectors") });
  return (
    <Card title="Connectors">
      {q.isPending && <Loading />}
      {q.isError && <ErrorBox error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className="flex flex-col gap-4">
          {q.data.connectors.length === 0 ? (
            <Empty>No connectors installed.</Empty>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
              {q.data.connectors.map((c) => (
                <ConnectorCard key={c.id} c={c} />
              ))}
            </div>
          )}
          <OAuthHelp redirectUri={q.data.redirect_uri} />
        </div>
      )}
    </Card>
  );
}

function ConnectorCard({ c }: { c: ConnectorInfo }) {
  const qc = useQueryClient();
  const toast = useToast();
  const st = connectorStatus(c);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["connectors"] });
    void qc.invalidateQueries({ queryKey: ["tools"] });
  };

  const toggle = useMutation({
    mutationFn: async (on: boolean) => {
      const cfg = await get<ConnectorsConfig>("/api/config/connectors");
      return put<ConnectorsConfig>("/api/config/connectors", { enabled: { ...cfg.enabled, [c.id]: on } });
    },
    onSuccess: (saved) => {
      qc.setQueryData(["config", "connectors"], saved);
      refresh();
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const connect = useMutation({
    mutationFn: () => post<{ auth_url: string }>(`/api/connectors/${encodeURIComponent(c.id)}/connect`),
    onSuccess: ({ auth_url }) => window.location.assign(auth_url),
    onError: (e) => toast.show(e.message, "error"),
  });
  const disconnect = useMutation({
    mutationFn: () => post(`/api/connectors/${encodeURIComponent(c.id)}/disconnect`),
    onSuccess: () => {
      toast.show(`Disconnected ${c.name}`);
      refresh();
    },
    onError: (e) => toast.show(e.message, "error"),
  });

  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-white/[0.07] bg-white/[0.02] p-3.5">
      <div className="flex items-start gap-2.5">
        <Dot ok={st.tone === "good" ? true : st.tone === "bad" ? false : null} label={st.text} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-white">{c.name}</span>
            <Badge tone={st.tone === "neutral" ? "neutral" : st.tone}>{st.text}</Badge>
            <Badge>{c.auth === "oauth2" ? "OAuth" : c.auth === "api_key" ? "API key" : "no auth"}</Badge>
          </div>
          <p className="mt-0.5 text-[12.5px] text-white/50">{c.description}</p>
        </div>
        <Toggle checked={c.enabled} disabled={toggle.isPending || !!c.error} onChange={(v) => toggle.mutate(v)} label={<span className="sr-only">Enable {c.name}</span>} />
      </div>
      {c.error && (
        <p role="alert" className="flex items-start gap-1.5 text-[12px] text-red-300">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden /> {c.error}
        </p>
      )}
      {c.missing_secrets.length > 0 && (
        <p className="text-[12px] text-amber-200/90">
          Missing secrets:{" "}
          {c.missing_secrets.map((s, i) => (
            <span key={s}>
              {i > 0 && ", "}
              <Link to="/secrets" className="focus-ring rounded font-mono text-sky-300 hover:underline">
                {s}
              </Link>
            </span>
          ))}
        </p>
      )}
      {c.tools.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {c.tools.map((t) => (
            <Badge key={t.name} tone={t.side_effect === "irreversible" ? "bad" : "neutral"}>
              <span title={t.description}>{t.name}</span>
            </Badge>
          ))}
        </div>
      )}
      {c.auth === "oauth2" && (
        <div className="flex gap-2">
          {c.connected ? (
            <Button size="sm" variant="ghost" busy={disconnect.isPending} onClick={() => disconnect.mutate()} icon={<Unplug className="size-3.5" />}>
              Disconnect
            </Button>
          ) : (
            <Button
              size="sm"
              variant="primary"
              busy={connect.isPending}
              disabled={!c.enabled || c.missing_secrets.length > 0}
              title={c.missing_secrets.length ? "Set the missing secrets first" : !c.enabled ? "Enable it first" : undefined}
              onClick={() => connect.mutate()}
              icon={<LogIn className="size-3.5" />}
            >
              Connect
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function OAuthHelp({ redirectUri }: { redirectUri: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [url, setUrl] = useState("");
  const complete = useMutation({
    mutationFn: () => post<{ connected: string }>("/api/connectors/oauth/complete", { url: url.trim() }),
    onSuccess: (r) => {
      toast.show(`Connected ${r.connected}`);
      setUrl("");
      void qc.invalidateQueries({ queryKey: ["connectors"] });
      void qc.invalidateQueries({ queryKey: ["tools"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (url.trim().length >= 10) complete.mutate();
  };
  return (
    <div className="grid gap-4 rounded-lg border border-white/[0.06] bg-black/20 p-3.5 lg:grid-cols-2">
      <Field label="OAuth redirect URI" hint="Add this exact URI to your Google Cloud OAuth client.">
        {() => <CopyField value={redirectUri} label="redirect URI" />}
      </Field>
      <form onSubmit={submit}>
        <Field label="Complete sign-in manually" hint="If the redirect couldn't reach this panel, paste the full URL your browser landed on.">
          {(id) => (
            <div className="flex gap-1.5">
              <Input id={id} value={url} placeholder="https://…/api/connectors/oauth/callback?state=…&code=…" onChange={(e) => setUrl(e.target.value)} />
              <Button type="submit" busy={complete.isPending} disabled={url.trim().length < 10} icon={<ExternalLink className="size-3.5" />}>
                Complete
              </Button>
            </div>
          )}
        </Field>
      </form>
    </div>
  );
}

// ------------------------------------------------------------------ tools

const SOURCE_ORDER: ToolInfo["source"][] = ["builtin", "connector", "mcp", "skill"];
const SOURCE_LABEL: Record<ToolInfo["source"], string> = { builtin: "Built-in", connector: "Connectors", mcp: "MCP servers", skill: "Skills" };

function ToolsCard() {
  const q = useQuery({ queryKey: ["tools"], queryFn: () => get<ToolInfo[]>("/api/tools") });
  const [filter, setFilter] = useState("");
  const groups = useMemo(() => {
    const f = filter.trim().toLowerCase();
    const list = (q.data ?? []).filter((t) => !f || `${t.display} ${t.label} ${t.description}`.toLowerCase().includes(f));
    return SOURCE_ORDER.map((s) => [s, list.filter((t) => t.source === s)] as const).filter(([, ts]) => ts.length);
  }, [q.data, filter]);

  return (
    <Card
      title={`Tools${q.data ? ` (${q.data.length})` : ""}`}
      actions={<Input aria-label="Filter tools" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} className="h-7 w-48" />}
      bodyClass="p-0"
    >
      {q.isPending && <div className="px-4"><Loading /></div>}
      {q.isError && <div className="p-4"><ErrorBox error={q.error} onRetry={() => void q.refetch()} /></div>}
      {q.data && groups.length === 0 && <Empty>No tools match.</Empty>}
      {groups.length > 0 && (
        <Table className="max-h-[620px] overflow-y-auto">
          <thead>
            <tr>
              <Th>Tool</Th>
              <Th>Description</Th>
              <Th>Effective side effect</Th>
              <Th>Override</Th>
              <Th className="text-right">Enabled</Th>
            </tr>
          </thead>
          {groups.map(([source, tools]) => (
            <tbody key={source}>
              <tr>
                <td colSpan={5} className="bg-white/[0.03] px-3 py-1.5 text-[11px] font-semibold tracking-wide text-white/50 uppercase">
                  {SOURCE_LABEL[source]} · {tools.length}
                </td>
              </tr>
              {tools.map((t) => (
                <ToolRow key={t.name} t={t} />
              ))}
            </tbody>
          ))}
        </Table>
      )}
    </Card>
  );
}

function ToolRow({ t }: { t: ToolInfo }) {
  const qc = useQueryClient();
  const toast = useToast();
  const patch = useMutation({
    mutationFn: (body: { enabled?: boolean; side_effect?: SideEffect }) => put(`/api/tools/${encodeURIComponent(t.name)}`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tools"] });
      void qc.invalidateQueries({ queryKey: ["config", "tools"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  return (
    <tr className={t.enabled ? undefined : "opacity-55"}>
      <Td className="whitespace-nowrap">
        <div className="font-mono text-[12px] text-white">{t.display}</div>
        {t.label && t.label !== t.display && <div className="text-[11.5px] text-white/40">{t.label}</div>}
      </Td>
      <Td className="max-w-[420px] text-white/55" title={t.description}>
        <span className="line-clamp-2">{t.description}</span>
      </Td>
      <Td>
        <SideEffectBadge level={t.side_effect} />
        {t.side_effect !== t.declared_side_effect && <div className="mt-0.5 text-[11px] text-white/35">declared: {t.declared_side_effect}</div>}
      </Td>
      <Td>
        <SideEffectSelect
          label={`Side effect for ${t.display}`}
          value={t.side_effect}
          options={stricterOrEqual(t.declared_side_effect)}
          disabled={patch.isPending}
          onChange={(v) => v && patch.mutate({ side_effect: v })}
        />
      </Td>
      <Td className="text-right">
        <div className="inline-flex">
          <Toggle checked={t.enabled} disabled={patch.isPending} onChange={(v) => patch.mutate({ enabled: v })} label={<span className="sr-only">Enable {t.display}</span>} />
        </div>
      </Td>
    </tr>
  );
}

// ------------------------------------------------------------------ config

function ToolsConfigCard() {
  const s = useSection("tools", { omit: ["overrides"] });
  const d = s.draft;
  return (
    <Card title="Tool settings" actions={<SaveBar dirty={s.dirty} saving={s.saving} onSave={() => void s.save()} onReset={s.reset} />}>
      {s.query.isPending && <Loading />}
      {s.query.isError && <ErrorBox error={s.query.error} onRetry={() => void s.query.refetch()} />}
      <FormIssues issues={s.issues} />
      {d && (
        <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
          <Field label="Web search backend" hint="auto picks the first configured (Brave, Tavily, SearXNG) and falls back to DuckDuckGo." errors={s.fieldIssues("web_search_backend")}>
            {(id) => (
              <Select id={id} value={d.web_search_backend} onChange={(e) => s.set("web_search_backend", e.target.value as ToolsConfig["web_search_backend"])}>
                {WEB_SEARCH_BACKENDS.map((b) => <option key={b}>{b}</option>)}
              </Select>
            )}
          </Field>
          <Field label="SearXNG URL" errors={s.fieldIssues("searxng_url")}>
            {(id) => <Input id={id} value={d.searxng_url} placeholder="http://searx.lan:8080" onChange={(e) => s.set("searxng_url", e.target.value)} />}
          </Field>
          <Field label="Files folder" hint="Where file tools may read and write." errors={s.fieldIssues("files_dir")} className="sm:col-span-2">
            {(id) => <Input id={id} value={d.files_dir} onChange={(e) => s.set("files_dir", e.target.value)} />}
          </Field>
          <div className="sm:col-span-2">
            <Toggle
              checked={d.url_allow_private}
              onChange={(v) => s.set("url_allow_private", v)}
              label="Allow reading private-network URLs"
              hint="Lets read_url fetch LAN, loopback and Tailscale addresses."
            />
            {d.url_allow_private && (
              <p role="alert" className="mt-2 flex items-start gap-1.5 rounded-md border border-amber/30 bg-amber/10 px-2.5 py-1.5 text-[12px] text-amber-200">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                A web page or prompt could then make Jarvis request internal services (routers, admin panels). Only enable this if you need it.
              </p>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function ConnectorsConfigCard() {
  const s = useSection("connectors", { omit: ["enabled"], invalidate: [["connectors"]] });
  const d = s.draft;
  return (
    <Card title="Connector settings" actions={<SaveBar dirty={s.dirty} saving={s.saving} onSave={() => void s.save()} onReset={s.reset} />}>
      {s.query.isPending && <Loading />}
      {s.query.isError && <ErrorBox error={s.query.error} onRetry={() => void s.query.refetch()} />}
      <FormIssues issues={s.issues} />
      {d && (
        <div className="grid gap-x-4 gap-y-3">
          <Field label="Redirect URI override" hint="Empty = this panel's own callback. Google needs https or loopback — with tailscale serve use your https *.ts.net URL." errors={s.fieldIssues("redirect_uri")}>
            {(id) => <Input id={id} value={d.redirect_uri} placeholder="https://jarvis.tailnet.ts.net/api/connectors/oauth/callback" onChange={(e) => s.set("redirect_uri", e.target.value)} />}
          </Field>
          <Field label="Now card refresh (s)" hint="60–3600" errors={s.fieldIssues("now_refresh_s")}>
            {(id) => (
              <div className="w-32">
                <NumberInput id={id} min={60} max={3600} value={d.now_refresh_s} onValue={(v) => s.set("now_refresh_s", v ?? 300)} />
              </div>
            )}
          </Field>
        </div>
      )}
    </Card>
  );
}

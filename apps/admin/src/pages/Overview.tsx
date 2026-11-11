import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { Link, Outlet, useNavigate } from "react-router";
import { Badge, Card, Dot, Empty, ErrorBox, Loading, PageHeader, Table, Td, Th, cx } from "../components/ui";
import { StatePill } from "../components/widgets";
import { get } from "../lib/api";
import {
  PIPELINE,
  STAGE_LABELS,
  barPct,
  fmtAgo,
  fmtCost,
  fmtDuration,
  fmtLatency,
  fmtTime,
  fmtTokens,
  truncate,
} from "../lib/format";
import { useLive } from "../lib/live";
import { endToEnd } from "../lib/trace";
import type { LlmCall, Overview as OverviewData, Stage, TurnRow } from "../lib/types";

export function Overview() {
  const live = useLive();
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => get<OverviewData>("/api/overview"), refetchInterval: 5_000 });

  return (
    <>
      <PageHeader
        title="Overview"
        sub={ov.data ? `Up ${fmtDuration(ov.data.uptime_s)}` : undefined}
        actions={<StatePill state={live.state ?? ov.data?.state} live={live.socket === "open"} />}
      />
      {ov.isPending && <Loading />}
      {ov.isError && <ErrorBox error={ov.error} onRetry={() => void ov.refetch()} />}
      {ov.data && (
        <div className="flex flex-col gap-4">
          <HealthTiles data={ov.data} />
          <div className="grid gap-4 lg:grid-cols-3">
            <ProvidersCard data={ov.data} />
            <VoiceCard data={ov.data} />
            <UsageCard data={ov.data} />
          </div>
          <LatencyCard stages={ov.data.stats.stages} turns={ov.data.stats.turns} />
        </div>
      )}
      <div className="mt-4 flex flex-col gap-4">
        <TurnsCard />
        <LlmCallsCard />
      </div>
      <Outlet />
    </>
  );
}

// ------------------------------------------------------------------ health

function Tile({ label, ok, value, warn }: { label: string; ok: boolean | null; value: string; warn?: boolean }) {
  return (
    <div className="glass flex items-center gap-3 rounded-xl px-3.5 py-3">
      <Dot ok={warn ? null : ok} label={`${label}: ${value}`} />
      <div className="min-w-0">
        <div className="text-[11px] tracking-wide text-white/45 uppercase">{label}</div>
        <div className={cx("truncate font-medium", warn ? "text-amber-300" : ok === false ? "text-red-300" : "text-white/90")}>
          {value}
        </div>
      </div>
    </div>
  );
}

function HealthTiles({ data }: { data: OverviewData }) {
  const live = useLive();
  const st = live.status ?? data.status;
  const sv = data.services;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-8">
      <Tile label="Brain" ok={sv.brain} value={sv.brain ? "Running" : "Down"} />
      <Tile label="Audio node" ok={sv.audio_node} value={sv.audio_node ? "Connected" : "Disconnected"} />
      <Tile label="UI clients" ok={sv.ui_clients > 0} value={String(sv.ui_clients)} />
      <Tile label="LLM" ok={st.llm} value={st.llm ? "Reachable" : "Unavailable"} />
      <Tile label="Internet" ok={st.internet} value={st.internet ? "Online" : "Offline"} />
      <Tile label="Mic" ok={st.mic} value={st.mic ? "Live" : "Off"} />
      <Tile label="Muted" ok={!st.muted} value={st.muted ? "Muted" : "No"} warn={st.muted} />
      <Tile label="Voice" ok={!st.offline_voice} value={st.offline_voice ? "Offline fallback" : "Primary engines"} warn={st.offline_voice} />
    </div>
  );
}

function ProvidersCard({ data }: { data: OverviewData }) {
  return (
    <Card title="LLM providers" actions={<Link to="/providers" className="focus-ring rounded text-[12px] text-sky-300 hover:underline">Manage</Link>}>
      {data.providers.length === 0 ? (
        <Empty>No providers enabled.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.providers.map((p) => (
            <li key={p.id} className="flex items-center gap-2.5">
              <Dot ok={p.healthy} />
              <span className="font-medium">{p.id}</span>
              <span className="truncate text-white/45">{p.model}</span>
              <span className="ml-auto text-[11.5px] whitespace-nowrap text-white/35">{fmtAgo(p.checked)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function VoiceCard({ data }: { data: OverviewData }) {
  const row = (label: string, ids: string[]) => (
    <div>
      <div className="mb-1 text-[11px] tracking-wide text-white/45 uppercase">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {ids.length ? ids.map((id, i) => <Badge key={id} tone={i === 0 ? "good" : "neutral"}>{id}</Badge>) : <span className="text-white/40">none available</span>}
      </div>
    </div>
  );
  return (
    <Card title="Voice engines in use" actions={<Link to="/voice" className="focus-ring rounded text-[12px] text-sky-300 hover:underline">Configure</Link>}>
      <div className="flex flex-col gap-3">
        {row("Speech-to-text", data.voice.stt)}
        {row("Text-to-speech", data.voice.tts)}
        <p className="text-[11.5px] text-white/35">First = primary, others are fallbacks.</p>
      </div>
    </Card>
  );
}

function UsageCard({ data }: { data: OverviewData }) {
  return (
    <Card title="Last 24 hours">
      <div className="grid grid-cols-2 gap-4">
        <Stat label="Turns" value={String(data.stats.last_24h.turns)} />
        <Stat label="Cost" value={fmtCost(data.stats.last_24h.cost_usd)} />
      </div>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] tracking-wide text-white/45 uppercase">{label}</div>
      <div className="mt-0.5 text-2xl font-semibold text-white tabular-nums">{value}</div>
    </div>
  );
}

// ------------------------------------------------------------------ latency

const STAGE_COLOR: Record<Stage, string> = {
  wake_to_listen: "#F59E0B",
  stt: "#0EA5E9",
  llm_first_token: "#8B5CF6",
  tts_first_audio: "#14B8A6",
  end_of_speech_to_audio: "#e2e8f0",
};

function LatencyCard({ stages, turns }: { stages: OverviewData["stats"]["stages"]; turns: number }) {
  const order: Stage[] = [...PIPELINE, "end_of_speech_to_audio"];
  const max = Math.max(0, ...order.map((s) => stages[s]?.p90 ?? 0));
  const p50Total = PIPELINE.reduce((a, s) => a + (stages[s]?.p50 ?? 0), 0);
  return (
    <Card title={`Latency per stage · last ${turns} turns`}>
      {turns === 0 ? (
        <Empty>No turns recorded yet.</Empty>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
          <Table>
            <thead>
              <tr>
                <Th>Stage</Th>
                <Th className="text-right">p50</Th>
                <Th className="text-right">p90</Th>
                <Th className="text-right">n</Th>
                <Th className="w-[40%]"> </Th>
              </tr>
            </thead>
            <tbody>
              {order.map((s) => {
                const st = stages[s];
                return (
                  <tr key={s}>
                    <Td className="whitespace-nowrap">
                      <span className="mr-2 inline-block size-2 rounded-sm" style={{ background: STAGE_COLOR[s] }} aria-hidden />
                      {STAGE_LABELS[s]}
                    </Td>
                    <Td className="text-right tabular-nums">{fmtLatency(st?.p50)}</Td>
                    <Td className="text-right text-white/60 tabular-nums">{fmtLatency(st?.p90)}</Td>
                    <Td className="text-right text-white/40 tabular-nums">{st?.n ?? 0}</Td>
                    <Td className="align-middle">
                      <div className="relative h-2 rounded-full bg-white/[0.05]" aria-hidden>
                        <div className="absolute inset-y-0 left-0 rounded-full opacity-30" style={{ width: `${barPct(st?.p90, max)}%`, background: STAGE_COLOR[s] }} />
                        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${barPct(st?.p50, max)}%`, background: STAGE_COLOR[s] }} />
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <div>
            <div className="mb-2 text-[11px] tracking-wide text-white/45 uppercase">Pipeline (p50)</div>
            <div className="flex h-6 overflow-hidden rounded-md bg-white/[0.04]" role="img" aria-label={`Total p50 ${fmtLatency(p50Total)}`}>
              {PIPELINE.map((s) => (
                <div
                  key={s}
                  title={`${STAGE_LABELS[s]}: ${fmtLatency(stages[s]?.p50)}`}
                  style={{ width: `${barPct(stages[s]?.p50, p50Total)}%`, background: STAGE_COLOR[s] }}
                  className="h-full border-r border-black/40 last:border-r-0"
                />
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-white/55">
              {PIPELINE.map((s) => (
                <span key={s} className="flex items-center gap-1">
                  <span className="size-2 rounded-sm" style={{ background: STAGE_COLOR[s] }} aria-hidden />
                  {STAGE_LABELS[s].split(" ")[0]} {fmtLatency(stages[s]?.p50)}
                </span>
              ))}
            </div>
            <p className="mt-3 text-[12px] text-white/50">
              Wake → first audio ≈ <span className="font-medium text-white tabular-nums">{fmtLatency(p50Total)}</span>
            </p>
          </div>
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ conversations

function TurnsCard() {
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["turns"], queryFn: () => get<TurnRow[]>("/api/turns?limit=50"), refetchInterval: 15_000 });
  return (
    <Card title="Last 50 conversations" bodyClass="p-0">
      {q.isPending && <div className="px-4"><Loading /></div>}
      {q.isError && <div className="p-4"><ErrorBox error={q.error} onRetry={() => void q.refetch()} /></div>}
      {q.data && q.data.length === 0 && <Empty>No conversations yet. Say “Jarvis…” to start one.</Empty>}
      {q.data && q.data.length > 0 && (
        <Table className="max-h-[520px] overflow-y-auto">
          <thead>
            <tr>
              <Th>Time</Th>
              <Th>User</Th>
              <Th>Reply</Th>
              <Th>Provider / model</Th>
              <Th className="text-right">Tokens</Th>
              <Th className="text-right">Cost</Th>
              <Th className="text-right">E2E</Th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((t) => (
              <tr key={t.id} onClick={() => navigate(`turns/${t.id}`)} className="cursor-pointer hover:bg-white/[0.035]">
                <Td className="whitespace-nowrap">
                  <Link to={`turns/${t.id}`} className="focus-ring rounded text-sky-300/90 tabular-nums hover:underline" onClick={(e) => e.stopPropagation()}>
                    {fmtTime(t.started)}
                  </Link>
                </Td>
                <Td className="max-w-64" title={t.user_text ?? ""}>{truncate(t.user_text, 80) || <span className="text-white/30">—</span>}</Td>
                <Td className="max-w-80 text-white/70" title={t.reply_text ?? ""}>
                  {t.error && <AlertTriangle className="mr-1 inline size-3.5 text-red-400" aria-label="error" />}
                  {truncate(t.reply_text, 100) || <span className="text-white/30">{t.error ? truncate(t.error, 80) : "—"}</span>}
                </Td>
                <Td className="whitespace-nowrap text-white/60">
                  {t.provider ?? "—"}
                  {t.model && <span className="text-white/35"> / {t.model}</span>}
                </Td>
                <Td className="text-right whitespace-nowrap text-white/60 tabular-nums">
                  {fmtTokens(t.input_tokens)} / {fmtTokens(t.output_tokens)}
                </Td>
                <Td className="text-right tabular-nums">{fmtCost(t.cost_usd)}</Td>
                <Td className="text-right tabular-nums">{fmtLatency(endToEnd(t.latency))}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function LlmCallsCard() {
  const q = useQuery({ queryKey: ["llm-calls"], queryFn: () => get<LlmCall[]>("/api/llm-calls?limit=30"), refetchInterval: 15_000 });
  return (
    <Card title="Recent LLM calls" bodyClass="p-0">
      {q.isPending && <div className="px-4"><Loading /></div>}
      {q.isError && <div className="p-4"><ErrorBox error={q.error} onRetry={() => void q.refetch()} /></div>}
      {q.data && q.data.length === 0 && <Empty>No LLM calls since the brain started.</Empty>}
      {q.data && q.data.length > 0 && (
        <Table className="max-h-[380px] overflow-y-auto">
          <thead>
            <tr>
              <Th>Time</Th>
              <Th>Provider</Th>
              <Th>Model</Th>
              <Th>Route</Th>
              <Th>Result</Th>
              <Th className="text-right">First token</Th>
              <Th className="text-right">Total</Th>
              <Th className="text-right">Tokens</Th>
              <Th className="text-right">Cost</Th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((c, i) => (
              <tr key={`${c.ts}-${i}`}>
                <Td className="whitespace-nowrap tabular-nums">{fmtTime(c.ts)}</Td>
                <Td>{c.provider}</Td>
                <Td className="text-white/60">{c.model}</Td>
                <Td><Badge>{c.route}</Badge></Td>
                <Td title={c.error}>{c.ok ? <Badge tone="good">ok</Badge> : <Badge tone="bad">{truncate(c.error, 40) || "error"}</Badge>}</Td>
                <Td className="text-right tabular-nums">{fmtLatency(c.first_token_s)}</Td>
                <Td className="text-right tabular-nums">{fmtLatency(c.total_s)}</Td>
                <Td className="text-right whitespace-nowrap text-white/60 tabular-nums">
                  {fmtTokens(c.input_tokens)} / {fmtTokens(c.output_tokens)}
                </Td>
                <Td className="text-right tabular-nums">{fmtCost(c.cost_usd)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

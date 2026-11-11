import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { useNavigate, useParams } from "react-router";
import { Badge, Empty, ErrorBox, Loading, Table, Td, Th } from "../components/ui";
import { Drawer } from "../components/widgets";
import { get } from "../lib/api";
import { STAGE_LABELS, barPct, fmtCost, fmtLatency, fmtOffset, fmtTime, fmtTokens, summarize } from "../lib/format";
import { buildTimeline } from "../lib/trace";
import type { Stage, TurnDetail } from "../lib/types";

export function TraceDrawer() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const close = useCallback(() => navigate("/"), [navigate]);
  const q = useQuery({ queryKey: ["turn", id], queryFn: () => get<TurnDetail>(`/api/turns/${encodeURIComponent(id)}`) });

  return (
    <Drawer title="Turn trace" onClose={close} wide>
      {q.isPending && <Loading />}
      {q.isError && <ErrorBox error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && <Trace turn={q.data} />}
    </Drawer>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-white/45 uppercase">{title}</h3>
      {children}
    </section>
  );
}

function Trace({ turn }: { turn: TurnDetail }) {
  const stages = Object.entries(turn.latency?.stages ?? {}) as [Stage, number][];
  const maxStage = Math.max(0, ...stages.map(([, v]) => v));
  const tools = turn.events?.tool_calls ?? [];
  const timeline = buildTimeline(turn);
  const duration = turn.ended && turn.started ? turn.ended - turn.started : null;

  return (
    <>
      <dl className="mb-6 grid grid-cols-2 gap-x-6 gap-y-2 text-[12.5px] sm:grid-cols-4">
        <Meta label="Started" value={fmtTime(turn.started)} />
        <Meta label="Duration" value={fmtLatency(duration)} />
        <Meta label="Provider" value={`${turn.provider ?? "—"}${turn.model ? ` / ${turn.model}` : ""}`} />
        <Meta label="Tokens · cost" value={`${fmtTokens(turn.input_tokens)} / ${fmtTokens(turn.output_tokens)} · ${fmtCost(turn.cost_usd)}`} />
      </dl>

      {turn.error && (
        <div role="alert" className="mb-6 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-red-200">
          <span className="font-medium">Error:</span> {turn.error}
        </div>
      )}

      <Section title="Conversation">
        <div className="flex flex-col gap-2">
          <Bubble who="User" text={turn.user_text} />
          <Bubble who="Jarvis" text={turn.reply_text} accent />
        </div>
      </Section>

      <Section title="Stage latencies">
        {stages.length === 0 ? (
          <Empty>No timing data.</Empty>
        ) : (
          <div className="flex flex-col gap-1.5">
            {stages.map(([s, v]) => (
              <div key={s} className="grid grid-cols-[170px_1fr_70px] items-center gap-3">
                <span className="text-white/70">{STAGE_LABELS[s] ?? s}</span>
                <div className="h-2 rounded-full bg-white/[0.05]" aria-hidden>
                  <div className="h-full rounded-full bg-cyan/80" style={{ width: `${barPct(v, maxStage)}%` }} />
                </div>
                <span className="text-right tabular-nums">{fmtLatency(v)}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title={`Tool calls (${tools.length})`}>
        {tools.length === 0 ? (
          <p className="text-white/40">No tools used.</p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Tool</Th>
                <Th>Args</Th>
                <Th>Result</Th>
                <Th className="text-right">Time</Th>
              </tr>
            </thead>
            <tbody>
              {tools.map((t, i) => (
                <tr key={i}>
                  <Td className="font-medium whitespace-nowrap">{t.tool}</Td>
                  <Td className="font-mono text-[11.5px] break-all text-white/60" title={JSON.stringify(t.args)}>
                    {summarize(t.args, 200)}
                  </Td>
                  <Td>
                    <Badge tone={t.ok ? "good" : "bad"} className="mr-1.5">{t.ok ? "ok" : "failed"}</Badge>
                    <span className="text-white/70">{summarize(t.summary, 200)}</span>
                  </Td>
                  <Td className="text-right tabular-nums">{fmtLatency(t.duration_s)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Section>

      <Section title={`Timeline (${timeline.length})`}>
        {timeline.length === 0 ? (
          <Empty>No events recorded for this turn.</Empty>
        ) : (
          <ol className="relative border-l border-white/10 pl-4">
            {timeline.map((it, i) => (
              <li key={i} className="relative py-1">
                <span
                  aria-hidden
                  className={`absolute top-[11px] -left-[19.5px] size-[7px] rounded-full ${it.kind === "mark" ? "border border-white/40 bg-ink" : "bg-white/60"}`}
                />
                <div className="flex items-baseline gap-2.5">
                  <span className="w-[78px] shrink-0 text-[11.5px] text-white/40 tabular-nums">{fmtOffset(it.t)}</span>
                  {it.kind === "mark" ? (
                    <span className="text-[11.5px] text-white/45 italic">mark · {it.type}</span>
                  ) : (
                    <>
                      <Badge tone={it.tone} className="shrink-0">{it.type}</Badge>
                      <span className="min-w-0 break-words text-white/75">{it.text}</span>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <p className="text-[11px] text-white/30">Turn id {turn.id}</p>
    </>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-white/40">{label}</dt>
      <dd className="truncate tabular-nums" title={value}>{value}</dd>
    </div>
  );
}

function Bubble({ who, text, accent }: { who: string; text: string | null; accent?: boolean }) {
  return (
    <div className={`rounded-lg border px-3 py-2 ${accent ? "border-cyan/20 bg-cyan/[0.06]" : "border-white/[0.08] bg-white/[0.03]"}`}>
      <div className="mb-0.5 text-[11px] text-white/40">{who}</div>
      <p className="whitespace-pre-wrap text-white/90">{text || <span className="text-white/30">—</span>}</p>
    </div>
  );
}

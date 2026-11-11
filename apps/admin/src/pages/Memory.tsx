import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlarmClock, Bell, Check, Pencil, Plus, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ConfirmButton } from "../components/bits";
import { useToast } from "../components/Toast";
import { Badge, Button, Card, Empty, ErrorBox, IconButton, Input, Loading, PageHeader, Table, Td, Th } from "../components/ui";
import { del, get, post, put } from "../lib/api";
import { fmtAgo, fmtTime } from "../lib/format";
import { fmtUntil } from "../lib/misc";
import type { MemoryFact, TimerInfo } from "../lib/types";

export function Memory() {
  return (
    <>
      <PageHeader title="Memory" sub="What Jarvis remembers about you, and what it's waiting to remind you of." />
      <div className="flex flex-col gap-4">
        <FactsCard />
        <TimersCard />
      </div>
    </>
  );
}

function FactsCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["memory"], queryFn: () => get<MemoryFact[]>("/api/memory") });
  const [fact, setFact] = useState("");
  const [filter, setFilter] = useState("");
  const add = useMutation({
    mutationFn: () => post<{ id: number }>("/api/memory", { fact: fact.trim() }),
    onSuccess: () => {
      setFact("");
      toast.show("Remembered");
      void qc.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (fact.trim().length >= 2) add.mutate();
  };
  const f = filter.trim().toLowerCase();
  const rows = (q.data ?? []).filter((m) => !f || m.fact.toLowerCase().includes(f));

  return (
    <Card
      title={`Remembered facts${q.data ? ` (${q.data.length})` : ""}`}
      actions={<Input aria-label="Filter facts" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} className="h-7 w-48" />}
      bodyClass="p-0"
    >
      <form onSubmit={submit} className="flex gap-2 border-b border-white/[0.06] p-3">
        <label htmlFor="new-fact" className="sr-only">
          New fact
        </label>
        <Input id="new-fact" value={fact} maxLength={500} placeholder="e.g. Prefers temperatures in Celsius" onChange={(e) => setFact(e.target.value)} />
        <Button type="submit" variant="primary" busy={add.isPending} disabled={fact.trim().length < 2} icon={<Plus className="size-3.5" />}>
          Add
        </Button>
      </form>
      {q.isPending && <div className="px-4"><Loading /></div>}
      {q.isError && <div className="p-4"><ErrorBox error={q.error} onRetry={() => void q.refetch()} /></div>}
      {q.data && rows.length === 0 && <Empty>{q.data.length ? "No facts match." : "Nothing remembered yet. Say “Jarvis, remember that…”."}</Empty>}
      {rows.length > 0 && (
        <Table className="max-h-[520px] overflow-y-auto">
          <thead>
            <tr>
              <Th>Fact</Th>
              <Th>Source</Th>
              <Th>Updated</Th>
              <Th className="text-right">Actions</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <FactRow key={m.id} m={m} />
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function FactRow({ m }: { m: MemoryFact }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(m.fact);
  const save = useMutation({
    mutationFn: () => put(`/api/memory/${m.id}`, { fact: text.trim() }),
    onSuccess: () => {
      setEditing(false);
      toast.show("Updated");
      void qc.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/memory/${m.id}`),
    onSuccess: () => {
      toast.show("Forgotten");
      void qc.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  return (
    <tr>
      <Td className="w-[60%]">
        {editing ? (
          <form
            className="flex gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim().length >= 2) save.mutate();
            }}
          >
            <Input aria-label="Edit fact" autoFocus value={text} maxLength={500} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setEditing(false)} />
            <IconButton label="Save fact" type="submit" disabled={save.isPending || text.trim().length < 2}>
              <Check className="size-3.5 text-teal-300" />
            </IconButton>
            <IconButton
              label="Cancel edit"
              onClick={() => {
                setEditing(false);
                setText(m.fact);
              }}
            >
              <X className="size-3.5" />
            </IconButton>
          </form>
        ) : (
          <span className="text-white/90">{m.fact}</span>
        )}
      </Td>
      <Td>
        <Badge tone={m.source === "admin" ? "info" : "neutral"}>{m.source}</Badge>
      </Td>
      <Td className="whitespace-nowrap text-white/50" title={fmtTime(m.updated)}>
        {fmtAgo(m.updated)}
      </Td>
      <Td className="text-right whitespace-nowrap">
        <div className="inline-flex gap-1">
          {!editing && (
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)} icon={<Pencil className="size-3.5" />} aria-label={`Edit “${m.fact}”`}>
              Edit
            </Button>
          )}
          <ConfirmButton busy={remove.isPending} onConfirm={() => remove.mutate()} ariaLabel={`Delete “${m.fact}”`} />
        </div>
      </Td>
    </tr>
  );
}

function TimersCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["timers"], queryFn: () => get<TimerInfo[]>("/api/timers"), refetchInterval: 10_000 });
  const cancel = useMutation({
    mutationFn: (id: number) => del<{ ok: boolean }>(`/api/timers/${id}`),
    onSuccess: (r) => {
      toast.show(r.ok ? "Cancelled" : "Already fired", r.ok ? "ok" : "error");
      void qc.invalidateQueries({ queryKey: ["timers"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  return (
    <Card title="Upcoming timers & reminders" bodyClass="p-0">
      {q.isPending && <div className="px-4"><Loading /></div>}
      {q.isError && <div className="p-4"><ErrorBox error={q.error} onRetry={() => void q.refetch()} /></div>}
      {q.data && q.data.length === 0 && <Empty>Nothing scheduled.</Empty>}
      {q.data && q.data.length > 0 && (
        <Table>
          <thead>
            <tr>
              <Th>Kind</Th>
              <Th>Label</Th>
              <Th>Due</Th>
              <Th />
              <Th className="text-right">Actions</Th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((t) => (
              <tr key={t.id}>
                <Td>
                  <Badge tone={t.kind === "timer" ? "warn" : "violet"}>
                    {t.kind === "timer" ? <AlarmClock className="size-3" aria-hidden /> : <Bell className="size-3" aria-hidden />}
                    {t.kind}
                  </Badge>
                </Td>
                <Td className="text-white/90">{t.label || <span className="text-white/35">—</span>}</Td>
                <Td className="whitespace-nowrap tabular-nums">{fmtTime(t.due)}</Td>
                <Td className="whitespace-nowrap text-white/50">{fmtUntil(t.due)}</Td>
                <Td className="text-right">
                  <Button size="sm" variant="ghost" busy={cancel.isPending && cancel.variables === t.id} onClick={() => cancel.mutate(t.id)} icon={<X className="size-3.5" />}>
                    Cancel
                  </Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

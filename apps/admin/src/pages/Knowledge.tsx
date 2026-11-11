import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Globe, Layers, Mail, RefreshCw, RotateCcw, Search, Upload } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { ConfirmButton, Stat } from "../components/bits";
import { GraphCanvas } from "../components/GraphCanvas";
import { useToast } from "../components/Toast";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, NumberInput, PageHeader, Select, Table, Td, Th, Toggle } from "../components/ui";
import { Drawer, FormIssues, SaveBar } from "../components/widgets";
import { api, del, get, post } from "../lib/api";
import { fmtAgo } from "../lib/format";
import { communityColor as communityDot } from "../lib/graph";
import { chunkLocation, fmtBytes, knowledgeBusy, sourceTitle } from "../lib/misc";
import type { GraphData, GraphQueryResult, KnowledgeChunk, KnowledgeConfig, KnowledgeOverview, KnowledgeSource, SearchHit, SourceStatus } from "../lib/types";
import { useSection } from "../lib/useSection";

const STATUS_TONE: Record<SourceStatus, "good" | "bad" | "info" | "warn" | "violet" | "neutral"> = {
  queued: "neutral",
  extracting: "info",
  embedding: "info",
  graph: "violet",
  ready: "good",
  error: "bad",
};

const ACCEPT = ".pdf,.md,.txt,.docx,.html,.htm,.csv,.json,.rst";

export function Knowledge() {
  const kb = useQuery({
    queryKey: ["knowledge"],
    queryFn: () => get<KnowledgeOverview>("/api/knowledge"),
    refetchInterval: (q) => (knowledgeBusy(q.state.data?.sources ?? []) || (q.state.data?.stats.queue ?? 0) > 0 ? 3000 : 20_000),
  });
  const [chunksOf, setChunksOf] = useState<KnowledgeSource | null>(null);

  return (
    <>
      <PageHeader
        title="Knowledge"
        sub={kb.data && <>Embedder <code className="text-white/70">{kb.data.embedder ?? "none available"}</code> · vectors in {kb.data.vector_backend} · watch folder <code className="text-white/70">{kb.data.watch_folder}</code></>}
      />
      {kb.isPending && <Loading />}
      {kb.isError && <ErrorBox error={kb.error} onRetry={() => void kb.refetch()} />}
      {kb.data && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-3 gap-3 md:grid-cols-6">
            <Stat label="Chunks" value={kb.data.stats.chunks} />
            <Stat label="Vectors" value={kb.data.stats.vectors} />
            <Stat label="Entities" value={kb.data.stats.entities} />
            <Stat label="Relations" value={kb.data.stats.relations} />
            <Stat label="Communities" value={kb.data.stats.communities} />
            <Stat label="Queue" value={kb.data.stats.queue} />
          </div>
          <AddSources />
          <SourcesCard sources={kb.data.sources} onChunks={setChunksOf} />
          <SearchCard />
          <GraphCard version={`${kb.data.stats.entities}/${kb.data.stats.relations}/${kb.data.stats.communities}`} />
          <KnowledgeConfigCard />
        </div>
      )}
      {chunksOf && <ChunksDrawer source={chunksOf} onClose={() => setChunksOf(null)} />}
    </>
  );
}

// ------------------------------------------------------------------ adding sources

function AddSources() {
  const qc = useQueryClient();
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState("");
  const [url, setUrl] = useState("");
  const [thread, setThread] = useState("");
  const refresh = () => void qc.invalidateQueries({ queryKey: ["knowledge"] });

  const uploadFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const list = [...files];
    let ok = 0;
    for (const [i, f] of list.entries()) {
      setProgress(`Uploading ${i + 1}/${list.length}: ${f.name} (${fmtBytes(f.size)})`);
      try {
        await api(`/api/knowledge/upload?filename=${encodeURIComponent(f.name)}`, { method: "POST", body: f, contentType: f.type || "application/octet-stream" });
        ok++;
      } catch (e) {
        toast.show(`${f.name}: ${(e as Error).message}`, "error");
      }
    }
    setProgress("");
    if (fileInput.current) fileInput.current.value = "";
    if (ok) toast.show(`Queued ${ok} file${ok > 1 ? "s" : ""}`);
    refresh();
  };

  const addUrl = useMutation({
    mutationFn: () => post("/api/knowledge/url", { url: url.trim() }),
    onSuccess: () => {
      setUrl("");
      toast.show("URL queued");
      refresh();
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const addMail = useMutation({
    mutationFn: () => post("/api/knowledge/mail", { thread_id: thread.trim() }),
    onSuccess: () => {
      setThread("");
      toast.show("Mail thread queued");
      refresh();
    },
    onError: (e) => toast.show(e.message, "error"),
  });

  return (
    <Card title="Add sources">
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="flex flex-col gap-1">
          <span className="text-[12px] font-medium text-white/60">Files</span>
          <input ref={fileInput} type="file" multiple accept={ACCEPT} className="sr-only" aria-label="Files to add" onChange={(e) => void uploadFiles(e.target.files)} />
          <Button busy={!!progress} onClick={() => fileInput.current?.click()} icon={<Upload className="size-3.5" />} className="self-start">
            Choose files…
          </Button>
          <p className="text-[11.5px] text-white/40" aria-live="polite">{progress || "PDF, Markdown, text, Word, HTML, CSV, JSON — up to 50 MB each."}</p>
        </div>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (url.trim().length >= 8) addUrl.mutate();
          }}
        >
          <Field label="Web page" hint="Fetched once and indexed.">
            {(id) => (
              <div className="flex gap-1.5">
                <Input id={id} type="url" value={url} placeholder="https://…" onChange={(e) => setUrl(e.target.value)} />
                <Button type="submit" busy={addUrl.isPending} disabled={url.trim().length < 8} icon={<Globe className="size-3.5" />}>
                  Add
                </Button>
              </div>
            )}
          </Field>
        </form>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (thread.trim().length >= 4) addMail.mutate();
          }}
        >
          <Field label="Gmail thread id" hint="Needs the Gmail connector.">
            {(id) => (
              <div className="flex gap-1.5">
                <Input id={id} value={thread} placeholder="18c4f…" className="font-mono" onChange={(e) => setThread(e.target.value)} />
                <Button type="submit" busy={addMail.isPending} disabled={thread.trim().length < 4} icon={<Mail className="size-3.5" />}>
                  Pin
                </Button>
              </div>
            )}
          </Field>
        </form>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ sources

function SourcesCard({ sources, onChunks }: { sources: KnowledgeSource[]; onChunks: (s: KnowledgeSource) => void }) {
  return (
    <Card title={`Sources (${sources.length})`} bodyClass="p-0">
      {sources.length === 0 ? (
        <Empty>Nothing indexed yet.</Empty>
      ) : (
        <Table className="max-h-[440px] overflow-y-auto">
          <thead>
            <tr>
              <Th>Source</Th>
              <Th>Kind</Th>
              <Th>Status</Th>
              <Th className="text-right">Chunks</Th>
              <Th>Updated</Th>
              <Th className="text-right">Actions</Th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <SourceRow key={s.id} s={s} onChunks={() => onChunks(s)} />
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function SourceRow({ s, onChunks }: { s: KnowledgeSource; onChunks: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["knowledge"] });
  const reingest = useMutation({
    mutationFn: () => post(`/api/knowledge/sources/${s.id}/reingest`),
    onSuccess: () => {
      toast.show("Re-ingesting");
      refresh();
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/knowledge/sources/${s.id}`),
    onSuccess: () => {
      toast.show("Source deleted");
      refresh();
      void qc.invalidateQueries({ queryKey: ["knowledge-graph"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const busy = s.status !== "ready" && s.status !== "error";
  return (
    <tr>
      <Td className="max-w-[380px]">
        <div className="truncate font-medium text-white" title={s.uri}>
          {sourceTitle(s)}
        </div>
        {s.error && <div className="text-[11.5px] text-red-300">{s.error}</div>}
      </Td>
      <Td>
        <Badge>{s.kind}</Badge>
      </Td>
      <Td>
        <Badge tone={STATUS_TONE[s.status]}>
          {busy && <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden />}
          {s.status}
        </Badge>
      </Td>
      <Td className="text-right tabular-nums">{s.chunks ?? "—"}</Td>
      <Td className="whitespace-nowrap text-white/50">{fmtAgo(s.updated)}</Td>
      <Td className="text-right whitespace-nowrap">
        <div className="inline-flex gap-1">
          <Button size="sm" variant="ghost" onClick={onChunks} disabled={!s.chunks} icon={<Layers className="size-3.5" />}>
            Chunks
          </Button>
          <Button size="sm" variant="ghost" busy={reingest.isPending} disabled={busy} onClick={() => reingest.mutate()} icon={<RotateCcw className="size-3.5" />}>
            Re-ingest
          </Button>
          <ConfirmButton busy={remove.isPending} onConfirm={() => remove.mutate()} ariaLabel={`Delete ${sourceTitle(s)}`} />
        </div>
      </Td>
    </tr>
  );
}

function ChunksDrawer({ source, onClose }: { source: KnowledgeSource; onClose: () => void }) {
  const q = useQuery({ queryKey: ["chunks", source.id], queryFn: () => get<KnowledgeChunk[]>(`/api/knowledge/sources/${source.id}/chunks`) });
  return (
    <Drawer title={`Chunks · ${sourceTitle(source)}`} onClose={onClose} wide>
      {q.isPending && <Loading />}
      {q.isError && <ErrorBox error={q.error} onRetry={() => void q.refetch()} />}
      {q.data?.length === 0 && <Empty>No chunks.</Empty>}
      <ol className="flex flex-col gap-2">
        {q.data?.map((c) => (
          <li key={c.id} className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
            <div className="mb-1 flex gap-2 text-[11px] text-white/40">
              <span className="tabular-nums">#{c.idx}</span>
              {chunkLocation(c.meta) && <span>{chunkLocation(c.meta)}</span>}
            </div>
            <p className="text-[12.5px] whitespace-pre-wrap text-white/80">{c.text}</p>
          </li>
        ))}
      </ol>
    </Drawer>
  );
}

// ------------------------------------------------------------------ search playground

function SearchCard() {
  const [query, setQuery] = useState("");
  const [k, setK] = useState(5);
  const [mode, setMode] = useState<"local" | "global">("local");
  const search = useMutation({
    mutationFn: () => post<{ hits: SearchHit[]; graph: GraphQueryResult }>("/api/knowledge/search", { query: query.trim(), k, mode }),
  });
  const r = search.data;
  return (
    <Card title="Search playground">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) search.mutate();
        }}
      >
        <Field label="Query" className="min-w-64 flex-1">
          {(id) => <Input id={id} value={query} maxLength={300} placeholder="What does the wake word pipeline do?" onChange={(e) => setQuery(e.target.value)} />}
        </Field>
        <Field label="Hits" className="w-20">
          {(id) => <NumberInput id={id} min={1} max={20} value={k} onValue={(v) => setK(Math.max(1, Math.min(20, v ?? 5)))} />}
        </Field>
        <Field label="Graph mode" className="w-48">
          {(id) => (
            <Select id={id} value={mode} onChange={(e) => setMode(e.target.value as "local" | "global")}>
              <option value="local">local (entities)</option>
              <option value="global">global (communities)</option>
            </Select>
          )}
        </Field>
        <Button type="submit" variant="primary" busy={search.isPending} disabled={!query.trim()} icon={<Search className="size-3.5" />}>
          Search
        </Button>
      </form>
      {search.isError && <div className="mt-3"><ErrorBox error={search.error} /></div>}
      {r && (
        <div className="mt-4 grid gap-4 xl:grid-cols-[3fr_2fr]">
          <div>
            <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-white/45 uppercase">Passages ({r.hits.length})</h3>
            {r.hits.length === 0 && <p className="text-white/40">No matching passages.</p>}
            <ol className="flex flex-col gap-2">
              {r.hits.map((h, i) => (
                <li key={i} className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
                  <div className="mb-1 flex items-center gap-2">
                    <Badge tone="info">
                      <FileText className="size-3" aria-hidden /> {h.citation}
                    </Badge>
                    <span className="ml-auto text-[11.5px] text-white/40 tabular-nums">score {h.score.toFixed(3)}</span>
                  </div>
                  <p className="line-clamp-6 text-[12.5px] whitespace-pre-wrap text-white/75">{h.text}</p>
                </li>
              ))}
            </ol>
          </div>
          <GraphResult g={r.graph} />
        </div>
      )}
    </Card>
  );
}

function GraphResult({ g }: { g: GraphQueryResult }) {
  return (
    <div>
      <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-white/45 uppercase">Graph ({g.mode})</h3>
      {g.mode === "global" ? (
        g.communities?.length ? (
          <ul className="flex flex-col gap-2">
            {g.communities.map((c) => (
              <li key={c.id} className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
                <div className="font-medium text-white">{c.title}</div>
                <p className="text-[12px] text-white/60">{c.summary}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-white/40">No community summaries yet.</p>
        )
      ) : (
        <div className="flex flex-col gap-3 text-[12.5px]">
          {!g.entities?.length && <p className="text-white/40">No matching entities.</p>}
          {!!g.entities?.length && (
            <div className="flex flex-wrap gap-1">
              {g.entities.map((e) => (
                <Badge key={e.name} tone="violet">
                  <span title={e.description}>{e.name}{e.type ? ` · ${e.type}` : ""}</span>
                </Badge>
              ))}
            </div>
          )}
          {!!g.relations?.length && (
            <ul className="flex flex-col gap-1">
              {g.relations.map((r, i) => (
                <li key={i} className="text-white/70" title={r.description}>
                  <span className="text-white">{r.source}</span> <span className="text-sky-300">—{r.label}→</span> <span className="text-white">{r.target}</span>
                </li>
              ))}
            </ul>
          )}
          {!!g.sources?.length && (
            <div className="flex flex-wrap gap-1">
              {g.sources.map((s, i) => <Badge key={i}>{s.citation}</Badge>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ graph

/** `version` changes whenever entity/relation/community counts do, so the graph refetches. */
function GraphCard({ version }: { version: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["knowledge-graph", version], queryFn: () => get<GraphData>("/api/knowledge/graph?limit=300"), staleTime: 30_000, placeholderData: (prev) => prev });
  const rebuild = useMutation({
    mutationFn: () => post<{ communities: number }>("/api/knowledge/communities/rebuild"),
    onSuccess: (r) => {
      toast.show(`${r.communities} communities`);
      void qc.invalidateQueries({ queryKey: ["knowledge-graph"] });
      void qc.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const reindex = useMutation({
    mutationFn: () => post<{ chunks: number }>("/api/knowledge/reindex"),
    onSuccess: (r) => {
      toast.show(`Re-embedded ${r.chunks} chunks`);
      void qc.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const d = q.data;
  return (
    <Card
      title={`Knowledge graph${d ? ` · ${d.nodes.length} entities, ${d.edges.length} relations` : ""}`}
      actions={
        <>
          <Button size="sm" variant="ghost" onClick={() => void q.refetch()} icon={<RefreshCw className="size-3.5" />}>
            Refresh
          </Button>
          <Button size="sm" busy={rebuild.isPending} onClick={() => rebuild.mutate()} title="Re-clusters entities and asks the LLM for summaries; can take a minute.">
            Rebuild communities
          </Button>
          <ConfirmButton
            label="Re-index"
            confirmLabel="Re-embed everything?"
            icon={<RotateCcw className="size-3.5" />}
            busy={reindex.isPending}
            onConfirm={() => reindex.mutate()}
          />
        </>
      }
    >
      {q.isPending && <Loading />}
      {q.isError && <ErrorBox error={q.error} onRetry={() => void q.refetch()} />}
      {d && d.nodes.length === 0 && <Empty>No entities yet — they appear after ingestion's graph step.</Empty>}
      {d && d.nodes.length > 0 && (
        <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
          <GraphCanvas data={d} />
          <div className="scroll-thin max-h-[520px] overflow-y-auto">
            <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-white/45 uppercase">Communities ({d.communities.length})</h3>
            {d.communities.length === 0 && <p className="text-[12.5px] text-white/40">None yet — press Rebuild communities.</p>}
            <ul className="flex flex-col gap-2">
              {d.communities.map((c) => (
                <li key={c.id} className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5">
                  <div className="flex items-center gap-2">
                    <span className="size-2.5 shrink-0 rounded-full" style={{ background: communityDot(c.id) }} aria-hidden />
                    <span className="font-medium text-white">{c.title || `Community ${c.id}`}</span>
                    <span className="ml-auto text-[11px] text-white/40">{c.size}</span>
                  </div>
                  {c.summary && <p className="mt-1 text-[12px] text-white/55">{c.summary}</p>}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Card>
  );
}


// ------------------------------------------------------------------ config

function KnowledgeConfigCard() {
  const s = useSection("knowledge", { invalidate: [["knowledge"]] });
  const d = s.draft;
  const set = s.set;
  return (
    <Card title="Knowledge settings" actions={<SaveBar dirty={s.dirty} saving={s.saving} onSave={() => void s.save()} onReset={s.reset} />}>
      {s.query.isPending && <Loading />}
      {s.query.isError && <ErrorBox error={s.query.error} onRetry={() => void s.query.refetch()} />}
      <FormIssues issues={s.issues} />
      {d && (
        <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
          <Field label="Embeddings provider" hint="auto = remote endpoint if set, else local, else OpenAI." errors={s.fieldIssues("embed_provider")}>
            {(id) => (
              <Select id={id} value={d.embed_provider} onChange={(e) => set("embed_provider", e.target.value as KnowledgeConfig["embed_provider"])}>
                {["auto", "remote", "local", "openai"].map((v) => <option key={v}>{v}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Remote embeddings URL" hint="OpenAI-compatible /v1 (Ollama, TEI…)" errors={s.fieldIssues("embed_base_url")}>
            {(id) => <Input id={id} value={d.embed_base_url} placeholder="http://gpu-box:11434/v1" onChange={(e) => set("embed_base_url", e.target.value)} />}
          </Field>
          <Field label="Remote model" errors={s.fieldIssues("embed_model")}>
            {(id) => <Input id={id} value={d.embed_model} onChange={(e) => set("embed_model", e.target.value)} />}
          </Field>
          <Field label="Local model" errors={s.fieldIssues("local_embed_model")}>
            {(id) => <Input id={id} value={d.local_embed_model} onChange={(e) => set("local_embed_model", e.target.value)} />}
          </Field>
          <Field label="OpenAI model" errors={s.fieldIssues("openai_embed_model")}>
            {(id) => <Input id={id} value={d.openai_embed_model} onChange={(e) => set("openai_embed_model", e.target.value)} />}
          </Field>
          <Field label="Vector backend" errors={s.fieldIssues("vector_backend")}>
            {(id) => (
              <Select id={id} value={d.vector_backend} onChange={(e) => set("vector_backend", e.target.value as KnowledgeConfig["vector_backend"])}>
                <option value="sqlite">sqlite (built-in)</option>
                <option value="qdrant">qdrant</option>
              </Select>
            )}
          </Field>
          <Field label="Qdrant URL" errors={s.fieldIssues("qdrant_url")}>
            {(id) => <Input id={id} value={d.qdrant_url} disabled={d.vector_backend !== "qdrant"} onChange={(e) => set("qdrant_url", e.target.value)} />}
          </Field>
          <Field label="Watch folder" hint="Files dropped here are ingested automatically." errors={s.fieldIssues("watch_folder")}>
            {(id) => <Input id={id} value={d.watch_folder} onChange={(e) => set("watch_folder", e.target.value)} />}
          </Field>
          <Field label="Chunk size (chars)" hint="300–6000; changes apply to new ingests." errors={s.fieldIssues("chunk_chars")}>
            {(id) => <NumberInput id={id} min={300} max={6000} step={50} value={d.chunk_chars} onValue={(v) => set("chunk_chars", v ?? 1100)} />}
          </Field>
          <Field label="Chunk overlap (chars)" hint="0–1000" errors={s.fieldIssues("chunk_overlap")}>
            {(id) => <NumberInput id={id} min={0} max={1000} step={10} value={d.chunk_overlap} onValue={(v) => set("chunk_overlap", v ?? 180)} />}
          </Field>
          <Field label="Re-ranking" hint="Cross-encoder re-rank — slow on a Pi (~3 s per query)." errors={s.fieldIssues("rerank")}>
            {(id) => (
              <Select id={id} value={d.rerank} onChange={(e) => set("rerank", e.target.value as KnowledgeConfig["rerank"])}>
                <option value="off">off</option>
                <option value="local">local</option>
              </Select>
            )}
          </Field>
          <Field label="Re-rank model" errors={s.fieldIssues("rerank_model")}>
            {(id) => <Input id={id} value={d.rerank_model} disabled={d.rerank === "off"} onChange={(e) => set("rerank_model", e.target.value)} />}
          </Field>
          <div className="pt-5">
            <Toggle checked={d.graph_enabled} onChange={(v) => set("graph_enabled", v)} label="Build knowledge graph" hint="Extract entities and relations with the LLM." />
          </div>
          <Field label="Graph extraction route" hint="Which provider chain extracts entities." errors={s.fieldIssues("graph_route")}>
            {(id) => (
              <Select id={id} value={d.graph_route} disabled={!d.graph_enabled} onChange={(e) => set("graph_route", e.target.value as KnowledgeConfig["graph_route"])}>
                <option value="chat">chat</option>
                <option value="tools">tools</option>
              </Select>
            )}
          </Field>
        </div>
      )}
    </Card>
  );
}

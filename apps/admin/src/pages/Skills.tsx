import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FileCode2, Pencil, Plus, RefreshCw, Save, Upload } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ConfirmButton } from "../components/bits";
import { useToast } from "../components/Toast";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, PageHeader, Select, Textarea, Toggle } from "../components/ui";
import { Drawer } from "../components/widgets";
import { api, del, get, put } from "../lib/api";
import { SKILL_NAME, skillTemplate } from "../lib/misc";
import type { SkillInfo, SkillsConfig } from "../lib/types";

interface EditTarget {
  name: string;
  path: string;
  /** New skill: start from a template instead of loading. */
  initial?: string;
}

export function Skills() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["skills"], queryFn: () => get<SkillInfo[]>("/api/skills") });
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const setList = (list: SkillInfo[]) => {
    qc.setQueryData(["skills"], list);
    void qc.invalidateQueries({ queryKey: ["tools"] });
  };
  const reload = useMutation({
    mutationFn: () => api<SkillInfo[]>("/api/skills/reload", { method: "POST" }),
    onSuccess: (l) => {
      setList(l);
      toast.show("Skills reloaded");
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const upload = useMutation({
    mutationFn: (f: File) => api<{ installed: string; skills: SkillInfo[] }>("/api/skills/upload", { method: "POST", body: f, contentType: "application/zip" }),
    onSuccess: (r) => {
      setList(r.skills);
      toast.show(`Installed ${r.installed}`);
    },
    onError: (e) => toast.show(e.message, "error"),
    onSettled: () => {
      if (file.current) file.current.value = "";
    },
  });

  return (
    <>
      <PageHeader
        title="Skills"
        sub="Instruction packs (SKILL.md + optional scripts) Jarvis loads when a trigger phrase matches."
        actions={
          <>
            <Button size="sm" variant="ghost" busy={reload.isPending} onClick={() => reload.mutate()} icon={<RefreshCw className="size-3.5" />}>
              Reload
            </Button>
            <input ref={file} type="file" accept=".zip,application/zip" className="sr-only" aria-label="Skill zip file" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
            <Button size="sm" busy={upload.isPending} onClick={() => file.current?.click()} icon={<Upload className="size-3.5" />}>
              Upload .zip
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-4">
        <NewSkill existing={(q.data ?? []).map((s) => s.name)} onCreate={(name) => setEditing({ name, path: "SKILL.md", initial: skillTemplate(name) })} />
        {q.isPending && <Loading />}
        {q.isError && <ErrorBox error={q.error} onRetry={() => void q.refetch()} />}
        {q.data && q.data.length === 0 && (
          <Card>
            <Empty>No skills installed.</Empty>
          </Card>
        )}
        <div className="grid gap-3 lg:grid-cols-2">
          {q.data?.map((s) => (
            <SkillCard key={s.name} s={s} onEdit={(path) => setEditing({ name: s.name, path })} onList={setList} />
          ))}
        </div>
      </div>
      {editing && <SkillEditor key={`${editing.name}/${editing.path}`} target={editing} files={q.data?.find((s) => s.name === editing.name)?.files ?? []} onClose={() => setEditing(null)} onSaved={setList} onSwitch={setEditing} />}
    </>
  );
}

function SkillCard({ s, onEdit, onList }: { s: SkillInfo; onEdit: (path: string) => void; onList: (l: SkillInfo[]) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const toggle = useMutation({
    mutationFn: async (on: boolean) => {
      const cfg = await get<SkillsConfig>("/api/config/skills");
      const disabled = on ? cfg.disabled.filter((n) => n !== s.name) : [...new Set([...cfg.disabled, s.name])];
      return put<SkillsConfig>("/api/config/skills", { disabled });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["skills"] });
      void qc.invalidateQueries({ queryKey: ["tools"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const remove = useMutation({
    mutationFn: () => del<SkillInfo[]>(`/api/skills/${encodeURIComponent(s.name)}`),
    onSuccess: (l) => {
      onList(l);
      toast.show(`Deleted ${s.name}`);
    },
    onError: (e) => toast.show(e.message, "error"),
  });

  return (
    <section className={`glass flex flex-col gap-2.5 rounded-xl p-4 ${s.enabled ? "" : "opacity-70"}`}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-mono text-[13px] font-semibold text-white">{s.name}</h2>
            {s.error ? <Badge tone="bad">error</Badge> : s.enabled ? <Badge tone="good">enabled</Badge> : <Badge>disabled</Badge>}
            {s.scripts.length > 0 && <Badge tone="violet">{s.scripts.length} script{s.scripts.length > 1 ? "s" : ""}</Badge>}
          </div>
          <p className="mt-1 text-[12.5px] text-white/60">{s.description || <span className="text-white/30">No description.</span>}</p>
        </div>
        <Toggle checked={s.enabled} disabled={toggle.isPending} onChange={(v) => toggle.mutate(v)} label={<span className="sr-only">Enable {s.name}</span>} />
      </div>
      {s.error && (
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-[12px] text-red-200">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden /> {s.error}
        </p>
      )}
      {s.triggers.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="mr-1 text-[11px] text-white/40">Triggers</span>
          {s.triggers.map((t) => <Badge key={t} tone="info">{t}</Badge>)}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1">
        <span className="mr-1 text-[11px] text-white/40">Files</span>
        {s.files.map((f) => (
          <button key={f} type="button" onClick={() => onEdit(f)} className="focus-ring inline-flex h-5 items-center gap-1 rounded border border-white/10 bg-white/[0.04] px-1.5 font-mono text-[11px] text-white/70 hover:bg-white/[0.09] hover:text-white">
            <FileCode2 className="size-3" aria-hidden /> {f}
          </button>
        ))}
      </div>
      <div className="flex gap-2 pt-1">
        <Button size="sm" onClick={() => onEdit("SKILL.md")} icon={<Pencil className="size-3.5" />}>
          Edit SKILL.md
        </Button>
        <ConfirmButton busy={remove.isPending} onConfirm={() => remove.mutate()} ariaLabel={`Delete ${s.name}`} />
      </div>
    </section>
  );
}

function NewSkill({ existing, onCreate }: { existing: string[]; onCreate: (name: string) => void }) {
  const [name, setName] = useState("");
  const error = name && !SKILL_NAME.test(name) ? "Lowercase letters, digits and dashes." : existing.includes(name) ? "A skill with that name exists." : "";
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name && !error) {
      onCreate(name);
      setName("");
    }
  };
  return (
    <Card title="New skill">
      <form onSubmit={submit} className="flex flex-wrap items-start gap-2">
        <Field label="Folder name" errors={error ? [error] : undefined} className="w-72">
          {(id) => <Input id={id} value={name} placeholder="my-skill" className="font-mono" onChange={(e) => setName(e.target.value.toLowerCase().replace(/[\s_]/g, "-"))} />}
        </Field>
        <Button type="submit" className="mt-[22px]" disabled={!name || !!error} icon={<Plus className="size-3.5" />}>
          Create from template
        </Button>
      </form>
    </Card>
  );
}

function SkillEditor({
  target,
  files,
  onClose,
  onSaved,
  onSwitch,
}: {
  target: EditTarget;
  files: string[];
  onClose: () => void;
  onSaved: (l: SkillInfo[]) => void;
  onSwitch: (t: EditTarget) => void;
}) {
  const toast = useToast();
  const isNew = target.initial !== undefined;
  const q = useQuery({
    queryKey: ["skill-file", target.name, target.path],
    queryFn: () => get<{ path: string; content: string }>(`/api/skills/${encodeURIComponent(target.name)}/file?path=${encodeURIComponent(target.path)}`),
    enabled: !isNew,
    gcTime: 0,
  });
  const [text, setText] = useState<string | null>(target.initial ?? null);
  const [saved, setSaved] = useState<string | null>(null);
  const [newPath, setNewPath] = useState("");
  useEffect(() => {
    if (q.data) {
      setText(q.data.content);
      setSaved(q.data.content);
    }
  }, [q.data]);
  const dirty = text !== null && text !== saved;

  const save = useMutation({
    mutationFn: () => put<SkillInfo[]>(`/api/skills/${encodeURIComponent(target.name)}/file?path=${encodeURIComponent(target.path)}`, { content: text ?? "" }),
    onSuccess: (l) => {
      setSaved(text);
      onSaved(l);
      const err = l.find((s) => s.name === target.name)?.error;
      if (err) toast.show(`Saved, but the skill has an error: ${err}`, "error");
      else toast.show("Saved");
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const onKey = (e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (dirty && !save.isPending) save.mutate();
    }
  };
  const close = () => {
    if (!dirty || confirm("Discard unsaved changes?")) onClose();
  };
  const fileList = files.includes(target.path) ? files : [target.path, ...files];

  return (
    <Drawer title={`${target.name} / ${target.path}`} onClose={close} wide>
      <div className="flex h-full flex-col gap-3" onKeyDown={onKey}>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="File" className="w-60">
            {(id) => (
              <Select id={id} value={target.path} onChange={(e) => (!dirty || confirm("Discard unsaved changes?")) && onSwitch({ name: target.name, path: e.target.value })}>
                {fileList.map((f) => <option key={f}>{f}</option>)}
              </Select>
            )}
          </Field>
          {!isNew && (
            <form
              className="flex items-end gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                const p = newPath.trim();
                if (p) onSwitch({ name: target.name, path: p, initial: "" });
                setNewPath("");
              }}
            >
              <Field label="New file" className="w-52">
                {(id) => <Input id={id} value={newPath} placeholder="scripts/run.py" className="font-mono text-[12px]" onChange={(e) => setNewPath(e.target.value)} />}
              </Field>
              <Button type="submit" size="md" disabled={!newPath.trim()}>
                Open
              </Button>
            </form>
          )}
          <div className="ml-auto flex items-center gap-2">
            {dirty && <Badge tone="warn">Unsaved</Badge>}
            <Button variant="primary" busy={save.isPending} disabled={!dirty && !isNew} onClick={() => save.mutate()} icon={<Save className="size-3.5" />}>
              Save <span className="text-white/60">⌘S</span>
            </Button>
          </div>
        </div>
        {q.isPending && !isNew && <Loading />}
        {q.isError && <ErrorBox error={q.error} />}
        {text !== null && (
          <Textarea
            aria-label={`Contents of ${target.path}`}
            value={text}
            spellCheck={false}
            autoFocus
            onChange={(e) => setText(e.target.value)}
            className="min-h-[60vh] flex-1 font-mono text-[12.5px] leading-relaxed whitespace-pre"
          />
        )}
        {target.path === "SKILL.md" && (
          <p className="text-[11.5px] text-white/40">
            Frontmatter needs <code>name</code> (= folder name), <code>description</code> and optional <code>triggers</code>.
          </p>
        )}
      </div>
    </Drawer>
  );
}

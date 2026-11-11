import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useToast } from "../components/Toast";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, PageHeader, Table, Td, Th } from "../components/ui";
import { del, put } from "../lib/api";
import { get } from "../lib/api";
import { fmtAgo } from "../lib/format";
import type { SecretInfo } from "../lib/types";

/** UPPER_SNAKE_CASE, starts with a letter, ≤ 64 chars. */
export function isSecretName(name: string): boolean {
  return /^[A-Z][A-Z0-9_]{0,63}$/.test(name);
}

export function Secrets() {
  const q = useQuery({ queryKey: ["secrets"], queryFn: () => get<SecretInfo[]>("/api/secrets") });
  const [editing, setEditing] = useState<string | null>(null);
  const [extra, setExtra] = useState<string[]>([]);
  const rows: SecretInfo[] = [
    ...(q.data ?? []),
    ...extra.filter((n) => !q.data?.some((s) => s.name === n)).map((name) => ({ name, description: "Custom", source: null, updated: null })),
  ];

  return (
    <>
      <PageHeader title="Secrets" sub="API keys and tokens. Values are write-only: they're never shown again after saving." />
      <div className="flex flex-col gap-4">
        <Card title="Stored secrets" bodyClass="p-0">
          {q.isPending && <div className="px-4"><Loading /></div>}
          {q.isError && <div className="p-4"><ErrorBox error={q.error} onRetry={() => void q.refetch()} /></div>}
          {q.data && rows.length === 0 && <Empty>No secrets.</Empty>}
          {q.data && rows.length > 0 && (
            <Table>
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Description</Th>
                  <Th>Source</Th>
                  <Th>Updated</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <SecretRow
                    key={s.name}
                    secret={s}
                    editing={editing === s.name}
                    onEdit={() => setEditing(editing === s.name ? null : s.name)}
                    onDone={() => {
                      setEditing(null);
                      setExtra((xs) => xs.filter((x) => x !== s.name));
                    }}
                  />
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <AddCustom
          existing={rows.map((r) => r.name)}
          onAdd={(name) => {
            setExtra((xs) => [...xs, name]);
            setEditing(name);
          }}
        />
      </div>
    </>
  );
}

function SourceBadge({ source }: { source: SecretInfo["source"] }) {
  if (source === "db") return <Badge tone="good">db</Badge>;
  if (source === "env") return <Badge tone="info">env</Badge>;
  return <Badge>unset</Badge>;
}

function SecretRow({ secret: s, editing, onEdit, onDone }: { secret: SecretInfo; editing: boolean; onEdit: () => void; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const save = useMutation({
    mutationFn: () => put(`/api/secrets/${encodeURIComponent(s.name)}`, { value }),
    onSuccess: () => {
      setValue("");
      onDone();
      toast.show(`${s.name} saved`);
      void qc.invalidateQueries({ queryKey: ["secrets"] });
      void qc.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/secrets/${encodeURIComponent(s.name)}`),
    onSuccess: () => {
      setConfirmDelete(false);
      toast.show(`${s.name} deleted`);
      void qc.invalidateQueries({ queryKey: ["secrets"] });
    },
    onError: (e) => toast.show(e.message, "error"),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (value.trim()) save.mutate();
  };

  return (
    <>
      <tr>
        <Td className="font-mono text-[12px] whitespace-nowrap text-white">{s.name}</Td>
        <Td className="text-white/55">{s.description || "—"}</Td>
        <Td>
          <SourceBadge source={s.source} />
        </Td>
        <Td className="whitespace-nowrap text-white/50">{s.updated ? fmtAgo(s.updated) : "—"}</Td>
        <Td className="text-right whitespace-nowrap">
          <div className="flex justify-end gap-1.5">
            <Button size="sm" variant={editing ? "ghost" : "secondary"} onClick={onEdit} icon={<KeyRound className="size-3.5" />}>
              {editing ? "Cancel" : s.source === "db" ? "Replace" : "Set"}
            </Button>
            {s.source === "db" &&
              (confirmDelete ? (
                <>
                  <Button size="sm" variant="danger" busy={remove.isPending} onClick={() => remove.mutate()}>
                    Confirm delete
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
                    Keep
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)} icon={<Trash2 className="size-3.5" />} aria-label={`Delete ${s.name}`}>
                  Delete
                </Button>
              ))}
          </div>
        </Td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={5} className="border-b border-white/[0.05] bg-white/[0.02] px-3 py-2.5">
            <form onSubmit={submit} className="flex items-center gap-2">
              <label htmlFor={`secret-${s.name}`} className="sr-only">
                New value for {s.name}
              </label>
              <Input
                id={`secret-${s.name}`}
                type="password"
                autoComplete="off"
                autoFocus
                placeholder={`New value for ${s.name}`}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="max-w-md font-mono"
              />
              <Button type="submit" variant="primary" size="sm" busy={save.isPending} disabled={!value.trim()}>
                Save secret
              </Button>
              {s.source === "env" && <span className="text-[11.5px] text-white/40">Overrides the environment value.</span>}
            </form>
          </td>
        </tr>
      )}
    </>
  );
}

function AddCustom({ existing, onAdd }: { existing: string[]; onAdd: (name: string) => void }) {
  const [name, setName] = useState("");
  const error = name && !isSecretName(name) ? "Use UPPER_SNAKE_CASE (letters, digits, _), starting with a letter." : existing.includes(name) ? "Already listed above." : "";
  return (
    <Card title="Add a custom secret">
      <form
        className="flex flex-wrap items-start gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (name && !error) {
            onAdd(name);
            setName("");
          }
        }}
      >
        <Field label="Name" errors={error ? [error] : undefined} className="w-72">
          {(id) => <Input id={id} value={name} placeholder="MY_SERVICE_TOKEN" className="font-mono" onChange={(e) => setName(e.target.value.toUpperCase().replace(/[\s-]/g, "_"))} />}
        </Field>
        <Button type="submit" className="mt-[22px]" disabled={!name || !!error} icon={<Plus className="size-3.5" />}>
          Add
        </Button>
      </form>
    </Card>
  );
}

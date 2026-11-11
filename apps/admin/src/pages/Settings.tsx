import { useQueryClient } from "@tanstack/react-query";
import { Download, LogOut, Upload } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { useToast } from "../components/Toast";
import { Button, Card, Field, Input, PageHeader } from "../components/ui";
import { api, post } from "../lib/api";

export function Settings() {
  return (
    <>
      <PageHeader title="Settings" />
      <div className="grid gap-4 xl:grid-cols-2">
        <PasswordCard />
        <div className="flex flex-col gap-4">
          <BackupCard />
          <SessionCard />
        </div>
      </div>
    </>
  );
}

function PasswordCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const mismatch = confirm.length > 0 && next !== confirm;
  const tooShort = next.length > 0 && next.length < 10;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (mismatch || tooShort) return;
    setBusy(true);
    setError("");
    try {
      await post("/api/auth/password", { current, new: next });
      toast.show("Password changed — please sign in again");
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
      await qc.invalidateQueries({ queryKey: ["auth"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't change the password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Change password">
      <form onSubmit={submit} className="flex max-w-sm flex-col gap-3">
        <Field label="Current password">
          {(id) => <Input id={id} type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />}
        </Field>
        <Field label="New password" hint="At least 10 characters." errors={tooShort ? ["At least 10 characters."] : undefined}>
          {(id) => <Input id={id} type="password" autoComplete="new-password" required minLength={10} value={next} onChange={(e) => setNext(e.target.value)} />}
        </Field>
        <Field label="Repeat new password" errors={mismatch ? ["Passwords don't match."] : undefined}>
          {(id) => <Input id={id} type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />}
        </Field>
        {error && (
          <p role="alert" className="text-[12.5px] text-red-300">
            {error}
          </p>
        )}
        <p className="text-[11.5px] text-white/40">All sessions, including this one, are signed out afterwards.</p>
        <div>
          <Button type="submit" variant="primary" busy={busy} disabled={!current || !next || mismatch || tooShort || next !== confirm}>
            Change password
          </Button>
        </div>
      </form>
    </Card>
  );
}

function BackupCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    setResult("");
    try {
      const text = await f.text();
      const r = await api<{ imported: string[] }>("/api/config/import", { method: "POST", body: text, contentType: "application/x-yaml" });
      setResult(r.imported.length ? `Imported: ${r.imported.join(", ")}` : "Nothing to import in that file.");
      toast.show("Config imported");
      void qc.invalidateQueries({ queryKey: ["config"] });
      void qc.invalidateQueries({ queryKey: ["voice"] });
      void qc.invalidateQueries({ queryKey: ["overview"] });
    } catch (e) {
      setResult("");
      toast.show(e instanceof Error ? e.message : "Import failed", "error");
    } finally {
      setBusy(false);
      if (file.current) file.current.value = "";
    }
  };

  return (
    <Card title="Backup & restore">
      <p className="mb-3 text-[12.5px] text-white/55">
        Export every config section as YAML. Secret values are never included — re-enter them on the Secrets page after a restore.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <a
          href="/api/config/export"
          download="jarvis-config.yaml"
          className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-md border border-white/10 bg-white/[0.06] px-3 font-medium text-white/85 hover:bg-white/[0.1]"
        >
          <Download className="size-3.5" aria-hidden /> Export YAML
        </a>
        <input ref={file} id="import-file" type="file" accept=".yaml,.yml,application/x-yaml,text/yaml" className="sr-only" onChange={(e) => void onFile(e.target.files?.[0])} />
        <Button busy={busy} onClick={() => file.current?.click()} icon={<Upload className="size-3.5" />}>
          Import YAML…
        </Button>
      </div>
      {result && <p className="mt-2 text-[12.5px] text-teal-300" role="status">{result}</p>}
    </Card>
  );
}

function SessionCard() {
  const qc = useQueryClient();
  const signOut = async () => {
    await post("/api/auth/logout").catch(() => {});
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
    await qc.invalidateQueries({ queryKey: ["auth"] });
  };
  return (
    <Card title="Session">
      <p className="mb-3 text-[12.5px] text-white/55">Sessions last up to the configured lifetime and are refreshed while you use the panel.</p>
      <Button variant="danger" onClick={() => void signOut()} icon={<LogOut className="size-3.5" />}>
        Sign out
      </Button>
    </Card>
  );
}

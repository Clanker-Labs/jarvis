import { useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { useRef, useState, type FormEvent, type ReactNode } from "react";
import { Button, Field, Input } from "../components/ui";
import { Orb } from "../components/widgets";
import { ApiError, post } from "../lib/api";

export function AuthCard({ title, sub, children }: { title: string; sub?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="glass w-full max-w-sm rounded-2xl p-7">
        <div className="mb-6 flex items-center gap-3">
          <Orb color="#0EA5E9" size={34} />
          <div>
            <h1 className="text-[15px] font-semibold text-white">{title}</h1>
            {sub && <p className="text-[12.5px] text-white/50">{sub}</p>}
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Login({ totpEnabled }: { totpEnabled: boolean }) {
  const qc = useQueryClient();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [askCode, setAskCode] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await post("/api/auth/login", askCode ? { password, code: code.trim() } : { password });
      await qc.invalidateQueries({ queryKey: ["auth"] });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && err.detail === "totp_required") {
        setAskCode(true);
        setTimeout(() => codeRef.current?.focus(), 0);
      } else {
        setError(err instanceof Error ? err.message : "Sign-in failed.");
        if (askCode) setCode("");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard title="JARVIS admin" sub={askCode ? "Two-factor authentication" : "Sign in to continue"}>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Password">
          {(id) => (
            <Input
              id={id}
              type="password"
              autoComplete="current-password"
              autoFocus
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              readOnly={askCode}
            />
          )}
        </Field>
        {askCode && (
          <Field label="6-digit code" hint="From your authenticator app.">
            {(id) => (
              <Input
                id={id}
                ref={codeRef}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                className="text-center text-base tracking-[0.5em]"
              />
            )}
          </Field>
        )}
        {error && (
          <p role="alert" className="text-[12.5px] text-red-300">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" busy={busy} icon={<Lock className="size-3.5" />}>
          {askCode ? "Verify" : "Sign in"}
        </Button>
        {askCode && (
          <button
            type="button"
            className="focus-ring rounded text-[12px] text-white/45 hover:text-white/70"
            onClick={() => {
              setAskCode(false);
              setCode("");
              setError("");
            }}
          >
            Use a different password
          </button>
        )}
        {!totpEnabled && !askCode && (
          <p className="text-[11.5px] text-white/35">First sign-in: you'll set up an authenticator app next.</p>
        )}
      </form>
    </AuthCard>
  );
}

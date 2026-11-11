import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState, type FormEvent } from "react";
import { Button, ErrorBox, Field, Input, Loading } from "../components/ui";
import { ApiError, post } from "../lib/api";
import { AuthCard } from "./Login";

interface Setup {
  secret: string;
  otpauth_uri: string;
}

/** "JBSWY3DPEHPK3PXP" → "JBSW Y3DP EHPK 3PXP" */
export function groupSecret(secret: string): string {
  return secret.replace(/(.{4})(?=.)/g, "$1 ");
}

export function TotpSetup() {
  const qc = useQueryClient();
  // A query (not an effect) so StrictMode / re-renders don't mint a second secret.
  const setup = useQuery({
    queryKey: ["totp-setup"],
    queryFn: () => post<Setup>("/api/auth/totp/setup"),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const [qr, setQr] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!setup.data) return;
    QRCode.toDataURL(setup.data.otpauth_uri, { margin: 1, width: 192, errorCorrectionLevel: "M" })
      .then(setQr)
      .catch(() => setQr(""));
  }, [setup.data]);

  // 409 = already enabled (e.g. another tab finished) → just re-check auth.
  useEffect(() => {
    if (setup.error instanceof ApiError && setup.error.status === 409) {
      void qc.invalidateQueries({ queryKey: ["auth"] });
    }
  }, [setup.error, qc]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await post("/api/auth/totp/enable", { code: code.trim() });
      qc.removeQueries({ queryKey: ["totp-setup"] });
      await qc.invalidateQueries({ queryKey: ["auth"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't verify the code.");
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    await post("/api/auth/logout").catch(() => {});
    qc.removeQueries({ queryKey: ["totp-setup"] });
    await qc.invalidateQueries({ queryKey: ["auth"] });
  };

  return (
    <AuthCard title="Set up two-factor sign-in" sub="Required before the admin panel unlocks.">
      {setup.isPending && <Loading label="Generating a secret…" />}
      {setup.isError && <ErrorBox error={setup.error} onRetry={() => void setup.refetch()} />}
      {setup.data && (
        <form onSubmit={submit} className="flex flex-col gap-4">
          <ol className="list-decimal space-y-1 pl-4 text-[12.5px] text-white/60">
            <li>Scan this code with an authenticator app (1Password, Aegis, Google Authenticator…).</li>
            <li>Enter the 6-digit code it shows.</li>
          </ol>
          <div className="flex justify-center">
            {qr ? (
              <img src={qr} alt="QR code for the authenticator app" width={192} height={192} className="rounded-lg bg-white p-1" />
            ) : (
              <div className="size-48 animate-pulse rounded-lg bg-white/5" />
            )}
          </div>
          <div>
            <p className="text-[12px] text-white/50">Can't scan? Enter this key manually:</p>
            <code
              data-testid="totp-secret"
              className="mt-1 block rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-center font-mono text-[13px] tracking-wider text-white select-all"
            >
              {groupSecret(setup.data.secret)}
            </code>
          </div>
          <Field label="6-digit code">
            {(id) => (
              <Input
                id={id}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                autoFocus
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                className="text-center text-base tracking-[0.5em]"
              />
            )}
          </Field>
          {error && (
            <p role="alert" className="text-[12.5px] text-red-300">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" busy={busy} disabled={code.length !== 6} icon={<ShieldCheck className="size-3.5" />}>
            Enable and continue
          </Button>
          <button type="button" onClick={() => void signOut()} className="focus-ring rounded text-[12px] text-white/45 hover:text-white/70">
            Sign out
          </button>
        </form>
      )}
    </AuthCard>
  );
}

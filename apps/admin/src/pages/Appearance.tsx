import { STATES } from "@jarvis/protocol";
import { Card, ErrorBox, Field, Input, Loading, NumberInput, PageHeader, Select, Slider, Toggle } from "../components/ui";
import { FormIssues, Orb, SaveBar } from "../components/widgets";
import { issuesAt, useSection } from "../lib/useSection";
import type { AppearanceConfig } from "../lib/types";

const HEX = /^#[0-9a-fA-F]{6}$/;

export function Appearance() {
  return (
    <>
      <PageHeader title="Appearance & preferences" sub="Kiosk look, location and assistant behaviour. Each section saves separately." />
      <div className="flex flex-col gap-4">
        <AppearanceSection />
        <div className="grid gap-4 xl:grid-cols-2">
          <LocationSection />
          <AgentSection />
        </div>
      </div>
    </>
  );
}

function SectionShell({
  title,
  s,
  children,
}: {
  title: string;
  s: { query: { isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown }; dirty: boolean; saving: boolean; save: () => unknown; reset: () => void; issues: Parameters<typeof FormIssues>[0]["issues"] };
  children: React.ReactNode;
}) {
  return (
    <Card title={title} actions={<SaveBar dirty={s.dirty} saving={s.saving} onSave={() => void s.save()} onReset={s.reset} />}>
      {s.query.isPending && <Loading />}
      {s.query.isError && <ErrorBox error={s.query.error} onRetry={() => void s.query.refetch()} />}
      <FormIssues issues={s.issues} />
      {children}
    </Card>
  );
}

// ------------------------------------------------------------------ appearance

function AppearanceSection() {
  const s = useSection("appearance");
  const d = s.draft;
  const setColor = (state: string, v: string) => s.set("colors", { ...(d?.colors ?? {}), [state]: v.toUpperCase() });
  return (
    <SectionShell title="Kiosk appearance" s={s}>
      {d && (
        <div className="flex flex-col gap-5">
          <div>
            <div className="mb-2 text-[12px] font-medium text-white/60">State colours</div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-8">
              {STATES.map((st) => (
                <ColorTile key={st} state={st} value={d.colors[st] ?? "#FFFFFF"} dim={1} onChange={(v) => setColor(st, v)} errors={issuesAt(s.issues, "colors", st)} />
              ))}
            </div>
          </div>
          <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
            <Field label="Render quality" hint="auto adapts to the device's frame rate.">
              {(id) => (
                <Select id={id} value={d.quality} onChange={(e) => s.set("quality", e.target.value as AppearanceConfig["quality"])}>
                  {["auto", "high", "medium", "low"].map((q) => <option key={q}>{q}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Night starts" errors={s.fieldIssues("night_start")}>
              {(id) => <Input id={id} type="time" value={d.night_start} onChange={(e) => s.set("night_start", e.target.value)} />}
            </Field>
            <Field label="Night ends" errors={s.fieldIssues("night_end")}>
              {(id) => <Input id={id} type="time" value={d.night_end} onChange={(e) => s.set("night_end", e.target.value)} />}
            </Field>
            <Field label="Night brightness" hint="Screen brightness during night hours." errors={s.fieldIssues("night_dim")}>
              {(id) => (
                <div className="flex items-center gap-3">
                  <div className="flex-1">
                    <Slider id={id} min={0.05} max={1} step={0.05} value={d.night_dim} onChange={(v) => s.set("night_dim", v)} format={(v) => `${Math.round(v * 100)}%`} />
                  </div>
                  <Orb color={d.colors.IDLE ?? "#0EA5E9"} size={30} dim={d.night_dim} />
                </div>
              )}
            </Field>
            <Field label="Screen sleep (minutes)" hint="0 = never. Idle time before the display sleeps." errors={s.fieldIssues("screen_sleep_min")}>
              {(id) => <NumberInput id={id} min={0} max={240} value={d.screen_sleep_min} onValue={(v) => s.set("screen_sleep_min", v ?? 0)} />}
            </Field>
            <div className="pt-5">
              <Toggle checked={d.show_cards} onChange={(v) => s.set("show_cards", v)} label="Show info cards" hint="Weather, time and recent conversation on the kiosk." />
            </div>
          </div>
        </div>
      )}
    </SectionShell>
  );
}

function ColorTile({ state, value, dim, onChange, errors }: { state: string; value: string; dim: number; onChange: (v: string) => void; errors: string[] }) {
  const valid = HEX.test(value);
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-white/[0.06] bg-black/30 p-3">
      <div className="flex h-16 items-center justify-center">
        <Orb color={valid ? value : "#444444"} size={60} dim={dim} />
      </div>
      <div className="text-[11px] font-semibold tracking-wider text-white/70">{state}</div>
      <div className="flex w-full items-center gap-1.5">
        <input
          type="color"
          aria-label={`${state} colour picker`}
          value={valid ? value.toLowerCase() : "#000000"}
          onChange={(e) => onChange(e.target.value)}
          className="focus-ring size-7 shrink-0 cursor-pointer rounded"
        />
        <Input
          aria-label={`${state} colour hex`}
          value={value}
          maxLength={7}
          onChange={(e) => onChange(e.target.value)}
          className={`h-7 px-1.5 font-mono text-[11.5px] ${valid ? "" : "border-danger/60"}`}
        />
      </div>
      {errors.map((m) => <p key={m} className="text-[11px] text-red-300">{m}</p>)}
    </div>
  );
}

// ------------------------------------------------------------------ location

function LocationSection() {
  const s = useSection("location");
  const d = s.draft;
  const tzHint = (() => {
    try {
      return `“auto” uses the device zone. This browser: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
    } catch {
      return "“auto” or an IANA zone like Europe/Paris";
    }
  })();
  return (
    <SectionShell title="Location" s={s}>
      {d && (
        <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
          <Field label="Place name" className="sm:col-span-2" errors={s.fieldIssues("name")} hint="Used for weather and as context for the assistant.">
            {(id) => <Input id={id} value={d.name} placeholder="e.g. Lyon, France" onChange={(e) => s.set("name", e.target.value)} />}
          </Field>
          <Field label="Latitude" errors={s.fieldIssues("latitude")}>
            {(id) => <NumberInput id={id} nullable step="any" min={-90} max={90} value={d.latitude} onValue={(v) => s.set("latitude", v)} />}
          </Field>
          <Field label="Longitude" errors={s.fieldIssues("longitude")}>
            {(id) => <NumberInput id={id} nullable step="any" min={-180} max={180} value={d.longitude} onValue={(v) => s.set("longitude", v)} />}
          </Field>
          <Field label="Timezone" hint={tzHint} errors={s.fieldIssues("timezone")}>
            {(id) => <Input id={id} value={d.timezone} onChange={(e) => s.set("timezone", e.target.value.trim())} />}
          </Field>
          <Field label="Units" errors={s.fieldIssues("units")}>
            {(id) => (
              <Select id={id} value={d.units} onChange={(e) => s.set("units", e.target.value as "metric" | "imperial")}>
                <option value="metric">metric (°C, km/h)</option>
                <option value="imperial">imperial (°F, mph)</option>
              </Select>
            )}
          </Field>
        </div>
      )}
    </SectionShell>
  );
}

// ------------------------------------------------------------------ agent

function AgentSection() {
  const s = useSection("agent");
  const d = s.draft;
  return (
    <SectionShell title="Assistant" s={s}>
      {d && (
        <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
          <Field label="Your name" hint="How Jarvis addresses you." className="sm:col-span-2" errors={s.fieldIssues("user_name")}>
            {(id) => <Input id={id} value={d.user_name} onChange={(e) => s.set("user_name", e.target.value)} />}
          </Field>
          <Field label="Max tool steps per turn" hint="1–32" errors={s.fieldIssues("max_steps")}>
            {(id) => <NumberInput id={id} min={1} max={32} value={d.max_steps} onValue={(v) => s.set("max_steps", v ?? 8)} />}
          </Field>
          <Field label="History turns" hint="Past exchanges sent as context (2–100)." errors={s.fieldIssues("history_turns")}>
            {(id) => <NumberInput id={id} min={2} max={100} value={d.history_turns} onValue={(v) => s.set("history_turns", v ?? 12)} />}
          </Field>
          <Field label="Session timeout (minutes)" hint="Start a fresh conversation after this much quiet." errors={s.fieldIssues("session_timeout_min")}>
            {(id) => <NumberInput id={id} min={1} max={1440} value={d.session_timeout_min} onValue={(v) => s.set("session_timeout_min", v ?? 30)} />}
          </Field>
        </div>
      )}
    </SectionShell>
  );
}

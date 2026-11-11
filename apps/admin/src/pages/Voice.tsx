import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Megaphone, Play, Radio, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useToast } from "../components/Toast";
import { Badge, Button, Card, ErrorBox, Field, Input, Loading, NumberInput, PageHeader, Select, Slider, Textarea, Toggle } from "../components/ui";
import { FormIssues, ReorderList, SaveBar } from "../components/widgets";
import { ApiError, api, get, post, request } from "../lib/api";
import { useEvents } from "../lib/events";
import { pct01 } from "../lib/format";
import type { Engine, VoiceConfig, VoiceInfo } from "../lib/types";
import { useSection } from "../lib/useSection";

export function Voice() {
  const info = useQuery({ queryKey: ["voice"], queryFn: () => get<VoiceInfo>("/api/voice"), refetchInterval: 10_000 });
  const s = useSection("voice");
  const d = s.draft;
  const node = info.data?.node_connected ?? false;

  return (
    <>
      <PageHeader
        title="Voice"
        sub={
          info.data && (
            <span className="inline-flex items-center gap-2">
              Audio node {node ? <Badge tone="good">connected</Badge> : <Badge tone="bad">not connected</Badge>}
            </span>
          )
        }
        actions={d && <SaveBar dirty={s.dirty} saving={s.saving} onSave={s.save} onReset={s.reset} />}
      />
      {(s.query.isPending || info.isPending) && <Loading />}
      {s.query.isError && <ErrorBox error={s.query.error} onRetry={() => void s.query.refetch()} />}
      {info.isError && <ErrorBox error={info.error} onRetry={() => void info.refetch()} />}
      {d && info.data && (
        <div className="flex flex-col gap-4">
          <FormIssues issues={s.issues} />
          <WakeCard threshold={d.wake_threshold} onThreshold={(v) => s.set("wake_threshold", v)} node={node} errors={s.fieldIssues("wake_threshold")} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Speech-to-text chain">
              <ReorderList
                label="STT chain"
                items={d.stt_chain}
                options={info.data.stt.map((e) => e.id)}
                onChange={(v) => s.set("stt_chain", v)}
                render={(id) => <EngineLabel id={id} engines={info.data.stt} />}
              />
            </Card>
            <Card title="Text-to-speech chain">
              <ReorderList
                label="TTS chain"
                items={d.tts_chain}
                options={info.data.tts.map((e) => e.id)}
                onChange={(v) => s.set("tts_chain", v)}
                render={(id) => <EngineLabel id={id} engines={info.data.tts} />}
              />
            </Card>
          </div>
          <EngineSettings d={d} set={s.set} />
          <ListeningCard d={d} set={s.set} fieldIssues={s.fieldIssues} />
          <PreviewCard engines={info.data.tts} node={node} />
        </div>
      )}
    </>
  );
}

function EngineLabel({ id, engines }: { id: string; engines: Engine[] }) {
  const e = engines.find((x) => x.id === id);
  return (
    <>
      <span className="truncate font-medium">{id}</span>
      {!e ? <Badge tone="bad">unknown</Badge> : e.available ? <Badge tone="good">available</Badge> : <Badge tone="warn">unavailable</Badge>}
      {e?.local && <Badge>local</Badge>}
      {e?.streaming && <Badge tone="info">streaming</Badge>}
    </>
  );
}

// ------------------------------------------------------------------ wake + live meter

/** Turn the node's mic monitor on while this component is mounted and the tab is visible. */
function useMonitor(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const set = (on: boolean, keepalive = false) =>
      void api("/api/voice/monitor", { method: "POST", body: { on }, keepalive }).catch(() => {});
    const onVis = () => set(document.visibilityState === "visible");
    const onHide = () => set(false, true);
    set(true);
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("pagehide", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("pagehide", onHide);
      set(false, true);
    };
  }, [enabled]);
}

type WakeTest = { phase: "idle" } | { phase: "armed"; until: number } | { phase: "hit"; score: number } | { phase: "miss" };

function WakeCard({
  threshold,
  onThreshold,
  node,
  errors,
}: {
  threshold: number;
  onThreshold: (v: number) => void;
  node: boolean;
  errors: string[];
}) {
  useMonitor(node);
  const [level, setLevel] = useState(0);
  const [score, setScore] = useState(0);
  const [peak, setPeak] = useState(0);
  const [test, setTest] = useState<WakeTest>({ phase: "idle" });
  const testRef = useRef(test);
  testRef.current = test;
  const lastLevel = useRef(0);

  useEvents((ev) => {
    if (ev.type === "listen.level") {
      lastLevel.current = Date.now();
      setLevel(ev.level);
    } else if (ev.type === "wake.score") {
      setScore(ev.score);
      setPeak((p) => Math.max(p * 0.98, ev.score));
    } else if (ev.type === "wake.detected" && testRef.current.phase === "armed") {
      setTest({ phase: "hit", score: ev.score });
    }
  });

  // Let the meter fall to zero if the level stream stops.
  useEffect(() => {
    const t = setInterval(() => {
      if (Date.now() - lastLevel.current > 600) setLevel(0);
    }, 300);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (test.phase !== "armed") return;
    const t = setTimeout(() => setTest({ phase: "miss" }), Math.max(0, test.until - Date.now()));
    return () => clearTimeout(t);
  }, [test]);

  return (
    <Card title="Wake word" actions={!node && <Badge tone="warn">Live meter needs the audio node</Badge>}>
      <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
        <div className="flex flex-col gap-4">
          <Meter label="Mic level" value={level} color="#0EA5E9" disabled={!node} />
          <Meter label="Wake score" value={score} peak={peak} threshold={threshold} color={score >= threshold ? "#14B8A6" : "#8B5CF6"} disabled={!node} />
          <Field label="Detection threshold" hint="Lower = more sensitive (more false wakes). Save to apply." errors={errors}>
            {(id) => <Slider id={id} min={0.05} max={0.99} step={0.01} value={threshold} onChange={onThreshold} format={(v) => v.toFixed(2)} />}
          </Field>
        </div>
        <div className="flex flex-col justify-center gap-3 rounded-lg border border-white/[0.06] bg-black/20 p-4">
          <p className="text-[12.5px] text-white/55">Say the wake word within 10 seconds to check it's detected at the saved threshold.</p>
          <Button
            variant="primary"
            disabled={!node}
            busy={test.phase === "armed"}
            icon={<Radio className="size-3.5" />}
            onClick={() => setTest({ phase: "armed", until: Date.now() + 10_000 })}
          >
            {test.phase === "armed" ? "Listening… say “Jarvis”" : "Test wake"}
          </Button>
          <div aria-live="polite" className="min-h-6 text-[13px]">
            {test.phase === "hit" && (
              <span className="flex items-center gap-1.5 text-teal-300">
                <CheckCircle2 className="size-4" aria-hidden /> Detected · score {test.score.toFixed(2)}
              </span>
            )}
            {test.phase === "miss" && (
              <span className="flex items-center gap-1.5 text-amber-300">
                <XCircle className="size-4" aria-hidden /> Not detected
              </span>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}

function Meter({
  label,
  value,
  peak,
  threshold,
  color,
  disabled,
}: {
  label: string;
  value: number;
  peak?: number;
  threshold?: number;
  color: string;
  disabled?: boolean;
}) {
  return (
    <div className={disabled ? "opacity-40" : undefined}>
      <div className="mb-1 flex justify-between text-[12px] text-white/60">
        <span>{label}</span>
        <span className="tabular-nums">{value.toFixed(2)}</span>
      </div>
      <div
        className="relative h-3 overflow-hidden rounded-full bg-white/[0.06]"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={Number(value.toFixed(2))}
      >
        <div className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-75" style={{ width: `${pct01(value)}%`, background: color, boxShadow: `0 0 12px ${color}` }} />
        {peak != null && peak > 0 && <div className="absolute inset-y-0 w-px bg-white/40" style={{ left: `${pct01(peak)}%` }} aria-hidden />}
        {threshold != null && (
          <div className="absolute -inset-y-0 w-0.5 bg-amber" style={{ left: `calc(${pct01(threshold)}% - 1px)` }} title={`Threshold ${threshold.toFixed(2)}`} aria-hidden />
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ settings

type SetFn = <F extends keyof VoiceConfig>(field: F, value: VoiceConfig[F]) => void;

function Text({ label, field, d, set, hint, placeholder }: { label: string; field: keyof VoiceConfig; d: VoiceConfig; set: SetFn; hint?: string; placeholder?: string }) {
  return (
    <Field label={label} hint={hint}>
      {(id) => <Input id={id} value={String(d[field] ?? "")} placeholder={placeholder} onChange={(e) => set(field, e.target.value as never)} />}
    </Field>
  );
}

function EngineSettings({ d, set }: { d: VoiceConfig; set: SetFn }) {
  return (
    <Card title="Engine settings">
      <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
        <Text label="Language" field="language" d={d} set={set} hint="e.g. en" />
        <Text label="Deepgram model" field="deepgram_model" d={d} set={set} />
        <Text label="OpenAI STT model" field="openai_stt_model" d={d} set={set} />
        <Text label="Local whisper model" field="whisper_local_model" d={d} set={set} />
        <Text label="Remote whisper URL" field="whisper_remote_url" d={d} set={set} placeholder="http://gpu-box:8000" hint="faster-whisper server; empty = off" />
        <Text label="Remote whisper model" field="whisper_remote_model" d={d} set={set} />
        <Text label="OpenAI TTS model" field="openai_tts_model" d={d} set={set} />
        <Text label="OpenAI voice" field="openai_voice" d={d} set={set} />
        <Text label="ElevenLabs voice id" field="elevenlabs_voice_id" d={d} set={set} />
        <Text label="ElevenLabs model" field="elevenlabs_model" d={d} set={set} />
        <Text label="Piper voice" field="piper_voice" d={d} set={set} hint="Offline fallback voice" />
        <Field label="OpenAI TTS instructions" className="sm:col-span-2 xl:col-span-4">
          {(id) => <Textarea id={id} rows={3} value={d.openai_tts_instructions} onChange={(e) => set("openai_tts_instructions", e.target.value)} />}
        </Field>
      </div>
    </Card>
  );
}

function ListeningCard({ d, set, fieldIssues }: { d: VoiceConfig; set: SetFn; fieldIssues: (f: keyof VoiceConfig & string) => string[] }) {
  return (
    <Card title="Listening">
      <div className="grid gap-x-6 gap-y-4 md:grid-cols-2 xl:grid-cols-4">
        <Field label="Follow-up window" hint="Listen again without the wake word (0 = off)." errors={fieldIssues("follow_up_s")}>
          {(id) => <Slider id={id} min={0} max={30} step={0.5} value={d.follow_up_s} onChange={(v) => set("follow_up_s", v)} format={(v) => `${v} s`} />}
        </Field>
        <Field label="End-of-speech silence" hint="Silence that ends an utterance." errors={fieldIssues("vad_end_silence_ms")}>
          {(id) => <Slider id={id} min={200} max={3000} step={50} value={d.vad_end_silence_ms} onChange={(v) => set("vad_end_silence_ms", v)} format={(v) => `${v} ms`} />}
        </Field>
        <Field label="Max utterance (s)" hint="3–120" errors={fieldIssues("max_utterance_s")}>
          {(id) => <NumberInput id={id} min={3} max={120} step={1} value={d.max_utterance_s} onValue={(v) => set("max_utterance_s", v ?? 30)} />}
        </Field>
        <div className="pt-5">
          <Toggle checked={d.chime} onChange={(v) => set("chime", v)} label="Wake chime" hint="Short tone when the wake word is heard." />
        </div>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ preview

function PreviewCard({ engines, node }: { engines: Engine[]; node: boolean }) {
  const toast = useToast();
  const [text, setText] = useState("Good evening. All systems are running normally.");
  const [engine, setEngine] = useState("");
  const [audio, setAudio] = useState<{ url: string; engine: string } | null>(null);
  const audioEl = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    if (!audio) return;
    return () => URL.revokeObjectURL(audio.url);
  }, [audio]);

  const preview = useMutation({
    mutationFn: async () => {
      const res = await request("/api/voice/preview", { method: "POST", body: { text, engine: engine || null } });
      return { blob: await res.blob(), engine: res.headers.get("X-TTS-Engine") ?? "?" };
    },
    onSuccess: ({ blob, engine: used }) => {
      setAudio({ url: URL.createObjectURL(blob), engine: used });
      setTimeout(() => void audioEl.current?.play().catch(() => {}), 0);
    },
    onError: (e) => toast.show(e.message, "error"),
  });

  const say = useMutation({
    mutationFn: () => post("/api/voice/say", { text }),
    onSuccess: () => toast.show("Speaking on Jarvis"),
    onError: (e) => toast.show(e instanceof ApiError && e.status === 409 ? "Audio node not connected." : e.message, "error"),
  });

  const valid = text.trim().length > 0 && text.length <= 500;
  return (
    <Card title="Voice preview">
      <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
        <Field label="Text" hint={`${text.length}/500`}>
          {(id) => <Textarea id={id} rows={3} maxLength={500} value={text} onChange={(e) => setText(e.target.value)} />}
        </Field>
        <div className="flex flex-col gap-3">
          <Field label="Engine">
            {(id) => (
              <Select id={id} value={engine} onChange={(e) => setEngine(e.target.value)}>
                <option value="">Saved chain (first available)</option>
                {engines.map((e) => (
                  <option key={e.id} value={e.id} disabled={!e.available}>
                    {e.id}
                    {e.available ? "" : " (unavailable)"}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <div className="flex gap-2">
            <Button variant="primary" busy={preview.isPending} disabled={!valid} onClick={() => preview.mutate()} icon={<Play className="size-3.5" />}>
              Preview here
            </Button>
            <Button busy={say.isPending} disabled={!valid || !node} title={node ? undefined : "Audio node not connected"} onClick={() => say.mutate()} icon={<Megaphone className="size-3.5" />}>
              Say on Jarvis
            </Button>
          </div>
        </div>
      </div>
      {audio && (
        <div className="mt-3 flex items-center gap-3">
          <audio ref={audioEl} src={audio.url} controls className="h-9 flex-1" />
          <Badge tone="info">engine: {audio.engine}</Badge>
        </div>
      )}
    </Card>
  );
}

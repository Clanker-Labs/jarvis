# Architecture

```
┌──────────── Raspberry Pi ─────────────┐        ┌──────────── Brain (any tailnet host) ─────────────┐
│ Chromium kiosk ← ui-server (:8080)     │  WS    │ FastAPI :8000                                      │
│   /ws/ui ─────────────────────────────────────► │  Orchestrator (state machine, turn lifecycle)      │
│ audio-node (Python)                    │  WS    │  ├─ STT adapters ── Deepgram / OpenAI / Whisper     │
│  mic 16 kHz → wake word → VAD ──────────────────►│  ├─ Agent loop ──── provider router                 │
│  playback ◄── TTS PCM ◄─────────────────────────│  ├─ Tool registry ─ built-ins · MCP · skills        │
│  barge-in: stop locally, then tell brain│        │  ├─ Knowledge ──── vector + graph RAG, memory       │
└────────────────────────────────────────┘        │  └─ TTS adapters ── OpenAI / ElevenLabs / Piper     │
                                                   │ Admin API + admin UI :9000 (password + TOTP)       │
                                                   └────────────────────────────────────────────────────┘
```

## Services

All services are docker compose services (`compose.yaml`); the kiosk browser is the only host process.

| Compose service | Profile | Runs on | Port | Notes |
|---|---|---|---|---|
| `ui` | `pi` | Pi | 127.0.0.1:8080 | static kiosk build + `/config.json` |
| `audio-node` | `pi` | Pi | — (client) | connects to `BRAIN_URL/ws/node`; PipeWire via Pulse socket |
| `brain` | `brain` | Pi or tailnet host | 8000 | `/health`, `/ws/ui`, `/ws/node` |
| (admin) | `brain` | same process as brain | 9000 | separate FastAPI app + separate frontend |
| `qdrant` | `qdrant` | brain host | 127.0.0.1:6333 | optional vector DB |
| `tailnet` | `tailnet` | every host | `TAILNET_PORTS` on the tailnet IP | forwards tailnet → 127.0.0.1 via the host's `tailscale serve` |

Every service binds to 127.0.0.1; the `tailnet` sidecar makes `TAILNET_PORTS` reachable at the
machine's Tailscale IP / MagicDNS name (tailnet only), e.g. `http://jarvis-iot:9000` for admin.

Moving the brain off the Pi: on the new host set `COMPOSE_PROFILES=brain,tailnet`,
`TAILNET_PORTS=8000 9000`, add the Pi's kiosk origin to `UI_ORIGINS`, and `make up`; on the Pi set
`COMPOSE_PROFILES=pi,tailnet`, `TAILNET_PORTS=8080`, `BRAIN_URL=ws://<host-ts-name>:8000`,
`BRAIN_WS_URL=ws://<host-ts-name>:8000/ws/ui`, then `make up`.

## Event flow of one turn

1. Node detects the wake word → `wake.detected` (+ local chime) → brain: `IDLE→WAKE→LISTENING`,
   sends `node.command listen.open`.
2. Node streams mic PCM (binary, stream id = capture id) + `listen.level` at ~30 Hz; brain forwards
   audio to the STT adapter, publishes `listen.partial`.
3. Silero VAD hears ~700 ms of silence → node sends `listen.end`. Brain finalises STT → `listen.final`
   → `THINKING`.
4. Agent streams `agent.token`; a sentence splitter feeds complete sentences to TTS as soon as they
   exist. First audio → `tts.start` + `SPEAKING`; each sentence → `tts.sentence` (subtitle); PCM
   goes to the node, which reports `tts.audio_level` from what it actually plays.
5. Tool calls → `agent.tool_call` (`TOOL` state + chip) → `agent.tool_result`. Irreversible tools
   pause for a spoken yes/no (`agent.confirm_request`).
6. Node finishes playback → `playback.done` → brain: `FOLLOW_UP` and `listen.open(timeout=8 s)`.
   Speech within the window starts a new turn without the wake word; silence → `IDLE`.

Barge-in: wake word or a tap while `SPEAKING` — the node stops playback *immediately* (locally),
sends `barge_in`; the brain cancels the agent/TTS tasks, and goes to `LISTENING`.

## Protocol

See `packages/protocol`. JSON text frames are events; binary frames are audio
(`uint32 stream_id` + PCM s16le mono). TypeScript types are generated — `make protocol`.

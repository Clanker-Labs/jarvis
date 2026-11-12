# JARVIS

A personal voice assistant for a Raspberry Pi touchscreen: a glowing "alive" energy core that
listens for "Hey Jarvis", thinks with tools, and answers out loud.

```
Pi (UI + audio + wake word)  ──WebSocket/JSON──►  Brain (agent, tools, knowledge, voice adapters)
                                                   Admin panel :9000 (separate app, auth)
```

| Path | What |
|---|---|
| `apps/ui` | Kiosk frontend — WebGL energy core + HUD (Vite, React, Three.js, Tailwind) |
| `apps/admin` | Admin dashboard (separate app, separate port) |
| `services/brain` | FastAPI orchestrator: agent loop, providers, tools/MCP/skills, RAG, STT/TTS |
| `services/audio-node` | Pi audio: mic capture, openWakeWord, Silero VAD, playback, barge-in |
| `packages/protocol` | The typed event protocol (pydantic → generated TypeScript) |
| `skills/` | Your skills (`SKILL.md` folders), hot-reloaded |
| `compose.yaml`, `deploy/` | docker compose stack, Dockerfiles, kiosk launcher |
| `docs/` | [Architecture](docs/ARCHITECTURE.md) · [Decisions](docs/DECISIONS.md) · [Pi setup](docs/SETUP_PI.md) · [Adding a connector](docs/ADDING_A_CONNECTOR.md) |

## Quick start

Everything is deployed with **docker compose**; only the kiosk browser runs on the host.

```bash
make env          # .env with generated secrets (prints the initial admin password)
make install-pi   # on the Pi: Docker, stack up, start-on-boot, kiosk autostart (docs/SETUP_PI.md)
make up           # (re)build + start — `make ps`, `make logs`, `make down`
```

Then, from any device on your tailnet: admin at `http://<pi-name>:9000`, the live UI at
`http://<pi-name>:8080` (the machine's Tailscale IP works too). Nothing is exposed on the LAN.

Development:

```bash
make sync         # Python (uv) + JS (pnpm) deps
make dev-brain    # brain on 127.0.0.1:8000
make dev-ui       # kiosk UI on 127.0.0.1:5173 — add ?demo=1 to cycle states without a brain
make test lint    # what CI runs
```

UI query flags: `?demo=1` (state cycle), `?fps=1` (frame counter), `?quality=high|medium|low`,
`?night=0|1` (force night mode), `?cursor=1` (show the mouse pointer).

## Status

All eight phases are built; see `docs/DECISIONS.md` for every default chosen and why.

| Phase | What works |
|---|---|
| 1 Skeleton | Typed protocol (pydantic → TS), brain `/health` + authenticated sockets, WebGL energy core with all states, kiosk launcher |
| 2 Voice | openWakeWord "hey jarvis" → Silero VAD → STT chain → TTS chain, barge-in, follow-up window, `make demo` |
| 3 Conversation | Anthropic / OpenAI-compatible / local providers, router with health + fallback, streaming sentence-by-sentence TTS, persona |
| 4 Admin | Password + TOTP, overview + traces + latency, providers, voice (live mic meter), appearance, secrets, YAML export/import |
| 5 Tools | Built-ins, MCP client (stdio + HTTP, hot reload), skills (hot reload), spoken confirmation for irreversible actions |
| 6 Connectors | Gmail + Google Calendar over OAuth (PKCE), Now card, 30-minute connector guide |
| 7 Knowledge | Ingestion, hybrid BM25 + vector RAG with citations, graph RAG (entities, relations, communities), semantic memory |
| 8 Agent CLIs | Claude Code / Codex / Cursor headless as background jobs with progress and spoken summaries |

Measured on this Pi 4 with no cloud keys (local Whisper → `chat:latest` on spark → Piper): "Hey Jarvis,
what time is it?" answered correctly; ~6–7 s from end of speech to first audio, half of it Whisper on
the Pi CPU. The < 1.5 s target needs Deepgram (streaming STT) + an API model + cloud TTS, or a GPU
Whisper on the tailnet.

### Needs you
- **API keys** (admin → Secrets): `ANTHROPIC_API_KEY` (tools route + fast tier), `DEEPGRAM_API_KEY`
  and/or `OPENAI_API_KEY` (fast STT/TTS), optionally `ELEVENLABS_API_KEY`.
- **Google OAuth client** for Gmail/Calendar — see `docs/ADDING_A_CONNECTOR.md` § OAuth redirect URI.
- **Docker** on the Pi (`make install-pi` installs it with sudo) — images build in CI but couldn't be
  built on the dev Pi without root.
- Confirm `LOCAL_LLM_BASE_URL` = spark (`http://100.115.124.66:8000/v1`, model `chat:latest`).
- Hardware: the dev board is a Pi 4 + HDMI, not a Pi 5 + Touch Display 2, and no speakerphone was
  attached — wake word/VAD were verified on recorded audio, not a live mic.

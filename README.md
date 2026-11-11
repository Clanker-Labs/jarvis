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
make install-pi   # on the Pi: Docker, stack up, kiosk autostart (see docs/SETUP_PI.md)
make up           # (re)build + start — `make ps`, `make logs`, `make down`
```

Development:

```bash
make sync         # Python (uv) + JS (pnpm) deps
make dev-brain    # brain on 127.0.0.1:8000
make dev-ui       # kiosk UI on 127.0.0.1:5173 — add ?demo=1 to cycle states without a brain
make test lint    # what CI runs
```

UI query flags: `?demo=1` (state cycle), `?fps=1` (frame counter), `?quality=high|medium|low`,
`?night=0|1` (force night mode), `?cursor=1` (show the mouse pointer).

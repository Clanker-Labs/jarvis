# Admin API

Served by the brain process on `ADMIN_HOST:ADMIN_PORT` (default `127.0.0.1:9000`), separate from the
kiosk app. The admin UI (`apps/admin`) is served from the same origin. All JSON.

## Auth & security rules

- Session cookie `jarvis_admin` (HttpOnly, SameSite=Strict). Send requests with
  `credentials: "same-origin"`.
- **Every non-GET request must send header `X-Jarvis-Admin: 1`** (CSRF guard), including login.
- `401` = not signed in (`detail: "totp_required"` on login means: ask for the 6-digit code).
- `403 {detail: "totp_setup_required"}` = signed in with password only; only the TOTP setup routes
  work until TOTP is enabled.
- `429` = too many failed logins.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/auth/state` | — | `{authenticated, totp_enabled, needs_totp_setup}` |
| POST | `/api/auth/login` | `{password, code?}` | `{ok, needs_totp_setup}` + cookie |
| POST | `/api/auth/logout` | — | `{ok}` |
| POST | `/api/auth/totp/setup` | — | `{secret, otpauth_uri}` (render the URI as a QR code) |
| POST | `/api/auth/totp/enable` | `{code}` | `{ok}` |
| POST | `/api/auth/password` | `{current, new}` (new ≥ 10 chars) | `{ok}`; all sessions end |

## Overview & traces

| Method | Path | Returns |
|---|---|---|
| GET | `/api/overview` | `{state, status:{llm,internet,mic,offline_voice,muted,brain}, services:{brain,audio_node,ui_clients}, providers:[{id,healthy:bool\|null,checked,model}], voice:{stt:[id],tts:[id]}, stats:{turns, stages:{<stage>:{p50,p90,mean,n}}, last_24h:{turns,cost_usd}}, uptime_s}` |
| GET | `/api/turns?limit=50&offset=0` | `[{id, started, ended, user_text, reply_text, provider, model, input_tokens, output_tokens, cost_usd, latency:{<stage>: seconds}, error}]` newest first |
| GET | `/api/turns/{id}` | row + `latency:{timings:{mark: unix_s}, stages:{…}}`, `events:{events:[protocol events], tool_calls:[{tool,args,ok,summary,duration_s}]}` |
| GET | `/api/llm-calls?limit=100` | `[{ts, provider, model, route, ok, first_token_s, total_s, input_tokens, output_tokens, cost_usd, error}]` |

Stages: `wake_to_listen`, `stt`, `llm_first_token`, `tts_first_audio`, `end_of_speech_to_audio`.

## Config

Sections: `voice`, `appearance`, `location`, `agent`, `providers` (more are added by later phases;
`GET /api/config` lists them all).

| Method | Path | Notes |
|---|---|---|
| GET | `/api/config` | `{section: {...}}` |
| GET | `/api/config/{section}` | one section |
| PUT | `/api/config/{section}` | partial patch; returns the full validated section; `422` with pydantic errors |
| GET | `/api/config/export` | YAML download (never contains secret values) |
| POST | `/api/config/import` | raw YAML body; `{imported:[sections]}` |

Notable fields:
- `voice`: `stt_chain[]`, `tts_chain[]` (ordered engine ids), `language`, `deepgram_model`, `openai_stt_model`, `whisper_remote_url`, `whisper_remote_model`, `whisper_local_model`, `openai_tts_model`, `openai_voice`, `openai_tts_instructions`, `elevenlabs_voice_id`, `elevenlabs_model`, `piper_voice`, `follow_up_s` (0–30), `vad_end_silence_ms`, `max_utterance_s`, `chime`, `wake_threshold` (0.05–0.99).
- `appearance`: `colors{IDLE,WAKE,LISTENING,THINKING,TOOL,SPEAKING,FOLLOW_UP,ERROR: "#RRGGBB"}`, `quality` (auto/high/medium/low), `night_start`, `night_end` ("HH:MM"), `night_dim` (0.05–1), `screen_sleep_min`, `show_cards`.
- `location`: `name`, `latitude`, `longitude`, `timezone` ("auto" or IANA), `units` (metric/imperial).
- `agent`: `max_steps`, `history_turns`, `user_name`, `session_timeout_min`.
- `providers`: `providers[{id, kind: anthropic|openai|local|cli, model, base_url, api_key_secret, enabled, tool_mode: native|json|auto, effort: low|medium|high|xhigh|max|null, input_price, output_price, cli}]`, `routes{chat:[ids], tools:[ids], task:[ids]}` (order = fallback order), `tool_keywords[]`, `task_keywords[]`, `health_interval_s`.

## Providers

| Method | Path | Returns |
|---|---|---|
| POST | `/api/providers/health` | `{id: bool}` (re-check now) |
| POST | `/api/providers/{id}/test` | `{ok, text, first_token_s, total_s}` or `{ok:false, error}` |

## Voice

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/voice` | — | `{stt:[{id,available,local,streaming}], tts:[{id,available,local}], config, node_connected, monitor}` |
| POST | `/api/voice/preview` | `{text, engine?}` | `audio/wav` (header `X-TTS-Engine`) |
| POST | `/api/voice/say` | `{text}` | speaks on the Jarvis device |
| POST | `/api/voice/monitor` | `{on}` | `{monitor}`; while on, the event socket carries `listen.level` (~12 Hz) and `wake.score` (~2 Hz); `wake.detected` fires as usual. Turn it off when leaving the page. |

## Secrets (write-only)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/secrets` | — | `[{name, description, source: "db"\|"env"\|null, updated}]` — never values |
| PUT | `/api/secrets/{NAME}` | `{value}` | `{ok}` (UPPER_SNAKE_CASE names) |
| DELETE | `/api/secrets/{NAME}` | — | `{ok}` |

## Live events

`WS /api/events` (same origin, cookie auth). Streams protocol events as JSON text frames — see
`packages/protocol/ts` (`import type { JarvisEvent } from "@jarvis/protocol"`). First frames are
`ui.snapshot` and `status.update`. Useful types: `state.change`, `status.update`, `listen.level`,
`wake.score`, `wake.detected`, `listen.final`, `agent.tool_call`, `agent.done`, `tts.audio_level`,
`error`.

## Tools & connectors (phase 5–6)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/tools` | — | `[{name, display, description, source: builtin\|mcp\|skill\|connector, label, icon, enabled, side_effect, declared_side_effect, override}]` |
| PUT | `/api/tools/{name}` | `{enabled?, side_effect?}` | effective `{name, enabled, side_effect}` — overrides can only make a tool **stricter**; show the effective level |
| GET | `/api/connectors` | — | `{connectors:[{id, name, description, auth: none\|api_key\|oauth2, enabled, error, required_secrets, missing_secrets, connected: bool\|null, active, tools:[{name, side_effect, description}]}], redirect_uri}` |
| POST | `/api/connectors/{id}/connect` | — | `{auth_url}` → open it (same tab or popup). Google redirects back to `/connectors?connected=<id>` or `?error=<msg>` |
| POST | `/api/connectors/oauth/complete` | `{url}` | fallback: paste the URL the browser landed on after consent |
| POST | `/api/connectors/{id}/disconnect` | — | `{ok}` |
| POST | `/api/connectors/now/refresh` | — | the Now card `{next_event_title, next_event_start, next_event_location, unread_important}` |

Config sections: `tools` (`web_search_backend`, `searxng_url`, `url_allow_private`, `files_dir`,
`overrides`), `connectors` (`enabled{id: bool}`, `redirect_uri`, `now_refresh_s`).

## MCP servers

Servers are edited through `PUT /api/config/mcp` with the **whole** list:
`{servers:[{name (a-z0-9_-), transport: stdio|http, command, args[], env{}, url, headers{}, enabled, allowed_tools: [names]|null (null = all), side_effects{tool: none|reversible|irreversible}, timeout_s}]}`.
Env/header values may reference secrets as `${SECRET_NAME}` — encourage that over pasting tokens.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/mcp` | — | `[server config without env/header values + env_keys, header_keys, status: starting\|connected\|error\|stopped\|disabled, error, tools:[{name, display, wire, description, side_effect, enabled, annotations}]]` |
| POST | `/api/mcp/test` | a server config | `{ok, tools:[{name, description, side_effect}]}` or `{ok:false, error}` (connects once, doesn't save) |
| POST | `/api/mcp/reload` | — | restarts all servers; returns the list |

## Skills

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/skills` | — | `[{name, description, triggers, enabled, error, scripts, files, manifest}]` |
| POST | `/api/skills/reload` | — | list |
| GET | `/api/skills/{name}/file?path=SKILL.md` | — | `{path, content}` |
| PUT | `/api/skills/{name}/file?path=…` | `{content}` | list (creates the skill if new; name = lowercase/digits/dashes) |
| DELETE | `/api/skills/{name}` | — | list |
| POST | `/api/skills/upload` | raw zip bytes (`Content-Type: application/zip`) | `{installed, skills}` |

Enable/disable: `PUT /api/config/skills {disabled: [names]}`. A new skill's SKILL.md needs frontmatter
`---\nname: <folder name>\ndescription: …\ntriggers: [...]\n---`.

## Knowledge (phase 7)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/knowledge` | — | `{sources:[{id, kind: file\|url\|mail, uri, title, status: queued\|extracting\|embedding\|graph\|ready\|error, error, chunks, created, updated}], stats:{chunks, vectors, entities, relations, communities, queue}, embedder, vector_backend, watch_folder}` — poll every few seconds while anything isn't ready/error |
| POST | `/api/knowledge/upload?filename=<name>` | raw file bytes (≤ 50 MB; pdf md txt docx html csv json rst) | `{source_id}` |
| POST | `/api/knowledge/url` | `{url}` | `{source_id}` |
| POST | `/api/knowledge/mail` | `{thread_id}` | `{source_id}` (Gmail must be connected) |
| GET | `/api/knowledge/sources/{id}/chunks` | — | `[{id, idx, text, meta:{page?, pages?, section?}}]` |
| POST | `/api/knowledge/sources/{id}/reingest` | — | `{ok}` |
| DELETE | `/api/knowledge/sources/{id}` | — | `{ok}` |
| GET | `/api/knowledge/graph?limit=300` | — | `{nodes:[{id, name, type, community, mentions}], edges:[{src, dst, label}], communities:[{id, title, summary, size}]}` |
| POST | `/api/knowledge/communities/rebuild` | — | `{communities}` (can take a minute: LLM summaries) |
| POST | `/api/knowledge/reindex` | — | `{chunks}` |
| POST | `/api/knowledge/search` | `{query, k?, mode?: local\|global}` | `{hits:[{citation, score, text, source_id}], graph:{…graph_query result}}` |

Config section `knowledge`: `embed_provider` (auto|remote|local|openai), `embed_base_url`, `embed_model`,
`local_embed_model`, `openai_embed_model`, `vector_backend` (sqlite|qdrant), `qdrant_url`, `chunk_chars`,
`chunk_overlap`, `rerank` (off|local), `rerank_model`, `graph_enabled`, `graph_route` (chat|tools),
`watch_folder`.

## Memory & timers

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/memory` | — | `[{id, fact, source, created, updated}]` |
| POST | `/api/memory` | `{fact}` | `{id}` |
| PUT | `/api/memory/{id}` | `{fact}` | `{ok}` |
| DELETE | `/api/memory/{id}` | — | `{ok}` |
| GET | `/api/timers` | — | `[{id, kind: timer\|reminder, label, due, created}]` |
| DELETE | `/api/timers/{id}` | — | `{ok}` |

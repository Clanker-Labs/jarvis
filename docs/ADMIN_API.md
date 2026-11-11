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

"""Admin REST + WebSocket API (port 9000). Every route except /api/auth/* requires a session."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
import wave
from typing import TYPE_CHECKING, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import PlainTextResponse
from jarvis_protocol import dump_event
from pydantic import BaseModel, Field, ValidationError

from ..config import SECTIONS
from ..providers.base import Message, ProviderError, TextDelta
from ..voice.speaker import one
from .auth import COOKIE, SESSION_TTL, AdminAuth

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

# Secrets the built-in features know about (others can be added freely).
KNOWN_SECRETS = {
    "ANTHROPIC_API_KEY": "Anthropic (Claude) API key",
    "OPENAI_API_KEY": "OpenAI API key (LLM, STT, TTS)",
    "OPENROUTER_API_KEY": "OpenRouter API key",
    "DEEPGRAM_API_KEY": "Deepgram streaming speech-to-text",
    "ELEVENLABS_API_KEY": "ElevenLabs text-to-speech",
    "WHISPER_REMOTE_API_KEY": "Remote faster-whisper server (if it requires a key)",
    "LOCAL_LLM_API_KEY": "Local LLM server (if it requires a key)",
}


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    code: str | None = Field(None, max_length=10)


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class PasswordBody(BaseModel):
    current: str
    new: str = Field(min_length=10, max_length=200)


class SecretBody(BaseModel):
    value: str = Field(min_length=1, max_length=8000)


class TextBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    engine: str | None = None


class MonitorBody(BaseModel):
    on: bool


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def build_router(rt: Runtime, auth: AdminAuth) -> APIRouter:
    api = APIRouter(prefix="/api")
    guard = Depends(auth.require)

    # ------------------------------------------------------------------ auth

    @api.get("/auth/state")
    async def auth_state(request: Request) -> dict[str, Any]:
        signed_in = auth.session_valid(request.cookies.get(COOKIE))
        return {
            "authenticated": signed_in,
            "totp_enabled": auth.totp_enabled,
            "needs_totp_setup": signed_in and not auth.totp_enabled,
        }

    @api.post("/auth/login")
    async def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
        if request.headers.get("x-jarvis-admin") != "1":
            raise HTTPException(403, "Missing CSRF header.")
        token = await asyncio.to_thread(auth.login, body.password, body.code, _client_ip(request))
        response.set_cookie(
            COOKIE,
            token,
            max_age=SESSION_TTL,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        return {"ok": True, "needs_totp_setup": not auth.totp_enabled}

    @api.post("/auth/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        auth.logout(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @api.post("/auth/totp/setup", dependencies=[guard])
    async def totp_setup() -> dict[str, str]:
        return auth.totp_begin()

    @api.post("/auth/totp/enable", dependencies=[guard])
    async def totp_enable(body: CodeBody) -> dict[str, bool]:
        auth.totp_enable(body.code)
        return {"ok": True}

    @api.post("/auth/password", dependencies=[guard])
    async def change_password(body: PasswordBody, response: Response) -> dict[str, bool]:
        await asyncio.to_thread(auth.change_password, body.current, body.new)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    # ------------------------------------------------------------------ overview + traces

    @api.get("/overview", dependencies=[guard])
    async def overview() -> dict[str, Any]:
        r = rt.router
        return {
            "state": rt.orchestrator.sm.state,
            "status": rt.status.flags,
            "services": {
                "brain": True,
                "audio_node": rt.hub.node_connected,
                "ui_clients": rt.hub.ui_count,
            },
            "providers": [
                {
                    "id": pid,
                    "healthy": r.health.get(pid),
                    "checked": r.health_checked.get(pid),
                    "model": getattr(p, "model", ""),
                }
                for pid, p in r.providers.items()
            ],
            "voice": {
                "stt": [e.id for e in rt.stt.active()],
                "tts": [e.id for e in rt.tts.active()],
            },
            "stats": rt.traces.stats(),
            "uptime_s": round(time.time() - rt.started_at),
        }

    @api.get("/turns", dependencies=[guard])
    async def turns(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return rt.traces.list(min(max(limit, 1), 200), max(offset, 0))

    @api.get("/turns/{turn_id}", dependencies=[guard])
    async def turn(turn_id: str) -> dict[str, Any]:
        row = rt.traces.get(turn_id)
        if not row:
            raise HTTPException(404, "No such turn.")
        return row

    @api.get("/llm-calls", dependencies=[guard])
    async def llm_calls(limit: int = 100) -> list[dict[str, Any]]:
        return [s.__dict__ for s in list(rt.router.stats)[-limit:]][::-1]

    # ------------------------------------------------------------------ config

    @api.get("/config", dependencies=[guard])
    async def get_config() -> dict[str, Any]:
        return rt.config.all()

    @api.get("/config/export", dependencies=[guard])
    async def export_config() -> PlainTextResponse:
        return PlainTextResponse(
            rt.config.export_yaml(),
            media_type="application/x-yaml",
            headers={"Content-Disposition": 'attachment; filename="jarvis-config.yaml"'},
        )

    @api.post("/config/import", dependencies=[guard])
    async def import_config(request: Request) -> dict[str, Any]:
        text = (await request.body()).decode()
        try:
            return {"imported": rt.config.import_yaml(text)}
        except (ValueError, ValidationError) as e:
            raise HTTPException(400, f"Invalid config: {e}") from e

    @api.get("/config/{section}", dependencies=[guard])
    async def get_section(section: str) -> dict[str, Any]:
        if section not in SECTIONS:
            raise HTTPException(404, "Unknown section.")
        return rt.config.get(section).model_dump(mode="json")

    @api.put("/config/{section}", dependencies=[guard])
    async def put_section(section: str, patch: dict[str, Any]) -> dict[str, Any]:
        if section not in SECTIONS:
            raise HTTPException(404, "Unknown section.")
        try:
            return rt.config.update(section, patch).model_dump(mode="json")
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False)) from e

    # ------------------------------------------------------------------ providers

    @api.post("/providers/health", dependencies=[guard])
    async def recheck() -> dict[str, bool]:
        return await rt.router.check_health()

    @api.post("/providers/{provider_id}/test", dependencies=[guard])
    async def test_provider(provider_id: str) -> dict[str, Any]:
        p = rt.router.providers.get(provider_id)
        if p is None:
            raise HTTPException(404, "Provider not enabled or unknown.")
        msgs = [
            Message("system", "Reply with one short friendly sentence."),
            Message("user", "Say hello to the admin."),
        ]
        t0 = time.monotonic()
        first, text = None, []
        try:
            async with asyncio.timeout(45):
                async for ev in p.run(msgs, [], stream=True):
                    if isinstance(ev, TextDelta):
                        first = first or time.monotonic() - t0
                        text.append(ev.text)
        except (ProviderError, TimeoutError) as e:
            return {"ok": False, "error": str(e) or "timeout"}
        return {
            "ok": True,
            "text": "".join(text),
            "first_token_s": first and round(first, 3),
            "total_s": round(time.monotonic() - t0, 3),
        }

    # ------------------------------------------------------------------ voice

    @api.get("/voice", dependencies=[guard])
    async def voice() -> dict[str, Any]:
        return {
            "stt": [
                {"id": k, "available": e.available(), "local": e.local, "streaming": e.streaming}
                for k, e in rt.stt_engines.items()
            ],
            "tts": [
                {"id": k, "available": e.available(), "local": e.local}
                for k, e in rt.tts_engines.items()
            ],
            "config": rt.config.voice.model_dump(mode="json"),
            "node_connected": rt.hub.node_connected,
            "monitor": rt.monitor,
        }

    @api.post("/voice/preview", dependencies=[guard])
    async def preview(body: TextBody) -> Response:
        """Synthesise with the chain (or one engine) and return a WAV for the browser."""
        try:
            utt = await rt.tts.speak(body.text, prefer=body.engine)
            pcm = b"".join([c async for c in utt.audio])
        except Exception as e:
            raise HTTPException(502, f"TTS failed: {e}") from e
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(utt.sample_rate)
            w.writeframes(pcm)
        return Response(
            buf.getvalue(), media_type="audio/wav", headers={"X-TTS-Engine": utt.engine}
        )

    @api.post("/voice/say", dependencies=[guard])
    async def say(body: TextBody) -> dict[str, bool]:
        if not rt.hub.node_connected:
            raise HTTPException(409, "Audio node not connected.")
        rt.spawn(rt.speaker.speak(one(body.text), turn_id=None))
        return {"ok": True}

    @api.post("/voice/monitor", dependencies=[guard])
    async def monitor(body: MonitorBody) -> dict[str, bool]:
        await rt.set_monitor(body.on)
        return {"monitor": rt.monitor}

    # ------------------------------------------------------------------ secrets

    @api.get("/secrets", dependencies=[guard])
    async def list_secrets() -> list[dict[str, Any]]:
        import os

        stored = {s["name"]: s for s in rt.secrets.names()}
        names = sorted(set(KNOWN_SECRETS) | set(stored))
        return [
            {
                "name": n,
                "description": KNOWN_SECRETS.get(n, ""),
                "source": "db" if n in stored else ("env" if os.environ.get(n) else None),
                "updated": stored.get(n, {}).get("updated"),
            }
            for n in names
        ]

    @api.put("/secrets/{name}", dependencies=[guard])
    async def set_secret(name: str, body: SecretBody) -> dict[str, bool]:
        if not name.replace("_", "").isalnum() or not name.isupper() or len(name) > 64:
            raise HTTPException(400, "Secret names are UPPER_SNAKE_CASE.")
        rt.secrets.set(name, body.value.strip())
        rt.on_secrets_changed()
        return {"ok": True}

    @api.delete("/secrets/{name}", dependencies=[guard])
    async def delete_secret(name: str) -> dict[str, bool]:
        rt.secrets.delete(name)
        rt.on_secrets_changed()
        return {"ok": True}

    # ------------------------------------------------------------------ live events

    @api.websocket("/events")
    async def events(ws: WebSocket) -> None:
        if not auth.ws_ok(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        sub = rt.bus.subscribe(maxsize=1024)
        try:
            await ws.send_text(dump_event(rt.orchestrator.snapshot()))
            await ws.send_text(dump_event(rt.status.snapshot()))

            async def drain_incoming() -> None:
                with contextlib.suppress(WebSocketDisconnect):
                    while True:
                        await ws.receive_text()  # ignore; detect disconnects

            reader = asyncio.create_task(drain_incoming())
            try:
                async for ev in sub:
                    if reader.done():
                        break
                    await ws.send_text(dump_event(ev))
            finally:
                reader.cancel()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sub.close()

    return api

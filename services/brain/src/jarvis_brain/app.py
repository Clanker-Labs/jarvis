"""Brain HTTP/WebSocket app (port 8000): health, kiosk UI socket, audio-node socket."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.staticfiles import StaticFiles
from jarvis_protocol import (
    PROTOCOL_VERSION,
    Hello,
    UiCommand,
    dump_event,
    parse_event,
    unpack_audio,
)
from pydantic import ValidationError

from . import __version__
from .hub import UI_HIDDEN, NodeLink
from .runtime import Runtime

log = logging.getLogger(__name__)


def _token_ok(runtime: Runtime, presented: str | None) -> bool:
    expected = runtime.settings.node_token.get_secret_value()
    if not expected:
        return runtime.settings.env == "dev"  # unauthenticated only in dev
    return presented is not None and hmac.compare_digest(presented, expected)


def create_app(runtime: Runtime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="JARVIS brain",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.runtime = runtime

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "protocol": PROTOCOL_VERSION,
            "state": runtime.orchestrator.sm.state,
            "audio_node": runtime.hub.node_connected,
            "ui_clients": runtime.hub.ui_count,
            **runtime.health_extra(),
        }

    @app.websocket("/ws/ui")
    async def ws_ui(ws: WebSocket) -> None:
        origin = (ws.headers.get("origin") or "").rstrip("/")
        if origin not in runtime.settings.allowed_origins:
            log.warning("rejected /ws/ui from origin %r", origin)
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if not _token_ok(runtime, ws.query_params.get("token")):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await ws.accept()
        runtime.hub.ui_count += 1
        sub = runtime.bus.subscribe(maxsize=512, predicate=lambda e: e.type not in UI_HIDDEN)

        async def pump() -> None:
            await ws.send_text(dump_event(runtime.orchestrator.snapshot()))
            await ws.send_text(dump_event(runtime.status.snapshot()))
            async for event in sub:
                await ws.send_text(dump_event(event))

        sender = asyncio.create_task(pump())
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    event = parse_event(raw)
                except ValidationError:
                    log.warning("bad event from UI: %.200s", raw)
                    continue
                if isinstance(event, UiCommand):
                    await runtime.orchestrator.on_ui_command(event)
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender
            sub.close()
            runtime.hub.ui_count -= 1

    @app.websocket("/ws/node")
    async def ws_node(ws: WebSocket) -> None:
        auth = ws.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None
        if not _token_ok(runtime, token):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await ws.accept()
        try:
            hello = parse_event(await ws.receive_text())
        except (ValidationError, WebSocketDisconnect):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if not isinstance(hello, Hello) or hello.role != "audio-node":
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if hello.protocol != PROTOCOL_VERSION:
            log.error("audio node speaks protocol %s, brain %s", hello.protocol, PROTOCOL_VERSION)
            await ws.close(code=4001, reason="protocol mismatch")
            return
        link = NodeLink(ws)
        await runtime.hub.attach_node(link)
        log.info("audio node connected (v%s)", hello.client_version)
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if (data := msg.get("bytes")) is not None:
                    try:
                        sid, pcm = unpack_audio(data)
                    except ValueError:
                        continue
                    await runtime.orchestrator.on_node_audio(sid, pcm)
                elif (text := msg.get("text")) is not None:
                    try:
                        event = parse_event(text)
                    except ValidationError:
                        log.warning("bad event from node: %.200s", text)
                        continue
                    await runtime.orchestrator.on_node_event(event)
        except WebSocketDisconnect:
            pass
        finally:
            await runtime.hub.detach_node(link)
            log.info("audio node disconnected")

    ui_dist = runtime.settings.ui_dist
    if ui_dist and Path(ui_dist).is_dir():
        app.mount("/", StaticFiles(directory=ui_dist, html=True), name="ui")

    return app

"""Connections to the audio node and kiosk UIs.

The hub only moves bytes and events; decisions live in the orchestrator.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from fastapi import WebSocket
from jarvis_protocol import Event, dump_event, pack_audio

log = logging.getLogger(__name__)

# Events never forwarded to kiosk UIs (internal plumbing).
UI_HIDDEN = frozenset({"node.command", "hello", "playback.done", "listen.end"})


class NodeLink:
    """The (single) connected audio node."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self._send_lock = asyncio.Lock()

    async def send_event(self, event: Event) -> None:
        async with self._send_lock:
            await self.ws.send_text(dump_event(event))

    async def send_audio(self, stream_id: int, pcm: bytes) -> None:
        async with self._send_lock:
            await self.ws.send_bytes(pack_audio(stream_id, pcm))


class Hub:
    def __init__(self) -> None:
        self.node: NodeLink | None = None
        self.ui_count = 0
        self.on_node_change: list[Callable[[bool], Awaitable[None] | None]] = []

    @property
    def node_connected(self) -> bool:
        return self.node is not None

    async def attach_node(self, link: NodeLink) -> None:
        if self.node is not None:
            log.warning("a second audio node connected; replacing the previous one")
            with contextlib.suppress(Exception):
                await self.node.ws.close(code=4000, reason="replaced")
        self.node = link
        await self._notify(True)

    async def detach_node(self, link: NodeLink) -> None:
        if self.node is link:
            self.node = None
            await self._notify(False)

    async def _notify(self, connected: bool) -> None:
        for cb in self.on_node_change:
            res = cb(connected)
            if asyncio.iscoroutine(res):
                await res

    async def to_node(self, event: Event) -> bool:
        node = self.node
        if node is None:
            return False
        try:
            await node.send_event(event)
            return True
        except Exception:
            log.warning("send to audio node failed", exc_info=True)
            return False

    async def audio_to_node(self, stream_id: int, pcm: bytes) -> bool:
        node = self.node
        if node is None:
            return False
        try:
            await node.send_audio(stream_id, pcm)
            return True
        except Exception:
            log.warning("audio send to node failed", exc_info=True)
            return False

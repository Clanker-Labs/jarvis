"""Wires the long-lived services together. One instance per brain process."""

from __future__ import annotations

from jarvis_protocol import StatusUpdate

from .bus import EventBus
from .hub import Hub
from .orchestrator import Orchestrator
from .settings import Settings


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bus = EventBus()
        self.hub = Hub()
        self.orchestrator = Orchestrator(self.bus, self.hub)
        self.hub.on_node_change.append(self._on_node_change)

    def status(self) -> StatusUpdate:
        return StatusUpdate(brain=True, mic=self.hub.node_connected)

    def health_extra(self) -> dict[str, object]:
        return {}

    def _on_node_change(self, _connected: bool) -> None:
        self.bus.publish(self.status())

    async def start(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        await self.orchestrator.start()

    async def stop(self) -> None:
        await self.orchestrator.stop()

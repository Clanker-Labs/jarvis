"""Conversation orchestrator: owns the state machine and reacts to node/UI events.

Phase 1 skeleton — wires the state machine to the bus. The voice pipeline is added in phase 2.
"""

from __future__ import annotations

import logging

from jarvis_protocol import (
    Event,
    State,
    StateChange,
    StateMachine,
    UiCommand,
    UiConfig,
    UiSnapshot,
)

from .bus import EventBus
from .hub import Hub

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, bus: EventBus, hub: Hub) -> None:
        self.bus = bus
        self.hub = hub
        self.sm = StateMachine()
        self.sm.subscribe(self._on_transition)

    def _on_transition(self, previous: State, state: State, reason: str) -> None:
        log.info("state %s → %s (%s)", previous, state, reason)
        self.bus.publish(StateChange(state=state, previous=previous, reason=reason))

    def snapshot(self) -> UiSnapshot:
        return UiSnapshot(state=self.sm.state, config=UiConfig())

    async def on_node_event(self, event: Event) -> None:
        self.bus.publish(event)

    async def on_node_audio(self, stream_id: int, pcm: bytes) -> None:
        pass

    async def on_ui_command(self, cmd: UiCommand) -> None:
        self.bus.publish(cmd)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

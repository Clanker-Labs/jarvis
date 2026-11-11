"""The conversation state machine that drives the UI.

``IDLE → WAKE → LISTENING → THINKING → (TOOL) → SPEAKING → FOLLOW_UP → IDLE``; any state may go
to ``ERROR``, and ``ERROR`` recovers to ``IDLE``. Barge-in (``SPEAKING → LISTENING``) and
touch push-to-talk (``IDLE → LISTENING``) are explicit edges.

Pure and synchronous so it can be unit-tested exhaustively; the brain wraps it with timers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .events import State

S = State

TRANSITIONS: dict[State, frozenset[State]] = {
    S.IDLE: frozenset({S.WAKE, S.LISTENING}),
    S.WAKE: frozenset({S.LISTENING, S.IDLE}),
    S.LISTENING: frozenset({S.THINKING, S.IDLE}),
    S.THINKING: frozenset({S.TOOL, S.SPEAKING, S.LISTENING, S.FOLLOW_UP, S.IDLE}),
    S.TOOL: frozenset({S.THINKING, S.SPEAKING, S.LISTENING, S.IDLE}),
    S.SPEAKING: frozenset({S.THINKING, S.TOOL, S.FOLLOW_UP, S.LISTENING, S.IDLE}),
    S.FOLLOW_UP: frozenset({S.LISTENING, S.WAKE, S.IDLE}),
    S.ERROR: frozenset({S.IDLE}),
}
# Every state may fail.
for _s in State:
    if _s is not S.ERROR:
        TRANSITIONS[_s] = TRANSITIONS[_s] | {S.ERROR}


class InvalidTransition(ValueError):
    pass


Listener = Callable[[State, State, str], None]


@dataclass
class StateMachine:
    state: State = S.IDLE
    _listeners: list[Listener] = field(default_factory=list)

    def can(self, target: State) -> bool:
        return target == self.state or target in TRANSITIONS[self.state]

    def to(self, target: State, reason: str = "") -> bool:
        """Transition to ``target``. Returns False for a no-op (already there)."""
        if target == self.state:
            return False
        if target not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state} → {target} ({reason})")
        previous, self.state = self.state, target
        for listener in self._listeners:
            listener(previous, target, reason)
        return True

    def force(self, target: State, reason: str = "") -> None:
        """Transition even if the edge is not declared (used for hard resets)."""
        if target == self.state:
            return
        previous, self.state = self.state, target
        for listener in self._listeners:
            listener(previous, target, reason)

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    @property
    def busy(self) -> bool:
        return self.state in (S.THINKING, S.TOOL, S.SPEAKING)

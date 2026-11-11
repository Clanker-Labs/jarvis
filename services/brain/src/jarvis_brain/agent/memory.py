"""Short-term conversation memory: a rolling window of recent turns + a summary of older ones.

History is kept as plain text (no provider-native blocks): each new turn starts an append-only
exchange, and dropping *all* earlier thinking blocks is always valid for the Anthropic API.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..providers.base import Message

log = logging.getLogger(__name__)

Summarizer = Callable[[str, list[tuple[str, str]]], Awaitable[str]]


@dataclass
class ShortTermMemory:
    window: int = 12  # turns kept verbatim
    session_timeout_s: float = 30 * 60  # a fresh conversation after this much silence
    turns: list[tuple[str, str]] = field(default_factory=list)
    summary: str = ""
    last_active: float = 0.0
    _summarizing: asyncio.Task[None] | None = None

    def _expire(self) -> None:
        if self.last_active and time.time() - self.last_active > self.session_timeout_s:
            log.info("conversation idle for %.0f min; starting fresh", self.session_timeout_s / 60)
            self.turns.clear()
            self.summary = ""

    def messages(self) -> list[Message]:
        self._expire()
        out: list[Message] = []
        for user, assistant in self.turns:
            out.append(Message("user", user))
            out.append(Message("assistant", assistant or "…"))
        return out

    def add(self, user: str, assistant: str, summarizer: Summarizer | None = None) -> None:
        self._expire()
        self.turns.append((user, assistant))
        self.last_active = time.time()
        if len(self.turns) > self.window:
            overflow = self.turns[: len(self.turns) - self.window // 2]
            self.turns = self.turns[len(overflow) :]
            if summarizer and (self._summarizing is None or self._summarizing.done()):
                self._summarizing = asyncio.create_task(self._summarize(summarizer, overflow))
            else:  # no summarizer: keep a crude trace so context isn't silently lost
                self.summary = (self.summary + " " + " ".join(u for u, _ in overflow))[-1500:]

    async def _summarize(self, summarizer: Summarizer, old: list[tuple[str, str]]) -> None:
        try:
            self.summary = (await summarizer(self.summary, old)).strip()[:2000]
            log.info("conversation summary updated (%d chars)", len(self.summary))
        except Exception as e:
            log.warning("summarising older turns failed: %s", e)

    def clear(self) -> None:
        self.turns.clear()
        self.summary = ""

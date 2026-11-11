"""In-process async pub/sub for protocol events.

Each subscriber gets a bounded queue. High-rate, lossy events (audio levels) are dropped for a
slow subscriber instead of blocking the publisher; everything else evicts the oldest level event
first and only then the oldest event of any kind.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable

from jarvis_protocol import Event

log = logging.getLogger(__name__)

LOSSY = frozenset({"tts.audio_level", "listen.level"})


class Subscription:
    def __init__(self, bus: EventBus, maxsize: int, predicate: Callable[[Event], bool] | None):
        self._bus = bus
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize)
        self.predicate = predicate
        self.dropped = 0

    def offer(self, event: Event) -> None:
        if self.predicate and not self.predicate(event):
            return
        if self.queue.full():
            if event.type in LOSSY:
                self.dropped += 1
                return
            self._evict()
        self.queue.put_nowait(event)

    def _evict(self) -> None:
        items = []
        while not self.queue.empty():
            items.append(self.queue.get_nowait())
        idx = next((i for i, e in enumerate(items) if e.type in LOSSY), 0)
        items.pop(idx)
        self.dropped += 1
        for e in items:
            self.queue.put_nowait(e)

    async def __aiter__(self) -> AsyncIterator[Event]:
        while True:
            yield await self.queue.get()

    def close(self) -> None:
        self._bus._subs.discard(self)


class EventBus:
    def __init__(self) -> None:
        self._subs: set[Subscription] = set()
        self._taps: list[Callable[[Event], None]] = []

    def publish(self, event: Event) -> None:
        for tap in self._taps:
            try:
                tap(event)
            except Exception:
                log.exception("event tap failed")
        for sub in list(self._subs):
            sub.offer(event)

    def tap(self, fn: Callable[[Event], None]) -> None:
        """Synchronous observer called for every event (tracing, metrics)."""
        self._taps.append(fn)

    def subscribe(
        self, *, maxsize: int = 256, predicate: Callable[[Event], bool] | None = None
    ) -> Subscription:
        sub = Subscription(self, maxsize, predicate)
        self._subs.add(sub)
        return sub

    @contextlib.contextmanager
    def subscription(self, **kw):  # type: ignore[no-untyped-def]
        sub = self.subscribe(**kw)
        try:
            yield sub
        finally:
            sub.close()

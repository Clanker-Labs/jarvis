"""Timers and reminders, persisted in SQLite and announced out loud when due."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal

from jarvis_protocol import SideEffect
from pydantic import BaseModel, Field, model_validator

from ...db import Database
from ..registry import Tool, ToolContext

log = logging.getLogger(__name__)


class TimerArgs(BaseModel):
    minutes: float = Field(0, ge=0, le=24 * 60)
    seconds: float = Field(0, ge=0, le=24 * 3600)
    label: str = Field("timer", max_length=80, description="Short name, e.g. 'pasta'.")

    @model_validator(mode="after")
    def _positive(self) -> TimerArgs:
        if self.minutes * 60 + self.seconds < 1:
            raise ValueError("duration must be at least one second")
        return self


class ReminderArgs(BaseModel):
    text: str = Field(min_length=1, max_length=200, description="What to remind the user about.")
    at: str | None = Field(
        None, description="Local time 'HH:MM' or ISO datetime '2026-11-12T18:30'."
    )
    in_minutes: float | None = Field(None, gt=0, le=60 * 24 * 365)

    @model_validator(mode="after")
    def _one(self) -> ReminderArgs:
        if (self.at is None) == (self.in_minutes is None):
            raise ValueError("give exactly one of 'at' or 'in_minutes'")
        return self


class CancelArgs(BaseModel):
    timer_id: int


def parse_when(at: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now().astimezone()
    at = at.strip()
    try:
        hh, mm = at.split(":")[:2]
        if len(at) <= 5:
            t = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            return t if t > now else t + timedelta(days=1)
    except ValueError:
        pass
    dt = datetime.fromisoformat(at)
    return dt if dt.tzinfo else dt.replace(tzinfo=now.tzinfo)


def spoken_duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} second{'s' * (seconds != 1)}"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m} minute{'s' * (m != 1)}" + (f" {s} seconds" if s and m < 5 else "")
    h, m = divmod(m, 60)
    return f"{h} hour{'s' * (h != 1)}" + (f" {m} minutes" if m else "")


class Scheduler:
    def __init__(
        self, db: Database, announce: Callable[[str, dict[str, Any]], Awaitable[None]]
    ) -> None:
        self.db = db
        self.announce = announce
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    def add(self, kind: Literal["timer", "reminder"], label: str, due: float) -> int:
        cur = self.db.execute(
            "INSERT INTO timers(kind, label, due, created) VALUES(?,?,?,?)",
            (kind, label, due, time.time()),
        )
        self._wake.set()
        return int(cur.lastrowid or 0)

    def pending(self) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM timers WHERE fired IS NULL ORDER BY due")

    def cancel(self, timer_id: int) -> bool:
        return (
            self.db.execute("DELETE FROM timers WHERE id=? AND fired IS NULL", (timer_id,)).rowcount
            > 0
        )

    async def _loop(self) -> None:
        while True:
            now = time.time()
            for row in self.db.query(
                "SELECT * FROM timers WHERE fired IS NULL AND due <= ? ORDER BY due", (now,)
            ):
                self.db.execute("UPDATE timers SET fired=? WHERE id=?", (now, row["id"]))
                late = now - row["due"]
                if row["kind"] == "timer":
                    text = (
                        f"Your {row['label']} timer is done."
                        if row["label"] != "timer"
                        else "Your timer is done."
                    )
                else:
                    text = f"A reminder: {row['label']}."
                if late > 300:
                    text += " Sorry, that's a little late — I was offline."
                try:
                    await self.announce(text, row)
                except Exception:
                    log.exception("announcing %s failed", row["kind"])
            nxt = self.db.one("SELECT MIN(due) AS due FROM timers WHERE fired IS NULL")
            wait = min(30.0, max(0.2, (nxt["due"] - time.time()) if nxt and nxt["due"] else 30.0))
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), wait)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


def make_timer_tools(sched: Scheduler) -> list[Tool]:
    async def set_timer(a: TimerArgs, _c: ToolContext) -> dict[str, Any]:
        secs = a.minutes * 60 + a.seconds
        tid = sched.add("timer", a.label, time.time() + secs)
        return {"id": tid, "duration": spoken_duration(secs), "label": a.label}

    async def set_reminder(a: ReminderArgs, _c: ToolContext) -> dict[str, Any]:
        if a.at:
            try:
                due = parse_when(a.at)
            except ValueError:
                return {"error": f"couldn't understand the time {a.at!r}"}
        else:
            due = datetime.now().astimezone() + timedelta(minutes=a.in_minutes or 0)
        if due.timestamp() <= time.time():
            return {"error": "that time has already passed"}
        tid = sched.add("reminder", a.text, due.timestamp())
        return {"id": tid, "when": due.strftime("%A %-d %B at %H:%M"), "text": a.text}

    async def list_(_a: BaseModel, _c: ToolContext) -> list[dict[str, Any]]:
        now = time.time()
        return [
            {
                "id": r["id"],
                "kind": r["kind"],
                "label": r["label"],
                "remaining": spoken_duration(max(0, r["due"] - now)),
                "at": datetime.fromtimestamp(r["due"]).strftime("%a %H:%M"),
            }
            for r in sched.pending()
        ]

    async def cancel(a: CancelArgs, _c: ToolContext) -> dict[str, Any]:
        return {"cancelled": sched.cancel(a.timer_id)}

    class NoArgs(BaseModel):
        pass

    return [
        Tool(
            "timer_set",
            "Start a countdown timer.",
            set_timer,
            params=TimerArgs,
            side_effect=SideEffect.REVERSIBLE,
            label="Timer",
            icon="timer",
        ),
        Tool(
            "reminder_set",
            "Set a reminder for a time (local 'HH:MM' or ISO) or in N minutes.",
            set_reminder,
            params=ReminderArgs,
            side_effect=SideEffect.REVERSIBLE,
            label="Reminder",
            icon="bell",
        ),
        Tool(
            "timers_list",
            "List running timers and upcoming reminders.",
            list_,
            params=NoArgs,
            label="Timers",
            icon="timer",
        ),
        Tool(
            "timer_cancel",
            "Cancel a timer or reminder by id.",
            cancel,
            params=CancelArgs,
            side_effect=SideEffect.REVERSIBLE,
            label="Timer",
            icon="timer",
        ),
    ]

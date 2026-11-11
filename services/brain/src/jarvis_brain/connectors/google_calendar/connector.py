"""Google Calendar connector (primary calendar, REST API v3 over httpx)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from jarvis_protocol import SideEffect
from pydantic import BaseModel, Field, model_validator

from ...tools.registry import Tool, ToolContext
from ..base import ConnectorContext

API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class EventsArgs(BaseModel):
    start_date: str | None = Field(None, description="YYYY-MM-DD (local). Omit for today.")
    days: int = Field(1, ge=1, le=31, description="How many days from start_date.")
    query: str | None = Field(None, max_length=100, description="Optional text to match.")


class CreateArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: str = Field(description="Local ISO datetime '2026-11-13T10:00', or a date for all-day.")
    end: str | None = Field(None, description="Local ISO datetime; omit to use duration_minutes.")
    duration_minutes: int = Field(60, ge=5, le=24 * 60)
    location: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=2000)
    attendees: list[str] = Field(
        default_factory=list, max_length=20, description="Email addresses."
    )

    @model_validator(mode="after")
    def _parse(self) -> CreateArgs:
        _local(self.start)
        if self.end:
            _local(self.end)
        return self


def _local(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.astimezone()  # naive = local time


def _event(e: dict[str, Any]) -> dict[str, Any]:
    start, end = e.get("start", {}), e.get("end", {})
    all_day = "date" in start
    return {
        "id": e.get("id"),
        "title": e.get("summary", "(no title)"),
        "all_day": all_day,
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "location": e.get("location"),
        "attendees": len(e.get("attendees", []) or []),
        "status": e.get("status"),
    }


def spoken_when(start: datetime, end: datetime | None, all_day: bool = False) -> str:
    today = datetime.now().astimezone().date()
    d = start.date()
    day = (
        "today"
        if d == today
        else "tomorrow"
        if d == today + timedelta(days=1)
        else start.strftime("%A %-d %B")
    )
    if all_day:
        return f"{day}, all day"
    s = f"{day} at {start.strftime('%H:%M')}"
    if end:
        mins = int((end - start).total_seconds() // 60)
        s += (
            " for an hour"
            if mins == 60
            else f" for {mins} minutes"
            if mins < 120
            else f" until {end.strftime('%H:%M')}"
        )
    return s


class GoogleCalendar:
    def __init__(self, ctx: ConnectorContext) -> None:
        self.ctx = ctx

    async def _list(
        self, t0: datetime, t1: datetime, q: str | None = None, limit: int = 25
    ) -> list[dict]:
        params: dict[str, Any] = {
            "timeMin": t0.isoformat(),
            "timeMax": t1.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": limit,
        }
        if q:
            params["q"] = q
        async with self.ctx.http() as c:
            r = await c.get(API, params=params, headers=await self.ctx.bearer())
            r.raise_for_status()
            return [_event(e) for e in r.json().get("items", []) if e.get("status") != "cancelled"]

    async def events(self, a: EventsArgs, _c: ToolContext | None = None) -> dict[str, Any]:
        day = date.fromisoformat(a.start_date) if a.start_date else datetime.now().date()
        t0 = datetime.combine(day, datetime.min.time()).astimezone()
        t1 = t0 + timedelta(days=a.days)
        return {
            "from": t0.date().isoformat(),
            "days": a.days,
            "events": await self._list(t0, t1, a.query),
        }

    async def create(self, a: CreateArgs, _c: ToolContext | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"summary": a.title}
        if len(a.start) == 10:  # all-day
            body["start"] = {"date": a.start}
            body["end"] = {"date": (date.fromisoformat(a.start) + timedelta(days=1)).isoformat()}
        else:
            s = _local(a.start)
            e = _local(a.end) if a.end else s + timedelta(minutes=a.duration_minutes)
            body["start"], body["end"] = {"dateTime": s.isoformat()}, {"dateTime": e.isoformat()}
        if a.location:
            body["location"] = a.location
        if a.description:
            body["description"] = a.description
        if a.attendees:
            body["attendees"] = [{"email": x} for x in a.attendees]
        async with self.ctx.http() as c:
            r = await c.post(
                API,
                json=body,
                headers=await self.ctx.bearer(),
                params={"sendUpdates": "all" if a.attendees else "none"},
            )
            r.raise_for_status()
            return {"created": True, "event": _event(r.json())}

    async def now(self) -> dict[str, Any]:
        t0 = datetime.now().astimezone()
        upcoming = [
            e for e in await self._list(t0, t0 + timedelta(days=7), limit=5) if not e["all_day"]
        ]
        if not upcoming:
            return {"next_event_title": None, "next_event_start": None, "next_event_location": None}
        e = upcoming[0]
        return {
            "next_event_title": e["title"],
            "next_event_start": _local(e["start"]).timestamp(),
            "next_event_location": e.get("location"),
        }

    def tools(self) -> list[Tool]:
        def describe_create(args: dict[str, Any]) -> str:
            start = args.get("start", "")
            try:
                if len(start) == 10:
                    when = spoken_when(_local(start + "T00:00"), None, all_day=True)
                else:
                    s = _local(start)
                    e = (
                        _local(args["end"])
                        if args.get("end")
                        else s + timedelta(minutes=args.get("duration_minutes", 60))
                    )
                    when = spoken_when(s, e)
            except (ValueError, KeyError):
                when = start
            who = f", inviting {', '.join(args['attendees'])}" if args.get("attendees") else ""
            return f'add "{args.get("title")}" to your calendar {when}{who}'

        return [
            Tool(
                "calendar_events",
                "List the user's calendar events for a day or range of days.",
                self.events,
                params=EventsArgs,
                label="Calendar",
                icon="calendar",
            ),
            Tool(
                "calendar_create_event",
                "Add an event to the user's calendar.",
                self.create,
                params=CreateArgs,
                side_effect=SideEffect.IRREVERSIBLE,
                label="Calendar",
                icon="calendar",
                describe=describe_create,
            ),
        ]


def create(ctx: ConnectorContext) -> GoogleCalendar:
    return GoogleCalendar(ctx)

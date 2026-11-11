"""Built-in tools: current time and weather."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, Field

from ...status import describe_wmo
from ..registry import Tool, ToolContext

if TYPE_CHECKING:
    from ...config import ConfigStore


class TimeArgs(BaseModel):
    timezone: str | None = Field(
        None, description="IANA timezone like 'Asia/Tokyo'. Omit for the user's local time."
    )


class WeatherArgs(BaseModel):
    place: str | None = Field(None, description="City or place name. Omit for the user's home.")
    days: int = Field(
        1,
        ge=1,
        le=7,
        description="Days of forecast starting today: 1 = today only, 2 = today and tomorrow.",
    )


def make_time_weather_tools(config: ConfigStore) -> list[Tool]:
    def local_tz() -> ZoneInfo | None:
        tz = config.location.timezone
        if tz and tz != "auto":
            try:
                return ZoneInfo(tz)
            except ZoneInfoNotFoundError:
                return None
        return None

    async def get_time(args: TimeArgs, _ctx: ToolContext) -> dict[str, Any]:
        tz = local_tz()
        if args.timezone:
            try:
                tz = ZoneInfo(args.timezone)
            except ZoneInfoNotFoundError:
                return {"error": f"unknown timezone {args.timezone}"}
        now = datetime.now(tz).astimezone(tz) if tz else datetime.now().astimezone()
        return {
            "iso": now.isoformat(timespec="minutes"),
            "spoken": now.strftime("%A %-d %B, %H:%M"),
            "timezone": str(now.tzinfo),
        }

    async def weather(args: WeatherArgs, _ctx: ToolContext) -> dict[str, Any]:
        loc = config.location
        lat, lon, name = loc.latitude, loc.longitude, loc.name
        async with httpx.AsyncClient(timeout=8) as client:
            if args.place:
                r = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": args.place, "count": 1},
                )
                r.raise_for_status()
                hits = r.json().get("results") or []
                if not hits:
                    return {"error": f"couldn't find {args.place}"}
                lat, lon = hits[0]["latitude"], hits[0]["longitude"]
                name = f"{hits[0]['name']}, {hits[0].get('country', '')}".strip(", ")
            if lat is None or lon is None:
                return {"error": "home location not set (admin → Appearance/Location)"}
            params: dict[str, Any] = {
                "latitude": lat,
                "longitude": lon,
                "timezone": "auto",
                "forecast_days": args.days,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
            }
            if loc.units == "imperial":
                params |= {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph"}
            r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            r.raise_for_status()
            d = r.json()
        cur = d["current"]
        daily = d["daily"]
        return {
            "place": name,
            "units": "°F, mph" if loc.units == "imperial" else "°C, km/h",
            "now": {
                "temperature": cur["temperature_2m"],
                "feels_like": cur["apparent_temperature"],
                "condition": describe_wmo(int(cur["weather_code"]))[0],
                "wind": cur["wind_speed_10m"],
            },
            "days": [
                {
                    "date": daily["time"][i],
                    "condition": describe_wmo(int(daily["weather_code"][i]))[0],
                    "high": daily["temperature_2m_max"][i],
                    "low": daily["temperature_2m_min"][i],
                    "rain_chance": daily["precipitation_probability_max"][i],
                }
                for i in range(len(daily["time"]))
            ],
        }

    return [
        Tool(
            "get_time",
            "Current date and time, here or in another timezone.",
            get_time,
            params=TimeArgs,
            label="Time",
            icon="clock",
        ),
        Tool(
            "weather",
            "Current weather and forecast for home or a named place.",
            weather,
            params=WeatherArgs,
            label="Weather",
            icon="cloud",
        ),
    ]

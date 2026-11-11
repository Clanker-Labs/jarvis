"""Live status for the UI: connection dots, offline-voice badge, weather, recent, Now card."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

import httpx
from jarvis_protocol import ConversationEntry, NowCard, StatusUpdate, Weather

from .bus import EventBus
from .config import ConfigStore

log = logging.getLogger(__name__)

# WMO weather codes → (condition, icon key)
_WMO: list[tuple[range | tuple[int, ...], str, str]] = [
    ((0,), "Clear", "clear"),
    ((1, 2), "Partly cloudy", "cloud"),
    ((3,), "Overcast", "cloud"),
    ((45, 48), "Fog", "fog"),
    (range(51, 68), "Rain", "rain"),
    (range(71, 78), "Snow", "snow"),
    (range(80, 83), "Showers", "rain"),
    ((85, 86), "Snow showers", "snow"),
    (range(95, 100), "Thunderstorm", "storm"),
]


def describe_wmo(code: int) -> tuple[str, str]:
    for codes, text, icon in _WMO:
        if code in codes:
            return text, icon
    return "Unknown", "cloud"


class StatusTracker:
    INTERNET_PROBES = ("https://1.1.1.1/cdn-cgi/trace", "https://www.google.com/generate_204")

    def __init__(self, bus: EventBus, config: ConfigStore) -> None:
        self.bus = bus
        self.config = config
        self.flags: dict[str, bool] = {
            "brain": True,
            "llm": False,
            "internet": False,
            "mic": False,
            "offline_voice": False,
            "muted": False,
        }
        self.weather: Weather | None = None
        self.weather_raw: dict[str, Any] = {}
        self.recent: list[ConversationEntry] = []
        self.now = NowCard()
        self._tasks: list[asyncio.Task[None]] = []
        self._stt_fb = False
        self._tts_fb = False

    def snapshot(self) -> StatusUpdate:
        return StatusUpdate(**self.flags, weather=self.weather)

    def update(self, **flags: bool) -> None:
        changed = False
        for k, v in flags.items():
            if self.flags.get(k) != v:
                self.flags[k] = v
                changed = True
        if changed:
            self.bus.publish(self.snapshot())

    def voice_used(
        self, *, stt_fallback: bool | None = None, tts_fallback: bool | None = None
    ) -> None:
        if stt_fallback is not None:
            self._stt_fb = stt_fallback
        if tts_fallback is not None:
            self._tts_fb = tts_fallback
        self.update(offline_voice=self._stt_fb or self._tts_fb)

    def add_recent(self, user: str, assistant: str) -> None:
        self.recent = [
            ConversationEntry(user=user, assistant=assistant, ts=time.time()),
            *self.recent,
        ][:3]

    def set_now(self, now: NowCard) -> None:
        self.now = now

    # ------------------------------------------------------------------ background checks

    async def _internet_loop(self) -> None:
        async with httpx.AsyncClient(timeout=4) as client:
            while True:
                ok = False
                for url in self.INTERNET_PROBES:
                    try:
                        r = await client.get(url)
                        ok = r.status_code < 500
                        if ok:
                            break
                    except httpx.HTTPError:
                        continue
                self.update(internet=ok)
                await asyncio.sleep(30 if ok else 10)

    async def refresh_weather(self) -> None:
        loc = self.config.location
        if loc.latitude is None or loc.longitude is None:
            return
        params = {
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "current": "temperature_2m,weather_code,apparent_temperature,wind_speed_10m",
            "daily": (
                "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"
            ),
            "timezone": loc.timezone or "auto",
            "forecast_days": 3,
        }
        if loc.units == "imperial":
            params |= {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph"}
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            r.raise_for_status()
            data = r.json()
        cur = data["current"]
        text, icon = describe_wmo(int(cur["weather_code"]))
        self.weather_raw = data
        self.weather = Weather(
            temperature_c=float(cur["temperature_2m"]), condition=text, icon=icon, location=loc.name
        )
        self.bus.publish(self.snapshot())

    async def _weather_loop(self) -> None:
        while True:
            try:
                await self.refresh_weather()
            except Exception as e:
                log.info("weather refresh failed: %s", e)
            await asyncio.sleep(900)

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._internet_loop()),
            asyncio.create_task(self._weather_loop()),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t

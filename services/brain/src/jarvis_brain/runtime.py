"""Wires the long-lived services together. One instance per brain process."""

from __future__ import annotations

import asyncio
import logging

from .agent.loop import Agent
from .bus import EventBus
from .config import ConfigStore, Section
from .db import Database
from .hub import Hub
from .orchestrator import Orchestrator, Responder
from .providers.router import Router
from .secrets import SecretStore
from .settings import Settings
from .status import StatusTracker
from .tools.builtin.time_weather import make_time_weather_tools
from .tools.registry import ToolRegistry
from .voice.speaker import Speaker
from .voice.stt import DeepgramSTT, LocalWhisperSTT, OpenAICompatSTT, STTChain, STTEngine
from .voice.tts import ElevenLabsTTS, OpenAITTS, PiperTTS, TTSChain, TTSEngine

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings, *, responder: Responder | None = None) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir = settings.data_dir / "models"
        self.db = Database(settings.data_dir / "jarvis.db")
        self.secrets = SecretStore(self.db, settings.secret_key.get_secret_value())
        self.config = ConfigStore(self.db)
        self.bus = EventBus()
        self.hub = Hub()
        self.status = StatusTracker(self.bus, self.config)

        voice = lambda: self.config.voice  # noqa: E731
        self.stt_engines: dict[str, STTEngine] = {
            "deepgram": DeepgramSTT(self.secrets, voice),
            "openai": OpenAICompatSTT(
                "openai",
                base_url=lambda: "https://api.openai.com/v1",
                model=lambda: self.config.voice.openai_stt_model,
                api_key=lambda: self.secrets.get("OPENAI_API_KEY"),
            ),
            "whisper_remote": OpenAICompatSTT(
                "whisper_remote",
                base_url=lambda: self.config.voice.whisper_remote_url,
                model=lambda: self.config.voice.whisper_remote_model,
                api_key=lambda: self.secrets.get("WHISPER_REMOTE_API_KEY"),
                local=True,
            ),
            "whisper_local": LocalWhisperSTT(voice, self.models_dir),
        }
        self.tts_engines: dict[str, TTSEngine] = {
            "openai": OpenAITTS(self.secrets, voice),
            "elevenlabs": ElevenLabsTTS(self.secrets, voice),
            "piper": PiperTTS(voice, self.models_dir),
        }
        self.stt = STTChain(self.stt_engines, voice)
        self.tts = TTSChain(self.tts_engines, voice)
        self.speaker = Speaker(self.bus, self.hub, self.tts)

        self.router = Router(self.config, self.secrets, settings)
        self.router.on_health.append(lambda ok: self.status.update(llm=ok))
        self.tools = ToolRegistry()
        for tool in make_time_weather_tools(self.config):
            self.tools.register(tool)
        self.agent = Agent(self.router, self.tools, self.config, self.status)
        self.orchestrator = Orchestrator(
            self.bus,
            self.hub,
            self.config,
            self.stt,
            self.speaker,
            responder or self.agent,
            self.status,
        )
        self.hub.on_node_change.append(self._on_node_change)
        self.config.on_change(self._on_config_change)

    def _on_node_change(self, connected: bool) -> None:
        self.status.update(mic=connected)

    def _on_config_change(self, section: str, _value: Section) -> None:
        if section == "appearance":
            self.bus.publish(self.orchestrator.snapshot())
        if section == "location":
            asyncio.get_running_loop().create_task(self.status.refresh_weather())

    def health_extra(self) -> dict[str, object]:
        return {
            "stt": [e.id for e in self.stt.active()],
            "tts": [e.id for e in self.tts.active()],
            "providers": {pid: self.router.health.get(pid) for pid in self.router.providers},
            **{k: v for k, v in self.status.flags.items() if k != "brain"},
        }

    async def warm_local_voice(self) -> None:
        """Load offline engines in the background so the first fallback isn't slow."""
        for engine in (self.tts_engines["piper"], self.stt_engines["whisper_local"]):
            if engine.available() and engine.id in (
                *self.config.voice.tts_chain,
                *self.config.voice.stt_chain,
            ):
                try:
                    await asyncio.to_thread(engine.warm)  # type: ignore[attr-defined]
                except Exception as e:
                    log.warning("could not warm %s: %s", engine.id, e)

    async def start(self) -> None:
        await self.orchestrator.start()
        if self.settings.background_tasks:
            await self.status.start()
            await self.router.start()
        if self.settings.warm_local_voice:
            self._warm_task = asyncio.create_task(self.warm_local_voice())

    async def stop(self) -> None:
        await self.orchestrator.stop()
        await self.router.stop()
        await self.status.stop()
        self.db.close()

"""Wires the long-lived services together. One instance per brain process."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from jarvis_protocol import NodeConfig

from .agent.loop import Agent
from .bus import EventBus
from .config import ConfigStore, Section
from .db import Database
from .hub import Hub
from .mcp.manager import MCPManager
from .orchestrator import Orchestrator, Responder
from .providers.router import Router
from .secrets import SecretStore
from .settings import Settings
from .skills_runtime.manager import SkillManager
from .status import StatusTracker
from .tools.builtin.files import make_file_tools
from .tools.builtin.memory import MemoryStore, make_memory_tools
from .tools.builtin.notes import make_notes_tools
from .tools.builtin.time_weather import make_time_weather_tools
from .tools.builtin.timers import Scheduler, make_timer_tools
from .tools.builtin.web import make_web_tools
from .tools.config import ToolsConfig
from .tools.registry import ToolRegistry
from .traces import TraceStore
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
        self.memory = MemoryStore(self.db)
        self.scheduler = Scheduler(self.db, self._announce)
        tools_cfg = lambda: self.config.get("tools", ToolsConfig)  # noqa: E731
        builtin = [
            *make_time_weather_tools(self.config),
            *make_notes_tools(self.db),
            *make_memory_tools(self.memory),
            *make_timer_tools(self.scheduler),
            *make_web_tools(self.secrets, tools_cfg),
            *make_file_tools(lambda: Path(tools_cfg().files_dir)),
        ]
        for tool in builtin:
            self.tools.register(tool)
        self._apply_tool_overrides()
        self.mcp = MCPManager(self.config, self.secrets, self.tools)
        self.skills = SkillManager(
            settings.skills_dir,
            settings.data_dir / "skill-runs",
            self.config,
            self.secrets,
            self.tools,
        )
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
        self.traces = TraceStore(self.db)
        self.bus.tap(self.traces.tap)
        self.orchestrator.on_turn_done.append(self.traces.save)
        self.started_at = time.time()
        self.monitor = False
        self.admin_auth: Any = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self.hub.on_node_change.append(self._on_node_change)
        self.config.on_change(self._on_config_change)

    def _apply_tool_overrides(self) -> None:
        cfg = self.config.get("tools", ToolsConfig)
        self.tools.overrides = {
            name: o.model_dump(exclude_none=True) for name, o in cfg.overrides.items()
        }

    async def _announce(self, text: str, _row: dict[str, Any]) -> None:
        if self.hub.node_connected:
            await self.orchestrator.announce(text)
        else:
            log.warning("announcement dropped (no audio node): %s", text)

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any] | None:
        """Fire-and-forget with a strong reference and error logging (no-op outside a loop)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return None
        task = loop.create_task(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception():
                log.error("background task failed", exc_info=t.exception())

        task.add_done_callback(done)
        return task

    def node_config(self) -> NodeConfig:
        v = self.config.voice
        return NodeConfig(
            wake_threshold=v.wake_threshold,
            chime=v.chime,
            vad_end_silence_ms=v.vad_end_silence_ms,
            max_utterance_s=v.max_utterance_s,
            monitor=self.monitor,
        )

    async def set_monitor(self, on: bool) -> None:
        self.monitor = on
        await self.hub.to_node(self.node_config())

    def on_secrets_changed(self) -> None:
        self.spawn(self.router.check_health())

    def _on_node_change(self, connected: bool) -> None:
        self.status.update(mic=connected)
        if connected:
            self.spawn(self.hub.to_node(self.node_config()))
        else:
            self.monitor = False

    def _on_config_change(self, section: str, _value: Section) -> None:
        if section == "appearance":
            self.bus.publish(self.orchestrator.snapshot())
        if section == "location":
            self.spawn(self.status.refresh_weather())
        if section == "tools":
            self._apply_tool_overrides()
        if section == "voice":
            self.spawn(self.hub.to_node(self.node_config()))

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
        await self.scheduler.start()
        await self.skills.start()
        await self.mcp.start()
        if self.settings.background_tasks:
            await self.status.start()
            await self.router.start()
        if self.settings.warm_local_voice:
            self._warm_task = asyncio.create_task(self.warm_local_voice())

    async def stop(self) -> None:
        await self.orchestrator.stop()
        await self.mcp.stop()
        await self.skills.stop()
        await self.scheduler.stop()
        await self.router.stop()
        await self.status.stop()
        self.db.close()

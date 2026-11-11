"""Runtime configuration: typed sections stored as JSON rows, edited from the admin panel.

Each section is a pydantic model with defaults, so a fresh install works with an empty DB. Updates
are validated, persisted, and broadcast to listeners (which hot-apply them — no restarts).
YAML export/import covers every section; secrets are never included.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any, Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .db import Database, dumps

log = logging.getLogger(__name__)


class Section(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class VoiceConfig(Section):
    # Ordered fallback chains; entries whose credentials/endpoints are missing are skipped.
    stt_chain: list[str] = ["deepgram", "openai", "whisper_remote", "whisper_local"]
    tts_chain: list[str] = ["openai", "elevenlabs", "piper"]
    language: str = "en"

    deepgram_model: str = "nova-3"
    openai_stt_model: str = "gpt-4o-mini-transcribe"
    # OpenAI-compatible transcription server on the tailnet (e.g. speaches/faster-whisper on a GPU).
    whisper_remote_url: str = ""
    whisper_remote_model: str = "Systran/faster-distil-whisper-small.en"
    whisper_local_model: str = "tiny.en"

    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_voice: str = "fable"
    openai_tts_instructions: str = (
        "Voice: a calm, composed British butler. Measured pace, warm but dry, quietly confident. "
        "Never theatrical."
    )
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"  # "George" — warm British male
    elevenlabs_model: str = "eleven_flash_v2_5"
    piper_voice: str = "en_GB-alan-medium"

    follow_up_s: float = Field(8.0, ge=0, le=30)
    chime: bool = True
    wake_threshold: float = Field(0.5, ge=0.05, le=0.99)


class AppearanceConfig(Section):
    colors: dict[str, str] = {
        "IDLE": "#0EA5E9",
        "WAKE": "#F59E0B",
        "LISTENING": "#F59E0B",
        "THINKING": "#8B5CF6",
        "TOOL": "#8B5CF6",
        "SPEAKING": "#14B8A6",
        "FOLLOW_UP": "#F59E0B",
        "ERROR": "#EF4444",
    }
    quality: Literal["auto", "high", "medium", "low"] = "auto"
    night_start: str = "23:00"
    night_end: str = "07:00"
    night_dim: float = Field(0.3, ge=0.05, le=1)
    screen_sleep_min: int = Field(10, ge=0, le=240)
    show_cards: bool = True


class LocationConfig(Section):
    name: str = ""
    latitude: float | None = None
    longitude: float | None = None
    timezone: str = "auto"
    units: Literal["metric", "imperial"] = "metric"


class AgentConfig(Section):
    max_steps: int = Field(8, ge=1, le=32)
    history_turns: int = Field(12, ge=2, le=100)
    user_name: str = ""
    # Phase 2 fallback: with no LLM configured, speak back what was heard.
    echo_when_no_llm: bool = True


SECTIONS: dict[str, type[Section]] = {
    "voice": VoiceConfig,
    "appearance": AppearanceConfig,
    "location": LocationConfig,
    "agent": AgentConfig,
}

T = TypeVar("T", bound=Section)
Listener = Callable[[str, Section], Any]


class ConfigStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: dict[str, Section] = {}
        self._listeners: list[Listener] = []

    @staticmethod
    def register(name: str, model: type[Section]) -> None:
        SECTIONS[name] = model

    def get(self, name: str, model: type[T] | None = None) -> T:
        if name not in self._cache:
            cls = SECTIONS[name]
            row = self.db.one("SELECT data FROM config WHERE section=?", (name,))
            self._cache[name] = cls.model_validate(json.loads(row["data"])) if row else cls()
        return self._cache[name]  # type: ignore[return-value]

    @property
    def voice(self) -> VoiceConfig:
        return self.get("voice")

    @property
    def appearance(self) -> AppearanceConfig:
        return self.get("appearance")

    @property
    def agent(self) -> AgentConfig:
        return self.get("agent")

    @property
    def location(self) -> LocationConfig:
        return self.get("location")

    def update(self, name: str, patch: dict[str, Any]) -> Section:
        current = self.get(name)
        merged = SECTIONS[name].model_validate({**current.model_dump(), **patch})
        self.db.execute(
            "INSERT INTO config(section, data, updated) VALUES(?,?,?) "
            "ON CONFLICT(section) DO UPDATE SET data=excluded.data, updated=excluded.updated",
            (name, dumps(merged.model_dump(mode="json")), time.time()),
        )
        self._cache[name] = merged
        for fn in self._listeners:
            try:
                fn(name, merged)
            except Exception:
                log.exception("config listener failed for %s", name)
        return merged

    def on_change(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def all(self) -> dict[str, dict[str, Any]]:
        return {n: self.get(n).model_dump(mode="json") for n in SECTIONS}

    def export_yaml(self) -> str:
        return yaml.safe_dump({"jarvis_config": 1, **self.all()}, sort_keys=False)

    def import_yaml(self, text: str) -> list[str]:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("expected a mapping")
        # Validate everything before writing anything.
        validated = {
            name: SECTIONS[name].model_validate(values)
            for name, values in data.items()
            if name in SECTIONS
        }
        for name, model in validated.items():
            self.update(name, model.model_dump())
        return sorted(validated)

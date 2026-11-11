from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class NodeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env",), extra="ignore", case_sensitive=False)

    brain_url: str = Field("ws://127.0.0.1:8000", alias="BRAIN_URL")
    node_token: SecretStr = Field(SecretStr(""), alias="JARVIS_NODE_TOKEN")

    input_device: str = Field("default", alias="AUDIO_INPUT_DEVICE")
    output_device: str = Field("default", alias="AUDIO_OUTPUT_DEVICE")

    models_dir: Path = Field(Path("data/models"), alias="JARVIS_MODELS_DIR")
    wake_model: str = Field("hey_jarvis", alias="WAKE_MODEL")
    wake_threshold: float = Field(0.5, alias="WAKE_THRESHOLD")
    # Ignore further detections for this long after one fires (debounce).
    wake_refractory_s: float = Field(1.5, alias="WAKE_REFRACTORY_S")

    vad_threshold: float = Field(0.5, alias="VAD_THRESHOLD")
    end_silence_ms: int = Field(700, alias="VAD_END_SILENCE_MS")
    max_utterance_s: float = Field(30.0, alias="VAD_MAX_UTTERANCE_S")
    # After the wake word, give up if no speech starts within this window.
    no_speech_timeout_s: float = Field(6.0, alias="VAD_NO_SPEECH_TIMEOUT_S")

    chime: bool = Field(True, alias="WAKE_CHIME")
    # Keep this much audio queued ahead of the speaker (lower = snappier barge-in + levels).
    playback_lead_ms: int = Field(160, alias="PLAYBACK_LEAD_MS")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @property
    def ws_url(self) -> str:
        return self.brain_url.rstrip("/") + "/ws/node"

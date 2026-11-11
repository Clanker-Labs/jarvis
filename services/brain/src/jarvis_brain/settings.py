"""Process-level settings from the environment (``.env``).

Only bootstrap values live here: where to bind, where data goes, the master secret. Everything a
user tunes at runtime (providers, voice, colours, routing…) lives in the config DB and is edited
from the admin panel — see :mod:`jarvis_brain.config`.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def is_safe_bind(host: str) -> bool:
    """Only loopback or Tailscale addresses — never a wildcard or LAN/public address.

    Containers use host networking (see compose.yaml), so this holds inside Docker too."""
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip in TAILSCALE_V4 or ip in TAILSCALE_V6


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",), env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    env: str = Field("dev", alias="JARVIS_ENV")
    data_dir: Path = Field(Path("data"), alias="JARVIS_DATA_DIR")
    # Internet/weather probes. Off in tests.
    background_tasks: bool = Field(True, alias="JARVIS_BACKGROUND_TASKS")
    # Load offline voice models (Piper, faster-whisper) at startup so a fallback is instant.
    warm_local_voice: bool = Field(True, alias="JARVIS_WARM_LOCAL_VOICE")

    brain_host: str = Field("127.0.0.1", alias="BRAIN_HOST")
    brain_port: int = Field(8000, alias="BRAIN_PORT")
    admin_host: str = Field("127.0.0.1", alias="ADMIN_HOST")
    admin_port: int = Field(9000, alias="ADMIN_PORT")

    # Shared device token: audio node + kiosk UI authenticate their WebSockets with it.
    node_token: SecretStr = Field(SecretStr(""), alias="JARVIS_NODE_TOKEN")
    # Master key (Fernet, urlsafe base64 32 bytes) encrypting secrets + OAuth tokens at rest.
    secret_key: SecretStr = Field(SecretStr(""), alias="JARVIS_SECRET_KEY")
    # Origins allowed to open /ws/ui (the kiosk page). Comma separated.
    ui_origins: str = Field(
        "http://127.0.0.1:8080,http://localhost:8080,http://localhost:5173", alias="UI_ORIGINS"
    )
    ui_dist: Path | None = Field(None, alias="JARVIS_UI_DIST")
    admin_dist: Path = Field(Path("apps/admin/dist"), alias="JARVIS_ADMIN_DIST")

    # Bootstrap values; can be overridden from admin (DB wins once set there).
    local_llm_base_url: str = Field("", alias="LOCAL_LLM_BASE_URL")
    local_llm_model: str = Field("", alias="LOCAL_LLM_MODEL")
    local_llm_api_key: SecretStr = Field(SecretStr(""), alias="LOCAL_LLM_API_KEY")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @field_validator("brain_host", "admin_host")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def allowed_origins(self) -> set[str]:
        return {o.strip().rstrip("/") for o in self.ui_origins.split(",") if o.strip()}

    def check_binds(self) -> None:
        for name, host in (("BRAIN_HOST", self.brain_host), ("ADMIN_HOST", self.admin_host)):
            if not is_safe_bind(host):
                raise SystemExit(
                    f"Refusing to bind {name}={host}: use 127.0.0.1 or this host's Tailscale IP "
                    "(100.x.y.z). See docs/DECISIONS.md#network-binding."
                )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

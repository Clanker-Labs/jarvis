"""Settings for built-in tools (admin → Tools & Connectors)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..config import ConfigStore, Section


class ToolOverride(Section):
    enabled: bool | None = None
    side_effect: Literal["none", "reversible", "irreversible"] | None = None  # can only raise


class ToolsConfig(Section):
    web_search_backend: Literal["auto", "brave", "tavily", "searxng", "duckduckgo"] = "auto"
    searxng_url: str = ""
    # read_url refuses private, loopback, link-local and Tailscale addresses unless allowed here.
    url_allow_private: bool = False
    files_dir: str = "data/files"
    overrides: dict[str, ToolOverride] = Field(default_factory=dict)


ConfigStore.register("tools", ToolsConfig)

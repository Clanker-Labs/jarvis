"""``manifest.yaml`` — one schema for connectors, skills and built-in tool groups.

id: gmail
name: Gmail
type: connector            # builtin | connector | mcp | skill
auth: oauth2               # none | api_key | oauth2
required_secrets: [GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET]
oauth: {provider: google, scopes: [...]}
enabled: true
tools:
  - {name: gmail_search, side_effect: none, description: Search mail}
  - {name: gmail_send, side_effect: irreversible}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from jarvis_protocol import SideEffect
from pydantic import BaseModel, ConfigDict, Field


class ManifestTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    side_effect: SideEffect = SideEffect.IRREVERSIBLE  # safe default
    description: str = ""


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,40}$")
    name: str
    type: Literal["builtin", "connector", "mcp", "skill"]
    auth: Literal["none", "api_key", "oauth2"] = "none"
    required_secrets: list[str] = []
    oauth: dict[str, Any] | None = None
    enabled: bool = True
    tools: list[ManifestTool] = []
    description: str = ""

    def tool(self, name: str) -> ManifestTool | None:
        return next((t for t in self.tools if t.name == name), None)

    @classmethod
    def load(cls, path: Path) -> Manifest:
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})

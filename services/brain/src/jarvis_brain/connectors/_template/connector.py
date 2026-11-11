"""Template connector (Home Assistant REST API). Copy the folder, rename, edit."""

from __future__ import annotations

from typing import Any

from jarvis_protocol import SideEffect
from pydantic import BaseModel, Field

from ...tools.registry import Tool, ToolContext
from ..base import ConnectorContext


class EntityArgs(BaseModel):
    entity_id: str = Field(description="e.g. 'light.kitchen' or 'lock.front_door'")


class HomeAssistant:
    def __init__(self, ctx: ConnectorContext) -> None:
        self.ctx = ctx

    def _base(self) -> tuple[str, dict[str, str]]:
        url = self.ctx.secrets.get("HOME_ASSISTANT_URL").rstrip("/")
        return url, {"Authorization": f"Bearer {self.ctx.secrets.get('HOME_ASSISTANT_TOKEN')}"}

    async def state(self, a: EntityArgs, _c: ToolContext) -> dict[str, Any]:
        url, headers = self._base()
        async with self.ctx.http() as c:
            r = await c.get(f"{url}/api/states/{a.entity_id}", headers=headers)
            r.raise_for_status()
            d = r.json()
            return {
                "entity": a.entity_id,
                "state": d["state"],
                "attributes": d.get("attributes", {}),
            }

    async def _service(self, domain: str, service: str, entity_id: str) -> dict[str, Any]:
        url, headers = self._base()
        async with self.ctx.http() as c:
            r = await c.post(
                f"{url}/api/services/{domain}/{service}",
                headers=headers,
                json={"entity_id": entity_id},
            )
            r.raise_for_status()
            return {"ok": True}

    async def turn_on(self, a: EntityArgs, _c: ToolContext) -> dict[str, Any]:
        return await self._service(a.entity_id.split(".")[0], "turn_on", a.entity_id)

    async def unlock(self, a: EntityArgs, _c: ToolContext) -> dict[str, Any]:
        return await self._service("lock", "unlock", a.entity_id)

    async def now(self) -> dict[str, Any]:
        return {}  # optional: fields for the UI's "Now" card

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "ha_state",
                "Get the state of a Home Assistant entity.",
                self.state,
                params=EntityArgs,
                label="Home",
                icon="home",
            ),
            Tool(
                "ha_turn_on",
                "Turn on a light, switch or scene.",
                self.turn_on,
                params=EntityArgs,
                side_effect=SideEffect.REVERSIBLE,
                label="Home",
                icon="home",
            ),
            Tool(
                "ha_unlock_door",
                "Unlock a door lock.",
                self.unlock,
                params=EntityArgs,
                side_effect=SideEffect.IRREVERSIBLE,
                label="Unlocking",
                icon="lock",
                describe=lambda args: f"unlock {args.get('entity_id', 'the door')}",
            ),
        ]


def create(ctx: ConnectorContext) -> HomeAssistant:
    return HomeAssistant(ctx)

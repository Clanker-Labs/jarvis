"""Admin API for tools, MCP servers, skills, long-term memory and timers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from ..mcp.manager import MCPServerConfig, display_name
from ..tools.config import ToolsConfig
from .auth import AdminAuth

if TYPE_CHECKING:
    from ..runtime import Runtime


class ToolPatch(BaseModel):
    enabled: bool | None = None
    side_effect: Literal["none", "reversible", "irreversible"] | None = None


class FileBody(BaseModel):
    content: str = Field(max_length=200_000)


class FactBody(BaseModel):
    fact: str = Field(min_length=2, max_length=500)


def build_tools_router(rt: Runtime, auth: AdminAuth) -> APIRouter:
    api = APIRouter(prefix="/api", dependencies=[Depends(auth.require)])

    # ------------------------------------------------------------------ tools

    @api.get("/tools")
    async def tools() -> list[dict[str, Any]]:
        overrides = rt.config.get("tools", ToolsConfig).overrides
        out = []
        for name in sorted(rt.tools._tools):
            t = rt.tools.get(name)
            if t is None:
                continue
            base = rt.tools._tools[name]
            display = name
            if name.startswith("mcp__"):
                _, server, remote = name.split("__", 2)
                display = display_name(server, remote)
            out.append(
                {
                    "name": name,
                    "display": display,
                    "description": t.description[:300],
                    "source": t.source,
                    "label": t.label,
                    "icon": t.icon,
                    "enabled": t.enabled,
                    "side_effect": t.side_effect,
                    "declared_side_effect": base.side_effect,
                    "override": overrides[name].model_dump(exclude_none=True)
                    if name in overrides
                    else None,
                }
            )
        return out

    @api.put("/tools/{name}")
    async def patch_tool(name: str, body: ToolPatch) -> dict[str, Any]:
        cfg = rt.config.get("tools", ToolsConfig)
        overrides = {k: v.model_dump(exclude_none=True) for k, v in cfg.overrides.items()}
        merged = {**overrides.get(name, {}), **body.model_dump(exclude_none=True)}
        overrides[name] = merged
        rt.config.update("tools", {"overrides": overrides})
        t = rt.tools.get(name)
        # Overrides can only make a tool *stricter*; report the effective level.
        return {
            "name": name,
            "enabled": t.enabled if t else None,
            "side_effect": t.side_effect if t else None,
        }

    @api.delete("/tools/{name}/override")
    async def clear_override(name: str) -> dict[str, Any]:
        cfg = rt.config.get("tools", ToolsConfig)
        overrides = {
            k: v.model_dump(exclude_none=True) for k, v in cfg.overrides.items() if k != name
        }
        rt.config.update("tools", {"overrides": overrides})
        t = rt.tools.get(name)
        return {
            "name": name,
            "enabled": t.enabled if t else None,
            "side_effect": t.side_effect if t else None,
        }

    # ------------------------------------------------------------------ MCP

    @api.get("/mcp")
    async def mcp_servers() -> list[dict[str, Any]]:
        return rt.mcp.describe()

    @api.post("/mcp/test")
    async def mcp_test(body: dict[str, Any]) -> dict[str, Any]:
        try:
            cfg = MCPServerConfig.model_validate(body)
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False)) from e
        return await rt.mcp.test(cfg)

    @api.post("/mcp/reload")
    async def mcp_reload() -> list[dict[str, Any]]:
        for name in list(rt.mcp.servers):
            await rt.mcp._stop_server(name)
        await rt.mcp.sync()
        return rt.mcp.describe()

    # ------------------------------------------------------------------ skills

    @api.get("/skills")
    async def skills() -> list[dict[str, Any]]:
        return rt.skills.describe()

    @api.post("/skills/reload")
    async def skills_reload() -> list[dict[str, Any]]:
        rt.skills.reload()
        return rt.skills.describe()

    @api.get("/skills/{name}/file")
    async def skill_file(name: str, path: str = "SKILL.md") -> dict[str, str]:
        try:
            return {"path": path, "content": rt.skills.read_file(name, path)}
        except (FileNotFoundError, ValueError, PermissionError) as e:
            raise HTTPException(404, str(e)) from e

    @api.put("/skills/{name}/file")
    async def write_skill_file(
        name: str, body: FileBody, path: str = "SKILL.md"
    ) -> list[dict[str, Any]]:
        try:
            rt.skills.write_file(name, path, body.content)
        except (ValueError, PermissionError) as e:
            raise HTTPException(400, str(e)) from e
        return rt.skills.describe()

    @api.delete("/skills/{name}")
    async def delete_skill(name: str) -> list[dict[str, Any]]:
        try:
            rt.skills.delete(name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return rt.skills.describe()

    @api.post("/skills/upload")
    async def upload_skill(request: Request) -> dict[str, Any]:
        data = await request.body()
        try:
            name = rt.skills.install_zip(data)
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # BadZipFile etc.
            raise HTTPException(400, f"not a valid zip: {e}") from e
        return {"installed": name, "skills": rt.skills.describe()}

    # ------------------------------------------------------------------ memory

    @api.get("/memory")
    async def memories() -> list[dict[str, Any]]:
        return rt.memory.all()

    @api.post("/memory")
    async def add_memory(body: FactBody) -> dict[str, int]:
        mem_id = rt.memory.add(body.fact, source="admin")
        rt.spawn(rt.kb.index_memory(mem_id, body.fact))
        return {"id": mem_id}

    @api.put("/memory/{mem_id}")
    async def edit_memory(mem_id: int, body: FactBody) -> dict[str, bool]:
        rt.memory.update(mem_id, body.fact)
        rt.spawn(rt.kb.index_memory(mem_id, body.fact))
        return {"ok": True}

    @api.delete("/memory/{mem_id}")
    async def delete_memory(mem_id: int) -> dict[str, bool]:
        return {"ok": rt.memory.delete(mem_id)}

    # ------------------------------------------------------------------ coding-agent tasks

    @api.get("/tasks")
    async def tasks() -> list[dict[str, Any]]:
        return [j.view() for j in sorted(rt.tasks.jobs.values(), key=lambda j: -j.started)]

    @api.post("/tasks/{job_id}/cancel")
    async def cancel_task(job_id: str) -> dict[str, bool]:
        return {"ok": rt.tasks.cancel(job_id)}

    # ------------------------------------------------------------------ timers

    @api.get("/timers")
    async def timers() -> list[dict[str, Any]]:
        return rt.scheduler.pending()

    @api.delete("/timers/{timer_id}")
    async def cancel_timer(timer_id: int) -> dict[str, bool]:
        return {"ok": rt.scheduler.cancel(timer_id)}

    return api

"""Background jobs for agent-CLI providers ("go fix the failing test in my repo").

Coding agents take minutes, so a confirmed task runs in the background: Jarvis says it's on it, the
kiosk shows live progress, and the result is announced (briefly, for the ear) when it finishes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from jarvis_protocol import AgentProgress, SideEffect
from pydantic import BaseModel, Field

from ..bus import EventBus
from ..providers.agent_cli import AgentCLIProvider
from ..providers.base import Completed, Message, Progress, ProviderError, TextDelta
from ..tools.registry import Tool, ToolContext

if TYPE_CHECKING:
    from ..providers.router import Router

log = logging.getLogger(__name__)

Status = Literal["running", "done", "failed", "cancelled"]


@dataclass
class Job:
    id: str
    provider_id: str
    agent: str
    task: str
    status: Status = "running"
    started: float = field(default_factory=time.time)
    ended: float | None = None
    progress: deque[str] = field(default_factory=lambda: deque(maxlen=50))
    result: str = ""
    error: str = ""
    cost_usd: float = 0.0
    handle: asyncio.Task[None] | None = None

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider_id,
            "agent": self.agent,
            "task": self.task,
            "status": self.status,
            "started": self.started,
            "ended": self.ended,
            "progress": list(self.progress),
            "result": self.result,
            "error": self.error,
            "cost_usd": round(self.cost_usd, 4),
        }


def spoken_summary_fallback(text: str, sentences: int = 2) -> str:
    from ..voice.text import speakable

    parts = re.split(r"(?<=[.!?])\s+", speakable(text))
    out = " ".join(parts[:sentences]).strip()
    return out[:400] or "It finished."


class TaskRunner:
    def __init__(
        self,
        bus: EventBus,
        announce: Callable[[str], Awaitable[None]],
        summarize: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self.bus = bus
        self.announce = announce
        self.summarize = summarize
        self.jobs: dict[str, Job] = {}

    def running(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.status == "running"]

    def start(self, provider: AgentCLIProvider, task: str) -> Job:
        job = Job(uuid.uuid4().hex[:8], provider.id, provider.display, task)
        self.jobs[job.id] = job
        job.handle = asyncio.create_task(self._run(job, provider), name=f"task:{job.id}")
        self._progress(job, f"{provider.display} is starting", force=True)
        return job

    def _progress(self, job: Job, msg: str, *, force: bool = False) -> None:
        job.progress.append(msg)
        now = time.monotonic()
        if force or now - getattr(job, "_last_pub", 0.0) > 1.5:
            job._last_pub = now  # type: ignore[attr-defined]
            self.bus.publish(
                AgentProgress(provider=job.agent, message=msg, turn_id=f"task:{job.id}")
            )

    async def _run(self, job: Job, provider: AgentCLIProvider) -> None:
        try:
            async for ev in provider.run([Message("user", job.task)], []):
                if isinstance(ev, Progress):
                    self._progress(job, ev.message)
                elif isinstance(ev, TextDelta):
                    job.result += ev.text
                elif isinstance(ev, Completed):
                    job.cost_usd = ev.cost_usd
            job.status = "done"
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.ended = time.time()
            self._progress(job, f"{job.agent} stopped", force=True)
            raise
        except ProviderError as e:
            job.status, job.error = "failed", str(e)
        except Exception as e:  # never let a background job die silently
            log.exception("task %s crashed", job.id)
            job.status, job.error = "failed", str(e)
        job.ended = time.time()
        self._progress(
            job, f"{job.agent} has {'finished' if job.status == 'done' else 'stopped'}", force=True
        )
        await self._report(job)

    async def _report(self, job: Job) -> None:
        if job.status == "done":
            summary = ""
            if self.summarize and job.result:
                with contextlib.suppress(Exception):
                    summary = (await asyncio.wait_for(self.summarize(job.result), 30)).strip()
            text = f"{job.agent} has finished. {summary or spoken_summary_fallback(job.result)}"
        else:
            reason = re.sub(r"^[\w-]+: ", "", job.error)[:160]
            text = f"{job.agent} couldn't finish the task: {reason}"
        with contextlib.suppress(Exception):
            await self.announce(text)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status != "running" or job.handle is None:
            return False
        job.handle.cancel()
        return True

    async def stop(self) -> None:
        for j in self.running():
            if j.handle:
                j.handle.cancel()


class DelegateArgs(BaseModel):
    task: str = Field(
        min_length=5,
        max_length=4000,
        description="The full task, in the user's words plus needed context.",
    )
    agent: str | None = Field(
        None, description="Provider id of the coding agent; omit for the default."
    )


class JobArgs(BaseModel):
    job_id: str | None = Field(None, description="Omit for the most recent job.")


def make_task_tools(runner: TaskRunner, router: Router) -> list[Tool]:
    def agents() -> list[AgentCLIProvider]:
        return [p for p in router.chain("task") if isinstance(p, AgentCLIProvider)]

    def pick(agent: str | None) -> AgentCLIProvider | None:
        options = agents()
        if agent:
            return next(
                (p for p in options if agent.lower() in (p.id.lower(), p.display.lower(), p.cli)),
                None,
            )
        return options[0] if options else None

    async def delegate(a: DelegateArgs, _c: ToolContext) -> dict[str, Any]:
        provider = pick(a.agent)
        if provider is None:
            return {"error": "no coding agent is configured (admin → Providers, kind 'cli')"}
        job = runner.start(provider, a.task)
        return {"started": True, "job_id": job.id, "agent": provider.display}

    async def status(a: JobArgs, _c: ToolContext) -> dict[str, Any]:
        jobs = sorted(runner.jobs.values(), key=lambda j: j.started, reverse=True)
        job = runner.jobs.get(a.job_id) if a.job_id else (jobs[0] if jobs else None)
        if job is None:
            return {"jobs": []}
        v = job.view()
        v["progress"] = v["progress"][-5:]
        v["result"] = v["result"][:1500]
        return v

    async def cancel(a: JobArgs, _c: ToolContext) -> dict[str, Any]:
        target = a.job_id or next(
            (j.id for j in sorted(runner.running(), key=lambda j: -j.started)), None
        )
        return {"cancelled": bool(target and runner.cancel(target))}

    def describe(args: dict[str, Any]) -> str:
        p = pick(args.get("agent"))
        who = p.display if p else "the coding agent"
        task = " ".join(str(args.get("task", "")).split())
        return f"hand this to {who}: {task if len(task) < 140 else task[:137] + '…'}"

    return [
        Tool(
            "delegate_task",
            "Hand a coding or build task to a coding agent (Claude Code, Codex, "
            "Cursor). It runs in the background and reports back.",
            delegate,
            params=DelegateArgs,
            side_effect=SideEffect.IRREVERSIBLE,
            label="Coding agent",
            icon="terminal",
            describe=describe,
        ),
        Tool(
            "task_status",
            "Check on a running or finished coding-agent task.",
            status,
            params=JobArgs,
            label="Task",
            icon="terminal",
        ),
        Tool(
            "task_cancel",
            "Stop a running coding-agent task.",
            cancel,
            params=JobArgs,
            side_effect=SideEffect.REVERSIBLE,
            label="Task",
            icon="terminal",
        ),
    ]

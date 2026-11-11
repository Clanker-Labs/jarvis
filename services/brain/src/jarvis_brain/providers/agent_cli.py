"""Agent-CLI providers: Claude Code, Codex CLI and Cursor Agent, run headless as subprocesses.

Flags (verified against each tool's docs/source, Nov 2026):
- Claude Code: ``claude -p <task> --output-format stream-json --verbose --permission-mode acceptEdits
  --allowedTools … --max-budget-usd N --no-session-persistence [--model M]``
- Codex:       ``codex exec --json --cd DIR --sandbox workspace-write --skip-git-repo-check --ephemeral
  [--model M] <task>`` (JSONL: thread.started, item.started/completed, turn.completed/failed)
- Cursor:      ``agent -p --output-format stream-json --force --trust --workspace DIR [--model M] <task>``
  (``CURSOR_API_KEY`` from secrets)

Each run gets its own working directory under the configured workspace, a hard timeout, and its
process group is killed on cancellation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import signal
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .base import AgentEvent, Completed, Message, Progress, ProviderError, TextDelta, ToolSpec

log = logging.getLogger(__name__)

CLI = Literal["claude", "codex", "cursor-agent"]
DISPLAY = {"claude": "Claude Code", "codex": "Codex", "cursor-agent": "Cursor"}
DEFAULT_BIN = {"claude": "claude", "codex": "codex", "cursor-agent": "agent"}
CLAUDE_TOOLS = "Read Edit Write MultiEdit Glob Grep LS Bash"


def _short(s: str, n: int = 60) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _name(path: str) -> str:
    return Path(str(path)).name or str(path)


@dataclass
class ParseState:
    final: str = ""
    texts: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    done: bool = False


# --------------------------------------------------------------------------- stream parsers
# Each takes one decoded JSON event and returns progress messages (if any), updating state.


def parse_claude(ev: dict[str, Any], st: ParseState) -> list[str]:
    out: list[str] = []
    t = ev.get("type")
    if t == "assistant":
        for block in (ev.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                st.texts.append(block["text"])
            elif block.get("type") == "tool_use":
                name, inp = block.get("name", ""), block.get("input") or {}
                if name in ("Edit", "MultiEdit", "Write"):
                    out.append(f"editing {_name(inp.get('file_path', ''))}")
                elif name == "Bash":
                    out.append(f"running {_short(inp.get('command', ''), 50)}")
                elif name in ("Read", "Glob", "Grep", "LS"):
                    out.append("reading the code")
                else:
                    out.append(f"using {name}")
    elif t == "result":
        st.done = True
        st.final = str(ev.get("result") or "")
        st.cost_usd = float(ev.get("total_cost_usd") or 0)
        usage = ev.get("usage") or {}
        st.input_tokens = int(usage.get("input_tokens") or 0)
        st.output_tokens = int(usage.get("output_tokens") or 0)
        if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
            st.error = st.final or str(ev.get("subtype"))
    return out


def parse_codex(ev: dict[str, Any], st: ParseState) -> list[str]:
    t = ev.get("type")
    item = ev.get("item") or {}
    it = item.get("type")
    if t == "item.started" and it == "command_execution":
        return [f"running {_short(item.get('command', ''), 50)}"]
    if t == "item.completed":
        if it == "agent_message" and item.get("text"):
            st.texts.append(item["text"])
        elif it == "file_change":
            files = ", ".join(_name(c.get("path", "")) for c in item.get("changes", [])[:3])
            return [f"changed {files}"] if files else []
        elif it == "error":
            st.error = str(item.get("message", "error"))
    elif t == "turn.completed":
        u = ev.get("usage") or {}
        st.input_tokens += int(u.get("input_tokens") or 0)
        st.output_tokens += int(u.get("output_tokens") or 0)
        st.done = True
        st.final = st.texts[-1] if st.texts else ""
    elif t in ("turn.failed", "error"):
        st.error = str(
            (ev.get("error") or {}).get("message")
            if isinstance(ev.get("error"), dict)
            else ev.get("message", "failed")
        )
    return []


def parse_cursor(ev: dict[str, Any], st: ParseState) -> list[str]:
    t, sub = ev.get("type"), ev.get("subtype")
    if t == "assistant" and "model_call_id" not in ev:
        for block in (ev.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                st.texts.append(block["text"])
    elif t == "tool_call" and sub == "started":
        call = ev.get("tool_call") or {}
        if "writeToolCall" in call:
            return [f"editing {_name(call['writeToolCall'].get('args', {}).get('path', ''))}"]
        if "readToolCall" in call:
            return ["reading the code"]
        fn = call.get("function") or {}
        return [f"using {fn.get('name', 'a tool')}"] if fn else []
    elif t == "result":
        st.done = True
        st.final = str(ev.get("result") or "")
        if ev.get("is_error"):
            st.error = st.final or "failed"
    return []


PARSERS: dict[str, Callable[[dict[str, Any], ParseState], list[str]]] = {
    "claude": parse_claude,
    "codex": parse_codex,
    "cursor-agent": parse_cursor,
}


def build_command(
    cli: CLI,
    binary: str,
    task: str,
    workdir: Path,
    *,
    model: str = "",
    max_budget_usd: float | None = None,
    allowed_tools: str = CLAUDE_TOOLS,
) -> list[str]:
    if cli == "claude":
        cmd = [
            binary,
            "-p",
            task,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--no-session-persistence",
            "--allowedTools",
            *shlex.split(allowed_tools),
        ]
        if max_budget_usd:
            cmd += ["--max-budget-usd", f"{max_budget_usd:g}"]
    elif cli == "codex":
        cmd = [
            binary,
            "exec",
            "--json",
            "--cd",
            str(workdir),
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--ephemeral",
        ]
    else:
        cmd = [
            binary,
            "-p",
            "--output-format",
            "stream-json",
            "--force",
            "--trust",
            "--workspace",
            str(workdir),
        ]
    if model:
        cmd += ["--model", model]
    if cli != "claude":
        cmd.append(task)
    return cmd


class AgentCLIProvider:
    """Runs one task per call; yields Progress events and the agent's final report as text."""

    capabilities = {"task", "streaming"}
    local = False

    def __init__(
        self,
        id: str,
        cli: CLI,
        *,
        binary: str = "",
        model: str = "",
        workspace: Path,
        timeout_s: float = 1800,
        max_budget_usd: float | None = 5.0,
        allowed_tools: str = CLAUDE_TOOLS,
        env: Callable[[], dict[str, str]] = dict,
    ) -> None:
        self.id, self.cli, self.model = id, cli, model
        self.binary = binary or DEFAULT_BIN[cli]
        self.workspace = workspace
        self.timeout_s, self.max_budget_usd, self.allowed_tools = (
            timeout_s,
            max_budget_usd,
            allowed_tools,
        )
        self._env = env

    @property
    def display(self) -> str:
        return DISPLAY[self.cli]

    def task_from(self, messages: list[Message]) -> str:
        task = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return task.strip()

    async def run(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        stream: bool = True,
        workdir: Path | None = None,
    ) -> AsyncIterator[AgentEvent]:
        task = self.task_from(messages)
        if not task:
            raise ProviderError(f"{self.id}: empty task")
        wd = workdir or self.workspace / time.strftime("%Y%m%d-%H%M%S")
        wd.mkdir(parents=True, exist_ok=True)
        cmd = build_command(
            self.cli,
            self.binary,
            task,
            wd,
            model=self.model,
            max_budget_usd=self.max_budget_usd,
            allowed_tools=self.allowed_tools,
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "HOME", "LANG", "TZ", "TERM", "USER")
        }
        env.update(self._env())
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=wd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=8 * 1024 * 1024,
            )
        except FileNotFoundError as e:
            raise ProviderError(f"{self.id}: {self.binary} is not installed") from e
        parser, st = PARSERS[self.cli], ParseState()
        deadline = time.monotonic() + self.timeout_s
        stderr_task = asyncio.create_task(proc.stderr.read()) if proc.stderr else None  # type: ignore[union-attr]
        t0 = time.monotonic()
        try:
            assert proc.stdout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError(f"{self.id}: timed out after {self.timeout_s / 60:.0f} min")
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                except TimeoutError as e:
                    raise ProviderError(
                        f"{self.id}: timed out after {self.timeout_s / 60:.0f} min"
                    ) from e
                if not line:
                    break
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict):
                    for msg in parser(ev, st):
                        yield Progress(f"{self.display} is {msg}")
            rc = await proc.wait()
        except BaseException:
            _kill(proc)
            raise
        finally:
            if stderr_task:
                stderr_task.cancel()
        err_text = ""
        if stderr_task and stderr_task.done() and not stderr_task.cancelled():
            err_text = (stderr_task.result() or b"").decode(errors="replace")[-400:]
        if st.error or (rc != 0 and not st.final):
            raise ProviderError(f"{self.id}: {st.error or err_text.strip() or f'exit code {rc}'}")
        final = (
            st.final or (st.texts[-1] if st.texts else "") or "Finished, with nothing to report."
        )
        yield TextDelta(final)
        yield Completed(
            Message("assistant", final),
            "end_turn",
            st.input_tokens,
            st.output_tokens,
            st.cost_usd,
            self.model or self.cli,
        )
        log.info(
            "%s finished in %.0fs (cost $%.3f) in %s",
            self.id,
            time.monotonic() - t0,
            st.cost_usd,
            wd,
        )

    async def health(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return await asyncio.wait_for(proc.wait(), 10) == 0
        except (FileNotFoundError, TimeoutError, PermissionError):
            return False


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()

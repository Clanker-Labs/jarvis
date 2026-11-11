import asyncio
import os
import sys
from pathlib import Path

import pytest
from jarvis_brain.agent.tasks import TaskRunner, make_task_tools
from jarvis_brain.bus import EventBus
from jarvis_brain.providers.agent_cli import AgentCLIProvider, build_command
from jarvis_brain.providers.base import Completed, Message, Progress, ProviderError, TextDelta

from .test_router_agent import FakeProvider, collect, make_agent

FAKE = Path(__file__).parent / "fixtures" / "fake_agent_cli.py"
TASK = "fix the failing test"


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """An executable that runs the fake CLI with the current interpreter."""
    b = tmp_path / "fakecli"
    b.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    b.chmod(0o755)
    return str(b)


def provider(kind, fake_bin, tmp_path, monkeypatch, **kw):
    monkeypatch.setenv(
        "FAKE_CLI_KIND", {"claude-code": "claude", "codex": "codex", "cursor": "cursor"}[kind]
    )
    cli = {"claude-code": "claude", "codex": "codex", "cursor": "cursor-agent"}[kind]
    env = (
        (lambda: {"CURSOR_API_KEY": "ck-test", "FAKE_CLI_KIND": os.environ["FAKE_CLI_KIND"]})
        if kind == "cursor"
        else (lambda: {k: v for k, v in os.environ.items() if k.startswith("FAKE_CLI")})
    )
    return AgentCLIProvider(kind, cli, binary=fake_bin, workspace=tmp_path / "work", env=env, **kw)


async def run_all(p, workdir=None):
    progress, text, done = [], "", None
    async for ev in p.run([Message("user", TASK)], [], workdir=workdir):
        if isinstance(ev, Progress):
            progress.append(ev.message)
        elif isinstance(ev, TextDelta):
            text += ev.text
        elif isinstance(ev, Completed):
            done = ev
    return progress, text, done


@pytest.mark.parametrize(
    "kind,expect_progress,expect_text",
    [
        (
            "claude-code",
            [
                "Claude Code is reading the code",
                "Claude Code is editing app.py",
                "Claude Code is running pytest -q",
            ],
            "All 12 tests pass",
        ),
        ("codex", ["Codex is running pytest -q", "Codex is changed app.py"], "tests green"),
        ("cursor", ["Cursor is editing app.py"], "Rewrote app.py"),
    ],
)
async def test_cli_streams_progress_and_result(
    kind, expect_progress, expect_text, fake_bin, tmp_path, monkeypatch
):
    p = provider(kind, fake_bin, tmp_path, monkeypatch)
    progress, text, done = await run_all(p, workdir=tmp_path / "job")
    assert progress == expect_progress
    assert expect_text in text and done is not None
    if kind == "claude-code":
        assert (
            done.cost_usd == 0.42 and (tmp_path / "job" / "fixed.txt").exists()
        )  # ran in its own dir


async def test_cli_failure_raises(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_FAIL", "1")
    p = provider("claude-code", fake_bin, tmp_path, monkeypatch)
    with pytest.raises(ProviderError, match="ran out of turns"):
        await run_all(p)


async def test_cli_missing_binary(tmp_path):
    p = AgentCLIProvider("x", "codex", binary=str(tmp_path / "nope"), workspace=tmp_path)
    with pytest.raises(ProviderError, match="not installed"):
        await run_all(p)


async def test_cli_timeout_kills_process(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_SLEEP", "30")
    p = provider("claude-code", fake_bin, tmp_path, monkeypatch, timeout_s=1)
    with pytest.raises(ProviderError, match="timed out"):
        await run_all(p)


def test_build_command_shapes(tmp_path):
    c = build_command("claude", "claude", "t", tmp_path, model="claude-opus-5-5", max_budget_usd=2)
    assert c[:3] == ["claude", "-p", "t"] and c[-2:] == ["--model", "claude-opus-5-5"]
    x = build_command("codex", "codex", "t", tmp_path)
    assert x[:3] == ["codex", "exec", "--json"] and x[-1] == "t"
    k = build_command("cursor-agent", "agent", "t", tmp_path)
    assert k[0] == "agent" and "--workspace" in k and k[-1] == "t"


async def test_runner_announces_summary_and_can_cancel(fake_bin, tmp_path, monkeypatch):
    said = []

    async def announce(text):
        said.append(text)

    runner = TaskRunner(EventBus(), announce)
    job = runner.start(provider("claude-code", fake_bin, tmp_path, monkeypatch), TASK)
    await job.handle
    assert job.status == "done" and said == [
        "Claude Code has finished. Fixed the off-by-one in app.py. All 12 tests pass."
    ]

    monkeypatch.setenv("FAKE_CLI_SLEEP", "30")
    slow = runner.start(provider("claude-code", fake_bin, tmp_path, monkeypatch), TASK)
    await asyncio.sleep(0.5)
    assert runner.cancel(slow.id)
    with pytest.raises(asyncio.CancelledError):
        await slow.handle
    assert slow.status == "cancelled"


async def test_voice_task_flow_confirm_then_background(fake_bin, tmp_path, monkeypatch):
    """'Go fix the failing test' → read-back (no LLM) → yes → background job → announcement."""
    said = []

    async def announce(text):
        said.append(text)

    cli = provider("claude-code", fake_bin, tmp_path, monkeypatch)
    llm = FakeProvider("llm", [])
    agent, router = make_agent(
        {"claude-code": cli, "llm": llm},
        routes={"chat": ["llm"], "tools": ["llm"], "task": ["claude-code", "llm"]},
    )
    runner = TaskRunner(EventBus(), announce)
    for t in make_task_tools(runner, router):
        agent.tools.register(t)

    text, _, ctx = await collect(agent, TASK)
    assert (
        text == f"Just to confirm, I'm about to hand this to Claude Code: {TASK}. Shall I go ahead?"
    )
    assert not llm.calls and not runner.jobs  # nothing started, no model call
    text, _, _ = await collect(agent, "yes go ahead")
    assert text == "Claude Code is on it. I'll let you know when it's done."
    job = next(iter(runner.jobs.values()))
    await job.handle
    assert said and said[0].startswith("Claude Code has finished.")


async def test_task_declined(fake_bin, tmp_path, monkeypatch):
    cli = provider("claude-code", fake_bin, tmp_path, monkeypatch)
    agent, router = make_agent(
        {"claude-code": cli}, routes={"chat": [], "tools": [], "task": ["claude-code"]}
    )
    runner = TaskRunner(EventBus(), lambda t: asyncio.sleep(0))
    for t in make_task_tools(runner, router):
        agent.tools.register(t)
    await collect(agent, TASK)
    text, _, _ = await collect(agent, "no")
    assert text == "Okay, I won't." and not runner.jobs

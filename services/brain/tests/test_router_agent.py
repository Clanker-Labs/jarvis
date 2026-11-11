from dataclasses import dataclass, field

import pytest
from jarvis_brain.agent.loop import ALL_FAILED, NO_LLM, OFFLINE_NOTE, Agent
from jarvis_brain.agent.memory import ShortTermMemory
from jarvis_brain.bus import EventBus
from jarvis_brain.config import ConfigStore
from jarvis_brain.db import Database
from jarvis_brain.orchestrator import Turn
from jarvis_brain.providers.anthropic_provider import AnthropicProvider
from jarvis_brain.providers.base import (
    Completed,
    Message,
    ProviderError,
    TextDelta,
    ToolCall,
    ToolCallRequested,
)
from jarvis_brain.providers.openai_compat import ThinkFilter, parse_json_tool_call
from jarvis_brain.providers.router import ProvidersConfig, Router, classify
from jarvis_brain.secrets import SecretStore
from jarvis_brain.settings import Settings
from jarvis_brain.status import StatusTracker
from jarvis_brain.tools.registry import Tool, ToolRegistry
from jarvis_protocol import State
from pydantic import BaseModel

# --------------------------------------------------------------------------- fakes


@dataclass
class FakeProvider:
    id: str
    script: list  # list of steps; each step = list of events or an Exception
    local: bool = False
    model: str = "fake-1"
    capabilities: set = field(default_factory=lambda: {"tools", "streaming"})
    calls: list = field(default_factory=list)

    async def run(self, messages, tools, *, stream=True):
        self.calls.append(list(messages))
        step = self.script.pop(0)
        for ev in step:
            if isinstance(ev, Exception):
                raise ev
            yield ev

    async def health(self):
        return True


def text_step(text, calls=()):
    evs = [TextDelta(text)] if text else []
    evs += [ToolCallRequested(c) for c in calls]
    evs.append(
        Completed(
            Message("assistant", text, tool_calls=list(calls)),
            "tool_use" if calls else "end_turn",
            10,
            5,
            0.001,
            "fake-1",
        )
    )
    return evs


class Ctx:
    def __init__(self):
        self.states, self.events = [], []

    def set_state(self, s, reason=""):
        self.states.append(s)

    def publish(self, ev):
        self.events.append(ev)


class EchoArgs(BaseModel):
    text: str


def make_agent(providers: dict, routes=None):
    db = Database(":memory:")
    cfg = ConfigStore(db)
    cfg.update(
        "providers",
        {
            "providers": [],
            "routes": routes
            or {"chat": list(providers), "tools": list(providers), "task": list(providers)},
        },
    )
    router = Router(cfg, SecretStore(db, ""), Settings(JARVIS_DATA_DIR="/tmp/x"))
    router.providers = providers
    tools = ToolRegistry()

    async def echo(args, ctx):
        return {"echo": args.text}

    tools.register(Tool("echo", "Echo text", echo, params=EchoArgs, label="Echo"))
    agent = Agent(router, tools, cfg, StatusTracker(EventBus(), cfg))
    return agent, router


async def collect(agent, text="hello"):
    turn = Turn(id="t1", user_text=text)
    ctx = Ctx()
    out = [d async for d in agent.respond(turn, ctx)]
    return "".join(out), turn, ctx


# --------------------------------------------------------------------------- router


def test_classify_routes():
    cfg = ProvidersConfig()
    assert classify("how are you today", cfg) == "chat"
    assert classify("what's the weather like tomorrow", cfg) == "tools"
    assert classify("Read my email from Sam", cfg) == "tools"
    assert classify("go fix the failing build in my repo", cfg) == "task"
    assert classify("I'd like a notebook", cfg) == "chat"  # word boundaries: not "note"


def test_chain_orders_healthy_first(tmp_path):
    a, b, c = FakeProvider("a", []), FakeProvider("b", []), FakeProvider("c", [])
    _, router = make_agent({"a": a, "b": b, "c": c})
    router.health = {"a": False, "b": True}
    assert [p.id for p in router.chain("chat")] == ["b", "c", "a"]


def test_default_routes_prefer_local_for_chat_and_strong_for_tools():
    cfg = ProvidersConfig()
    assert cfg.routes["chat"][0] == "local"
    assert cfg.routes["tools"][0] == "claude-strong"
    models = {p.id: p.model for p in cfg.providers}
    assert models["claude-fast"] == "claude-haiku-5-5"
    assert models["claude-strong"] == "claude-opus-5-5"


# --------------------------------------------------------------------------- agent loop


async def test_plain_reply_and_memory():
    p = FakeProvider("p", [text_step("Good evening.")])
    agent, _ = make_agent({"p": p})
    text, turn, _ = await collect(agent)
    assert text == "Good evening."
    assert turn.provider == "p" and turn.input_tokens == 10
    assert agent.memory.turns == [("hello", "Good evening.")]
    # Next turn sees the history as plain text.
    p.script.append(text_step("Again."))
    await collect(agent, "again")
    roles = [m.role for m in p.calls[-1]]
    assert roles == ["system", "user", "assistant", "user", "system"]


async def test_tool_loop_runs_tool_and_reports_states():
    call = ToolCall("c1", "echo", {"text": "hi"})
    p = FakeProvider("p", [text_step("", [call]), text_step("It said hi.")])
    agent, _ = make_agent({"p": p})
    text, turn, ctx = await collect(agent)
    assert text == "It said hi."
    assert ctx.states == [State.TOOL, State.THINKING]
    tool_msg = p.calls[1][-1]
    assert tool_msg.role == "tool" and '"echo": "hi"' in tool_msg.content and not tool_msg.is_error
    assert turn.tool_calls[0]["ok"] is True
    kinds = [e.type for e in ctx.events]
    assert "agent.tool_call" in kinds and "agent.tool_result" in kinds


async def test_invalid_tool_args_are_reported_not_run():
    call = ToolCall("c1", "echo", {"wrong": 1})
    p = FakeProvider("p", [text_step("", [call]), text_step("Sorry.")])
    agent, _ = make_agent({"p": p})
    await collect(agent)
    tool_msg = p.calls[1][-1]
    assert tool_msg.is_error and "Invalid arguments" in tool_msg.content


async def test_failover_before_any_output():
    bad = FakeProvider("bad", [[ProviderError("down")]])
    good = FakeProvider("good", [text_step("Here.")])
    agent, router = make_agent({"bad": bad, "good": good})
    text, turn, _ = await collect(agent)
    assert text == "Here." and turn.provider == "good"
    assert router.health["bad"] is False


async def test_no_failover_after_speaking():
    bad = FakeProvider("bad", [[TextDelta("Well, "), ProviderError("cut off")]])
    good = FakeProvider("good", [text_step("never used")])
    agent, _ = make_agent({"bad": bad, "good": good})
    text, _, _ = await collect(agent)
    assert text == "Well, " and not good.calls


async def test_all_providers_fail():
    agent, _ = make_agent({"a": FakeProvider("a", [[ProviderError("x")]])})
    text, _, _ = await collect(agent)
    assert text == ALL_FAILED


async def test_no_providers_configured():
    agent, _ = make_agent({})
    text, _, _ = await collect(agent)
    assert text == NO_LLM


async def test_offline_note_when_falling_back_to_local():
    cloud = FakeProvider("cloud", [[ProviderError("no internet")], [ProviderError("still")]])
    local = FakeProvider("local", [text_step("It's ten."), text_step("Eleven.")], local=True)
    agent, _ = make_agent({"cloud": cloud, "local": local})
    text, _, _ = await collect(agent)
    assert text == OFFLINE_NOTE + "It's ten."
    text2, _, _ = await collect(agent)
    assert text2 == "Eleven."  # said once per degraded period


async def test_max_steps():
    call = ToolCall("c", "echo", {"text": "x"})
    p = FakeProvider("p", [text_step("", [call]) for _ in range(3)])
    agent, _ = make_agent({"p": p})
    agent.config.update("agent", {"max_steps": 3})
    text, _, _ = await collect(agent)
    assert "more steps" in text


# --------------------------------------------------------------------------- providers


def test_anthropic_mapping_system_context_and_tool_results():
    p = AnthropicProvider("a", "claude-opus-5-5", lambda: "k")
    msgs = [
        Message("system", "persona"),
        Message("user", "hi"),
        Message("system", "Now: Tuesday"),
        Message("assistant", "", tool_calls=[ToolCall("t1", "x", {}), ToolCall("t2", "y", {})]),
        Message("tool", "r1", tool_call_id="t1"),
        Message("tool", "r2", tool_call_id="t2", is_error=True),
    ]
    system, out = p._convert(msgs)
    assert system == "persona"
    assert [m["role"] for m in out] == ["user", "system", "assistant", "user"]
    results = out[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and results[1]["is_error"]


def test_anthropic_mapping_older_model_folds_context():
    p = AnthropicProvider("a", "claude-sonnet-4-6", lambda: "k")
    _, out = p._convert(
        [Message("system", "persona"), Message("user", "hi"), Message("system", "ctx")]
    )
    assert out[-1]["role"] == "user" and "<context>" in out[-1]["content"]


def test_anthropic_replays_raw_only_for_same_provider():
    raw = [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "a"}]
    m = Message("assistant", "a", raw=raw, raw_provider="a")
    _, out = AnthropicProvider("a", "claude-opus-5-5", lambda: "k")._convert(
        [Message("user", "q"), m]
    )
    assert out[-1]["content"] is raw
    _, out = AnthropicProvider("b", "claude-opus-5-5", lambda: "k")._convert(
        [Message("user", "q"), m]
    )
    assert out[-1]["content"] == [{"type": "text", "text": "a"}]


def test_json_tool_call_parsing():
    known = {"weather"}
    c = parse_json_tool_call('```json\n{"tool": "weather", "arguments": {"days": 2}}\n```', known)
    assert c and c.name == "weather" and c.args == {"days": 2}
    assert parse_json_tool_call("It's sunny.", known) is None
    for bad in (
        '{"tool": "nope", "arguments": {}}',
        '{"tool": "weather", "arguments": 3}',
        "{oops",
    ):
        with pytest.raises(ValueError):
            parse_json_tool_call(bad, known)


def test_think_filter_across_chunks():
    f = ThinkFilter()
    out = "".join(f.push(c) for c in ["Hel", "lo <thi", "nk>secret</th", "ink> world"]) + f.flush()
    assert out == "Hello  world"


def test_memory_window_and_expiry():
    m = ShortTermMemory(window=4, session_timeout_s=60)
    for i in range(6):
        m.add(f"u{i}", f"a{i}")
    assert len(m.turns) <= 4 and m.summary  # overflow folded into a summary
    m.last_active -= 120
    assert m.messages() == [] and m.summary == ""

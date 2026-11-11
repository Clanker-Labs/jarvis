import pytest
from jarvis_brain.agent.confirm import DECLINED, classify_answer
from jarvis_brain.providers.base import ToolCall
from jarvis_brain.tools.registry import Tool
from jarvis_protocol import SideEffect
from pydantic import BaseModel

from .test_router_agent import FakeProvider, collect, make_agent, text_step


class EmailArgs(BaseModel):
    to: str
    body: str


def add_send_email(agent, sent: list):
    async def send(args, ctx):
        sent.append(args.model_dump())
        return {"sent": True}

    agent.tools.register(
        Tool(
            "send_email",
            "Send an email",
            send,
            params=EmailArgs,
            side_effect=SideEffect.IRREVERSIBLE,
            label="Email",
            describe=lambda a: f'send an email to {a["to"]} saying "{a["body"]}"',
        )
    )


CALL = ToolCall("c1", "send_email", {"to": "Sam", "body": "running late"})


@pytest.mark.parametrize(
    "text,expected",
    [
        ("yes", "yes"),
        ("Yes please.", "yes"),
        ("yeah go ahead", "yes"),
        ("Do it, Jarvis", "yes"),
        ("send it", "yes"),
        ("okay", "yes"),
        ("no", "no"),
        ("No thanks.", "no"),
        ("cancel", "no"),
        ("don't", "no"),
        ("hold on", "no"),
        ("actually send it to Bob instead", "other"),
        ("yes but change the subject", "other"),
        ("no I meant tomorrow", "other"),
        ("", "other"),
    ],
)
def test_classify_answer(text, expected):
    assert classify_answer(text) == expected


async def test_irreversible_tool_waits_for_yes_then_runs():
    sent = []
    p = FakeProvider("p", [text_step("", [CALL]), text_step("Sent.")])
    agent, _ = make_agent({"p": p})
    add_send_email(agent, sent)

    text, _, ctx = await collect(agent, "email Sam that I'm running late")
    assert sent == []  # nothing happened yet
    assert 'send an email to Sam saying "running late"' in text and text.endswith(
        "Shall I go ahead?"
    )
    assert any(e.type == "agent.confirm_request" for e in ctx.events)
    assert agent.pending is not None

    text, turn, _ = await collect(agent, "yes")
    assert sent == [{"to": "Sam", "body": "running late"}]
    assert text == "Sent."
    assert agent.pending is None
    # The model saw the tool result appended after its own tool call (append-only history).
    last_msgs = p.calls[-1]
    assert last_msgs[-1].role == "tool" and last_msgs[-2].tool_calls[0].id == "c1"
    assert turn.tool_calls[0]["tool"] == "send_email"


async def test_no_declines_without_calling_the_model():
    sent = []
    p = FakeProvider("p", [text_step("", [CALL])])
    agent, _ = make_agent({"p": p})
    add_send_email(agent, sent)
    await collect(agent, "email Sam")
    text, _, ctx = await collect(agent, "no thanks")
    assert text == DECLINED and sent == [] and len(p.calls) == 1
    assert any(e.type == "agent.tool_result" and not e.ok for e in ctx.events)


async def test_other_answer_cancels_and_is_a_new_request():
    sent = []
    p = FakeProvider("p", [text_step("", [CALL]), text_step("Who should it go to?")])
    agent, _ = make_agent({"p": p})
    add_send_email(agent, sent)
    await collect(agent, "email Sam")
    text, _, _ = await collect(agent, "actually send it to Bob instead")
    assert sent == [] and agent.pending is None
    assert text == "Who should it go to?"
    assert p.calls[-1][-2].content == "actually send it to Bob instead"  # fresh turn


async def test_expired_confirmation_never_executes():
    sent = []
    p = FakeProvider("p", [text_step("", [CALL]), text_step("Hm?")])
    agent, _ = make_agent({"p": p})
    add_send_email(agent, sent)
    await collect(agent, "email Sam")
    agent.pending.created -= 120
    await collect(agent, "yes")
    assert sent == []


async def test_reversible_and_safe_tools_run_immediately_before_the_irreversible_one():
    sent, notes = [], []

    class NoteArgs(BaseModel):
        text: str

    async def note(args, ctx):
        notes.append(args.text)
        return "ok"

    p = FakeProvider(
        "p", [text_step("", [ToolCall("n1", "note", {"text": "x"}), CALL]), text_step("Done.")]
    )
    agent, _ = make_agent({"p": p})
    add_send_email(agent, sent)
    agent.tools.register(
        Tool("note", "note", note, params=NoteArgs, side_effect=SideEffect.REVERSIBLE)
    )
    await collect(agent, "note x and email Sam")
    assert notes == ["x"] and sent == []
    await collect(agent, "yes")
    assert sent and notes == ["x"]


def test_override_cannot_lower_side_effect():
    from jarvis_brain.tools.registry import ToolRegistry

    async def h(a, c):
        return None

    reg = ToolRegistry()
    reg.register(Tool("danger", "d", h, side_effect=SideEffect.IRREVERSIBLE))
    reg.overrides["danger"] = {"side_effect": "none"}
    assert reg.get("danger").side_effect == SideEffect.IRREVERSIBLE
    reg.register(Tool("safe", "s", h))
    reg.overrides["safe"] = {"side_effect": "irreversible"}
    assert reg.get("safe").side_effect == SideEffect.IRREVERSIBLE


async def test_unconfirmed_direct_execution_is_blocked():
    sent = []
    agent, _ = make_agent({"p": FakeProvider("p", [])})
    add_send_email(agent, sent)
    from jarvis_brain.orchestrator import Turn
    from jarvis_brain.tools.registry import ToolContext

    from .test_router_agent import Ctx

    outcome = await agent._run_tool(CALL, Turn(id="t"), ToolContext(), Ctx())
    assert not outcome.ok and sent == []

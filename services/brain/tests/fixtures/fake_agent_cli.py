"""Stands in for claude / codex / agent in tests: checks the verified flags, writes a file in its
working directory, and prints an event stream shaped like the real CLI's."""

import json
import os
import sys
import time

kind = os.environ["FAKE_CLI_KIND"]
args = sys.argv[1:]
emit = lambda e: print(json.dumps(e), flush=True)  # noqa: E731

if os.environ.get("FAKE_CLI_SLEEP"):
    emit({"type": "system", "subtype": "init"})
    time.sleep(float(os.environ["FAKE_CLI_SLEEP"]))

if kind == "claude":
    assert args[:2] == ["-p", "fix the failing test"], args
    for flag in (
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "--max-budget-usd",
        "--no-session-persistence",
    ):
        assert flag in args, flag
    with open("fixed.txt", "w") as f:
        f.write("ok")
    emit({"type": "system", "subtype": "init", "cwd": os.getcwd()})
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "app.py"}}]
            },
        }
    )
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": "/w/app.py"}}
                ]
            },
        }
    )
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]
            },
        }
    )
    if os.environ.get("FAKE_CLI_FAIL"):
        emit(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": True,
                "result": "ran out of turns",
            }
        )
        sys.exit(1)
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "total_cost_usd": 0.42,
            "result": "Fixed the off-by-one in **app.py**. All 12 tests pass.",
            "usage": {"input_tokens": 900, "output_tokens": 120},
        }
    )
elif kind == "codex":
    assert (
        args[0] == "exec"
        and "--json" in args
        and args[args.index("--sandbox") + 1] == "workspace-write"
    )
    assert "--skip-git-repo-check" in args and "--cd" in args and args[-1] == "fix the failing test"
    emit({"type": "thread.started", "thread_id": "t1"})
    emit({"type": "turn.started"})
    emit(
        {
            "type": "item.started",
            "item": {
                "id": "i1",
                "type": "command_execution",
                "command": "pytest -q",
                "status": "in_progress",
            },
        }
    )
    emit(
        {
            "type": "item.completed",
            "item": {
                "id": "i2",
                "type": "file_change",
                "changes": [{"path": "src/app.py", "kind": "update"}],
                "status": "completed",
            },
        }
    )
    emit(
        {
            "type": "item.completed",
            "item": {"id": "i3", "type": "agent_message", "text": "Patched app.py; tests green."},
        }
    )
    emit(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 500, "cached_input_tokens": 0, "output_tokens": 80},
        }
    )
else:
    assert (
        args[:3] == ["-p", "--output-format", "stream-json"]
        and "--force" in args
        and "--trust" in args
    )
    assert "--workspace" in args and args[-1] == "fix the failing test"
    assert os.environ.get("CURSOR_API_KEY") == "ck-test"
    emit({"type": "system", "subtype": "init", "model": "x"})
    emit(
        {
            "type": "tool_call",
            "subtype": "started",
            "call_id": "c1",
            "tool_call": {"writeToolCall": {"args": {"path": "src/app.py"}}},
        }
    )
    emit(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 900,
            "result": "Rewrote app.py.",
        }
    )

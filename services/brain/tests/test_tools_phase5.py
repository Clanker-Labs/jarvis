import asyncio
import io
import sys
import zipfile
from pathlib import Path

import pytest
from jarvis_brain.config import ConfigStore
from jarvis_brain.db import Database
from jarvis_brain.mcp.manager import MCPManager, MCPServerConfig, default_side_effect, wire_name
from jarvis_brain.providers.base import ToolCall
from jarvis_brain.secrets import SecretStore
from jarvis_brain.skills_runtime.manager import SkillManager
from jarvis_brain.tools.builtin.files import make_file_tools, resolve_inside
from jarvis_brain.tools.builtin.memory import MemoryStore, make_memory_tools
from jarvis_brain.tools.builtin.notes import make_notes_tools
from jarvis_brain.tools.builtin.timers import Scheduler, make_timer_tools, parse_when
from jarvis_brain.tools.builtin.web import BlockedURL, check_url
from jarvis_brain.tools.registry import Tool, ToolContext, ToolRegistry
from jarvis_protocol import SideEffect

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def db():
    return Database(":memory:")


def reg_with(*tools):
    r = ToolRegistry()
    for t in tools:
        r.register(t)
    return r


async def run(reg, name, args):
    return await reg.execute(ToolCall("c", name, args), ToolContext())


# --------------------------------------------------------------------------- registry


async def test_schema_tools_are_validated():
    async def h(a, c):
        return a

    reg = reg_with(
        Tool(
            "x",
            "x",
            h,
            schema={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
        )
    )
    assert (await run(reg, "x", {"n": 3})).ok
    bad = await run(reg, "x", {"n": "three"})
    assert not bad.ok and "Invalid arguments" in bad.content


# --------------------------------------------------------------------------- notes, memory


async def test_notes_add_search_delete(db):
    reg = reg_with(*make_notes_tools(db))
    await run(reg, "notes_add", {"text": "Maldives trip: book the seaplane"})
    await run(reg, "notes_add", {"text": "Buy milk"})
    found = await run(reg, "notes_search", {"query": "maldives seaplane"})
    assert "seaplane" in found.content and "milk" not in found.content
    assert reg.get("notes_delete").side_effect == SideEffect.IRREVERSIBLE
    assert "Buy milk" in reg.get("notes_delete").readback({"note_id": 2})
    # FTS syntax in user text must not break the query
    assert (await run(reg, "notes_search", {"query": 'AND "( NEAR'})).ok


async def test_memory_dedupes_and_recalls(db):
    store = MemoryStore(db)
    reg = reg_with(*make_memory_tools(store))
    await run(reg, "memory_remember", {"fact": "Prefers Earl Grey tea"})
    await run(reg, "memory_remember", {"fact": "prefers earl grey tea"})
    assert len(store.all()) == 1
    assert "Earl Grey" in (await run(reg, "memory_recall", {"query": "tea"})).content


# --------------------------------------------------------------------------- timers


async def test_timer_fires_and_announces(db):
    said = []

    async def announce(text, row):
        said.append(text)

    sched = Scheduler(db, announce)
    reg = reg_with(*make_timer_tools(sched))
    await sched.start()
    try:
        out = await run(reg, "timer_set", {"seconds": 1, "label": "pasta"})
        assert out.ok and "1 second" in out.content
        async with asyncio.timeout(4):
            while not said:
                await asyncio.sleep(0.05)
        assert said == ["Your pasta timer is done."]
        assert sched.pending() == []
    finally:
        await sched.stop()


async def test_reminder_validation(db):
    sched = Scheduler(db, lambda t, r: None)
    reg = reg_with(*make_timer_tools(sched))
    assert not (await run(reg, "reminder_set", {"text": "x"})).ok  # neither at nor in_minutes
    ok = await run(reg, "reminder_set", {"text": "call mum", "in_minutes": 30})
    assert ok.ok and sched.pending()[0]["label"] == "call mum"


def test_parse_when_rolls_to_tomorrow():
    from datetime import datetime

    now = datetime(2026, 11, 12, 22, 0).astimezone()
    assert parse_when("07:30", now).day == 13
    assert parse_when("23:15", now).day == 12


# --------------------------------------------------------------------------- web, files


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9000/api/secrets",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://100.115.124.66:8000/",
        "http://[::1]/",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data",
    ],
)
async def test_read_url_blocks_private_targets(url):
    with pytest.raises(BlockedURL):
        await check_url(url, allow_private=False)


async def test_read_url_allows_public_ip():
    await check_url("https://1.1.1.1/", allow_private=False)


async def test_read_file_stays_inside_folder(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "trip.md").write_text("# Maldives\nSeaplane at 9.")
    (tmp_path.parent / "secret.txt").write_text("nope")
    reg = reg_with(*make_file_tools(lambda: tmp_path))
    assert "Seaplane" in (await run(reg, "read_file", {"path": "docs/trip.md"})).content
    assert "outside" in (await run(reg, "read_file", {"path": "../secret.txt"})).content
    with pytest.raises(PermissionError):
        resolve_inside(tmp_path, "/../../etc/passwd")


# --------------------------------------------------------------------------- skills


SKILL_MD = """---
name: dice
description: Roll dice for games.
triggers: [roll a die, dice]
---
Run the roll script with the number of sides.
"""


def make_skill(root: Path, manifest: bool):
    d = root / "dice"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD)
    (d / "scripts" / "roll.py").write_text("import sys; print('rolled', sys.argv[1])")
    if manifest:
        (d / "manifest.yaml").write_text(
            "id: dice\nname: Dice\ntype: skill\ntools:\n  - {name: roll, side_effect: none, description: Roll a die}\n"
        )


def skill_mgr(tmp_path, db):
    reg = ToolRegistry()
    cfg = ConfigStore(db)
    m = SkillManager(tmp_path / "skills", tmp_path / "runs", cfg, SecretStore(db, ""), reg)
    return m, reg, cfg


async def test_skill_catalog_and_scripts(tmp_path, db):
    m, reg, cfg = skill_mgr(tmp_path, db)
    make_skill(tmp_path / "skills", manifest=False)
    m.reload()
    load = reg.get("skill_load")
    assert (
        "dice: Roll dice for games." in load.description and "Run the roll" not in load.description
    )
    script = reg.get("skill__dice__roll")
    assert script.side_effect == SideEffect.IRREVERSIBLE  # undeclared → needs confirmation
    out = await run(reg, "skill_load", {"name": "dice"})
    assert "Run the roll script" in out.content
    res = await m.run_script(m.skills["dice"], m.skills["dice"].scripts[0], ["20"])
    assert res["ok"] and "rolled 20" in res["stdout"]
    # manifest lowers it explicitly; disabling removes the tools
    (tmp_path / "skills" / "dice" / "manifest.yaml").write_text(
        "id: dice\nname: Dice\ntype: skill\ntools:\n  - {name: roll, side_effect: none}\n"
    )
    m.reload()
    assert reg.get("skill__dice__roll").side_effect == SideEffect.NONE
    cfg.update("skills", {"disabled": ["dice"]})
    assert reg.get("skill__dice__roll") is None and reg.get("skill_load") is None


def test_skill_errors_are_reported(tmp_path, db):
    m, reg, _ = skill_mgr(tmp_path, db)
    bad = tmp_path / "skills" / "broken"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("no frontmatter")
    m.reload()
    assert "frontmatter" in m.describe()[0]["error"]


def test_skill_zip_install_and_zip_slip(tmp_path, db):
    m, reg, _ = skill_mgr(tmp_path, db)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("dice/SKILL.md", SKILL_MD)
        z.writestr("dice/scripts/roll.py", "print(1)")
    assert m.install_zip(buf.getvalue()) == "dice"
    assert reg.get("skill__dice__roll")
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("SKILL.md", SKILL_MD)
        z.writestr("../../escape.txt", "x")
    with pytest.raises(ValueError):
        m.install_zip(evil.getvalue())
    assert not (tmp_path / "escape.txt").exists()


# --------------------------------------------------------------------------- MCP


def test_mcp_names_and_default_side_effects():
    from mcp_types import ToolAnnotations

    assert wire_name("github", "create.issue") == "mcp__github__create_issue"
    assert default_side_effect(None) == SideEffect.IRREVERSIBLE
    assert default_side_effect(ToolAnnotations(read_only_hint=True)) == SideEffect.NONE
    assert default_side_effect(ToolAnnotations(destructive_hint=False)) == SideEffect.REVERSIBLE


async def test_mcp_stdio_server_end_to_end(db):
    cfg = ConfigStore(db)
    reg = ToolRegistry()
    mgr = MCPManager(cfg, SecretStore(db, ""), reg)
    server = {
        "name": "echo",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(FIX / "mcp_echo_server.py")],
    }
    test = await mgr.test(MCPServerConfig(**server))
    assert test["ok"] and {t["name"] for t in test["tools"]} == {"shout", "wipe_everything"}
    cfg.update("mcp", {"servers": [server]})
    await mgr.sync()
    try:
        async with asyncio.timeout(20):
            while reg.get("mcp__echo__shout") is None:
                await asyncio.sleep(0.1)
        assert reg.get("mcp__echo__shout").side_effect == SideEffect.NONE
        assert reg.get("mcp__echo__wipe_everything").side_effect == SideEffect.IRREVERSIBLE
        out = await run(reg, "mcp__echo__shout", {"text": "hello"})
        assert out.ok and "HELLO" in out.content
        # Hot reload: restricting allowed tools removes the other one without a restart.
        cfg.update("mcp", {"servers": [{**server, "allowed_tools": ["shout"]}]})
        await mgr.sync()
        async with asyncio.timeout(20):
            while reg.get("mcp__echo__shout") is None:
                await asyncio.sleep(0.1)
        assert reg.get("mcp__echo__wipe_everything") is None
    finally:
        await mgr.stop()
    assert reg.get("mcp__echo__shout") is None

"""Skills: folders in ``skills/<name>/`` with a ``SKILL.md`` (YAML frontmatter: name, description,
triggers) and optional ``scripts/`` + ``manifest.yaml``.

Only names/descriptions/trigger hints go into the prompt (inside the ``skill_load`` tool's
description); the agent loads a skill's full instructions when it decides to use it. Each script
becomes its own tool (``skill__<skill>__<script>``) so it carries its own side-effect level from the
manifest — undeclared scripts are treated as irreversible (confirmation required).
Edits on disk (or from admin) are picked up live via a file watcher.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jarvis_protocol import SideEffect
from pydantic import BaseModel, Field, ValidationError

from ..config import ConfigStore, Section
from ..manifest import Manifest
from ..secrets import SecretStore
from ..tools.registry import Tool, ToolContext, ToolRegistry

log = logging.getLogger(__name__)

PREFIX = "skill__"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
MAX_ZIP = 5_000_000
SCRIPT_TYPES = {".py": [sys.executable, "-I"], ".sh": ["bash"], ".js": ["node"]}


class SkillsConfig(Section):
    disabled: list[str] = []
    script_timeout_s: int = Field(60, ge=1, le=600)


ConfigStore.register("skills", SkillsConfig)


class Frontmatter(BaseModel):
    name: str
    description: str = Field(min_length=1, max_length=1024)
    triggers: list[str] = []


@dataclass
class Skill:
    name: str
    path: Path
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    body: str = ""
    manifest: Manifest | None = None
    scripts: list[Path] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def parse_skill(folder: Path) -> Skill:
    skill = Skill(name=folder.name, path=folder)
    md = folder / "SKILL.md"
    if not md.is_file():
        skill.error = "missing SKILL.md"
        return skill
    m = _FRONT.match(md.read_text(errors="replace"))
    if not m:
        skill.error = "SKILL.md needs YAML frontmatter (--- name/description ---)"
        return skill
    try:
        fm = Frontmatter.model_validate(yaml.safe_load(m.group(1)) or {})
    except (yaml.YAMLError, ValidationError) as e:
        skill.error = f"bad frontmatter: {e}"
        return skill
    if fm.name != folder.name:
        skill.error = f"frontmatter name {fm.name!r} must match folder name {folder.name!r}"
        return skill
    skill.description, skill.triggers, skill.body = fm.description, fm.triggers, m.group(2).strip()
    mf = folder / "manifest.yaml"
    if mf.is_file():
        try:
            skill.manifest = Manifest.model_validate(yaml.safe_load(mf.read_text()) or {})
        except (yaml.YAMLError, ValidationError) as e:
            skill.error = f"bad manifest.yaml: {e}"
            return skill
    sd = folder / "scripts"
    if sd.is_dir():
        skill.scripts = sorted(p for p in sd.iterdir() if p.is_file() and p.suffix in SCRIPT_TYPES)
    return skill


class ScriptArgs(BaseModel):
    args: list[str] = Field(
        default_factory=list, max_length=20, description="Command-line arguments for the script."
    )


class LoadArgs(BaseModel):
    name: str


class ReadArgs(BaseModel):
    name: str
    path: str = Field(description="File path inside the skill folder, e.g. 'reference.md'.")


class SkillManager:
    def __init__(
        self,
        root: Path,
        work_dir: Path,
        config: ConfigStore,
        secrets: SecretStore,
        registry: ToolRegistry,
    ) -> None:
        self.root = root
        self.work_dir = work_dir
        self.config = config
        self.secrets = secrets
        self.registry = registry
        self.skills: dict[str, Skill] = {}
        self._watch: asyncio.Task[None] | None = None
        config.on_change(lambda name, _v: name == "skills" and self.reload())

    @property
    def cfg(self) -> SkillsConfig:
        return self.config.get("skills")

    # ------------------------------------------------------------------ loading

    def reload(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        skills = {}
        for folder in sorted(self.root.iterdir()):
            if folder.is_dir() and NAME_RE.match(folder.name):
                skills[folder.name] = parse_skill(folder)
        self.skills = skills
        self._register()
        bad = [s.name for s in skills.values() if s.error]
        log.info(
            "skills: %d loaded%s", len(skills), f" ({len(bad)} with errors: {bad})" if bad else ""
        )

    def active(self) -> list[Skill]:
        disabled = set(self.cfg.disabled)
        return [s for s in self.skills.values() if s.ok and s.name not in disabled]

    def _register(self) -> None:
        self.registry.unregister_prefix(PREFIX)
        self.registry.unregister_prefix("skill_load")
        self.registry.unregister_prefix("skill_read_file")
        active = self.active()
        if not active:
            return
        catalog = "\n".join(
            f"- {s.name}: {s.description}"
            + (f" (use for: {', '.join(s.triggers)})" if s.triggers else "")
            for s in active
        )
        self.registry.register(
            Tool(
                "skill_load",
                "Load a skill's full instructions before doing a task it covers. Available skills:\n"
                + catalog,
                self._load,
                params=LoadArgs,
                source="skill",
                label="Skill",
                icon="sparkles",
            )
        )
        self.registry.register(
            Tool(
                "skill_read_file",
                "Read an extra file that a loaded skill's instructions refer to.",
                self._read_file,
                params=ReadArgs,
                source="skill",
                label="Skill",
                icon="sparkles",
            )
        )
        for s in active:
            for script in s.scripts:
                self.registry.register(self._script_tool(s, script))

    def _script_tool(self, skill: Skill, script: Path) -> Tool:
        decl = skill.manifest.tool(script.stem) if skill.manifest else None
        level = decl.side_effect if decl else SideEffect.IRREVERSIBLE
        desc = (
            decl.description
            if decl and decl.description
            else f"Run {script.name} from the {skill.name} skill."
        )
        name = f"{PREFIX}{skill.name}__{re.sub(r'[^a-zA-Z0-9_-]', '_', script.stem)}"[:64]

        async def run(a: ScriptArgs, _c: ToolContext) -> dict[str, Any]:
            return await self.run_script(skill, script, a.args)

        return Tool(
            name,
            desc,
            run,
            params=ScriptArgs,
            side_effect=level,
            source="skill",
            label=f"{skill.name}: {script.stem}",
            icon="sparkles",
            timeout_s=self.cfg.script_timeout_s + 5,
            describe=lambda args, s=script: (
                f"run the {skill.name} script {s.stem}"
                + (f" with {' '.join(args.get('args') or [])}" if args.get("args") else "")
            ),
        )

    async def _load(self, a: LoadArgs, _c: ToolContext) -> dict[str, Any]:
        s = next((x for x in self.active() if x.name == a.name), None)
        if s is None:
            return {"error": f"no active skill named {a.name!r}"}
        files = sorted(
            str(p.relative_to(s.path))
            for p in s.path.rglob("*")
            if p.is_file() and p.name not in ("SKILL.md", "manifest.yaml")
        )
        tools = [t.name for t in self.registry.all() if t.name.startswith(f"{PREFIX}{s.name}__")]
        return {"name": s.name, "instructions": s.body, "files": files, "script_tools": tools}

    async def _read_file(self, a: ReadArgs, _c: ToolContext) -> dict[str, Any]:
        s = self.skills.get(a.name)
        if s is None or not s.ok:
            return {"error": f"no skill named {a.name!r}"}
        target = safe_join(s.path, a.path)
        if target is None or not target.is_file():
            return {"error": "no such file in the skill"}
        return {"path": a.path, "text": target.read_text(errors="replace")[:12000]}

    async def run_script(self, skill: Skill, script: Path, args: list[str]) -> dict[str, Any]:
        work = self.work_dir / skill.name
        work.mkdir(parents=True, exist_ok=True)
        env = {k: os.environ[k] for k in ("PATH", "LANG", "TZ") if k in os.environ}
        env["HOME"] = str(work)
        env["SKILL_DIR"] = str(skill.path.resolve())
        for secret in skill.manifest.required_secrets if skill.manifest else []:
            env[secret] = self.secrets.get(secret)
        proc = await asyncio.create_subprocess_exec(
            *SCRIPT_TYPES[script.suffix],
            str(script.resolve()),  # noqa: ASYNC240 — one stat, not worth a thread hop
            *args,
            cwd=work,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.cfg.script_timeout_s)
        except TimeoutError:
            proc.kill()
            return {"ok": False, "error": f"timed out after {self.cfg.script_timeout_s}s"}
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": out.decode(errors="replace")[-8000:],
            "stderr": err.decode(errors="replace")[-2000:],
        }

    # ------------------------------------------------------------------ admin operations

    def describe(self) -> list[dict[str, Any]]:
        disabled = set(self.cfg.disabled)
        return [
            {
                "name": s.name,
                "description": s.description,
                "triggers": s.triggers,
                "enabled": s.name not in disabled,
                "error": s.error,
                "scripts": [p.name for p in s.scripts],
                "files": sorted(
                    str(p.relative_to(s.path)) for p in s.path.rglob("*") if p.is_file()
                ),
                "manifest": s.manifest.model_dump(mode="json") if s.manifest else None,
            }
            for s in self.skills.values()
        ]

    def read_file(self, name: str, path: str) -> str:
        target = self._skill_path(name, path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return target.read_text(errors="replace")

    def write_file(self, name: str, path: str, content: str) -> None:
        if not NAME_RE.match(name):
            raise ValueError("skill names are lowercase letters, digits and dashes")
        target = self._skill_path(name, path, create=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.reload()

    def delete(self, name: str) -> None:
        folder = self._skill_path(name, ".")
        if folder.is_dir():
            shutil.rmtree(folder)
        self.reload()

    def install_zip(self, data: bytes) -> str:
        """Install a skill from a zip (either the folder itself, or its contents)."""
        if len(data) > MAX_ZIP:
            raise ValueError("zip is too large (5 MB max)")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            md = [n for n in names if n.endswith("SKILL.md")]
            if len(md) != 1:
                raise ValueError("the zip must contain exactly one SKILL.md")
            prefix = md[0][: -len("SKILL.md")]
            m = _FRONT.match(z.read(md[0]).decode(errors="replace"))
            meta = yaml.safe_load(m.group(1)) if m else None
            name = (meta or {}).get("name", "")
            if not NAME_RE.match(str(name)):
                raise ValueError("SKILL.md frontmatter needs a valid 'name'")
            dest = (self.root / name).resolve()
            if not dest.is_relative_to(self.root.resolve()):
                raise ValueError("invalid skill name")
            staging = self.root / f".{name}.installing"
            shutil.rmtree(staging, ignore_errors=True)
            total = 0
            for n in names:
                if not n.startswith(prefix):
                    continue
                rel = n[len(prefix) :]
                target = safe_join(staging, rel, create=True)
                if target is None:
                    raise ValueError(f"unsafe path in zip: {n}")  # zip-slip
                info = z.getinfo(n)
                total += info.file_size
                if total > 4 * MAX_ZIP:
                    raise ValueError("zip expands too large")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(n))
        shutil.rmtree(dest, ignore_errors=True)
        staging.rename(dest)
        self.reload()
        return name

    def _skill_path(self, name: str, path: str, *, create: bool = False) -> Path:
        if not NAME_RE.match(name):
            raise ValueError("bad skill name")
        target = safe_join(self.root / name, path, create=create)
        if target is None:
            raise PermissionError("path escapes the skill folder")
        return target

    # ------------------------------------------------------------------ watcher

    async def start(self) -> None:
        self.reload()
        self._watch = asyncio.create_task(self._watch_loop())

    async def _watch_loop(self) -> None:
        from watchfiles import awatch

        try:
            async for _changes in awatch(self.root, debounce=500, recursive=True):
                self.reload()
        except Exception as e:  # watcher is a convenience; admin edits reload explicitly
            log.warning("skills watcher stopped: %s", e)

    async def stop(self) -> None:
        if self._watch:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch


def safe_join(root: Path, rel: str, *, create: bool = False) -> Path | None:
    root = root.resolve()
    target = (root / rel.lstrip("/")).resolve()
    if target != root and not target.is_relative_to(root):
        return None
    return target

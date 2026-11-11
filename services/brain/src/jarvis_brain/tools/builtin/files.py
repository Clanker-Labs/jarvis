"""read_file / list_files: read-only access to one allowed folder (`tools.files_dir`)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..registry import Tool, ToolContext
from .web import MAX_CHARS

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".log",
    ".ini",
    ".toml",
    ".html",
    ".htm",
    ".xml",
    ".rst",
}


class ReadArgs(BaseModel):
    path: str = Field(
        min_length=1, max_length=500, description="Path relative to the files folder."
    )


class ListArgs(BaseModel):
    folder: str = Field("", max_length=500, description="Sub-folder, relative; empty for the root.")


def resolve_inside(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``; refuse anything that escapes it (.., absolute, symlinks)."""
    root = root.resolve()
    target = (root / rel.lstrip("/")).resolve()
    if not target.is_relative_to(root):
        raise PermissionError("path is outside the allowed folder")
    return target


def read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages[:60])
    if suffix == ".docx":
        import zipfile

        from lxml import etree

        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml")
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        root = etree.fromstring(xml, parser)
        return "\n".join(
            "".join(t.text or "" for t in p.iterfind(".//w:t", ns))
            for p in root.iterfind(".//w:p", ns)
        )
    if suffix in TEXT_SUFFIXES or not suffix:
        return path.read_text(errors="replace")
    raise ValueError(f"can't read {suffix} files")


def make_file_tools(files_dir: Callable[[], Path]) -> list[Tool]:
    async def read_file(a: ReadArgs, _c: ToolContext) -> dict[str, Any]:
        try:
            p = resolve_inside(files_dir(), a.path)
        except PermissionError as e:
            return {"error": str(e)}
        if not p.is_file():
            return {"error": f"no such file: {a.path}"}
        if p.stat().st_size > 20_000_000:
            return {"error": "file is too large"}
        try:
            text = await asyncio.to_thread(read_text, p)
        except ValueError as e:
            return {"error": str(e)}
        return {"path": a.path, "text": text[:MAX_CHARS], "truncated": len(text) > MAX_CHARS}

    async def list_files(a: ListArgs, _c: ToolContext) -> dict[str, Any]:
        root = files_dir()
        try:
            d = resolve_inside(root, a.folder)
        except PermissionError as e:
            return {"error": str(e)}
        if not d.is_dir():
            return {"error": "no such folder"}
        entries = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:200]
        return {
            "folder": a.folder or "/",
            "entries": [
                {
                    "name": e.name + ("/" if e.is_dir() else ""),
                    "size": e.stat().st_size if e.is_file() else None,
                }
                for e in entries
            ],
        }

    return [
        Tool(
            "read_file",
            "Read a document from the user's files folder (txt, md, pdf, docx…).",
            read_file,
            params=ReadArgs,
            label="Reading file",
            icon="file",
        ),
        Tool(
            "list_files",
            "List documents in the user's files folder.",
            list_files,
            params=ListArgs,
            label="Files",
            icon="folder",
        ),
    ]

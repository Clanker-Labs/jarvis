"""Built-in notes: add / list / search / delete (SQLite + FTS5)."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from jarvis_protocol import SideEffect
from pydantic import BaseModel, Field

from ...db import Database
from ..registry import Tool, ToolContext
from ._util import fts_query


class AddArgs(BaseModel):
    text: str = Field(min_length=1, max_length=4000, description="The note, in the user's words.")


class ListArgs(BaseModel):
    limit: int = Field(5, ge=1, le=50)


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(5, ge=1, le=20)


class DeleteArgs(BaseModel):
    note_id: int


def _fmt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "text": row["text"],
        "created": datetime.fromtimestamp(row["created"]).strftime("%a %-d %b %H:%M"),
    }


def make_notes_tools(db: Database) -> list[Tool]:
    async def add(a: AddArgs, _c: ToolContext) -> dict[str, Any]:
        cur = db.execute(
            "INSERT INTO notes(text, created) VALUES(?,?)", (a.text.strip(), time.time())
        )
        return {"saved": True, "id": cur.lastrowid}

    async def list_(a: ListArgs, _c: ToolContext) -> list[dict[str, Any]]:
        return [
            _fmt(r)
            for r in db.query("SELECT * FROM notes ORDER BY created DESC LIMIT ?", (a.limit,))
        ]

    async def search(a: SearchArgs, _c: ToolContext) -> list[dict[str, Any]]:
        rows = db.query(
            "SELECT n.* FROM notes_fts f JOIN notes n ON n.id = f.rowid WHERE notes_fts MATCH ? "
            "ORDER BY bm25(notes_fts) LIMIT ?",
            (fts_query(a.query), a.limit),
        )
        return [_fmt(r) for r in rows]

    async def delete(a: DeleteArgs, _c: ToolContext) -> dict[str, Any]:
        cur = db.execute("DELETE FROM notes WHERE id=?", (a.note_id,))
        return {"deleted": cur.rowcount > 0}

    def describe_delete(args: dict[str, Any]) -> str:
        row = db.one("SELECT text FROM notes WHERE id=?", (args.get("note_id"),))
        return f'delete your note "{row["text"][:80]}"' if row else "delete that note"

    return [
        Tool(
            "notes_add",
            "Save a note for the user.",
            add,
            params=AddArgs,
            side_effect=SideEffect.REVERSIBLE,
            label="Note",
            icon="note",
        ),
        Tool(
            "notes_list",
            "List the user's most recent notes.",
            list_,
            params=ListArgs,
            label="Notes",
            icon="note",
        ),
        Tool(
            "notes_search",
            "Search the user's notes by keywords.",
            search,
            params=SearchArgs,
            label="Notes",
            icon="note",
        ),
        Tool(
            "notes_delete",
            "Delete one of the user's notes by id.",
            delete,
            params=DeleteArgs,
            side_effect=SideEffect.IRREVERSIBLE,
            label="Delete note",
            icon="note",
            describe=describe_delete,
        ),
    ]

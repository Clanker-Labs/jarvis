"""Long-term memory: facts about the user that Jarvis learns (separate from documents).

Viewable/deletable in admin → Memory. Recall is keyword (FTS5) based; phase 7 adds embeddings.
"""

from __future__ import annotations

import time
from typing import Any

from jarvis_protocol import SideEffect
from pydantic import BaseModel, Field

from ...db import Database
from ..registry import Tool, ToolContext
from ._util import fts_query


class RememberArgs(BaseModel):
    fact: str = Field(
        min_length=3,
        max_length=500,
        description="One self-contained fact, e.g. 'Prefers tea to coffee'.",
    )


class RecallArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(5, ge=1, le=20)


class MemoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, fact: str, source: str = "conversation") -> int:
        now = time.time()
        existing = self.db.one(
            "SELECT id FROM memories WHERE lower(fact)=lower(?)", (fact.strip(),)
        )
        if existing:
            self.db.execute("UPDATE memories SET updated=? WHERE id=?", (now, existing["id"]))
            return int(existing["id"])
        cur = self.db.execute(
            "INSERT INTO memories(fact, source, created, updated) VALUES(?,?,?,?)",
            (fact.strip(), source, now, now),
        )
        return int(cur.lastrowid or 0)

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT m.id, m.fact, m.created FROM memories_fts f JOIN memories m ON m.id = f.rowid "
            "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
            (fts_query(query), limit),
        )

    def all(self) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT id, fact, source, created, updated FROM memories ORDER BY updated DESC"
        )

    def update(self, mem_id: int, fact: str) -> None:
        self.db.execute(
            "UPDATE memories SET fact=?, updated=?, embedding=NULL WHERE id=?",
            (fact.strip(), time.time(), mem_id),
        )

    def delete(self, mem_id: int) -> bool:
        return self.db.execute("DELETE FROM memories WHERE id=?", (mem_id,)).rowcount > 0


def make_memory_tools(store: MemoryStore) -> list[Tool]:
    async def remember(a: RememberArgs, _c: ToolContext) -> dict[str, Any]:
        return {"remembered": True, "id": store.add(a.fact)}

    async def recall(a: RecallArgs, _c: ToolContext) -> list[dict[str, Any]]:
        return [{"fact": r["fact"]} for r in store.search(a.query, a.limit)]

    return [
        Tool(
            "memory_remember",
            "Remember a lasting fact about the user (preferences, people, routines). "
            "Use when the user tells you something worth knowing next time.",
            remember,
            params=RememberArgs,
            side_effect=SideEffect.REVERSIBLE,
            label="Remembering",
            icon="brain",
        ),
        Tool(
            "memory_recall",
            "Recall facts you've learned about the user.",
            recall,
            params=RecallArgs,
            label="Memory",
            icon="brain",
        ),
    ]

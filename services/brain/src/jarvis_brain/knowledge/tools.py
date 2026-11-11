"""Knowledge tools exposed to the agent: kb_search and kb_graph_query (with citations)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..tools.registry import Tool, ToolContext
from .base import KnowledgeBase


class SearchArgs(BaseModel):
    query: str = Field(
        min_length=2, max_length=300, description="What to look for in the user's documents."
    )
    k: int = Field(5, ge=1, le=10)


class GraphArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    mode: Literal["local", "global"] = Field(
        "local", description="local: an entity and its connections; global: big-picture themes."
    )


def make_kb_tools(kb: KnowledgeBase) -> list[Tool]:
    async def search(a: SearchArgs, _c: ToolContext) -> dict[str, Any]:
        hits = await kb.search(a.query, a.k)
        if not hits:
            return {"results": [], "note": "nothing relevant in the user's documents"}
        return {
            "results": [
                {"citation": h.citation(), "kind": h.kind, "text": h.text[:1500]} for h in hits
            ],
            "how_to_cite": "Say where it came from in one short phrase, e.g. 'from your notes on X'.",
        }

    async def graph(a: GraphArgs, _c: ToolContext) -> dict[str, Any]:
        return await kb.graph_query(a.query, a.mode)

    return [
        Tool(
            "kb_search",
            "Search the user's own documents, saved web pages and pinned emails. "
            "Returns passages with citations.",
            search,
            params=SearchArgs,
            label="Searching your documents",
            icon="book",
        ),
        Tool(
            "kb_graph_query",
            "Ask the user's knowledge graph how people, places and projects in their "
            "documents connect (local) or what the main themes are (global).",
            graph,
            params=GraphArgs,
            label="Knowledge graph",
            icon="share",
        ),
    ]

"""Admin API for the knowledge base."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import AdminAuth

if TYPE_CHECKING:
    from ..runtime import Runtime

MAX_UPLOAD = 50_000_000


class UrlBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class ThreadBody(BaseModel):
    thread_id: str = Field(min_length=4, max_length=64)


class QueryBody(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    k: int = Field(5, ge=1, le=20)
    mode: str = "local"


def build_knowledge_router(rt: Runtime, auth: AdminAuth) -> APIRouter:
    api = APIRouter(prefix="/api/knowledge", dependencies=[Depends(auth.require)])
    kb = rt.kb

    @api.get("")
    async def overview() -> dict[str, Any]:
        one = lambda sql: (rt.db.one(sql) or {"n": 0})["n"]  # noqa: E731
        vec = kb.index("chunks")
        emb = kb.embedder()
        return {
            "sources": kb.sources(),
            "stats": {
                "chunks": one("SELECT COUNT(*) AS n FROM kb_chunks"),
                "vectors": await vec.count() if vec else 0,
                "entities": one("SELECT COUNT(*) AS n FROM kb_entities"),
                "relations": one("SELECT COUNT(*) AS n FROM kb_relations"),
                "communities": one("SELECT COUNT(*) AS n FROM kb_communities"),
                "queue": kb.queue.qsize(),
            },
            "embedder": emb.id if emb else None,
            "vector_backend": kb.cfg.vector_backend,
            "watch_folder": kb.cfg.watch_folder,
        }

    @api.post("/upload")
    async def upload(request: Request, filename: str) -> dict[str, int]:
        data = await request.body()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "File too large (50 MB max).")
        try:
            return {"source_id": kb.add_file(filename, data)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @api.post("/url")
    async def add_url(body: UrlBody) -> dict[str, int]:
        try:
            return {"source_id": kb.add_url(body.url)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @api.post("/mail")
    async def pin_mail(body: ThreadBody) -> dict[str, int]:
        try:
            return {"source_id": kb.add_mail_thread(body.thread_id)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @api.get("/sources/{sid}/chunks")
    async def chunks(sid: int, limit: int = 200) -> list[dict[str, Any]]:
        return kb.chunks(sid, min(limit, 1000))

    @api.post("/sources/{sid}/reingest")
    async def reingest(sid: int) -> dict[str, bool]:
        rt.db.execute("UPDATE kb_sources SET status='queued', content_hash=NULL WHERE id=?", (sid,))
        kb.queue.put_nowait(sid)
        return {"ok": True}

    @api.delete("/sources/{sid}")
    async def delete(sid: int) -> dict[str, bool]:
        return {"ok": await kb.delete_source(sid)}

    @api.get("/graph")
    async def graph(limit: int = 300) -> dict[str, Any]:
        return kb.graph(min(limit, 1000))

    @api.post("/communities/rebuild")
    async def rebuild() -> dict[str, int]:
        return {"communities": await kb.rebuild_communities()}

    @api.post("/reindex")
    async def reindex() -> dict[str, int]:
        return {"chunks": await kb.reindex()}

    @api.post("/search")
    async def search(body: QueryBody) -> dict[str, Any]:
        hits = await kb.search(body.query, body.k)
        return {
            "hits": [
                {
                    "citation": h.citation(),
                    "score": round(h.score, 4),
                    "text": h.text,
                    "source_id": h.source_id,
                }
                for h in hits
            ],
            "graph": await kb.graph_query(body.query, body.mode),
        }

    return api

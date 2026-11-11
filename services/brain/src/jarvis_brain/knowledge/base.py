"""The knowledge base: ingestion, hybrid retrieval and a lightweight graph RAG.

- Sources (files, URLs, pinned mail threads) → chunks with page/section metadata.
- Retrieval: BM25 (SQLite FTS5) + vectors (sqlite-vec or Qdrant), fused with reciprocal rank
  fusion, optionally re-ranked by a cross-encoder.
- Graph RAG (LightRAG-style): the LLM extracts entities/relations per chunk; Louvain communities
  get short LLM summaries. Local queries walk an entity's neighbourhood; global queries rank the
  community summaries.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

from ..config import ConfigStore
from ..db import Database
from ..providers.base import Message, ProviderError, TextDelta
from ..providers.router import Router
from ..secrets import SecretStore
from ..tools.builtin._util import fts_query
from .chunking import Chunk, Segment, chunk_segments, extract_file
from .config import KnowledgeConfig
from .embeddings import Embedder, Reranker, build_embedder
from .vectors import QdrantIndex, SqliteVecIndex, VectorIndex

log = logging.getLogger(__name__)

EXTRACT_PROMPT = (
    "Extract the key entities and relationships from the text for a personal knowledge graph.\n"
    "Entities: people, places, organisations, projects, events, products, dates or other things that "
    "matter. Relationships: how two of those entities are connected.\n"
    'Reply with JSON only: {"entities": [{"name": str, "type": str, "description": str}], '
    '"relations": [{"source": str, "target": str, "label": str, "description": str}]}.\n'
    "At most 12 entities and 12 relations. Use the names exactly as written in the text."
)
COMMUNITY_PROMPT = (
    "These entities and relationships come from the user's documents. Write a title (max 6 words) "
    "and a 2-3 sentence summary of what this group is about. "
    'Reply with JSON only: {"title": str, "summary": str}.'
)
FILE_TYPES = {".pdf", ".md", ".markdown", ".txt", ".docx", ".html", ".htm", ".csv", ".json", ".rst"}


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())[:200]


def _json_obj(text: str) -> dict[str, Any] | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


@dataclass
class Hit:
    chunk_id: int
    source_id: int
    title: str
    uri: str
    kind: str
    text: str
    meta: dict[str, Any]
    score: float

    def citation(self) -> str:
        where = self.title or self.uri
        if page := self.meta.get("page"):
            where += f", page {page}"
        elif sec := self.meta.get("section"):
            where += f" — {sec}"
        return where


class KnowledgeBase:
    def __init__(
        self,
        db: Database,
        config: ConfigStore,
        secrets: SecretStore,
        router: Router,
        data_dir: Path,
        models_dir: Path,
    ) -> None:
        self.db, self.config, self.secrets, self.router = db, config, secrets, router
        self.files_dir = data_dir / "knowledge" / "files"
        self.models_dir = models_dir
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.mail_fetcher: Callable[[str], Awaitable[tuple[str, str]]] | None = None
        self._embedder: Embedder | None = None
        self._embedder_sig: tuple[Any, ...] = ()
        self._indexes: dict[str, VectorIndex] = {}
        self._reranker: Reranker | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.graph_lock = asyncio.Lock()
        config.on_change(lambda name, _v: name == "knowledge" and self._reset_backends())

    @property
    def cfg(self) -> KnowledgeConfig:
        return self.config.get("knowledge")

    # ------------------------------------------------------------------ backends

    def _reset_backends(self) -> None:
        self._embedder_sig = ()
        self._indexes.clear()
        self._reranker = None

    def embedder(self) -> Embedder | None:
        c = self.cfg
        sig = (
            c.embed_provider,
            c.embed_base_url,
            c.embed_model,
            c.local_embed_model,
            c.openai_embed_model,
            self.secrets.has("OPENAI_API_KEY"),
        )
        if sig != self._embedder_sig:
            self._embedder = build_embedder(c, self.secrets, self.models_dir)
            self._embedder_sig = sig
        return self._embedder

    def index(self, name: str) -> VectorIndex | None:
        if name not in self._indexes:
            try:
                self._indexes[name] = (
                    QdrantIndex(self.cfg.qdrant_url, name, self.db)
                    if self.cfg.vector_backend == "qdrant"
                    else SqliteVecIndex(self.db, name)
                )
            except RuntimeError as e:
                log.warning("vector index unavailable: %s", e)
                return None
        return self._indexes[name]

    async def _vectors(self, name: str, texts: list[str], *, query: bool = False):  # type: ignore[no-untyped-def]
        emb, idx = self.embedder(), self.index(name)
        if emb is None or idx is None or not texts:
            return None, None
        vecs = await emb.embed(texts, query=query)
        if await idx.ensure(vecs.shape[1], emb.id) and not query:
            log.warning("embedding model changed: re-index from admin → Knowledge")
        return idx, vecs

    # ------------------------------------------------------------------ sources

    def _upsert_source(
        self, kind: str, uri: str, title: str, meta: dict[str, Any] | None = None
    ) -> int:
        now = time.time()
        row = self.db.one("SELECT id FROM kb_sources WHERE uri=?", (uri,))
        if row:
            self.db.execute(
                "UPDATE kb_sources SET status='queued', error=NULL, updated=?, title=COALESCE(?, title)"
                " WHERE id=?",
                (now, title or None, row["id"]),
            )
            return int(row["id"])
        cur = self.db.execute(
            "INSERT INTO kb_sources(kind, uri, title, status, created, updated, meta) VALUES(?,?,?,?,?,?,?)",
            (kind, uri, title, "queued", now, now, json.dumps(meta or {})),
        )
        return int(cur.lastrowid or 0)

    def add_file(self, filename: str, data: bytes) -> int:
        safe = re.sub(r"[^\w.\- ]", "_", Path(filename).name)[:120] or "document"
        if Path(safe).suffix.lower() not in FILE_TYPES:
            raise ValueError(f"unsupported file type: {Path(safe).suffix}")
        self.files_dir.mkdir(parents=True, exist_ok=True)
        dest = self.files_dir / safe
        dest.write_bytes(data)
        sid = self._upsert_source("file", f"file://{dest.resolve()}", Path(safe).stem)
        self.queue.put_nowait(sid)
        return sid

    def add_path(self, path: Path) -> int:
        sid = self._upsert_source("file", f"file://{path.resolve()}", path.stem)
        self.queue.put_nowait(sid)
        return sid

    def add_url(self, url: str) -> int:
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL must start with http(s)://")
        sid = self._upsert_source("url", url, "")
        self.queue.put_nowait(sid)
        return sid

    def add_mail_thread(self, thread_id: str) -> int:
        if self.mail_fetcher is None:
            raise ValueError("connect Gmail first to pin mail threads")
        sid = self._upsert_source("mail", f"gmail:thread:{thread_id}", "")
        self.queue.put_nowait(sid)
        return sid

    async def delete_source(self, sid: int) -> bool:
        ids = [r["id"] for r in self.db.query("SELECT id FROM kb_chunks WHERE source_id=?", (sid,))]
        if (idx := self.index("chunks")) is not None:
            with contextlib.suppress(Exception):
                await idx.delete(ids)
        row = self.db.one("SELECT uri, kind FROM kb_sources WHERE id=?", (sid,))
        self.db.execute(
            "DELETE FROM kb_sources WHERE id=?", (sid,)
        )  # cascades chunks/mentions/relations
        self.db.execute(
            "DELETE FROM kb_entities WHERE id NOT IN (SELECT entity_id FROM kb_mentions)"
        )
        if row and row["kind"] == "file" and row["uri"].startswith("file://"):
            await asyncio.to_thread(self._remove_uploaded, Path(row["uri"][7:]))
        return row is not None

    def _remove_uploaded(self, p: Path) -> None:
        """Delete the stored copy of an uploaded file (never anything outside files_dir)."""
        if p.is_file() and p.resolve().is_relative_to(self.files_dir.resolve()):
            p.unlink()

    def sources(self) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT id, kind, uri, title, status, error, chunks, created, updated FROM kb_sources"
            " ORDER BY updated DESC"
        )

    def chunks(self, sid: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id, idx, text, meta FROM kb_chunks WHERE source_id=? ORDER BY idx LIMIT ?",
            (sid, limit),
        )
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return rows

    # ------------------------------------------------------------------ ingestion

    async def _extract(self, src: dict[str, Any]) -> tuple[str, list[Segment]]:
        kind, uri = src["kind"], src["uri"]
        if kind == "file":
            return await asyncio.to_thread(extract_file, Path(uri[7:]))
        if kind == "url":
            from ..tools.builtin.web import extract_text, fetch

            final, body, ctype = await fetch(uri, self.config.get("tools").url_allow_private)
            title, text = await asyncio.to_thread(extract_text, body, ctype, final)
            return title or final, [Segment(text)]
        if kind == "mail" and self.mail_fetcher:
            title, text = await self.mail_fetcher(uri.rsplit(":", 1)[-1])
            return title, [Segment(text)]
        raise ValueError(f"can't ingest {kind}")

    async def ingest(self, sid: int) -> None:
        src = self.db.one("SELECT * FROM kb_sources WHERE id=?", (sid,))
        if not src:
            return

        def status(s: str, error: str | None = None) -> None:
            self.db.execute(
                "UPDATE kb_sources SET status=?, error=?, updated=? WHERE id=?",
                (s, error, time.time(), sid),
            )

        try:
            status("extracting")
            title, segments = await self._extract(src)
            full = "\n".join(s.text for s in segments)
            digest = hashlib.sha256(full.encode()).hexdigest()
            if digest == src["content_hash"] and src["chunks"]:
                status("ready")
                return
            chunks = chunk_segments(segments, self.cfg.chunk_chars, self.cfg.chunk_overlap)
            if not chunks:
                raise ValueError("no text found")
            old = [
                r["id"] for r in self.db.query("SELECT id FROM kb_chunks WHERE source_id=?", (sid,))
            ]
            if old and (idx := self.index("chunks")):
                await idx.delete(old)
            with self.db.tx() as c:
                c.execute("DELETE FROM kb_chunks WHERE source_id=?", (sid,))
                ids = [
                    c.execute(
                        "INSERT INTO kb_chunks(source_id, idx, text, meta) VALUES(?,?,?,?)",
                        (sid, i, ch.text, json.dumps(ch.meta)),
                    ).lastrowid
                    for i, ch in enumerate(chunks)
                ]
                c.execute(
                    "UPDATE kb_sources SET title=?, content_hash=?, chunks=? WHERE id=?",
                    (title[:300] if title else src["title"], digest, len(chunks), sid),
                )
            status("embedding")
            await self._embed_chunks([int(i) for i in ids if i], [c.text for c in chunks])
            if self.cfg.graph_enabled:
                status("graph")
                await self._extract_graph([int(i) for i in ids if i], chunks)
            status("ready")
            log.info("ingested %s (%d chunks)", title or src["uri"], len(chunks))
        except Exception as e:
            log.warning("ingestion of %s failed: %s", src["uri"], e)
            status("error", str(e)[:500])

    async def _embed_chunks(self, ids: list[int], texts: list[str]) -> None:
        for i in range(0, len(ids), 64):
            idx, vecs = await self._vectors("chunks", texts[i : i + 64])
            if idx is not None:
                await idx.upsert(ids[i : i + 64], vecs)

    async def reindex(self) -> int:
        """Re-embed every chunk (after changing the embedding model)."""
        rows = self.db.query("SELECT id, text FROM kb_chunks ORDER BY id")
        await self._embed_chunks([r["id"] for r in rows], [r["text"] for r in rows])
        mems = self.db.query("SELECT id, fact FROM memories")
        if mems:
            idx, vecs = await self._vectors("memories", [m["fact"] for m in mems])
            if idx is not None:
                await idx.upsert([m["id"] for m in mems], vecs)
        return len(rows)

    async def _worker(self) -> None:
        while True:
            sid = await self.queue.get()
            await self.ingest(sid)

    # ------------------------------------------------------------------ LLM helpers

    async def _llm_json(self, system: str, user: str) -> dict[str, Any] | None:
        msgs = [Message("system", system), Message("user", user)]
        for provider in self.router.chain(self.cfg.graph_route):
            try:
                text = "".join(
                    [
                        ev.text
                        async for ev in provider.run(msgs, [], stream=True)
                        if isinstance(ev, TextDelta)
                    ]
                )
            except ProviderError:
                continue
            return _json_obj(text)
        return None

    # ------------------------------------------------------------------ graph

    async def _extract_graph(self, chunk_ids: list[int], chunks: list[Chunk]) -> None:
        async with self.graph_lock:
            for cid, ch in zip(chunk_ids, chunks, strict=True):
                data = await self._llm_json(EXTRACT_PROMPT, ch.text[:4000])
                if not data:
                    continue
                self._store_graph(cid, data)

    def _store_graph(self, chunk_id: int, data: dict[str, Any]) -> None:
        ents: dict[str, int] = {}
        with self.db.tx() as c:
            for e in (data.get("entities") or [])[:20]:
                if not isinstance(e, dict) or not str(e.get("name", "")).strip():
                    continue
                name, k = str(e["name"]).strip()[:200], _key(str(e["name"]))
                desc = str(e.get("description", ""))[:400]
                row = c.execute(
                    "SELECT id, description FROM kb_entities WHERE key=?", (k,)
                ).fetchone()
                if row:
                    eid = row[0]
                    if desc and desc not in (row[1] or ""):
                        c.execute(
                            "UPDATE kb_entities SET description=? WHERE id=?",
                            ((f"{row[1]} {desc}" if row[1] else desc)[:800], eid),
                        )
                else:
                    eid = c.execute(
                        "INSERT INTO kb_entities(name, key, type, description) VALUES(?,?,?,?)",
                        (name, k, str(e.get("type", ""))[:40], desc),
                    ).lastrowid
                ents[k] = int(eid)
                c.execute(
                    "INSERT OR IGNORE INTO kb_mentions(entity_id, chunk_id) VALUES(?,?)",
                    (eid, chunk_id),
                )
            for r in (data.get("relations") or [])[:20]:
                if not isinstance(r, dict):
                    continue
                s, t = (
                    ents.get(_key(str(r.get("source", "")))),
                    ents.get(_key(str(r.get("target", "")))),
                )
                if s and t and s != t:
                    c.execute(
                        "INSERT INTO kb_relations(src, dst, label, description, chunk_id) VALUES(?,?,?,?,?)",
                        (
                            s,
                            t,
                            str(r.get("label", "related to"))[:80],
                            str(r.get("description", ""))[:300],
                            chunk_id,
                        ),
                    )

    async def rebuild_communities(self, max_summaries: int = 30) -> int:
        """Louvain communities over the entity graph, each with a short LLM summary."""
        async with self.graph_lock:
            g = nx.Graph()
            for r in self.db.query(
                "SELECT src, dst, COUNT(*) AS w FROM kb_relations GROUP BY src, dst"
            ):
                g.add_edge(r["src"], r["dst"], weight=r["w"])
            if g.number_of_nodes() == 0:
                return 0
            comms = sorted(
                nx.community.louvain_communities(g, weight="weight", seed=42), key=len, reverse=True
            )
            self.db.execute("DELETE FROM kb_communities")
            self.db.execute("UPDATE kb_entities SET community=NULL")
            summaries: list[tuple[int, str]] = []
            for i, members in enumerate(comms):
                ids = list(members)
                ph = ",".join("?" * len(ids))
                self.db.execute(f"UPDATE kb_entities SET community=? WHERE id IN ({ph})", (i, *ids))
                title, summary = "", ""
                if len(ids) >= 3 and i < max_summaries:
                    ents = self.db.query(
                        f"SELECT name, type, description FROM kb_entities WHERE id IN ({ph}) LIMIT 25",
                        ids,
                    )
                    rels = self.db.query(
                        f"SELECT a.name AS s, r.label, b.name AS t FROM kb_relations r JOIN kb_entities a ON a.id=r.src"
                        f" JOIN kb_entities b ON b.id=r.dst WHERE r.src IN ({ph}) LIMIT 40",
                        ids,
                    )
                    listing = "\n".join(
                        f"- {e['name']} ({e['type']}): {e['description']}" for e in ents
                    )
                    listing += "\n" + "\n".join(f"- {r['s']} {r['label']} {r['t']}" for r in rels)
                    data = await self._llm_json(COMMUNITY_PROMPT, listing[:6000]) or {}
                    title, summary = (
                        str(data.get("title", ""))[:120],
                        str(data.get("summary", ""))[:1200],
                    )
                self.db.execute(
                    "INSERT INTO kb_communities(id, title, summary, size, updated) VALUES(?,?,?,?,?)",
                    (i, title, summary, len(ids), time.time()),
                )
                if summary:
                    summaries.append((i, f"{title}. {summary}"))
            if summaries:
                idx, vecs = await self._vectors("communities", [s for _, s in summaries])
                if idx is not None:
                    await idx.upsert([i for i, _ in summaries], vecs)
            return len(comms)

    def graph(self, limit: int = 300) -> dict[str, Any]:
        nodes = self.db.query(
            "SELECT e.id, e.name, e.type, e.community, COUNT(m.chunk_id) AS mentions FROM kb_entities e"
            " LEFT JOIN kb_mentions m ON m.entity_id = e.id GROUP BY e.id ORDER BY mentions DESC LIMIT ?",
            (limit,),
        )
        ids = {n["id"] for n in nodes}
        edges = [
            r
            for r in self.db.query("SELECT src, dst, label FROM kb_relations LIMIT 2000")
            if r["src"] in ids and r["dst"] in ids
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "communities": self.db.query(
                "SELECT id, title, summary, size FROM kb_communities ORDER BY size DESC"
            ),
        }

    # ------------------------------------------------------------------ retrieval

    def _hit(self, chunk_id: int, score: float) -> Hit | None:
        r = self.db.one(
            "SELECT c.id, c.source_id, c.text, c.meta, s.title, s.uri, s.kind FROM kb_chunks c"
            " JOIN kb_sources s ON s.id = c.source_id WHERE c.id=?",
            (chunk_id,),
        )
        if not r:
            return None
        return Hit(
            r["id"],
            r["source_id"],
            r["title"] or "",
            r["uri"],
            r["kind"],
            r["text"],
            json.loads(r["meta"] or "{}"),
            score,
        )

    async def search(self, query: str, k: int = 5) -> list[Hit]:
        n = max(20, k * 4)
        bm25 = [
            r["id"]
            for r in self.db.query(
                "SELECT rowid AS id FROM kb_chunks_fts WHERE kb_chunks_fts MATCH ? ORDER BY bm25(kb_chunks_fts) LIMIT ?",
                (fts_query(query), n),
            )
        ]
        vec: list[int] = []
        try:
            idx, qv = await self._vectors("chunks", [query], query=True)
            if idx is not None:
                vec = [i for i, _ in await idx.search(qv[0], n)]
        except Exception as e:
            log.info("vector search unavailable: %s", e)
        fused: dict[int, float] = {}
        for ranking in (bm25, vec):  # reciprocal rank fusion
            for rank, cid in enumerate(ranking):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (60 + rank)
        hits = [
            h
            for cid, s in sorted(fused.items(), key=lambda x: -x[1])[:n]
            if (h := self._hit(cid, s))
        ]
        if self.cfg.rerank == "local" and len(hits) > 1:
            try:
                if self._reranker is None:
                    self._reranker = Reranker(self.cfg.rerank_model, self.models_dir / "fastembed")
                top = hits[:8]
                scores = await self._reranker.scores(query, [h.text[:800] for h in top])
                for h, s in zip(top, scores, strict=True):
                    h.score = s
                hits = sorted(top, key=lambda h: -h.score) + hits[8:]
            except Exception as e:
                log.info("re-ranking skipped: %s", e)
        return hits[:k]

    async def graph_query(self, query: str, mode: str = "local", k: int = 8) -> dict[str, Any]:
        if mode == "global":
            out: list[dict[str, Any]] = []
            try:
                idx, qv = await self._vectors("communities", [query], query=True)
                ranked = await idx.search(qv[0], 3) if idx is not None else []
            except Exception:
                ranked = []
            ids = [i for i, _ in ranked] or [
                r["id"]
                for r in self.db.query(
                    "SELECT id FROM kb_communities WHERE summary != '' ORDER BY size DESC LIMIT 3"
                )
            ]
            for cid in ids:
                c = self.db.one(
                    "SELECT id, title, summary, size FROM kb_communities WHERE id=?", (cid,)
                )
                if c:
                    out.append(c)
            return {"mode": "global", "communities": out}
        seeds = [
            r["rowid"]
            for r in self.db.query(
                "SELECT rowid FROM kb_entities_fts WHERE kb_entities_fts MATCH ? ORDER BY bm25(kb_entities_fts) LIMIT 5",
                (fts_query(query),),
            )
        ]
        if not seeds:  # fall back to entities mentioned in the best matching chunks
            for h in await self.search(query, 3):
                seeds += [
                    r["entity_id"]
                    for r in self.db.query(
                        "SELECT entity_id FROM kb_mentions WHERE chunk_id=? LIMIT 5", (h.chunk_id,)
                    )
                ]
        seeds = list(dict.fromkeys(seeds))[:6]
        if not seeds:
            return {"mode": "local", "entities": [], "relations": [], "sources": []}
        ph = ",".join("?" * len(seeds))
        rels = self.db.query(
            f"SELECT a.name AS source, r.label, b.name AS target, r.description, r.chunk_id FROM kb_relations r"
            f" JOIN kb_entities a ON a.id=r.src JOIN kb_entities b ON b.id=r.dst"
            f" WHERE r.src IN ({ph}) OR r.dst IN ({ph}) LIMIT ?",
            (*seeds, *seeds, k * 3),
        )
        ents = self.db.query(
            f"SELECT name, type, description FROM kb_entities WHERE id IN ({ph})", seeds
        )
        chunk_ids = list(
            dict.fromkeys(
                [r["chunk_id"] for r in rels if r["chunk_id"]]
                + [
                    r["chunk_id"]
                    for r in self.db.query(
                        f"SELECT chunk_id FROM kb_mentions WHERE entity_id IN ({ph}) LIMIT 6", seeds
                    )
                ]
            )
        )[:4]
        sources = [
            {"citation": h.citation(), "text": h.text[:600]}
            for c in chunk_ids
            if (h := self._hit(c, 0))
        ]
        for r in rels:
            r.pop("chunk_id", None)
        return {"mode": "local", "entities": ents, "relations": rels, "sources": sources}

    # ------------------------------------------------------------------ memory vectors

    async def index_memory(self, mem_id: int, fact: str) -> None:
        idx, vecs = await self._vectors("memories", [fact])
        if idx is not None:
            await idx.upsert([mem_id], vecs)

    async def recall_memory(self, query: str, k: int = 5) -> list[int]:
        idx, qv = await self._vectors("memories", [query], query=True)
        if idx is None:
            return []
        return [i for i, s in await idx.search(qv[0], k) if s > 0.35]

    # ------------------------------------------------------------------ lifecycle

    async def _watch(self) -> None:
        from watchfiles import Change, awatch

        folder = Path(self.cfg.watch_folder)

        def initial_scan() -> list[Path]:
            folder.mkdir(parents=True, exist_ok=True)
            return [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in FILE_TYPES]

        for p in await asyncio.to_thread(initial_scan):
            self.add_path(p)
        try:
            async for changes in awatch(folder, debounce=1500):
                for change, path in changes:
                    p = Path(path)
                    if p.suffix.lower() not in FILE_TYPES:
                        continue
                    if change == Change.deleted:
                        row = self.db.one(
                            "SELECT id FROM kb_sources WHERE uri=?", (f"file://{p.resolve()}",)
                        )
                        if row:
                            await self.delete_source(row["id"])
                    elif p.is_file():
                        self.add_path(p)
        except Exception as e:
            log.warning("knowledge folder watch stopped: %s", e)

    async def warm(self) -> None:
        """Load the embedding model now so the first spoken question isn't slow."""
        if self.embedder() is not None:
            with contextlib.suppress(Exception):
                await self._vectors("chunks", ["warm up"], query=True)

    async def start(self, *, watch: bool = True) -> None:
        for r in self.db.query("SELECT id FROM kb_sources WHERE status NOT IN ('ready', 'error')"):
            self.queue.put_nowait(r["id"])  # resume interrupted jobs
        self._tasks.append(asyncio.create_task(self._worker()))
        if watch:
            self._tasks.append(asyncio.create_task(self._watch()))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t

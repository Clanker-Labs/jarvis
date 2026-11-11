"""Vector indexes: sqlite-vec (default, zero-ops, Pi-friendly) or Qdrant (brain host)."""

from __future__ import annotations

import logging
from typing import Protocol

import httpx
import numpy as np

from ..db import Database

log = logging.getLogger(__name__)


class VectorIndex(Protocol):
    async def ensure(self, dim: int, model_id: str) -> bool:
        """Prepare for vectors of ``dim`` from ``model_id``. True if existing vectors were dropped."""
        ...

    async def upsert(self, ids: list[int], vecs: np.ndarray) -> None: ...
    async def delete(self, ids: list[int]) -> None: ...
    async def search(self, vec: np.ndarray, k: int) -> list[tuple[int, float]]: ...
    async def count(self) -> int: ...


class SqliteVecIndex:
    def __init__(self, db: Database, name: str) -> None:
        if not db.load_sqlite_vec():
            raise RuntimeError("sqlite-vec extension not available")
        self.db, self.name = db, name
        self.table = f"vec_{name}"

    async def ensure(self, dim: int, model_id: str) -> bool:
        key = f"vec:{self.name}"
        current = self.db.kv_get(key)
        wanted = f"{model_id}|{dim}"
        exists = self.db.one("SELECT 1 FROM sqlite_master WHERE name=?", (self.table,)) is not None
        if current == wanted and exists:
            return False
        self.db.execute(f"DROP TABLE IF EXISTS {self.table}")
        self.db.execute(
            f"CREATE VIRTUAL TABLE {self.table} USING vec0(embedding float[{int(dim)}] distance_metric=cosine)"
        )
        self.db.kv_set(key, wanted)
        log.info("vector index %s reset for %s (dim %d)", self.name, model_id, dim)
        return current is not None

    async def upsert(self, ids: list[int], vecs: np.ndarray) -> None:
        with self.db.tx() as c:
            for i, v in zip(ids, vecs, strict=True):
                c.execute(f"DELETE FROM {self.table} WHERE rowid=?", (i,))
                c.execute(
                    f"INSERT INTO {self.table}(rowid, embedding) VALUES(?,?)",
                    (i, np.asarray(v, dtype=np.float32).tobytes()),
                )

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            return
        with self.db.tx() as c:
            c.executemany(f"DELETE FROM {self.table} WHERE rowid=?", [(i,) for i in ids])

    async def search(self, vec: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.db.one("SELECT 1 FROM sqlite_master WHERE name=?", (self.table,)) is None:
            return []
        rows = self.db.query(
            f"SELECT rowid, distance FROM {self.table} WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (np.asarray(vec, dtype=np.float32).tobytes(), k),
        )
        return [(int(r["rowid"]), 1.0 - float(r["distance"])) for r in rows]

    async def count(self) -> int:
        if self.db.one("SELECT 1 FROM sqlite_master WHERE name=?", (self.table,)) is None:
            return 0
        return int(self.db.one(f"SELECT COUNT(*) AS n FROM {self.table}")["n"])


class QdrantIndex:
    """Qdrant over REST (no client library)."""

    def __init__(self, url: str, name: str, db: Database) -> None:
        self.url, self.name, self.db = url.rstrip("/"), f"jarvis_{name}", db

    async def _req(self, method: str, path: str, **kw) -> dict:  # type: ignore[no-untyped-def]
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, f"{self.url}/collections/{self.name}{path}", **kw)
            if r.status_code == 404 and method == "GET":
                return {}
            r.raise_for_status()
            return r.json()

    async def ensure(self, dim: int, model_id: str) -> bool:
        key = f"qdrant:{self.name}"
        wanted = f"{model_id}|{dim}"
        current = self.db.kv_get(key)
        info = await self._req("GET", "")
        if current == wanted and info:
            return False
        if info:
            await self._req("DELETE", "")
        await self._req("PUT", "", json={"vectors": {"size": dim, "distance": "Cosine"}})
        self.db.kv_set(key, wanted)
        return current is not None

    async def upsert(self, ids: list[int], vecs: np.ndarray) -> None:
        points = [
            {"id": i, "vector": [float(x) for x in v]} for i, v in zip(ids, vecs, strict=True)
        ]
        await self._req("PUT", "/points?wait=true", json={"points": points})

    async def delete(self, ids: list[int]) -> None:
        if ids:
            await self._req("POST", "/points/delete?wait=true", json={"points": ids})

    async def search(self, vec: np.ndarray, k: int) -> list[tuple[int, float]]:
        res = await self._req(
            "POST", "/points/search", json={"vector": [float(x) for x in vec], "limit": k}
        )
        return [(int(p["id"]), float(p["score"])) for p in res.get("result", [])]

    async def count(self) -> int:
        info = await self._req("GET", "")
        return int(info.get("result", {}).get("points_count", 0) or 0)

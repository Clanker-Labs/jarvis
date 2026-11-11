"""Embedding adapters: remote OpenAI-compatible endpoint, in-process fastembed, OpenAI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np

from ..secrets import SecretStore
from .config import KnowledgeConfig

log = logging.getLogger(__name__)


class Embedder(Protocol):
    id: str  # "<provider>:<model>" — stored with vectors; a change triggers re-indexing

    async def embed(self, texts: list[str], *, query: bool = False) -> np.ndarray: ...


class OpenAICompatEmbedder:
    def __init__(
        self, base_url: str, model: str, api_key: str = "", provider: str = "remote"
    ) -> None:
        self.base_url, self.model, self.key = base_url.rstrip("/"), model, api_key
        self.id = f"{provider}:{model}"

    async def embed(self, texts: list[str], *, query: bool = False) -> np.ndarray:
        out: list[list[float]] = []
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        async with httpx.AsyncClient(timeout=60) as c:
            for i in range(0, len(texts), 64):
                r = await c.post(
                    f"{self.base_url}/embeddings",
                    headers=headers,
                    json={"model": self.model, "input": texts[i : i + 64]},
                )
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                out += [d["embedding"] for d in data]
        return _normalise(np.asarray(out, dtype=np.float32))


class FastEmbedEmbedder:
    def __init__(self, model: str, cache_dir: Path) -> None:
        self.model_name, self.cache_dir = model, cache_dir
        self.id = f"local:{model}"
        self._model = None
        self._lock = asyncio.Lock()

    def _load(self):  # type: ignore[no-untyped-def]
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir), threads=2)
        return self._model

    async def embed(self, texts: list[str], *, query: bool = False) -> np.ndarray:
        def run() -> np.ndarray:
            m = self._load()
            vecs = list(m.query_embed(texts) if query else m.embed(texts, batch_size=16))
            return np.asarray(vecs, dtype=np.float32)

        async with self._lock:
            return _normalise(await asyncio.to_thread(run))


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.where(n == 0, 1, n)


def build_embedder(cfg: KnowledgeConfig, secrets: SecretStore, models_dir: Path) -> Embedder | None:
    choice = cfg.embed_provider
    if choice in ("auto", "remote") and cfg.embed_base_url and cfg.embed_model:
        return OpenAICompatEmbedder(
            cfg.embed_base_url, cfg.embed_model, secrets.get("EMBED_API_KEY")
        )
    if choice in ("auto", "local"):
        try:
            import fastembed  # noqa: F401

            return FastEmbedEmbedder(cfg.local_embed_model, models_dir / "fastembed")
        except ImportError:
            if choice == "local":
                log.error("fastembed not installed (extra 'local-embeddings')")
    if choice in ("auto", "openai") and secrets.has("OPENAI_API_KEY"):
        return OpenAICompatEmbedder(
            "https://api.openai.com/v1",
            cfg.openai_embed_model,
            secrets.get("OPENAI_API_KEY"),
            provider="openai",
        )
    return None


class Reranker:
    """Cross-encoder re-ranking (fastembed ONNX). Optional; failures fall back to fused order."""

    def __init__(self, model: str, cache_dir: Path) -> None:
        self.model_name, self.cache_dir = model, cache_dir
        self._model = None
        self._lock = asyncio.Lock()

    async def scores(self, query: str, docs: list[str]) -> list[float]:
        def run() -> list[float]:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._model = TextCrossEncoder(
                    self.model_name, cache_dir=str(self.cache_dir), threads=2
                )
            return [float(s) for s in self._model.rerank(query, docs)]

        async with self._lock:
            return await asyncio.to_thread(run)


EmbedderFactory = Callable[[], Embedder | None]

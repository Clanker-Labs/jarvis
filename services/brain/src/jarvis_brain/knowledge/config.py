"""Knowledge base settings (admin → Knowledge)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..config import ConfigStore, Section


class KnowledgeConfig(Section):
    # Embeddings: "auto" = remote endpoint if set, else in-process fastembed, else OpenAI.
    embed_provider: Literal["auto", "remote", "local", "openai"] = "auto"
    embed_base_url: str = ""  # OpenAI-compatible /v1 (Ollama, TEI, vLLM, LiteLLM…) on the tailnet
    embed_model: str = ""
    local_embed_model: str = "BAAI/bge-small-en-v1.5"
    openai_embed_model: str = "text-embedding-3-small"
    vector_backend: Literal["sqlite", "qdrant"] = "sqlite"
    qdrant_url: str = "http://127.0.0.1:6333"
    chunk_chars: int = Field(1100, ge=300, le=6000)
    chunk_overlap: int = Field(180, ge=0, le=1000)
    # Cross-encoder re-ranking: ~3 s per query on a Pi 4 CPU, so off by default; turn it on when
    # the brain runs on a faster host.
    rerank: Literal["off", "local"] = "off"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    graph_enabled: bool = True
    graph_route: Literal["chat", "tools"] = "chat"  # which provider chain extracts entities
    watch_folder: str = "data/knowledge/inbox"  # files dropped here are ingested automatically


ConfigStore.register("knowledge", KnowledgeConfig)

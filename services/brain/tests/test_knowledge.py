import hashlib
import json
import re

import numpy as np
import pytest
from jarvis_brain.config import ConfigStore
from jarvis_brain.db import Database
from jarvis_brain.knowledge.base import KnowledgeBase, _json_obj
from jarvis_brain.knowledge.chunking import Segment, chunk_segments
from jarvis_brain.knowledge.tools import make_kb_tools
from jarvis_brain.providers.base import ToolCall
from jarvis_brain.providers.router import Router
from jarvis_brain.secrets import SecretStore
from jarvis_brain.settings import Settings
from jarvis_brain.tools.registry import ToolContext, ToolRegistry

from .test_router_agent import FakeProvider, text_step

WORDS = re.compile(r"[a-z]+")


class HashEmbedder:
    """Deterministic bag-of-words embedder: shared words → similar vectors."""

    def __init__(self, dim=64, tag="v1"):
        self.dim, self.id = dim, f"fake:{tag}:{dim}"

    async def embed(self, texts, *, query=False):
        out = np.zeros((len(texts), self.dim), np.float32)
        for i, t in enumerate(texts):
            for w in WORDS.findall(t.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
        n = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(n == 0, 1, n)


TRIP = """# Maldives trip

## Flights
We fly to Male on the 14th of March with Emirates, leaving London at 9pm.

## Transfers
The seaplane to the resort leaves Male at 9am. Ask Aisha at the resort desk to confirm the seaplane.

## Packing
Snorkel, reef-safe sun cream, and the underwater camera."""

GRAPH_JSON = {
    "entities": [
        {"name": "Aisha", "type": "person", "description": "Works the resort desk"},
        {
            "name": "Seaplane",
            "type": "transport",
            "description": "Transfer from Male to the resort",
        },
        {"name": "Male", "type": "place", "description": "Capital of the Maldives"},
    ],
    "relations": [
        {
            "source": "Aisha",
            "target": "Seaplane",
            "label": "confirms",
            "description": "confirms bookings",
        },
        {"source": "Seaplane", "target": "Male", "label": "departs from", "description": "9am"},
    ],
}


@pytest.fixture
def kb(tmp_path):
    db = Database(tmp_path / "kb.db")
    cfg = ConfigStore(db)
    cfg.update(
        "knowledge",
        {"rerank": "off", "graph_enabled": True, "watch_folder": str(tmp_path / "inbox")},
    )
    router = Router(cfg, SecretStore(db, ""), Settings(JARVIS_DATA_DIR=tmp_path))
    cfg.update(
        "providers",
        {"providers": [], "routes": {"chat": ["llm"], "tools": ["llm"], "task": ["llm"]}},
    )
    llm = FakeProvider("llm", [[*text_step(json.dumps(GRAPH_JSON))] for _ in range(20)])
    router.providers = {"llm": llm}  # after the config update (which rebuilds providers)
    k = KnowledgeBase(db, cfg, SecretStore(db, ""), router, tmp_path, tmp_path / "models")
    k._embedder, k._embedder_sig = HashEmbedder(), None
    k.embedder = lambda: k._embedder  # type: ignore[method-assign]
    return k


async def ingest_trip(kb):
    sid = kb.add_file("maldives.md", TRIP.encode())
    await kb.ingest(kb.queue.get_nowait())
    return sid


def test_chunking_keeps_pages_sections_and_overlap():
    segs = [
        Segment("Intro paragraph. " * 30, page=1),
        Segment("## Costs\n\n" + "Price detail. " * 60, page=2),
    ]
    chunks = chunk_segments(segs, size=400, overlap=80)
    assert all(len(c.text) <= 600 for c in chunks)
    assert chunks[0].meta["page"] == 1 and chunks[-1].meta["page"] == 2
    assert chunks[-1].meta.get("section") == "Costs"
    # overlap: each chunk starts with text from the end of the previous one
    assert any(chunks[i + 1].text[:20] in chunks[i].text for i in range(len(chunks) - 1))


def test_json_obj_tolerates_fences_and_chatter():
    assert _json_obj('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert _json_obj("no json here") is None


async def test_ingest_and_hybrid_search_with_citation(kb):
    sid = await ingest_trip(kb)
    src = kb.sources()[0]
    assert src["status"] == "ready" and src["title"] == "Maldives trip" and src["chunks"] >= 1
    hits = await kb.search("when does the seaplane leave", 3)
    assert hits and "seaplane" in hits[0].text.lower()
    assert hits[0].citation().startswith("Maldives trip")
    assert await kb.index("chunks").count() == src["chunks"]
    # Re-ingesting unchanged content is a no-op.
    kb.queue.put_nowait(sid)
    await kb.ingest(kb.queue.get_nowait())
    assert kb.sources()[0]["chunks"] == src["chunks"]


async def test_kb_search_tool_returns_citations(kb):
    await ingest_trip(kb)
    reg = ToolRegistry()
    for t in make_kb_tools(kb):
        reg.register(t)
    out = await reg.execute(
        ToolCall("1", "kb_search", {"query": "underwater camera packing"}), ToolContext()
    )
    data = json.loads(out.content)
    assert "Maldives trip" in data["results"][0]["citation"] and "how_to_cite" in data


async def test_graph_extraction_local_and_global(kb):
    await ingest_trip(kb)
    g = kb.graph()
    names = {n["name"] for n in g["nodes"]}
    assert {"Aisha", "Seaplane", "Male"} <= names and len(g["edges"]) >= 2
    local = await kb.graph_query("Who is Aisha?")
    assert any(r["source"] == "Aisha" and r["label"] == "confirms" for r in local["relations"])
    assert local["sources"] and "Maldives trip" in local["sources"][0]["citation"]
    # Community summaries come from the (fake) LLM; feed it a summary for the rebuild.
    llm = kb.router.providers["llm"]
    llm.script[:0] = [
        text_step(json.dumps({"title": "Maldives transfers", "summary": "Seaplane logistics."}))
    ]
    assert await kb.rebuild_communities() >= 1
    glob = await kb.graph_query("travel logistics", mode="global")
    assert glob["communities"] and glob["communities"][0]["title"] == "Maldives transfers"


async def test_delete_source_cascades(kb):
    sid = await ingest_trip(kb)
    assert await kb.delete_source(sid)
    assert kb.sources() == [] and kb.graph()["nodes"] == []
    assert await kb.index("chunks").count() == 0
    assert await kb.search("seaplane") == []
    assert not list(kb.files_dir.glob("*"))


async def test_semantic_memory_recall(kb):
    kb.db.execute(
        "INSERT INTO memories(id, fact, created, updated) VALUES(1, 'Prefers Earl Grey tea', 0, 0)"
    )
    await kb.index_memory(1, "Prefers Earl Grey tea")
    assert await kb.recall_memory("what tea does she prefer, earl grey or green") == [1]


async def test_embedding_model_change_resets_index(kb):
    await ingest_trip(kb)
    kb._embedder = HashEmbedder(dim=32, tag="v2")
    await kb.reindex()
    assert kb.db.kv_get("vec:chunks").startswith("fake:v2:32")
    assert (await kb.search("seaplane"))[0].text


async def test_unsupported_upload_rejected(kb):
    with pytest.raises(ValueError):
        kb.add_file("evil.exe", b"MZ")


def test_chunk_section_is_where_the_chunk_starts():
    alpha = "Alpha text goes on. " * 30
    beta = "Beta text goes on. " * 30
    text = f"# Doc\n\n## Alpha\n\n{alpha}\n\n## Beta\n\n{beta}"
    chunks = chunk_segments([Segment(text)], size=300, overlap=0)
    for c in chunks:
        body = c.text.replace("## Beta", "").replace("## Alpha", "").replace("# Doc", "").strip()
        if body.startswith("Beta"):
            assert c.meta["section"] == "Beta"
        elif body.startswith("Alpha"):
            assert c.meta["section"] in ("Alpha", "Doc")
    assert (
        "\n\nAlpha text goes on. Alpha" in chunks[0].text
        or "Alpha text goes on. Alpha" in chunks[1].text
    )

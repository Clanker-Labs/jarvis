"""Turn documents into overlapping, citation-friendly chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_SENT_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass
class Segment:
    text: str
    page: int | None = None


@dataclass
class Chunk:
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def extract_file(path: Path) -> tuple[str, list[Segment]]:
    """→ (title, segments). PDFs keep page numbers."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        title = (
            reader.metadata.title if reader.metadata and reader.metadata.title else ""
        ) or path.stem
        return title, [
            Segment(p.extract_text() or "", page=i + 1) for i, p in enumerate(reader.pages)
        ]
    from ..tools.builtin.files import read_text

    text = read_text(path)
    title = path.stem.replace("_", " ").replace("-", " ")
    if suffix in (".md", ".markdown"):
        for line in text.splitlines():
            m = _HEADING.match(line)
            if m:
                title = m.group(1)
                break
    return title, [Segment(text)]


def _paragraphs(seg: Segment) -> list[tuple[str, int | None, str | None]]:
    out: list[tuple[str, int | None, str | None]] = []
    heading: str | None = None
    for block in re.split(r"\n\s*\n", seg.text):
        block = block.strip()
        if not block:
            continue
        first = block.splitlines()[0]
        m = _HEADING.match(first)
        if m:
            heading = m.group(1)
        out.append((re.sub(r"[ \t]+", " ", block), seg.page, heading))
    return out


def _tail(text: str, n: int) -> str:
    """The last ~n characters, starting at a sentence boundary when possible."""
    if n <= 0 or len(text) <= n:
        return text if n > 0 else ""
    tail = text[-n:]
    parts = _SENT_END.split(tail, maxsplit=1)
    return parts[1] if len(parts) == 2 and len(parts[1]) > n // 3 else tail


def chunk_segments(segments: list[Segment], size: int = 1100, overlap: int = 180) -> list[Chunk]:
    chunks: list[Chunk] = []
    buf, pages, section = "", set(), None
    chunk_section: str | None = None  # the heading in force where the current chunk *starts*

    def flush() -> None:
        nonlocal buf, pages, chunk_section
        if buf.strip():
            meta: dict[str, Any] = {}
            if pages:
                meta["pages"] = sorted(pages)
                meta["page"] = min(pages)
            if chunk_section:
                meta["section"] = chunk_section
            chunks.append(Chunk(buf.strip(), meta))
        carry = _tail(buf, overlap)
        buf, pages = carry, set()
        chunk_section = section

    for seg in segments:
        for para, page, heading in _paragraphs(seg):
            if heading:
                section = heading
            if not buf.strip():
                chunk_section = section
            # Very long paragraphs are split on sentences.
            pieces = [para] if len(para) <= size else _SENT_END.split(para)
            for j, piece in enumerate(pieces):
                if len(buf) + len(piece) + 2 > size and buf.strip():
                    flush()
                # Sentences of one split paragraph stay one paragraph.
                sep = " " if j > 0 and buf else "\n\n"
                buf = f"{buf}{sep}{piece}" if buf else piece
                if page is not None:
                    pages.add(page)
                while len(buf) > size * 1.5:  # a single huge sentence
                    head, buf = buf[:size], buf[size - overlap :]
                    chunks.append(Chunk(head.strip(), {"section": section} if section else {}))
    if buf.strip() and (not chunks or buf.strip() != _tail(chunks[-1].text, overlap).strip()):
        flush()
    return chunks

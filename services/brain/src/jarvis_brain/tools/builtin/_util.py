from __future__ import annotations

import re

_WORD = re.compile(r"[\w'-]+", re.UNICODE)


def fts_query(text: str, *, any_term: bool = True) -> str:
    """Turn free text into a safe FTS5 query (quoted terms, OR/AND joined)."""
    terms = [t.replace('"', "") for t in _WORD.findall(text.lower()) if len(t) > 1]
    if not terms:
        return '""'
    return (" OR " if any_term else " AND ").join(f'"{t}"*' for t in terms[:12])

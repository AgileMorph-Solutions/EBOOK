"""RAG grounding — retrieve source passages relevant to a chapter so the writer
writes *from* the source, not from imagination.

Phase-0 uses a dependency-light lexical retriever. In deployment this is swapped
for pgvector cosine search over passage embeddings (same interface).
"""
from __future__ import annotations

import re
from collections import Counter

from bookforge.schemas import Passage

_WORD = re.compile(r"[a-z]{3,}")
_STOP = set("the and for that with this from have были your you are was were has".split())


def _vec(text: str) -> Counter:
    return Counter(w for w in _WORD.findall(text.lower()) if w not in _STOP)


def retrieve(query: str, passages: list[Passage], k: int = 8) -> list[Passage]:
    """Return the top-k passages most lexically relevant to `query`."""
    q = _vec(query)
    if not q:
        return passages[:k]
    scored = []
    for p in passages:
        pv = _vec(p.text)
        overlap = sum(min(q[w], pv[w]) for w in q)
        if overlap:
            scored.append((overlap, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:k]] or passages[:k]

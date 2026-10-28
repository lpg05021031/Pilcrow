"""Small fuzzy matcher tuned for file paths and command names (port of web/src/lib/fuzzy.ts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, List, Optional, Sequence, TypeVar

T = TypeVar("T")


@dataclass
class FuzzyScore:
    score: float
    hits: List[int] = field(default_factory=list)


@dataclass
class Match(Generic[T]):
    item: Any
    score: float
    hits: List[int]


def fuzzy_score(query: str, text: str) -> Optional[FuzzyScore]:
    """Score `text` against `query`; None when not all query chars appear in order."""
    if not query:
        return FuzzyScore(0, [])
    q = query.lower()
    t = text.lower()
    # Fast path: contiguous substring scores highest, especially in the basename.
    sub = t.find(q)
    base = t.rfind("/") + 1
    if sub >= 0:
        hits = [sub + i for i in range(len(q))]
        score = 1000 - sub - len(t) * 0.5
        if sub >= base:
            score += 400
        if sub == base:
            score += 300
        return FuzzyScore(score, hits)
    hits = []
    ti = 0
    score = 0.0
    prev = -2
    for ch in q:
        found = -1
        while ti < len(t):
            if t[ti] == ch:
                found = ti
                ti += 1
                break
            ti += 1
        if found < 0:
            return None
        hits.append(found)
        if found == prev + 1:
            score += 15
        if found == 0 or t[found - 1] in "/._-":
            score += 25
        if found >= base:
            score += 8
        prev = found
    score -= len(t) * 0.4 + (hits[0] if hits else 0) * 0.2
    return FuzzyScore(score, hits)


def fuzzy_filter(query: str, items: Sequence[T], key: Callable[[T], str], limit: int = 50) -> List[Match[T]]:
    out: List[Match[T]] = []
    for item in items:
        r = fuzzy_score(query, key(item))
        if r:
            out.append(Match(item, r.score, r.hits))
    out.sort(key=lambda m: -m.score)
    return out[:limit]

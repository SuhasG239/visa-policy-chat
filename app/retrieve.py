"""Hybrid retrieval: vector search + BM25 keyword search, merged with rank fusion.

Users ask in everyday words ("grace period", "CPT"); regulations use formal ones
("period of preparation for departure", "curricular practical training"). The
GLOSSARY adds the formal terms to the keyword query so both searches can match.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from rank_bm25 import BM25Okapi

from app import embed, store

log = logging.getLogger(__name__)

DEFAULT_K = 8           # chunks returned (CLAUDE.md: 5 to 8)
CANDIDATES = 25         # taken from each search before merging
RRF_K = 10              # rank fusion constant; low values favor top ranks (60 buried #1 hits in tests)

# Everyday term -> regulatory wording. Keys are matched as whole words, lowercase.
GLOSSARY = {
    "grace period": "period of preparation for departure 60 days",
    "cpt": "curricular practical training",
    "opt": "optional practical training",
    "stem": "science technology engineering mathematics 24-month extension",
    "unemployment": "unemployment aggregate days",
    "on campus": "on-campus employment",
    "on-campus": "on-campus employment",
    "off campus": "off-campus employment severe economic hardship",
    "work": "employment",
    "job": "employment",
    "unemployed": "unemployment aggregate days",
    "travel": "temporary absence readmitted",
    "outside the us": "temporary absence five months readmitted",
    "outside the united states": "temporary absence five months readmitted",
    "come back": "temporary absence readmitted",
    "return to the us": "temporary absence readmitted",
    "re-enter": "temporary absence readmitted",
    "reentry": "temporary absence readmitted",
    "re-entry": "temporary absence readmitted",
    "transfer": "school transfer",
    "change of status": "change of nonimmigrant classification",
    "change status": "change of nonimmigrant classification",
    "cap gap": "cap-gap extension h-1b petition",
    "cap-gap": "cap-gap extension h-1b petition",
    "dso": "designated school official",
    "reduced course load": "reduced course load rcl",
    "part-time": "reduced course load full course of study",
    "spouse": "f-2 spouse",
    "dependents": "f-2 spouse children",
    "out of status": "violation of status reinstatement",
    "reinstatement": "reinstatement to student status",
    "ead": "employment authorization document",
    "extension": "extension of stay",
}
STOPWORDS = set(
    "a an and are as at be by can do does for from how i if in is it my of on or "
    "the to what when where which who will with you your this that me".split()
)
TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")


SUFFIXES = ("ment", "ing", "ed", "es", "s")


def _stem(token: str) -> str:
    """Crude suffix stripping so 'unemployed'/'unemployment' and 'day'/'days' match."""
    if not token.isalpha():
        return token
    for suffix in SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4 and not token.endswith(("ss", "us", "is")):
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase, stemmed word tokens, keeping forms like 'f-1', 'i-20', '214.2'."""
    return [_stem(t) for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


def expand_query(question: str) -> str:
    """Question plus regulatory wording for any glossary terms it contains."""
    q = question.lower()
    extra = [v for k, v in GLOSSARY.items() if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", q)]
    return " ".join([question, *extra])


class _KeywordIndex:
    """BM25 over every stored chunk; rebuilt when the chunk set changes."""

    def __init__(self) -> None:
        self.ids: tuple[str, ...] = ()
        self.chunks: list[dict[str, Any]] = []
        self.bm25: BM25Okapi | None = None

    def refresh(self) -> None:
        chunks = store.all_chunks()
        ids = tuple(sorted(c["chunk_id"] for c in chunks))
        if ids == self.ids:
            return
        self.ids, self.chunks = ids, chunks
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks]) if chunks else None
        log.info("Built keyword index over %d chunks", len(chunks))

    def search(self, query: str, k: int) -> list[dict[str, Any]]:
        self.refresh()
        if self.bm25 is None:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self.chunks[i] for i in ranked[:k] if scores[i] > 0]


_keyword = _KeywordIndex()


SPLIT = re.compile(
    r"(?<=\?)\s+|,?\s+and\s+(?=(?:how|what|when|where|can|could|do|does|is|are|will|which|who|if)\b)",
    re.IGNORECASE,
)


def split_question(question: str) -> list[str]:
    """Split a compound question ("How long X, and how many Y?") into its parts."""
    parts = [p.strip() for p in SPLIT.split(question) if p and len(p.split()) >= 3]
    return parts or [question]


def retrieve(question: str, k: int = DEFAULT_K, scope: str | None = None) -> list[dict[str, Any]]:
    """Top-k chunks for a question, best first.

    Compound questions are split so each part gets its share of the k results
    (taken in turn); otherwise one part can crowd out the other.
    Each result has the chunk's text and metadata plus `score` (fusion score).
    """
    parts = split_question(question)
    if len(parts) == 1:
        return _retrieve_one(question, k, scope)
    lists = [_retrieve_one(p, k, scope) for p in parts]
    merged: dict[str, dict[str, Any]] = {}
    for rank in range(k):
        for hits in lists:
            if rank < len(hits) and len(merged) < k:
                merged.setdefault(hits[rank]["chunk_id"], hits[rank])
    return list(merged.values())


def _retrieve_one(question: str, k: int, scope: str | None) -> list[dict[str, Any]]:
    """Hybrid search for a single question: vector + BM25, merged by rank fusion."""
    # Glossary terms help with acronyms ("CPT") but can blur a longer question's
    # embedding, so vector search runs on both the plain and the expanded question.
    expanded = expand_query(question)
    queries = [question] if expanded == question else [question, expanded]
    result_lists = [store.search(embed.embed_query(q), k=CANDIDATES, scope=scope) for q in queries]
    result_lists.append([
        c for c in _keyword.search(expanded, CANDIDATES * 2) if not scope or scope in c["scope"]
    ][:CANDIDATES])

    fused: dict[str, dict[str, Any]] = {}
    for hits in result_lists:
        for rank, chunk in enumerate(hits):
            entry = fused.setdefault(chunk["chunk_id"], {**chunk, "score": 0.0})
            entry["score"] += 1.0 / (RRF_K + rank + 1)
    results = sorted(fused.values(), key=lambda c: c["score"], reverse=True)[:k]
    for c in results:
        c.pop("distance", None)
    log.debug("retrieve(%r) -> %s", question, [c["chunk_id"] for c in results])
    return results

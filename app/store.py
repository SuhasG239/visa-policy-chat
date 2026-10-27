"""Vector store wrapper. The only module that imports chromadb.

Public API: upsert_chunks(), delete_by_section(), search(), all_chunks(), stats(), reset().
Swapping to another vector database should only require rewriting this file.

The collection records the embedding model it was built with. Opening it with a
different EMBED_MODEL raises EmbeddingModelMismatch; the caller must reset() and
re-index everything rather than mix vectors from two models.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from functools import lru_cache
from typing import Any

import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv

from app import embed

load_dotenv()
log = logging.getLogger(__name__)

DEFAULT_DB_DIR = "db"
DEFAULT_COLLECTION = "visa_policy"
BATCH_SIZE = 1000

REQUIRED_FIELDS = (
    "chunk_id", "section_key", "source", "citation", "title", "url",
    "effective_date", "source_updated", "fetched_at", "doc_type", "scope",
)
SOURCES = {"uscis_pm", "ecfr", "federal_register", "uscis_alert", "sevp", "uscis_web", "fam", "visa_bulletin"}
DOC_TYPES = {"regulation", "policy_guidance", "proposed_rule", "final_rule", "alert", "court_order"}
AFFECTED_SEP = " || "


class EmbeddingModelMismatch(RuntimeError):
    """The index was built with a different embedding model than EMBED_MODEL."""


def _settings() -> tuple[str, str]:
    return os.getenv("DB_DIR", DEFAULT_DB_DIR), os.getenv("DB_COLLECTION", DEFAULT_COLLECTION)


@lru_cache(maxsize=4)
def _client(db_dir: str):
    # Telemetry off: this project sends nothing anywhere.
    return chromadb.PersistentClient(path=db_dir, settings=Settings(anonymized_telemetry=False))


def _collection():
    db_dir, name = _settings()
    client = _client(db_dir)
    model = embed.model_name()
    existing = [c if isinstance(c, str) else c.name for c in client.list_collections()]
    if name not in existing:
        log.info("Creating collection %s (embed_model=%s)", name, model)
        return client.create_collection(
            name, metadata={"embed_model": model, "hnsw:space": "cosine"}
        )
    col = client.get_collection(name)
    stored = (col.metadata or {}).get("embed_model")
    if stored != model:
        raise EmbeddingModelMismatch(
            f"Index '{name}' was built with {stored!r} but EMBED_MODEL is {model!r}. "
            "Run a full re-index (store.reset() then re-ingest)."
        )
    return col


def _to_metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    """Validate a chunk and flatten it to Chroma-safe scalar metadata."""
    missing = [f for f in REQUIRED_FIELDS if f not in chunk]
    if missing:
        raise ValueError(f"Chunk {chunk.get('chunk_id')!r} missing fields: {missing}")
    if chunk["source"] not in SOURCES:
        raise ValueError(f"Unknown source {chunk['source']!r}")
    if chunk["doc_type"] not in DOC_TYPES:
        raise ValueError(f"Unknown doc_type {chunk['doc_type']!r}")
    if not chunk.get("text", "").strip():
        raise ValueError(f"Chunk {chunk['chunk_id']!r} has no text")

    meta: dict[str, Any] = {}
    for key, value in chunk.items():
        if key in ("text", "scope", "affected_by"):
            continue
        meta[key] = "" if value is None else value
    # Court orders/alerts that affect this chunk (ingest/court_orders.py); stored as one string.
    meta["affected_by"] = AFFECTED_SEP.join(chunk.get("affected_by") or [])
    scope = list(chunk["scope"])
    # Comma string for display, plus one boolean per tag so search can filter on it.
    meta["scope"] = ",".join(scope)
    for tag in scope:
        meta[f"scope_{tag}"] = True
    return meta


def _from_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in meta.items() if not k.startswith("scope_")}
    out["scope"] = [t for t in str(meta.get("scope", "")).split(",") if t]
    out["affected_by"] = [t for t in str(meta.get("affected_by", "")).split(AFFECTED_SEP) if t]
    return out


def upsert_chunks(chunks: list[dict[str, Any]], embeddings: list[list[float]]) -> int:
    """Insert or replace chunks. Each chunk has `text` plus all REQUIRED_FIELDS.

    Returns the number of chunks written.
    """
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must be the same length")
    ids = [c["chunk_id"] for c in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate chunk_id in batch")
    metas = [_to_metadata(c) for c in chunks]
    col = _collection()
    for i in range(0, len(chunks), BATCH_SIZE):
        col.upsert(
            ids=ids[i:i + BATCH_SIZE],
            documents=[c["text"] for c in chunks[i:i + BATCH_SIZE]],
            embeddings=embeddings[i:i + BATCH_SIZE],
            metadatas=metas[i:i + BATCH_SIZE],
        )
    return len(chunks)


def delete_by_section(section_key: str) -> int:
    """Delete every chunk of a section. Returns the number deleted."""
    col = _collection()
    ids = col.get(where={"section_key": section_key}, include=[])["ids"]
    if ids:
        col.delete(ids=ids)
    log.debug("Deleted %d chunks for section %s", len(ids), section_key)
    return len(ids)


def search(
    query_embedding: list[float],
    k: int = 8,
    where: dict[str, Any] | None = None,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """Nearest-neighbour search. Returns dicts with text, metadata, and distance.

    `where` is a metadata filter (e.g. {"source": "ecfr"}); `scope` limits results
    to chunks tagged with that scope (e.g. "opt").
    """
    col = _collection()
    if col.count() == 0:
        return []
    filters = [f for f in (where, {f"scope_{scope}": True} if scope else None) if f]
    combined = None if not filters else filters[0] if len(filters) == 1 else {"$and": filters}
    res = col.query(
        query_embeddings=[query_embedding],
        n_results=min(k, col.count()),
        where=combined,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {"text": doc, "distance": dist, **_from_metadata(meta)}
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def all_chunks() -> list[dict[str, Any]]:
    """Every chunk with its text and metadata (for keyword search over the corpus)."""
    res = _collection().get(include=["documents", "metadatas"])
    return [
        {"text": doc, **_from_metadata(meta)}
        for doc, meta in zip(res["documents"], res["metadatas"])
    ]


def stats() -> dict[str, Any]:
    """Chunk count, embedding model, and per-source / per-doc_type counts."""
    col = _collection()
    metas = col.get(include=["metadatas"])["metadatas"]
    return {
        "collection": col.name,
        "embed_model": (col.metadata or {}).get("embed_model"),
        "count": len(metas),
        "by_source": dict(Counter(m.get("source") for m in metas)),
        "by_doc_type": dict(Counter(m.get("doc_type") for m in metas)),
    }


def reset() -> None:
    """Drop the whole collection (used for a full re-index after a model change)."""
    db_dir, name = _settings()
    client = _client(db_dir)
    existing = [c if isinstance(c, str) else c.name for c in client.list_collections()]
    if name in existing:
        client.delete_collection(name)
        log.warning("Deleted collection %s", name)

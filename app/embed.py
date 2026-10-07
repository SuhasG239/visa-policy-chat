"""Embedding wrapper. The only module that loads an embedding model.

The model name comes from EMBED_MODEL in .env. The store records it in the
index metadata so embeddings from two models are never mixed.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def model_name() -> str:
    """Configured embedding model name."""
    return os.getenv("EMBED_MODEL", DEFAULT_MODEL)


def _query_prefix() -> str:
    return os.getenv("EMBED_QUERY_PREFIX", DEFAULT_QUERY_PREFIX)


@lru_cache(maxsize=2)
def _model(name: str):
    # Imported lazily: sentence-transformers pulls in torch, which is slow to load.
    from sentence_transformers import SentenceTransformer

    log.info("Loading embedding model %s", name)
    return SentenceTransformer(name, device="cpu")


def embed_documents(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Embed document chunks. Vectors are L2-normalized."""
    if not texts:
        return []
    vectors = _model(model_name()).encode(
        texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False
    )
    return vectors.tolist()


def embed_query(text: str) -> list[float]:
    """Embed a search query (with the model's query instruction prefix)."""
    vector = _model(model_name()).encode(
        _query_prefix() + text, normalize_embeddings=True, show_progress_bar=False
    )
    return vector.tolist()


def count_tokens(text: str) -> int:
    """Number of model tokens in `text` (excluding special tokens)."""
    return len(_model(model_name()).tokenizer.encode(text, add_special_tokens=False))


def max_tokens() -> int:
    """Longest input the model embeds; anything beyond is silently truncated."""
    return int(_model(model_name()).max_seq_length)


def dimension() -> int:
    """Embedding vector size of the configured model."""
    model = _model(model_name())
    # Renamed in sentence-transformers 6; keep the old name for older installs.
    getter = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    return int(getter())

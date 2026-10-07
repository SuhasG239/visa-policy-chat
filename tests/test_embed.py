"""Smoke test for app/embed.py. Downloads the embedding model on first run."""

import math

import pytest

from app import embed

pytestmark = pytest.mark.slow


def test_embeddings_are_normalized_and_relevant():
    docs = embed.embed_documents([
        "An F-1 student may work on campus up to 20 hours a week while school is in session.",
        "The recipe calls for two cups of flour and a pinch of salt.",
    ])
    query = embed.embed_query("How many hours can F-1 students work on campus?")

    assert len(docs) == 2 and len(query) == embed.dimension()
    assert math.isclose(sum(x * x for x in query), 1.0, rel_tol=1e-3)

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    assert dot(query, docs[0]) > dot(query, docs[1])


def test_empty_input():
    assert embed.embed_documents([]) == []

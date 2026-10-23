"""Smoke tests for app/store.py using fake embeddings (no model download)."""

import pytest

from app import store


def _chunk(chunk_id: str, section_key: str, text: str, scope=("f1",)) -> dict:
    return {
        "chunk_id": chunk_id,
        "section_key": section_key,
        "text": text,
        "source": "ecfr",
        "citation": f"8 CFR {section_key}",
        "title": "Test section",
        "url": "https://www.ecfr.gov/current/title-8",
        "effective_date": None,
        "source_updated": "2026-01-01",
        "fetched_at": "2026-10-06T00:00:00Z",
        "doc_type": "regulation",
        "scope": list(scope),
    }


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("DB_COLLECTION", "test")
    monkeypatch.setenv("EMBED_MODEL", "fake-model")
    yield
    store._client.cache_clear()


def test_upsert_search_delete_stats():
    chunks = [
        _chunk("ecfr-a-001", "a", "on-campus work", scope=("f1",)),
        _chunk("ecfr-a-002", "a", "more on-campus work", scope=("f1",)),
        _chunk("ecfr-b-001", "b", "OPT unemployment", scope=("f1", "opt")),
    ]
    vecs = [[1, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 0, 1, 0]]
    assert store.upsert_chunks(chunks, vecs) == 3

    hits = store.search([0, 0, 1, 0], k=1)
    assert hits[0]["chunk_id"] == "ecfr-b-001"
    assert hits[0]["scope"] == ["f1", "opt"]
    assert hits[0]["effective_date"] == ""

    opt_only = store.search([1, 0, 0, 0], k=5, scope="opt")
    assert [h["chunk_id"] for h in opt_only] == ["ecfr-b-001"]

    s = store.stats()
    assert s["count"] == 3 and s["embed_model"] == "fake-model"
    assert s["by_source"] == {"ecfr": 3}

    assert store.delete_by_section("a") == 2
    assert store.stats()["count"] == 1
    assert store.delete_by_section("missing") == 0


def test_upsert_is_idempotent():
    c = _chunk("ecfr-a-001", "a", "text")
    store.upsert_chunks([c], [[1, 0, 0, 0]])
    store.upsert_chunks([c], [[1, 0, 0, 0]])
    assert store.stats()["count"] == 1


def test_rejects_bad_chunks():
    bad = _chunk("x", "a", "text")
    del bad["citation"]
    with pytest.raises(ValueError, match="citation"):
        store.upsert_chunks([bad], [[1, 0, 0, 0]])
    bad = _chunk("x", "a", "text")
    bad["doc_type"] = "blog"
    with pytest.raises(ValueError):
        store.upsert_chunks([bad], [[1, 0, 0, 0]])


def test_model_mismatch_requires_reindex(monkeypatch):
    store.upsert_chunks([_chunk("ecfr-a-001", "a", "text")], [[1, 0, 0, 0]])
    monkeypatch.setenv("EMBED_MODEL", "other-model")
    with pytest.raises(store.EmbeddingModelMismatch):
        store.stats()
    store.reset()
    assert store.stats() == {
        "collection": "test", "embed_model": "other-model", "count": 0,
        "by_source": {}, "by_doc_type": {},
    }


def test_affected_by_round_trips():
    c = _chunk("ecfr-a-001", "a", "text")
    c["affected_by"] = ["USCIS alert, June 12, 2026 (Court Order)", "91 FR 1 (Other)"]
    store.upsert_chunks([c, _chunk("ecfr-b-001", "b", "more")], [[1, 0, 0, 0], [0, 1, 0, 0]])
    by_id = {h["chunk_id"]: h for h in store.all_chunks()}
    assert by_id["ecfr-a-001"]["affected_by"] == c["affected_by"]
    assert by_id["ecfr-b-001"]["affected_by"] == []

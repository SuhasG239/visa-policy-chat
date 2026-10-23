"""Offline tests for ingest/sync.py: section diffs, failure handling, and the sync log."""

import json

import pytest

from app import store
from ingest import sync

SOURCES = [{"id": "src-a", "type": "fake"}, {"id": "src-b", "type": "fake"}]


def chunk(cid, section, text):
    return {"chunk_id": cid, "section_key": section, "text": text, "source": "ecfr",
            "citation": f"8 CFR {section}", "title": "t", "url": "https://www.ecfr.gov", "effective_date": None,
            "source_updated": "2026-01-01", "fetched_at": "now", "doc_type": "regulation", "scope": ["f1"]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("DB_COLLECTION", "sync-test")
    monkeypatch.setenv("EMBED_MODEL", "fake-model")
    monkeypatch.setattr(sync, "MANIFEST_FILE", tmp_path / "manifest.json")
    monkeypatch.setattr(sync, "SYNC_LOG", tmp_path / "sync-log.jsonl")
    monkeypatch.setattr(sync, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(sync, "load_sources", lambda: SOURCES)
    monkeypatch.setattr(sync.embed, "embed_documents", lambda texts: [[1.0, float(len(t)), 0.0, 0.0] for t in texts])
    monkeypatch.setattr(sync.embed, "model_name", lambda: "fake-model")
    monkeypatch.setattr(sync, "PoliteClient", lambda *a, **k: None)
    outputs: dict[str, object] = {}
    monkeypatch.setattr(sync._Fetchers, "build", lambda self, src: _build(outputs, src))
    yield outputs, tmp_path
    store._client.cache_clear()


def _build(outputs, src):
    result = outputs[src["id"]]
    if isinstance(result, Exception):
        raise result
    return result, {"last_fetched": "now"}


def ids():
    return sorted(c["chunk_id"] for c in store.all_chunks())


def test_changed_sections_are_replaced_and_vanished_ones_deleted(env):
    outputs, tmp = env
    outputs["src-a"] = [chunk("a1", "214.1(a)", "v1"), chunk("a2", "214.1(b)", "v1")]
    outputs["src-b"] = [chunk("b1", "248.1(a)", "v1")]
    first = sync.sync()
    assert first["ok"] and ids() == ["a1", "a2", "b1"]

    outputs["src-a"] = [chunk("a1", "214.1(a)", "v2")]        # (a) changed, (b) gone
    second = sync.sync()
    assert second["sources"]["src-a"] == {"status": "updated", "changed": ["214.1(a)"], "removed": ["214.1(b)"]}
    assert second["sources"]["src-b"] == {"status": "unchanged"}
    assert ids() == ["a1", "b1"]
    assert next(c for c in store.all_chunks() if c["chunk_id"] == "a1")["text"] == "v2"
    assert len((tmp / "sync-log.jsonl").read_text().splitlines()) == 2


def test_failed_source_keeps_its_chunks_and_blocks_last_synced(env):
    outputs, tmp = env
    outputs["src-a"] = [chunk("a1", "214.1(a)", "v1")]
    outputs["src-b"] = [chunk("b1", "248.1(a)", "v1")]
    sync.sync()
    synced_at = json.loads((tmp / "manifest.json").read_text())["last_successful_sync"]

    outputs["src-b"] = ConnectionError("site down")
    record = sync.sync()
    assert not record["ok"]
    assert record["sources"]["src-b"]["status"] == "error" and "site down" in record["sources"]["src-b"]["error"]
    assert ids() == ["a1", "b1"]                                # b's previous chunks survive
    assert json.loads((tmp / "manifest.json").read_text())["last_successful_sync"] == synced_at


def test_court_order_tags_reindex_the_affected_section(env):
    outputs, _ = env
    reg = chunk("a1", "214.1(a)", "Rule text.")
    outputs["src-a"] = [reg]
    outputs["src-b"] = []
    sync.sync()
    order = {**chunk("o1", "order", "A court order enjoining 8 CFR 214.1(a)."),
             "citation": "USCIS alert, May 1, 2026", "doc_type": "court_order", "title": "Order"}
    outputs["src-b"] = [order]
    record = sync.sync()
    assert record["court_order_tags"] == {"USCIS alert, May 1, 2026 (Order)": 1}
    assert record["sources"]["src-a"]["changed"] == ["214.1(a)"]
    tagged = next(c for c in store.all_chunks() if c["chunk_id"] == "a1")
    assert tagged["affected_by"] == ["USCIS alert, May 1, 2026 (Order)"]

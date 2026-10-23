"""API tests via FastAPI's TestClient, against the real pipeline (no mocks).

Tests marked `llm` call Claude and are skipped without ANTHROPIC_API_KEY.
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.api import app

needs_key = pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
SOURCE_FIELDS = {"chunk_id", "citation", "title", "url", "source_updated",
                 "effective_date", "doc_type", "scope", "affected_by"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:   # runs the startup warm-up
        yield c


def test_health_and_meta(client):
    assert client.get("/health").json() == {"ok": True}
    meta = client.get("/meta").json()
    assert set(meta) == {"model", "last_synced", "chunk_count", "scope", "disclaimer"}
    assert meta["model"] == llm.model_name() and meta["chunk_count"] > 0
    assert "not legal advice" in meta["disclaimer"]


def test_rejects_bad_requests(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422
    bad_role = {"question": "Hi", "history": [{"role": "system", "content": "x"}]}
    assert client.post("/ask", json=bad_role).status_code == 422


def test_cors_allows_only_frontend_origin(client):
    pre = {"Access-Control-Request-Method": "POST"}
    ok = client.options("/ask", headers={"Origin": "http://localhost:3000", **pre})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.options("/ask", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers


@pytest.mark.llm
@needs_key
def test_ask_returns_cited_answer(client):
    r = client.post("/ask", json={
        "question": "How many hours a week can an F-1 student work on campus while school is in session?",
        "history": [],
    })
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"text", "sources", "model", "removed_links"}
    assert body["sources"] and all(set(s) == SOURCE_FIELDS for s in body["sources"])
    assert "20 hours" in body["text"]
    assert any(s["citation"].startswith("8 CFR 214.2(f)(9)") for s in body["sources"])
    urls = {s["url"] for s in body["sources"]}
    assert any(u in body["text"] for u in urls)   # inline links point at real sources


@pytest.mark.llm
@needs_key
def test_ask_stream_emits_tokens_then_done(client):
    with client.stream("POST", "/ask/stream", json={"question": "What is CPT?", "history": []}) as r:
        assert r.status_code == 200
        raw = "".join(r.iter_text()).replace("\r\n", "\n")
    events = []
    for block in raw.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line and not line.startswith(":"))
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    kinds = [k for k, _ in events]
    assert kinds[-1] == "done" and kinds.count("done") == 1
    assert kinds.count("token") > 1 and set(kinds[:-1]) == {"token"}
    done = events[-1][1]
    assert set(done) == {"text", "sources", "model", "removed_links"}
    assert done["text"] == "".join(d["text"] for k, d in events if k == "token") or done["removed_links"]


@pytest.mark.llm
def test_ask_reports_api_errors_as_502(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-invalid-key-for-test")
    llm._client.cache_clear()
    try:
        r = client.post("/ask", json={"question": "What is OPT?", "history": []})
    finally:
        llm._client.cache_clear()   # next caller rebuilds with the real key
    assert r.status_code == 502
    assert "API key" in r.json()["detail"]

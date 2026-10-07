"""Tests for the local token-usage log (no API calls)."""

import json
from types import SimpleNamespace

from app import usage


def _u(inp, out):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_read_input_tokens=0, cache_creation_input_tokens=0)


def test_record_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_USAGE_LOG", raising=False)
    usage.record("claude-sonnet-5-5", _u(10, 10))
    assert not list(tmp_path.iterdir())


def test_record_and_estimate(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("LLM_USAGE_LOG", str(log))
    usage.record("claude-sonnet-5-5", _u(3_500, 1_500))
    usage.record("claude-sonnet-5-5", _u(3_500, 1_500))
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert set(rows[0]) == {"ts", "model", "input_tokens", "output_tokens",
                            "cache_read_input_tokens", "cache_creation_input_tokens"}
    s = usage.summary(rows)
    assert s["calls"] == 2
    assert s["actual_cost_usd"] == 0.044               # 2 x (3500*$2 + 1500*$10) / 1M
    assert s["estimated_cost_usd"]["claude-opus-5-5"] == 0.088

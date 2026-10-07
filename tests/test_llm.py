"""Smoke test for app/llm.py. Skipped if no Anthropic API key is configured."""

import os

import pytest

from app import llm

pytestmark = pytest.mark.llm


def test_reports_hosted_provider():
    assert llm.is_local() is False
    assert llm.model_name()


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_chat_round_trip():
    reply = llm.chat(
        [{"role": "user", "content": "Reply with exactly the word: pong"}],
        system="You are a test endpoint. Follow the instruction literally.",
        max_tokens=1024,
        effort="low",
    )
    assert "pong" in reply.lower()

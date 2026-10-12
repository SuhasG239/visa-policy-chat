"""LLM provider wrapper. The only module that talks to a language model.

Calls Claude through the official Anthropic SDK. The API key is read by the SDK
from ANTHROPIC_API_KEY (set it in .env); the model comes from LLM_MODEL.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import anthropic
from dotenv import load_dotenv

from app import usage

load_dotenv()
log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
PROVIDER_NAME = "Anthropic"
# On a safety-classifier decline, the API retries on Anthropic's recommended fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """The LLM call failed (network, auth, rate limit, server error)."""


class LLMRefusal(LLMError):
    """The model (and its fallback) declined to answer."""


def model_name() -> str:
    """Configured model name."""
    return os.getenv("LLM_MODEL", DEFAULT_MODEL)


def is_local() -> bool:
    """False: questions are sent to Anthropic's API. The UI must say so."""
    return False


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def chat(
    messages: list[dict[str, str]],
    system: str | None = None,
    max_tokens: int = 16000,
    effort: str | None = None,
) -> str:
    """Send a conversation (user/assistant turns) and return the reply text.

    `effort` is low|medium|high|xhigh|max; defaults to LLM_EFFORT from .env.
    Raises LLMRefusal if the request is declined.
    """
    kwargs = {}
    if system:
        kwargs["system"] = system
    log.debug("LLM request: model=%s messages=%d", model_name(), len(messages))
    try:
        response = _client().beta.messages.create(
            model=model_name(),
            max_tokens=max_tokens,
            messages=messages,
            output_config={"effort": effort or os.getenv("LLM_EFFORT", DEFAULT_EFFORT)},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            **kwargs,
        )
    except anthropic.AuthenticationError as exc:
        raise LLMError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Rate limited by Anthropic. Wait a minute and try again.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the Anthropic API. Check your internet connection.") from exc
    usage.record(response.model, response.usage)
    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise LLMRefusal(f"Request declined (category={category})")
    if response.stop_reason == "max_tokens":
        log.warning("LLM reply hit max_tokens=%d and was truncated", max_tokens)
    return "".join(b.text for b in response.content if b.type == "text")

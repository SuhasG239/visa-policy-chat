"""Opt-in local token-usage log, and cost estimates from it.

When LLM_USAGE_LOG is set in .env, each LLM call appends one JSON line with the
timestamp, model, and token counts. Never conversation content.

    python -m app.usage     # totals, actual cost, and what the same calls would cost on other models
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# USD per 1M tokens: input, output, cache read. Cache writes bill at 1.25x input.
# Source: Anthropic pricing as of 2026-09; check current prices before relying on these.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}


def _log_path() -> Path | None:
    value = os.getenv("LLM_USAGE_LOG", "").strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def record(model: str, u: Any) -> None:
    """Append one call's token counts to the usage log (if enabled)."""
    path = _log_path()
    if path is None:
        return
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,   # includes thinking tokens
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as exc:   # never let usage logging break an answer
        log.warning("Could not write usage log %s: %s", path, exc)


def cost(row: dict[str, Any], model: str) -> float:
    """USD cost of one logged call if billed at `model`'s prices."""
    p_in, p_out, p_cache = PRICES[model]
    return (
        row["input_tokens"] * p_in
        + row["output_tokens"] * p_out
        + row["cache_read_input_tokens"] * p_cache
        + row["cache_creation_input_tokens"] * p_in * 1.25
    ) / 1_000_000


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals, actual cost, and per-model cost estimates for logged calls."""
    n = len(rows)
    actual = sum(cost(r, r["model"]) for r in rows if r["model"] in PRICES)
    estimates = {m: sum(cost(r, m) for r in rows) for m in PRICES}
    return {
        "calls": n,
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "actual_cost_usd": round(actual, 4),
        "estimated_cost_usd": {m: round(c, 4) for m, c in estimates.items()},
        "estimated_cost_per_call_usd": {m: round(c / n, 5) if n else 0 for m, c in estimates.items()},
    }


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    path = _log_path()
    if path is None or not path.exists():
        raise SystemExit("No usage log yet. Set LLM_USAGE_LOG in .env and ask some questions.")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    print(json.dumps(summary(rows), indent=2))
    print("Note: estimates reuse the logged token counts. A different model may think "
          "(and so bill output tokens) more or less for the same question.")


if __name__ == "__main__":
    main()

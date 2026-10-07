"""Shared ingest helpers: load sources.yaml and make polite HTTP requests."""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
import yaml

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = Path(__file__).resolve().parent / "sources.yaml"
RAW_DIR = ROOT / "data" / "raw"
MANIFEST_FILE = ROOT / "data" / "manifest.json"
MAX_RETRIES = 3


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    return yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))


def load_sources(source_type: str | None = None) -> list[dict[str, Any]]:
    """All sources from sources.yaml, optionally filtered by type."""
    sources = _config()["sources"]
    return [s for s in sources if source_type is None or s["type"] == source_type]


class PoliteClient:
    """requests wrapper: descriptive User-Agent, delay between calls, 503 retry."""

    def __init__(self) -> None:
        http = _config()["http"]
        self.delay = float(http["request_delay_seconds"])
        self.timeout = float(http["timeout_seconds"])
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": http["user_agent"], "Accept-Encoding": "gzip"})
        self._last = 0.0

    def get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        """GET with rate limiting. Raises for non-2xx after retries."""
        for attempt in range(1, MAX_RETRIES + 1):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            log.debug("GET %s %s", url, params or "")
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code in (429, 503) and attempt < MAX_RETRIES:
                retry_after = min(int(resp.headers.get("Retry-After", "30")), 300)
                log.warning("%s returned %d; retrying in %ds", url, resp.status_code, retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")

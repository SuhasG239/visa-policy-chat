"""Fetch USCIS Policy Manual chapters and newsroom alerts (HTML; no API).

Pages are cached for the day in data/raw/<source id>_<date>/, so re-running a
sync doesn't hit uscis.gov again. Requests are spaced by the source's
request_delay_seconds (uscis.gov robots.txt asks for a 10-second crawl delay).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ingest.config import RAW_DIR, PoliteClient, load_sources
from ingest.parse_uscis import Alert, PmChapter, parse_alert_body, parse_alert_list, parse_pm_chapter

log = logging.getLogger(__name__)


@dataclass
class UscisFetch:
    """Result of fetching one USCIS source."""

    source: dict[str, Any]
    fetched_at: str
    raw_dir: str
    chapters: list[PmChapter] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


class _DayCache:
    """Fetch-once-per-day page cache in data/raw/<source id>_<date>/."""

    def __init__(self, src: dict[str, Any], today: date) -> None:
        self.dir = RAW_DIR / f"{src['id']}_{today.isoformat()}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.client: PoliteClient | None = None
        self.delay = src.get("request_delay_seconds")

    def get(self, url: str, name: str) -> str:
        path: Path = self.dir / name
        if path.exists():
            return path.read_text(encoding="utf-8")
        self.client = self.client or PoliteClient(self.delay)
        html = self.client.get(url).text
        path.write_text(html, encoding="utf-8")
        return html


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_policy_manual(src: dict[str, Any], today: date) -> UscisFetch:
    """Download (or reuse today's copies of) each configured chapter and parse it."""
    cache = _DayCache(src, today)
    result = UscisFetch(src, _now(), cache.dir.name)
    for n in src["chapters"]:
        html = cache.get(src["chapter_url"].format(n=n), f"chapter-{n}.html")
        chapter = parse_pm_chapter(html, n, src.get("skip_headings"))
        result.chapters.append(chapter)
        log.info("%s: chapter %d (%s), %d paragraphs, %d updates",
                 src["id"], n, chapter.title, len(chapter.paragraphs), len(chapter.updates))
    return result


def _matches(text: str, terms: list[str]) -> bool:
    """Whole-word, case-insensitive match ("OPT" must not match "adopted")."""
    return any(re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", text, re.IGNORECASE) for t in terms)


def keep_alert(alert: Alert, src: dict[str, Any]) -> bool:
    """F-1 alerts, plus court orders whose text reaches student benefit requests."""
    text = f"{alert.title} {alert.summary} {alert.body}"
    if _matches(text, src["include_terms"]):
        return True
    return _matches(text, src["court_order_terms"]) and _matches(text, src["court_order_relevance_terms"])


def fetch_alerts(src: dict[str, Any], today: date) -> UscisFetch:
    """Scan the newest alert list pages; keep F-1 alerts and court orders, with full text."""
    cache = _DayCache(src, today)
    result = UscisFetch(src, _now(), cache.dir.name)
    terms = src["include_terms"] + src["court_order_terms"]
    for page in range(int(src["max_pages"])):
        url = src["url"] + (f"?page={page}" if page else "")
        rows = parse_alert_list(cache.get(url, f"list-{page}.html"))
        if not rows:
            break
        for alert in rows:
            if not _matches(f"{alert.title} {alert.summary}", terms):
                continue   # skip the detail page unless the summary looks relevant
            slug = re.sub(r"[^a-z0-9-]", "", alert.url.rsplit("/", 1)[-1])[:80]
            alert.body = parse_alert_body(cache.get(alert.url, f"alert-{slug}.html"))
            if keep_alert(alert, src):
                result.alerts.append(alert)
    log.info("%s: kept %d alerts", src["id"], len(result.alerts))
    return result


def fetch_all(source_id: str | None = None, today: date | None = None) -> list[UscisFetch]:
    """Fetch every USCIS source in sources.yaml (or just `source_id`)."""
    today = today or date.today()
    results = []
    for src in load_sources("uscis_pm") + load_sources("uscis_alert"):
        if source_id and src["id"] != source_id:
            continue
        results.append(fetch_policy_manual(src, today) if src["type"] == "uscis_pm" else fetch_alerts(src, today))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for r in fetch_all():
        for a in r.alerts:
            print(f"{a.date}  {a.title[:90]}")

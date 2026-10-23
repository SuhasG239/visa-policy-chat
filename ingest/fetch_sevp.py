"""Fetch and parse Study in the States (SEVP) pages for F-1 students.

Pages are listed in sources.yaml and cached for the day in
data/raw/<source id>_<date>/. Each page's guidance sits in the article body
under .region-content: <h2> headings are sections, <h3>/<h4> (including FAQ
accordion headings) are subsections, and a "Last updated: <date>" line gives
the page's own update date.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from ingest.config import RAW_DIR, PoliteClient, load_sources
from ingest.parse import Paragraph

log = logging.getLogger(__name__)

LAST_UPDATED = re.compile(r"Last updated:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "section"


def _clean(text: str) -> str:
    return " ".join(text.replace(" ", " ").split())


@dataclass
class SevpPage:
    """One Study in the States page as paragraphs under (section, subsection) slugs."""

    path: str                                   # "students/maintaining-status"
    url: str
    title: str
    updated: str                                # YYYY-MM-DD, or "" if the page doesn't say
    paragraphs: list[Paragraph] = field(default_factory=list)
    headings: dict[tuple[str, ...], str] = field(default_factory=dict)
    anchors: dict[tuple[str, ...], str] = field(default_factory=dict)


def parse_page(html: str, path: str, url: str, skip_headings: list[str] | None = None) -> SevpPage:
    """Parse one page into paragraphs grouped by heading."""
    soup = BeautifulSoup(html, "lxml")
    title = _clean(soup.title.get_text()).split(" | ")[0] if soup.title else path
    region = soup.select_one(".region-content") or soup
    m = LAST_UPDATED.search(region.get_text(" "))
    updated = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat() if m else ""
    page = SevpPage(path, url, title, updated)
    skip = [s.lower() for s in (skip_headings or [])]
    bodies = region.select("article .field--name-body") or [region]
    cur: tuple[str, ...] = ()
    skipping = False
    for body in bodies:
        for el in body.find_all(["h2", "h3", "h4", "p", "li", "table"]):
            if el.name in ("h2", "h3", "h4"):
                text = _clean(el.get_text(" "))
                if not text or text == title:
                    continue
                if el.name == "h2":
                    cur = (slug(text),)
                    skipping = any(s in text.lower() for s in skip) and "f-1" not in text.lower()
                else:
                    cur = cur[:1] + (slug(text),)
                page.headings[cur] = text
                if el.get("id"):
                    page.anchors[cur] = el["id"]
                continue
            if skipping or el.find_parent(["li", "table"]) is not None or el.find_parent(["h2", "h3", "h4"]):
                continue
            text = _clean(el.get_text(" "))
            if not text or LAST_UPDATED.match(text):
                continue
            page.paragraphs.append(Paragraph(cur, f"- {text}" if el.name == "li" else text))
    return page


@dataclass
class SevpFetch:
    """Result of fetching the Study in the States source."""

    source: dict[str, Any]
    fetched_at: str
    raw_dir: str
    pages: list[SevpPage]


def fetch(src: dict[str, Any], today: date | None = None) -> SevpFetch:
    """Download (or reuse today's copies of) every configured page and parse it."""
    today = today or date.today()
    raw_dir = RAW_DIR / f"{src['id']}_{today.isoformat()}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client: PoliteClient | None = None
    pages = []
    for path in src["pages"]:
        url = f"{src['base_url']}/{path}"
        cached = raw_dir / f"{slug(path)}.html"
        if cached.exists():
            html = cached.read_text(encoding="utf-8")
        else:
            client = client or PoliteClient(src.get("request_delay_seconds"))
            html = client.get(url).text
            cached.write_text(html, encoding="utf-8")
        page = parse_page(html, path, url, src.get("skip_headings"))
        log.info("%s: %s (%d paragraphs, updated %s)", src["id"], page.title, len(page.paragraphs), page.updated or "?")
        pages.append(page)
    return SevpFetch(src, datetime.now(timezone.utc).isoformat(timespec="seconds"), raw_dir.name, pages)


def fetch_all(source_id: str | None = None) -> list[SevpFetch]:
    """Fetch every SEVP source in sources.yaml (or just `source_id`)."""
    return [fetch(s) for s in load_sources("sevp") if not source_id or s["id"] == source_id]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for r in fetch_all():
        print(f"{len(r.pages)} pages; newest update {max(p.updated for p in r.pages)}")

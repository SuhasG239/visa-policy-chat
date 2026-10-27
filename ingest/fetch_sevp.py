"""Fetch and parse official guidance web pages: Study in the States (SEVP) and
USCIS program pages (e.g. H-1B Specialty Occupations).

Pages are listed in sources.yaml and cached for the day in
data/raw/<source id>_<date>/. Each page's guidance sits in the article body
under .region-content. By default <h2> headings are sections and <h3>/<h4>
(including FAQ accordion headings) are subsections; a source can instead list
`section_tags` (USCIS pages use <h4> for sections) and set `bold_subheadings`
so a paragraph that is all bold text starts a subsection. The page's own update
date comes from "Last updated: <Month D, YYYY>" (SEVP) or
"Last Reviewed/Updated: MM/DD/YYYY" (USCIS).
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
LAST_REVIEWED = re.compile(r"Last Reviewed/Updated:\s*(\d{2}/\d{2}/\d{4})")


def _updated(text: str) -> str:
    """The page's own update date as YYYY-MM-DD, or "" if it doesn't say."""
    if m := LAST_UPDATED.search(text):
        return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    if m := LAST_REVIEWED.search(text):
        return datetime.strptime(m.group(1), "%m/%d/%Y").date().isoformat()
    return ""


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


def _all_bold(el) -> bool:
    bold = "".join(b.get_text() for b in el.find_all(["strong", "b"]))
    return bool(bold.strip()) and _clean(bold) == _clean(el.get_text(" "))


def parse_page(html: str, path: str, url: str, skip_headings: list[str] | None = None,
               section_tags: list[str] | None = None, bold_subheadings: bool = False) -> SevpPage:
    """Parse one page into paragraphs grouped by (section, subsection) heading."""
    soup = BeautifulSoup(html, "lxml")
    title = _clean(soup.title.get_text()).split(" | ")[0] if soup.title else path
    region = soup.select_one(".region-content") or soup
    page = SevpPage(path, url, title, _updated(region.get_text(" ")))
    skip = [s.lower() for s in (skip_headings or [])]
    sections = set(section_tags or ["h2"])
    bodies = region.select("article .field--name-body") or [region]
    cur: tuple[str, ...] = ()
    skipping = False
    for body in bodies:
        for el in body.find_all(["h2", "h3", "h4", "p", "li", "table"]):
            heading = el.name in ("h2", "h3", "h4")
            if not heading and bold_subheadings and el.name == "p" and cur and _all_bold(el):
                heading = True   # a bold-only paragraph labels a subsection
            if heading:
                text = _clean(el.get_text(" ")).rstrip(":")
                if not text or text == title:
                    continue
                if el.name in sections:
                    cur = (slug(text),)
                    skipping = any(s in text.lower() for s in skip) and "f-1" not in text.lower()
                else:
                    cur = cur[:1] + (slug(text),)
                page.headings[cur] = text
                if el.name == "p":   # keep bold labels (often FAQ questions) in the text too
                    page.paragraphs.append(Paragraph(cur, text))
                if el.get("id"):
                    page.anchors[cur] = el["id"]
                continue
            if skipping or el.find_parent(["li", "table"]) is not None or el.find_parent(["h2", "h3", "h4"]):
                continue
            text = _clean(el.get_text(" "))
            if not text or LAST_UPDATED.match(text) or LAST_REVIEWED.match(text):
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
        page = parse_page(html, path, url, src.get("skip_headings"),
                          src.get("section_tags"), bool(src.get("bold_subheadings")))
        log.info("%s: %s (%d paragraphs, updated %s)", src["id"], page.title, len(page.paragraphs), page.updated or "?")
        pages.append(page)
    return SevpFetch(src, datetime.now(timezone.utc).isoformat(timespec="seconds"), raw_dir.name, pages)


def fetch_all(source_id: str | None = None) -> list[SevpFetch]:
    """Fetch every guidance-page source (SEVP and USCIS pages) in sources.yaml, or just `source_id`."""
    srcs = load_sources("sevp") + load_sources("uscis_web")
    return [fetch(s) for s in srcs if not source_id or s["id"] == source_id]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for r in fetch_all():
        print(f"{len(r.pages)} pages; newest update {max(p.updated for p in r.pages)}")

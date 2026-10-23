"""Parse USCIS HTML pages: Policy Manual chapters, their Updates tab, and newsroom alerts.

Policy Manual chapter pages keep the guidance in div#guidance: lettered sections
are <h2> ("A. General"), numbered subsections are <h3> ("3. STEM OPT Extension"),
and each heading carries an anchor such as <a id="S-C-3">. Footnote markers like
"[1]" are dropped; the footnotes themselves are citations, not guidance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from ingest.parse import Paragraph

FOOTNOTE = re.compile(r"\s*\[\d+\]")
SECTION_HEADING = re.compile(r"^([A-Z])\.\s+(.*)$")
SUBSECTION_HEADING = re.compile(r"^(\d+)\.\s+(.*)$")
STOP_HEADINGS = {"Footnotes", "Resources", "Appendices", "Updates"}
LONG_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}")


def _clean(text: str) -> str:
    return " ".join(FOOTNOTE.sub("", text).replace(" ", " ").split())


@dataclass
class PmUpdate:
    """One entry from a chapter's Updates tab (a policy alert or technical update)."""

    title: str
    date: str          # YYYY-MM-DD
    body: str
    effective: str | None = None


@dataclass
class PmChapter:
    """One Policy Manual chapter, as paragraphs keyed by (section letter, subsection number)."""

    number: int
    title: str                                     # "Practical Training"
    paragraphs: list[Paragraph] = field(default_factory=list)
    headings: dict[tuple[str, ...], str] = field(default_factory=dict)   # ("C",) -> "F-1 Student ..."
    anchors: dict[tuple[str, ...], str] = field(default_factory=dict)    # ("C", "3") -> "S-C-3"
    updates: list[PmUpdate] = field(default_factory=list)


def _effective_date(text: str) -> str | None:
    """'This policy became effective October 23, 2026' -> '2026-10-23'."""
    m = re.search(r"effective(?:\s+on)?\s+(" + LONG_DATE.pattern + ")", text)
    return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat() if m else None


def _anchor(tag: Tag) -> str | None:
    a = tag.find("a", id=True) or tag.find("a", attrs={"name": True})
    return (a.get("id") or a.get("name")) if a else None


def parse_pm_chapter(html: str, number: int, skip_headings: list[str] | None = None) -> PmChapter:
    """Parse one chapter page into paragraphs under their section and subsection."""
    soup = BeautifulSoup(html, "lxml")
    h1 = _clean(soup.find("h1").get_text(" ")) if soup.find("h1") else f"Chapter {number}"
    chapter = PmChapter(number=number, title=re.sub(r"^Chapter\s+\d+\s*-\s*", "", h1))
    body = soup.select_one("#guidance .field--name-body") or soup.select_one("#guidance")
    skip = [s.lower() for s in (skip_headings or [])]
    path: tuple[str, ...] = ()
    skipping = False
    for el in body.find_all(["h2", "h3", "p", "li", "table"]) if body else []:
        if el.name in ("h2", "h3"):
            text = _clean(el.get_text(" "))
            if el.name == "h2" and text in STOP_HEADINGS:
                break
            m = (SECTION_HEADING if el.name == "h2" else SUBSECTION_HEADING).match(text)
            if not m:
                continue
            path = (m.group(1),) if el.name == "h2" else (path[:1] + (m.group(1),))
            if el.name == "h2":
                skipping = any(s in text.lower() for s in skip) and "f-1" not in text.lower()
            chapter.headings[path] = m.group(2)
            if _anchor(el):
                chapter.anchors[path] = _anchor(el)
            continue
        if skipping or el.find_parent(["li", "table"]) is not None:
            continue   # nested items are part of their parent's text
        text = _clean(el.get_text(" "))
        if el.name == "li":
            text = f"- {text}"
        if text:
            chapter.paragraphs.append(Paragraph(path, text))

    for entry in soup.select("#updates .pm-updates"):
        title_el, time_el = entry.select_one(".pm-resource__update_header"), entry.select_one("time")
        content = entry.select_one(".pm-resource__content")
        if not (title_el and time_el):
            continue
        text = _clean(content.get_text(" ")) if content else ""
        chapter.updates.append(PmUpdate(_clean(title_el.get_text(" ")), time_el["datetime"][:10], text, _effective_date(text)))
    return chapter


@dataclass
class Alert:
    """A USCIS newsroom alert."""

    title: str
    url: str
    date: str          # YYYY-MM-DD
    summary: str
    body: str = ""


def parse_alert_list(html: str, base: str = "https://www.uscis.gov") -> list[Alert]:
    """Rows of the newsroom alerts list page."""
    soup = BeautifulSoup(html, "lxml")
    alerts = []
    for row in soup.select(".views-row"):
        link, time_el = row.select_one(".views-field-title a"), row.select_one("time")
        if not (link and time_el):
            continue
        summary = row.select_one(".views-field-body")
        alerts.append(Alert(
            title=_clean(link.get_text(" ")),
            url=base + link["href"] if link["href"].startswith("/") else link["href"],
            date=time_el["datetime"][:10],
            summary=_clean(summary.get_text(" ")) if summary else "",
        ))
    return alerts


def parse_alert_body(html: str) -> str:
    """Main text of a single alert page."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one("article .field--name-body") or soup.select_one(".field--name-body")
    if not body:
        return ""
    parts = [_clean(el.get_text(" ")) for el in body.find_all(["p", "li"]) if el.find_parent("li") is None]
    return "\n".join(p for p in parts if p) or _clean(body.get_text(" "))

"""Chunks for USCIS sources: Policy Manual sections, policy alerts, and newsroom alerts.

Policy Manual: one replacement unit (section_key) per lettered section of a
chapter, split on its numbered subsections when long. Citations look like
"USCIS Policy Manual, Vol. 2, Part F, Ch. 5.C.3" and link to the section anchor.
"""

from __future__ import annotations

import re
from typing import Any

from ingest.chunk import MAX_TOKENS, Counter, _default_counter, _long_date, _split, _text
from ingest.parse import Paragraph
from ingest.parse_uscis import Alert, PmChapter, PmUpdate

HEADING_CHARS = 70
UPDATES_URL = "https://www.uscis.gov/policy-manual/updates"


def _short(text: str) -> str:
    return text if len(text) <= HEADING_CHARS else text[: HEADING_CHARS - 1] + "…"


def _pm_cite(name: str, chapter: int, paths: list[tuple[str, ...]]) -> tuple[str, tuple[str, ...]]:
    """'Ch. 5.C.3', or 'Ch. 5.C.1–3' when a piece spans subsections; plus the path to link to."""
    first, last = paths[0], paths[-1]
    if first == last or not first:
        return f"{name}, Ch. {chapter}" + "".join(f".{p}" for p in first), first
    if first[0] != last[0]:
        return f"{name}, Ch. {chapter}.{first[0]}–{last[0]}", first[:1]
    if len(first) > 1 and len(last) > 1 and first[1] != last[1]:
        return f"{name}, Ch. {chapter}.{first[0]}.{first[1]}–{last[1]}", first
    return f"{name}, Ch. {chapter}.{first[0]}", first[:1]


def chunk_policy_manual(chapters: list[PmChapter], src: dict[str, Any], fetched_at: str,
                        count_tokens: Counter | None = None) -> list[dict[str, Any]]:
    """Chunks for every section of every chapter."""
    count = count_tokens or _default_counter()
    name = src["name"]
    base_id = src["id"]
    chunks: list[dict[str, Any]] = []
    for ch in chapters:
        updated = max((u.date for u in ch.updates), default="")
        scope = list(dict.fromkeys(src["scope"] + src.get("chapter_scope", {}).get(ch.number, [])))
        units: dict[tuple[str, ...], list[Paragraph]] = {}
        for p in ch.paragraphs:
            units.setdefault(p.path[:1], []).append(p)
        for unit, paras in units.items():
            section_key = f"{base_id}-ch{ch.number}-s{unit[0] if unit else '0'}"
            pieces = _split(paras, 1, MAX_TOKENS - 70, count)
            for i, piece in enumerate(pieces, start=1):
                citation, link = _pm_cite(name, ch.number, [p.path for p in piece])
                trail = [f"Ch. {ch.number} {ch.title}"] + [
                    f"{link[:k][-1]}. {ch.headings[link[:k]]}" for k in range(1, len(link) + 1) if link[:k] in ch.headings
                ]
                anchor = ch.anchors.get(link) or ch.anchors.get(link[:1])
                url = src["chapter_url"].format(n=ch.number) + (f"#{anchor}" if anchor else "")
                chunks.append({
                    "chunk_id": f"{section_key}-{i:03d}",
                    "section_key": section_key,
                    "text": f"{citation} — {' > '.join(_short(t) for t in trail)}\n\n{_text(piece)}",
                    "source": "uscis_pm",
                    "citation": citation,
                    "title": " > ".join(trail[:2]),
                    "url": url,
                    "effective_date": None,
                    "source_updated": updated,
                    "fetched_at": fetched_at,
                    "doc_type": "policy_guidance",
                    "scope": scope,
                })
    return chunks


def _words(text: str, terms: list[str]) -> bool:
    return any(re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", text, re.IGNORECASE) for t in terms)


def policy_alerts(chapters: list[PmChapter], terms: list[str]) -> list[tuple[PmUpdate, list[int]]]:
    """Distinct substantive F-1 updates across chapters, with the chapters each affects."""
    seen: dict[tuple[str, str], tuple[PmUpdate, list[int]]] = {}
    for ch in chapters:
        for u in ch.updates:
            if "replaces all instances of the term" in u.body or not _words(f"{u.title} {u.body}", terms):
                continue   # wording-only technical updates, or not about F-1
            seen.setdefault((u.date, u.title), (u, []))[1].append(ch.number)
    return sorted(seen.values(), key=lambda x: x[0].date, reverse=True)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def chunk_policy_alerts(chapters: list[PmChapter], src: dict[str, Any], fetched_at: str,
                        count_tokens: Counter | None = None) -> list[dict[str, Any]]:
    """One chunk per substantive policy alert or technical update affecting the chapters."""
    count = count_tokens or _default_counter()
    chunks = []
    for u, affected in policy_alerts(chapters, src["alert_include_terms"]):
        section_key = f"uscis-pm-update-{u.date}-{_slug(u.title)}"
        citation = f"USCIS Policy Manual update, {_long_date(u.date)}"
        header = (f"{citation} — {u.title}\nAffects {src['name']}, Ch. {', '.join(map(str, affected))}."
                  + (f" Effective {_long_date(u.effective)}." if u.effective else ""))
        pieces = _split([Paragraph((), u.body)], 0, MAX_TOKENS - count(header) - 10, count)
        for i, piece in enumerate(pieces, start=1):
            chunks.append({
                "chunk_id": f"{section_key}-{i:03d}", "section_key": section_key,
                "text": f"{header}\n\n{_text(piece)}", "source": "uscis_pm", "citation": citation,
                "title": u.title, "url": UPDATES_URL, "effective_date": u.effective,
                "source_updated": u.date, "fetched_at": fetched_at, "doc_type": "policy_guidance",
                "scope": list(src["scope"]),
            })
    return chunks


def chunk_alerts(alerts: list[Alert], src: dict[str, Any], fetched_at: str,
                 count_tokens: Counter | None = None) -> list[dict[str, Any]]:
    """Chunks for kept newsroom alerts; court orders get doc_type court_order."""
    count = count_tokens or _default_counter()
    chunks = []
    for a in alerts:
        court = _words(f"{a.title} {a.body}", src["court_order_terms"])
        label = "Court order" if court else "USCIS alert"
        section_key = f"uscis-alert-{a.date}-{_slug(a.title)}"
        citation = f"USCIS alert, {_long_date(a.date)}"
        header = f"{citation} — {label}: {a.title}"
        paras = [Paragraph((), t) for t in (a.body or a.summary).split("\n") if t.strip()]
        for i, piece in enumerate(_split(paras, 0, MAX_TOKENS - count(header) - 10, count), start=1):
            chunks.append({
                "chunk_id": f"{section_key}-{i:03d}", "section_key": section_key,
                "text": f"{header}\n\n{_text(piece)}", "source": "uscis_alert", "citation": citation,
                "title": a.title, "url": a.url, "effective_date": None, "source_updated": a.date,
                "fetched_at": fetched_at, "doc_type": "court_order" if court else "alert",
                "scope": list(src["scope"]),
            })
    return chunks

"""Chunks for Study in the States pages: one replacement unit per <h2> section."""

from __future__ import annotations

from typing import Any

from ingest.chunk import MAX_TOKENS, Counter, _default_counter, _split, _text
from ingest.fetch_sevp import SevpPage, slug
from ingest.parse import Paragraph


def chunk_sevp(pages: list[SevpPage], src: dict[str, Any], fetched_at: str,
               count_tokens: Counter | None = None) -> list[dict[str, Any]]:
    """Chunks for every section of every page, cited as 'Study in the States: <page> (<section>)'."""
    count = count_tokens or _default_counter()
    chunks: list[dict[str, Any]] = []
    for page in pages:
        scope = list(dict.fromkeys(src["scope"] + list(src["pages"].get(page.path) or [])))
        units: dict[tuple[str, ...], list[Paragraph]] = {}
        for p in page.paragraphs:
            units.setdefault(p.path[:1], []).append(p)
        page_slug = slug(page.path.rsplit("/", 1)[-1])
        for unit, paras in units.items():
            section = page.headings.get(unit, "")
            section_key = f"sevp-{page_slug}-{unit[0] if unit else 'intro'}"
            citation = f"{src['name']}: {page.title}" + (f" ({section})" if section else "")
            for i, piece in enumerate(_split(paras, 1, MAX_TOKENS - 60, count), start=1):
                sub = piece[0].path if len(piece[0].path) > 1 and all(p.path == piece[0].path for p in piece) else unit
                anchor = page.anchors.get(sub) or page.anchors.get(unit)
                trail = " > ".join(t for t in (page.title, section, page.headings.get(sub, "") if sub != unit else "") if t)
                chunks.append({
                    "chunk_id": f"{section_key}-{i:03d}",
                    "section_key": section_key,
                    "text": f"{citation} — {trail}\n\n{_text(piece)}",
                    "source": "sevp",
                    "citation": citation,
                    "title": " > ".join(t for t in (page.title, section) if t),
                    "url": page.url + (f"#{anchor}" if anchor else ""),
                    "effective_date": None,
                    "source_updated": page.updated,
                    "fetched_at": fetched_at,
                    "doc_type": "policy_guidance",
                    "scope": scope,
                })
    return chunks

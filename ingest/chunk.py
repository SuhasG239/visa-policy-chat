"""Turn parsed sections into chunks with metadata and stable IDs.

Chunks follow the regulation's own outline. A "unit" (the replacement unit,
identified by section_key) is a top-level paragraph such as 8 CFR 214.1(a), or
(f)(n) for sources limited to one paragraph such as 214.2(f). Units longer than
the token budget are split on sub-paragraph boundaries; each piece carries its
own citation, and a header line with the citation and parent headings.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ingest.parse import Paragraph, Section

MAX_TOKENS = 400        # whole chunk, header included
HEADER_RESERVE = 70     # tokens kept free for the header line
HEADING_CHARS = 70      # truncate long headings in the header trail
SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-Z(“\"])")

Counter = Callable[[str], int]


def _default_counter() -> Counter:
    from app import embed
    return embed.count_tokens


def _cite_path(path: tuple[str, ...]) -> str:
    return "".join(f"({p})" for p in path)


def _common_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    prefix = paths[0]
    for p in paths[1:]:
        n = 0
        while n < min(len(prefix), len(p)) and prefix[n] == p[n]:
            n += 1
        prefix = prefix[:n]
    return prefix


def citation_for(title: str, number: str, paths: list[tuple[str, ...]]) -> tuple[str, tuple[str, ...]]:
    """Human citation for paragraphs spanning `paths`, plus the path to link to."""
    prefix = _common_prefix(paths)
    first, last = paths[0], paths[-1]
    base = f"{title} CFR {number}{_cite_path(prefix)}"
    if first == prefix or len(last) <= len(prefix) or first[len(prefix)] == last[len(prefix)]:
        return base, prefix
    return f"{base}({first[len(prefix)]})–({last[len(prefix)]})", first[: len(prefix) + 1]


def _text(paras: list[Paragraph]) -> str:
    return "\n".join(p.text for p in paras)


def _split_long_paragraph(p: Paragraph, budget: int, count: Counter) -> list[list[Paragraph]]:
    pieces: list[list[Paragraph]] = []
    cur = ""
    for sentence in SENTENCE_END.split(p.text):
        while count(sentence) > budget:   # one enormous sentence: cut on words
            words = sentence.split()
            half = max(1, len(words) // 2)
            head, sentence = " ".join(words[:half]), " ".join(words[half:])
            if cur:
                pieces.append([Paragraph(p.path, cur)])
                cur = ""
            pieces.append([Paragraph(p.path, head)])
        candidate = f"{cur} {sentence}".strip()
        if cur and count(candidate) > budget:
            pieces.append([Paragraph(p.path, cur)])
            cur = sentence
        else:
            cur = candidate
    if cur:
        pieces.append([Paragraph(p.path, cur)])
    return pieces


def _split(paras: list[Paragraph], depth: int, budget: int, count: Counter) -> list[list[Paragraph]]:
    """Split paragraphs into pieces under `budget`, preferring outline boundaries."""
    if count(_text(paras)) <= budget:
        return [paras]
    groups: dict[tuple[str, ...], list[Paragraph]] = {}
    for p in paras:
        key = p.path[: depth + 1] if len(p.path) > depth else ("__intro__",)
        groups.setdefault(key, []).append(p)
    if list(groups) == [("__intro__",)]:   # nothing deeper: pack paragraph by paragraph
        out: list[list[Paragraph]] = []
        for p in paras:
            out.extend(_split_long_paragraph(p, budget, count) if count(p.text) > budget else [[p]])
        return _pack(out, budget, count)
    if len(groups) == 1:
        return _split(paras, depth + 1, budget, count)
    pieces: list[list[Paragraph]] = []
    for group in groups.values():
        pieces.extend(_split(group, depth + 1, budget, count))
    return _pack(pieces, budget, count)


def _pack(pieces: list[list[Paragraph]], budget: int, count: Counter) -> list[list[Paragraph]]:
    """Merge consecutive small pieces while they fit."""
    out: list[list[Paragraph]] = []
    for piece in pieces:
        if out and count(_text(out[-1] + piece)) <= budget:
            out[-1] = out[-1] + piece
        else:
            out.append(piece)
    return out


def _trail(sec: Section, path: tuple[str, ...]) -> str:
    names = [sec.headings[path[:i]] for i in range(1, len(path) + 1) if path[:i] in sec.headings]
    names = names or [sec.heading.rstrip(".")]
    return " > ".join(n if len(n) <= HEADING_CHARS else n[: HEADING_CHARS - 1] + "…" for n in names)


def _unit_title(sec: Section, unit: tuple[str, ...]) -> str:
    label = f"§ {sec.number}{_cite_path(unit)}"
    heading = sec.headings.get(unit) or (sec.heading if not unit else "")
    return f"{label} {heading}".strip()


def chunk_ecfr(
    sections: list[Section],
    src: dict[str, Any],
    fetched_at: str,
    amended: dict[str, str],
    count_tokens: Counter | None = None,
) -> list[dict[str, Any]]:
    """Chunks (dicts ready for store.upsert_chunks, minus embeddings) for one eCFR source."""
    count = count_tokens or _default_counter()
    title = str(src["title"])
    only = src.get("paragraph")
    budget = MAX_TOKENS - HEADER_RESERVE
    chunks: list[dict[str, Any]] = []

    for sec in sections:
        paras = [p for p in sec.paragraphs if not only or p.path[:1] == (only,)]
        units: dict[tuple[str, ...], list[Paragraph]] = {}
        for p in paras:
            units.setdefault(p.path[: 2 if only else 1], []).append(p)
        notes = [n for n in sec.effective_date_notes if not only or f"({only})" in n]
        if notes:
            units[("effective-date-notes",)] = [Paragraph((), n) for n in notes]

        for unit, unit_paras in units.items():
            section_key = f"ecfr-{title}-{sec.number}" + "".join(f"-{u}" for u in unit)
            is_note = unit == ("effective-date-notes",)
            scope = list(src["scope"]) + list(src.get("paragraph_scope", {}).get("-".join(unit), []))
            pieces = [unit_paras] if is_note else _split(unit_paras, len(unit), budget, count)
            for i, piece in enumerate(pieces, start=1):
                if is_note:
                    citation, link = f"{title} CFR {sec.number} (effective date notes)", ()
                else:
                    citation, link = citation_for(title, sec.number, [p.path for p in piece])
                url = f"https://www.ecfr.gov/current/title-{title}/section-{sec.number}"
                if link:
                    url += f"#p-{sec.number}{_cite_path(link)}"
                header = f"{citation} — {_trail(sec, link)}"
                chunks.append({
                    "chunk_id": f"{section_key}-{i:03d}",
                    "section_key": section_key,
                    "text": f"{header}\n\n{_text(piece)}",
                    "source": "ecfr",
                    "citation": citation,
                    "title": "Effective date notes" if is_note else _unit_title(sec, unit),
                    "url": url,
                    "effective_date": None,
                    "source_updated": amended.get(sec.number, ""),
                    "fetched_at": fetched_at,
                    "doc_type": "regulation",
                    "scope": list(dict.fromkeys(scope)),
                })
    return chunks

"""Turn parsed sections into chunks with metadata and stable IDs.

Chunks follow the regulation's own outline. A "unit" (the replacement unit,
identified by section_key) is a top-level paragraph such as 8 CFR 214.1(a), or
(f)(n) for sources limited to one paragraph such as 214.2(f). Units longer than
the token budget are split on sub-paragraph boundaries; each piece carries its
own citation, and a header line with the citation and parent headings.

Federal Register documents become summary chunks (status, dates, abstract);
every piece repeats the status line so a proposed rule is never unlabeled.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
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
    # Name the exact starting paragraph: a piece that begins partway through (ii)
    # is "(ii)(E)–(iii)", not "(ii)–(iii)", which would claim all of (ii).
    return f"{base}{_cite_path(first[len(prefix):])}–({last[len(prefix)]})", first


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
                pieces.append([replace(p, text=cur)])
                cur = ""
            pieces.append([replace(p, text=head)])
        candidate = f"{cur} {sentence}".strip()
        if cur and count(candidate) > budget:
            pieces.append([replace(p, text=cur)])
            cur = sentence
        else:
            cur = candidate
    if cur:
        pieces.append([replace(p, text=cur)])
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


def _split_terms(paras: list[Paragraph], budget: int, count: Counter) -> list[list[Paragraph]]:
    """A "Definitions" section: keep each term's paragraphs together, then pack."""
    groups: dict[str, list[Paragraph]] = {}
    for p in paras:
        groups.setdefault(p.term, []).append(p)
    pieces: list[list[Paragraph]] = []
    for group in groups.values():
        pieces.extend(_split(group, 0, budget, count))
    return _pack(pieces, budget, count)


def _term_citation(title: str, number: str, piece: list[Paragraph]) -> str:
    """'20 CFR 655.715 (definition of "Employer")': numbered items restart per term."""
    terms = list(dict.fromkeys(p.term for p in piece if p.term))
    base = f"{title} CFR {number}"
    if not terms:
        return base
    if len(terms) == 1:
        return f"{base} (definition of “{terms[0]}”)"
    return f"{base} (definitions “{terms[0]}” through “{terms[-1]}”)"


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
    # Optional: keep only these subparagraphs of `paragraph`, e.g. the H-1B parts of (h).
    subs = {str(s) for s in src.get("subparagraphs") or []}
    budget = MAX_TOKENS - HEADER_RESERVE
    chunks: list[dict[str, Any]] = []

    def keep(path: tuple[str, ...]) -> bool:
        if only and path[:1] != (only,):
            return False
        return not subs or len(path) < 2 or path[1] in subs

    def keep_note(note: str) -> bool:
        if not only:
            return True
        if subs:
            return any(f"({only})({s})" in note for s in subs)
        return f"({only})" in note

    only_sections = {str(s) for s in src.get("sections") or []}   # optional: keep just these sections
    for sec in sections:
        if only_sections and sec.number not in only_sections:
            continue
        paras = [p for p in sec.paragraphs if keep(p.path)]
        by_term = any(p.term for p in paras)
        units: dict[tuple[str, ...], list[Paragraph]] = {}
        for p in paras:
            units.setdefault(() if by_term else p.path[: 2 if only else 1], []).append(p)
        notes = [n for n in sec.effective_date_notes if keep_note(n)]
        if notes:
            units[("effective-date-notes",)] = [Paragraph((), n) for n in notes]

        for unit, unit_paras in units.items():
            section_key = f"ecfr-{title}-{sec.number}" + "".join(f"-{u}" for u in unit)
            is_note = unit == ("effective-date-notes",)
            scope = (list(src["scope"]) + list(src.get("paragraph_scope", {}).get("-".join(unit), []))
                     + list(src.get("section_scope", {}).get(sec.number, [])))
            if is_note:
                pieces = [unit_paras]
            elif by_term:
                pieces = _split_terms(unit_paras, budget, count)
            else:
                pieces = _split(unit_paras, len(unit), budget, count)
            for i, piece in enumerate(pieces, start=1):
                if is_note:
                    citation, link = f"{title} CFR {sec.number} (effective date notes)", ()
                elif by_term:
                    citation, link = _term_citation(title, sec.number, piece), ()
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


def _long_date(iso: str | None) -> str:
    if not iso:
        return "an unstated date"
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def _fr_status(doc: dict[str, Any], today: date) -> str:
    """One line every piece of a Federal Register document carries."""
    if doc["type"] == "Proposed Rule":
        close = doc.get("comments_close_on")
        when = f" Comments {'close' if (close or '') >= today.isoformat() else 'closed'} {_long_date(close)}." if close else ""
        return ("Status: PROPOSED RULE, NOT IN EFFECT. A proposed rule does not change current law "
                "unless a final rule is published." + when)
    eff = doc.get("effective_on")
    verb = "takes effect" if (eff or "") > today.isoformat() else "took effect"
    return f"Status: Final rule; {verb} {_long_date(eff)}."


def chunk_federal_register(
    docs: list[dict[str, Any]],
    src: dict[str, Any],
    fetched_at: str,
    count_tokens: Counter | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Summary chunks (status, dates, abstract) for each kept Federal Register document."""
    count = count_tokens or _default_counter()
    today = today or date.today()
    chunks: list[dict[str, Any]] = []
    for doc in docs:
        proposed = doc["type"] == "Proposed Rule"
        label = "Proposed rule" if proposed else "Final rule"
        header = f"{doc['citation']} — {label}: {doc['title']}\n{_fr_status(doc, today)}"
        agencies = ", ".join(doc.get("agency_names") or [])
        rins = ", ".join(doc.get("regulation_id_numbers") or [])
        body = [
            f"Published {_long_date(doc['publication_date'])} by {agencies}." + (f" RIN {rins}." if rins else ""),
            f"Action: {doc.get('action') or label}",
            f"Summary: {doc.get('abstract') or 'No summary provided.'}",
        ]
        if doc.get("dates"):
            body.append(f"Dates: {doc['dates']}")
        budget = MAX_TOKENS - count(header) - 10
        pieces = _split([Paragraph((), t) for t in body], 0, budget, count)

        text_lower = f"{doc['title']} {doc.get('abstract') or ''}".lower()
        scope = list(src["scope"]) + [
            tag for term, tags in src.get("term_scope", {}).items() if term in text_lower for tag in tags
        ]
        section_key = f"fr-{doc['document_number']}"
        for i, piece in enumerate(pieces, start=1):
            chunks.append({
                "chunk_id": f"{section_key}-{i:03d}",
                "section_key": section_key,
                "text": f"{header}\n\n{_text(piece)}",
                "source": "federal_register",
                "citation": doc["citation"],
                "title": doc["title"],
                "url": doc["html_url"],
                "effective_date": None if proposed else doc.get("effective_on"),
                "source_updated": doc["publication_date"],
                "fetched_at": fetched_at,
                "doc_type": "proposed_rule" if proposed else "final_rule",
                "scope": list(dict.fromkeys(scope)),
            })
    return chunks

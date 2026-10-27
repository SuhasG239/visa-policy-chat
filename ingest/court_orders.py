"""Tag policy chunks affected by court orders and suspending alerts (CLAUDE.md sync rule 5).

A court order (or an alert that suspends, enjoins, or vacates a policy) names what
it affects. We extract those references from its text and tag every chunk that
*is* one of them with the order's citation in `affected_by`:

  - CFR citations          "8 CFR 214.2(f)(5)"   -> eCFR chunks inside that paragraph
  - Federal Register cites "91 FR 64566"         -> that Federal Register document
  - Policy Manual chapters "Volume 2, Part F, Chapter 5" -> that chapter's chunks
  - policy memo / alert IDs "PM-602-0192", "PA-2025-26"   -> chunks that mention the same ID
  - proclamations by title "Proclamation, Restriction on Entry of Certain Nonimmigrant Workers"
                                                -> every chunk of a section that names it

Answers built from a tagged chunk then carry a warning (prompts.py, the UI callout).
"""

from __future__ import annotations

import re
from typing import Any

CFR_REF = re.compile(r"\b(\d+)\s*C\.?F\.?R\.?\s*(?:§+\s*)?(\d+\.\d+)((?:\([A-Za-z0-9]+\))*)")
FR_REF = re.compile(r"\b(\d+)\s+FR\s+(\d+)\b")
PM_REF = re.compile(r"Volume\s+(\d+),?\s+Part\s+([A-Z]),?\s+Chapter\s+(\d+)", re.IGNORECASE)
MEMO_REF = re.compile(r"\b(P[MA])[\s-]+(\d{3,4})[\s-]+(\d{2,4})\b")
# "Proclamation[ (PP) 10949][ on][,] [“]Title Case Name Of At Least Four Words"
PROC_REF = re.compile(r"Proclamation\s*(?:\(PP\)\s*)?(?:\d+\s*)?(?:on\s+)?[,:]?\s*[“\"]?"
                      r"([A-Z][\w-]*\s+(?:(?:of|on|the|and|for|to|from|in|a|an|[A-Z][\w-]*)\s+){2,}[A-Z][\w-]*)")
SUSPENDING = re.compile(r"\b(?:enjoin\w*|vacat\w*|suspend\w*|stay(?:ed|s)?|injunction|set aside)\b", re.IGNORECASE)


def _memo_id(m: re.Match) -> str:
    return f"{m.group(1).upper()}-{m.group(2)}-{m.group(3)}"


def references(text: str) -> dict[str, set]:
    """Everything a court order or alert text points at, normalized."""
    return {
        "cfr": {(f"{m.group(1)} CFR {m.group(2)}", tuple(re.findall(r"\(([^()]+)\)", m.group(3))))
                for m in CFR_REF.finditer(text)},
        "fr": {f"{m.group(1)} FR {m.group(2)}" for m in FR_REF.finditer(text)},
        "pm": {(m.group(1), m.group(2).upper(), m.group(3)) for m in PM_REF.finditer(text)},
        "memo": {_memo_id(m) for m in MEMO_REF.finditer(text)},
        "proclamation": {_norm(m.group(1)) for m in PROC_REF.finditer(text)},
    }


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def names_proclamation(refs: dict[str, set], chunk: dict[str, Any]) -> bool:
    """True if the chunk names one of the referenced proclamations by title."""
    text = _norm(chunk["text"])
    return any(title in text for title in refs.get("proclamation", ()))


def _cfr_parts(citation: str) -> tuple[str, tuple[str, ...]] | None:
    m = re.match(r"^(\d+ CFR \d+\.\d+)((?:\([^()]+\))*)", citation)
    return (m.group(1), tuple(re.findall(r"\(([^()]+)\)", m.group(2)))) if m else None


def affects(refs: dict[str, set], chunk: dict[str, Any]) -> bool:
    """True if the chunk is one of the referenced provisions or documents."""
    cfr = _cfr_parts(chunk["citation"])
    if cfr and any(cfr[0] == sec and cfr[1][: len(path)] == path for sec, path in refs["cfr"]):
        return True
    if chunk["citation"] in refs["fr"]:
        return True
    for vol, part, ch in refs["pm"]:
        if re.match(rf"^USCIS Policy Manual, Vol\. {vol}, Part {part}, Ch\. {ch}(?:\.|$)", chunk["citation"]):
            return True
    mentioned = {_memo_id(m) for m in MEMO_REF.finditer(chunk["text"])}
    return bool(refs["memo"] & mentioned)


def is_order(chunk: dict[str, Any]) -> bool:
    """Court orders, and alerts whose text suspends, enjoins, or vacates something."""
    return chunk["doc_type"] == "court_order" or (chunk["doc_type"] == "alert" and bool(SUSPENDING.search(chunk["text"])))


def tag(chunks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Set `affected_by` on every chunk; returns {order citation: [tagged chunk ids]}."""
    orders = [c for c in chunks if is_order(c)]
    tagged: dict[str, list[str]] = {}
    for c in chunks:
        c["affected_by"] = []
    for order in orders:
        refs = references(order["text"])
        # A proclamation is usually named once per section; the whole section implements it.
        naming = {c["section_key"] for c in chunks if not is_order(c) and names_proclamation(refs, c)}
        for c in chunks:
            if c is order or is_order(c) or not (affects(refs, c) or c["section_key"] in naming):
                continue
            label = f"{order['citation']} ({order['title']})"
            if label not in c["affected_by"]:
                c["affected_by"].append(label)
                tagged.setdefault(label, []).append(c["chunk_id"])
    return tagged

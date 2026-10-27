"""Parse raw source files into clean, structured sections.

eCFR XML stores a section's paragraphs as a flat list of <P> elements whose text
starts with labels such as "(f)", "(10)", "(ii)", "(A)", "(<I>1</I>)". The CFR
nesting order is: (a) -> (1) -> (i) -> (A) -> (italic 1) -> (italic i).
Labels like "(i)", "(v)", "(x)" can be a letter or a roman numeral; we resolve
them from the current position in the outline and the label that follows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from lxml import etree

log = logging.getLogger(__name__)

ITALIC_TAGS = {"I", "E"}
SKIP_TAGS = {"HEAD", "CITA", "SECAUTH", "EDNOTE", "AUTH", "SOURCE", "PSPACE"}
OPEN, CLOSE = "\x01", "\x02"   # markers for italic text while detecting labels
LABEL = re.compile(rf"\(({OPEN})?([0-9]{{1,3}}|[a-z]{{1,4}}|[A-Z]{{1,2}}){CLOSE}?\)")
HEADING = re.compile(rf"\s*{OPEN}([^{CLOSE}]+){CLOSE}")
NESTED = re.compile(rf"\s*[—–-]?\s*(?={LABEL.pattern})")
# An unlabeled definition: an italic term, then "means", "includes", or "refers to".
DEFINITION = re.compile(rf"^\s*{OPEN}([^{CLOSE}]+){CLOSE}\s*,?\s+(?:means|includes|refers to)\b")


def _roman(n: int) -> str:
    out = ""
    for value, sym in ((40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out, n = out + sym, n - value
    return out


ROMAN = {_roman(n): n for n in range(1, 90)}
FIRST = {0: "a", 1: "1", 2: "i", 3: "A", 4: "1", 5: "i"}


@dataclass
class Paragraph:
    """One paragraph of regulation text at a position in the outline."""

    path: tuple[str, ...]   # e.g. ("f", "10", "ii"); () for unlabeled section text
    text: str
    term: str = ""          # in a "Definitions" section: the term this paragraph defines


@dataclass
class Section:
    """One CFR section."""

    number: str                                   # "214.2"
    heading: str                                  # "Special requirements for ..."
    paragraphs: list[Paragraph] = field(default_factory=list)
    headings: dict[tuple[str, ...], str] = field(default_factory=dict)  # path -> italic heading
    effective_date_notes: list[str] = field(default_factory=list)


def _succ(label: str, level: int) -> str:
    if level in (1, 4):
        return str(int(label) + 1)
    if level in (2, 5):
        return _roman(ROMAN[label] + 1)
    # letters: a..z then aa, bb, ...
    if label[-1] in "zZ":
        return ("a" if label.islower() else "A") * (len(label) + 1)
    return chr(ord(label[0]) + 1) * len(label)


def _candidates(label: str, italic: bool) -> list[int]:
    if label.isdigit():
        return [4] if italic else [1]
    if label.isupper():
        return [3]
    if italic:
        return [5]
    return [0, 2] if label in ROMAN else [0]


def _marked_text(el: etree._Element) -> str:
    """Element text with italic runs wrapped in OPEN/CLOSE markers."""
    parts = [el.text or ""]
    for child in el:
        inner = "".join(child.itertext())
        parts.append(f"{OPEN}{inner}{CLOSE}" if child.tag in ITALIC_TAGS else inner)
        parts.append(child.tail or "")
    return "".join(parts)


def _clean(text: str) -> str:
    return " ".join(text.replace(OPEN, "").replace(CLOSE, "").split())


def _split_labels(marked: str) -> tuple[list[tuple[str, bool]], dict[int, str]]:
    """Leading labels of a paragraph, plus italic headings keyed by label index.

    Handles "(f) <I>Students</I>—(1) <I>Admission</I>—(i) ...", "(1)(i) ...",
    and "(ii) <I>Heading.</I> (A) text".
    """
    labels: list[tuple[str, bool]] = []
    headings: dict[int, str] = {}
    pos = 0
    m = LABEL.match(marked)
    while m:
        labels.append((m.group(2), bool(m.group(1))))
        pos = m.end()
        nxt = LABEL.match(marked, pos)
        if nxt:
            m = nxt
            continue
        h = HEADING.match(marked, pos)
        if not h:
            break
        headings[len(labels) - 1] = _clean(h.group(1)).rstrip(".—– ")
        pos = h.end()
        n = NESTED.match(marked, pos)
        m = LABEL.match(marked, n.end()) if n else None
    return labels, headings


class _Outline:
    """Tracks the current position in the paragraph outline."""

    def __init__(self) -> None:
        self.stack: list[tuple[int, str]] = []   # (level, label)
        self.problems = 0

    def _fits(self, label: str, level: int) -> bool:
        for lvl, lab in self.stack:
            if lvl == level:
                return _succ(lab, level) == label
        deeper = not self.stack or level > self.stack[-1][0]
        return deeper and label == FIRST[level]

    def push(self, label: str, italic: bool, next_label: tuple[str, bool] | None) -> None:
        cands = _candidates(label, italic)
        valid = [lvl for lvl in cands if self._fits(label, lvl)]
        if len(valid) > 1:   # letter vs roman, e.g. "(i)" after "(h)" or after "(1)"
            roman_next = next_label is not None and (
                next_label[0] in ("A", _succ(label, 2)) and not next_label[1]
            )
            valid = [2] if roman_next else [0]
        if valid:
            level = valid[0]
        else:
            self.problems += 1
            on_stack = [lvl for lvl in cands if any(s[0] == lvl for s in self.stack)]
            level = on_stack[-1] if on_stack else cands[-1]
            log.debug("Unexpected label (%s) after %s; using level %d", label, self.stack, level)
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, label))

    def back_to(self, matches) -> bool:
        """Pop to the deepest enclosing path for which `matches(path)` is true; False if none."""
        for depth in range(len(self.stack), 0, -1):
            if matches(tuple(lab for _, lab in self.stack[:depth])):
                del self.stack[depth:]
                return True
        return False

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(lab for _, lab in self.stack)


def _table_text(el: etree._Element) -> str:
    rows = []
    for cap in el.iter("CAPTION"):
        rows.append(_clean("".join(cap.itertext())))
    for tr in el.iter("TR"):
        cells = [_clean("".join(c.itertext())) for c in tr if c.tag in ("TD", "TH")]
        rows.append(" | ".join(cells))
    return "\n".join(r for r in rows if r)


def _parse_section(div: etree._Element) -> Section:
    head = _clean("".join(div.find("HEAD").itertext())) if div.find("HEAD") is not None else ""
    number = div.get("N", "")
    heading = re.sub(rf"^§\s*{re.escape(number)}\s*", "", head)
    sec = Section(number=number, heading=heading)

    # Pass 1: collect items in order, with their leading labels.
    items: list[tuple[list[tuple[str, bool]], dict[int, str], str, bool]] = []
    for el in div:
        if el.tag in SKIP_TAGS or not isinstance(el.tag, str):
            continue
        if el.tag == "EFFDNOT":
            notes = [_clean("".join(p.itertext())) for p in el.iter("P")]
            sec.effective_date_notes.extend(n for n in notes if n)
            continue
        if el.tag in ("P", "FP"):
            marked = _marked_text(el)
            labels, heads = _split_labels(marked)
            definition = DEFINITION.match(marked)
            items.append((labels, heads, _clean(marked), _clean(definition.group(1)) if definition else ""))
        elif el.tag == "DIV" and el.find(".//TABLE") is not None:
            items.append(([], {}, _table_text(el), ""))
        else:
            text = _clean("".join(el.itertext()))
            if text:
                items.append(([], {}, text, ""))

    # Pass 2: resolve each label's outline level, with one label of lookahead.
    flat = [lab for labels, _, _, _ in items for lab in labels]
    outline = _Outline()
    whole_definitions = "definition" in sec.heading.lower()
    term = ""
    i = 0
    for labels, heads, text, defined in items:
        if defined and not labels:
            # An unlabeled "Term means ..." paragraph belongs to the enclosing
            # "Definitions" paragraph, not to the last numbered item before it.
            found = outline.back_to(lambda path: "definition" in sec.headings.get(path, "").lower())
            if not found and whole_definitions:
                outline.stack.clear()   # a whole "Definitions" section: each term starts at the top
                term = defined
        for j, (label, italic) in enumerate(labels):
            outline.push(label, italic, flat[i + 1] if i + 1 < len(flat) else None)
            i += 1
            if j in heads:
                sec.headings[outline.path] = heads[j]
        if text:
            sec.paragraphs.append(Paragraph(outline.path, text, term))
    if outline.problems:
        log.warning("§ %s: %d labels did not fit the outline", number, outline.problems)
    return sec


def parse_ecfr(xml: bytes) -> list[Section]:
    """Parse eCFR Versioner XML (a part or a section) into Sections."""
    root = etree.fromstring(xml)
    divs = [d for d in root.iter() if isinstance(d.tag, str) and d.get("TYPE") == "SECTION"]
    return [_parse_section(d) for d in divs]

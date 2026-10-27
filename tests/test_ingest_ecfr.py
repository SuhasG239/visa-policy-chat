"""Offline tests for eCFR parsing and chunking (inline XML, word-count tokens)."""

from ingest.chunk import chunk_ecfr, citation_for
from ingest.parse import parse_ecfr

XML = """<?xml version="1.0" encoding="UTF-8"?>
<DIV8 N="214.2" TYPE="SECTION">
<HEAD>§ 214.2 Special requirements.</HEAD>
<P>(e) <I>Treaty traders</I>—(1) Text e1.</P>
<P>(f) <I>Students</I>—(1) <I>Admission</I>—(i) <I>Eligibility.</I> Body f1i.</P>
<P>(A) Item A.</P>
<P>(ii) <I>Port of entry.</I> (A) Body f1iiA.</P>
<P>(<I>1</I>) Italic one.</P>
<P>(<I>2</I>) Italic two.</P>
<P>(2) <I>Maintenance.</I> Body f2.</P>
<P>(5) <I>Period of stay</I>—(i) General.</P>
<P>(v) <I>Departure.</I> Sixty days to depart.</P>
<P>(g) <I>Representatives.</I> Body g.</P>
<P>(h) <I>Temporary employees.</I></P>
<P>(1) Body h1.</P>
<P>(i) Body h1i.</P>
<P>(ii) Body h1ii.</P>
<P>(i) <I>Letter i.</I> Body i.</P>
<P>(1) Body i1.</P>
<CITA>[38 FR 35425]</CITA>
<EFFDNOT><HED>Effective Date Notes:</HED><P>1. Paragraph (h)(31) added, effective through 2026.</P></EFFDNOT>
</DIV8>
""".encode()

SRC = {"id": "t", "type": "ecfr", "title": "8", "part": "214", "section": "214.2",
       "paragraph": "f", "scope": ["f1"], "paragraph_scope": {"f-5": ["grace_period"]}}


def words(text: str) -> int:
    return len(text.split())


def test_outline_paths():
    sec = parse_ecfr(XML)[0]
    paths = ["".join(f"({x})" for x in p.path) for p in sec.paragraphs]
    assert paths == [
        "(e)(1)", "(f)(1)(i)", "(f)(1)(i)(A)", "(f)(1)(ii)(A)", "(f)(1)(ii)(A)(1)",
        "(f)(1)(ii)(A)(2)", "(f)(2)", "(f)(5)(i)", "(f)(5)(v)", "(g)", "(h)",
        "(h)(1)", "(h)(1)(i)", "(h)(1)(ii)", "(i)", "(i)(1)",
    ]
    assert sec.heading == "Special requirements."
    assert sec.headings[("f",)] == "Students"
    assert sec.headings[("f", "1", "ii")] == "Port of entry"
    assert sec.effective_date_notes == ["1. Paragraph (h)(31) added, effective through 2026."]


def test_chunks_keep_only_paragraph_and_carry_metadata():
    chunks = chunk_ecfr(parse_ecfr(XML), SRC, "2026-10-06T00:00:00+00:00",
                        {"214.2": "2026-09-30"}, count_tokens=words)
    assert {c["section_key"] for c in chunks} == {
        "ecfr-8-214.2-f-1", "ecfr-8-214.2-f-2", "ecfr-8-214.2-f-5"}   # (h) note dropped
    f5 = next(c for c in chunks if c["section_key"] == "ecfr-8-214.2-f-5")
    assert f5["chunk_id"] == "ecfr-8-214.2-f-5-001"
    assert f5["citation"] == "8 CFR 214.2(f)(5)(i)–(v)"
    assert f5["url"].endswith("section-214.2#p-214.2(f)(5)(i)")
    assert f5["scope"] == ["f1", "grace_period"]
    assert f5["source_updated"] == "2026-09-30"
    assert f5["text"].startswith("8 CFR 214.2(f)(5)(i)–(v) — Students > Period of stay")


def test_long_units_split_on_subparagraphs(monkeypatch):
    import ingest.chunk as chunk_mod
    # (f)(1)(i) group is 9 words, (f)(1)(ii) group is 13: each fits, together they don't.
    monkeypatch.setattr(chunk_mod, "MAX_TOKENS", chunk_mod.HEADER_RESERVE + 14)
    chunks = chunk_ecfr(parse_ecfr(XML), SRC, "now", {}, count_tokens=words)
    f1 = [c for c in chunks if c["section_key"] == "ecfr-8-214.2-f-1"]
    assert [c["chunk_id"][-3:] for c in f1] == ["001", "002"]
    assert [c["citation"] for c in f1] == ["8 CFR 214.2(f)(1)(i)", "8 CFR 214.2(f)(1)(ii)(A)"]


DEFINITIONS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DIV8 N="655.715" TYPE="SECTION">
<HEAD>§ 655.715 Definitions.</HEAD>
<P>For the purposes of this subpart:</P>
<P><I>Aggrieved party</I> means a person adversely affected, including:</P>
<P>(1) A worker.</P>
<P>(2) A union.</P>
<P><I>Place of employment</I> means the worksite.</P>
<P>(1) Example one.</P>
</DIV8>
""".encode()


def test_definitions_section_restarts_numbering_per_term():
    sec = parse_ecfr(DEFINITIONS_XML)[0]
    paths = ["".join(f"({x})" for x in p.path) for p in sec.paragraphs]
    assert paths == ["", "", "(1)", "(2)", "", "(1)"]
    assert [p.term for p in sec.paragraphs] == ["", "Aggrieved party", "Aggrieved party", "Aggrieved party",
                                                "Place of employment", "Place of employment"]
    src = {"id": "t", "type": "ecfr", "title": "20", "part": "655", "scope": ["h1b"]}
    chunks = chunk_ecfr([sec], src, "now", {}, count_tokens=words)
    assert [c["citation"] for c in chunks] == ["20 CFR 655.715 (definitions “Aggrieved party” through “Place of employment”)"]
    assert {c["section_key"] for c in chunks} == {"ecfr-20-655.715"}


def test_citation_ranges():
    assert citation_for("8", "214.1", [("a",)])[0] == "8 CFR 214.1(a)"
    assert citation_for("8", "214.1", [("a", "1"), ("a", "3", "i")])[0] == "8 CFR 214.1(a)(1)–(3)"
    assert citation_for("8", "248.3", [()])[0] == "8 CFR 248.3"

"""Offline tests for Federal Register selection and chunking (no network, word-count tokens)."""

from datetime import date

from ingest.chunk import chunk_federal_register, citation_for
from ingest.fetch_federal_register import select

TODAY = date(2026, 10, 23)
SRC = {
    "id": "fr-test", "type": "federal_register", "scope": ["f1"],
    "include_terms": ["F-1", "practical training", "214.1"],
    "final_rule_window_days": 365,
    "term_scope": {"practical training": ["opt"]},
}


def doc(number, type_, title, abstract="", published="2026-10-01", effective=None, rins=(), closes=None):
    return {
        "document_number": number, "type": type_, "title": title, "abstract": abstract,
        "publication_date": published, "effective_on": effective, "comments_close_on": closes,
        "regulation_id_numbers": list(rins), "citation": f"91 FR {number[-4:]}",
        "html_url": f"https://www.federalregister.gov/d/{number}", "agency_names": ["Homeland Security Department"],
        "action": "Notice of proposed rulemaking." if type_ == "Proposed Rule" else "Final rule.",
        "dates": "Comments must be received by November 9, 2026.",
    }


def words(text: str) -> int:
    return len(text.split())


def test_select_keeps_relevant_current_documents():
    docs = [
        doc("2026-0001", "Proposed Rule", "Optional Practical Training Fees", rins=["A"]),
        doc("2026-0002", "Proposed Rule", "H-2A petition timing", rins=["B"]),                  # off topic
        doc("2025-0003", "Proposed Rule", "Fixed admission for F-1 students", published="2025-08-28", rins=["C"]),
        doc("2026-0004", "Rule", "Fixed admission for F-1 students", published="2026-07-17",
            effective="2026-09-15", rins=["C"]),                                                 # supersedes 0003
        doc("2024-0005", "Rule", "F-1 flexibility", published="2024-12-18", effective="2025-01-17"),  # in eCFR now
        doc("2024-0006", "Rule", "F-1 change effective later", published="2024-11-01", effective="2027-01-01"),
    ]
    kept = [d["document_number"] for d in select(docs, SRC, TODAY)]
    assert kept == ["2026-0001", "2026-0004", "2024-0006"]


def test_proposed_rule_chunk_is_labeled_on_every_piece():
    long_abstract = "This rule proposes new fees for optional practical training. " * 80
    d = doc("2026-0001", "Proposed Rule", "Optional Practical Training Fees", long_abstract, closes="2026-11-09")
    chunks = chunk_federal_register([d], SRC, "now", count_tokens=words, today=TODAY)
    assert len(chunks) > 1   # long abstract was split
    for c in chunks:
        assert "PROPOSED RULE, NOT IN EFFECT" in c["text"]
        assert "Comments close November 9, 2026" in c["text"]
        assert c["doc_type"] == "proposed_rule" and c["effective_date"] is None
        assert c["source"] == "federal_register" and c["citation"] == "91 FR 0001"
        assert c["section_key"] == "fr-2026-0001" and c["scope"] == ["f1", "opt"]
    assert [c["chunk_id"] for c in chunks][:2] == ["fr-2026-0001-001", "fr-2026-0001-002"]


def test_final_rule_status_says_when_it_takes_effect():
    future = doc("2026-0009", "Rule", "F-1 rule", published="2026-10-01", effective="2027-01-01")
    past = doc("2026-0010", "Rule", "F-1 rule", published="2026-07-17", effective="2026-09-15")
    texts = [c["text"] for c in chunk_federal_register([future, past], SRC, "now", count_tokens=words, today=TODAY)]
    assert "Final rule; takes effect January 1, 2027." in texts[0]
    assert "Final rule; took effect September 15, 2026." in texts[1]


def test_range_citation_names_the_exact_start():
    cite, link = citation_for("8", "214.2", [("f", "5", "ii", "E"), ("f", "5", "iii")])
    assert cite == "8 CFR 214.2(f)(5)(ii)(E)–(iii)"
    assert link == ("f", "5", "ii", "E")
    assert citation_for("8", "214.2", [("f", "5", "i"), ("f", "5", "v")])[0] == "8 CFR 214.2(f)(5)(i)–(v)"

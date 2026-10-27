"""Tests for court-order tagging (ingest/court_orders.py) and attaching orders at retrieval."""

from app import retrieve
from ingest.court_orders import is_order, references, tag


def chunk(cid, citation, text, doc_type="regulation", title="T"):
    return {"chunk_id": cid, "section_key": cid, "citation": citation, "title": title,
            "text": text, "doc_type": doc_type}


ORDER = chunk("uscis-alert-1-001", "USCIS alert, June 12, 2026",
              "A court order enjoining 8 CFR 214.2(f)(10)(ii)(C), vacating 91 FR 64566, and staying "
              "Volume 2, Part F, Chapter 5 guidance and PM-602-0192.", "court_order", "Court Order on OPT")


def test_references_are_extracted_and_normalized():
    refs = references(ORDER["text"] + " See also PA 2025-26 and 8 C.F.R. § 248.1.")
    assert ("8 CFR 214.2", ("f", "10", "ii", "C")) in refs["cfr"] and ("8 CFR 248.1", ()) in refs["cfr"]
    assert refs["fr"] == {"91 FR 64566"}
    assert refs["pm"] == {("2", "F", "5")}
    assert refs["memo"] == {"PM-602-0192", "PA-2025-26"}


def test_tag_marks_exactly_the_affected_chunks():
    chunks = [
        ORDER,
        chunk("stem", "8 CFR 214.2(f)(10)(ii)(C)(5)", "E-Verify employers."),       # inside the cited paragraph
        chunk("cpt", "8 CFR 214.2(f)(10)(i)", "Curricular practical training."),     # sibling: not affected
        chunk("fee", "91 FR 64566", "OPT fees.", "proposed_rule"),
        chunk("pm5", "USCIS Policy Manual, Vol. 2, Part F, Ch. 5.C.3", "STEM OPT.", "policy_guidance"),
        chunk("pm50", "USCIS Policy Manual, Vol. 2, Part F, Ch. 50.A", "Other.", "policy_guidance"),  # not Ch. 5
        chunk("memo", "USCIS Policy Manual update, May 1, 2026", "Implements PM 602-0192.", "policy_guidance"),
    ]
    tagged = tag(chunks)
    label = "USCIS alert, June 12, 2026 (Court Order on OPT)"
    assert sorted(tagged[label]) == ["fee", "memo", "pm5", "stem"]
    assert next(c for c in chunks if c["chunk_id"] == "cpt")["affected_by"] == []
    assert ORDER["affected_by"] == []   # an order never tags itself


def test_only_orders_and_suspending_alerts_tag():
    assert is_order(ORDER)
    assert is_order(chunk("a", "USCIS alert, May 1, 2026", "USCIS suspends the policy.", "alert"))
    assert not is_order(chunk("b", "USCIS alert, May 1, 2026", "USCIS opens a new office.", "alert"))


def test_retrieval_attaches_the_order_for_a_tagged_result(monkeypatch):
    order = {**ORDER, "affected_by": []}
    results = [
        {**chunk("stem", "8 CFR 214.2(f)(10)(ii)(C)(5)", "x"), "affected_by": ["USCIS alert, June 12, 2026 (Court Order on OPT)"]},
        {**chunk("other", "8 CFR 214.2(f)(9)(i)", "y"), "affected_by": []},
    ]
    monkeypatch.setattr(retrieve._keyword, "refresh", lambda: None)
    monkeypatch.setattr(retrieve._keyword, "chunks", [order])
    out = retrieve._attach_orders(results)
    assert [c["chunk_id"] for c in out] == ["stem", "uscis-alert-1-001"]   # replaced the untagged result


def test_proclamation_named_by_title_tags_whole_section():
    order = {"chunk_id": "o", "section_key": "o", "citation": "USCIS alert, October 20, 2025", "title": "Proclamation",
             "doc_type": "court_order",
             "text": "A court vacated guidance implementing the Proclamation, Restriction on Entry of Certain "
                     "Nonimmigrant Workers, including the $100,000 payment."}
    named = {"chunk_id": "p-001", "section_key": "p", "citation": "USCIS: H-1B", "title": "", "doc_type": "policy_guidance",
             "text": "The President issued a Proclamation, Restriction on Entry of Certain Nonimmigrant Workers."}
    same_section = {**named, "chunk_id": "p-002", "text": "How to pay the $100,000 payment: use pay.gov."}
    other = {**named, "chunk_id": "q-001", "section_key": "q", "text": "Entry of certain workers is unrelated here."}
    tagged = tag([order, named, same_section, other])
    assert list(tagged.values()) == [["p-001", "p-002"]]
    assert other["affected_by"] == [] and order["affected_by"] == []

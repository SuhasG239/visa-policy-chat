"""Offline tests for Study in the States parsing and chunking (inline HTML)."""

from ingest.chunk_sevp import chunk_sevp
from ingest.fetch_sevp import parse_page

PAGE = """<html><head><title>Maintaining Status | Study in the States</title></head><body>
<div class="region-content"><article>
<div class="margin-top-0">Last updated: April 29, 2025</div>
<div class="field--name-body">
<p>While studying in the United States, it is important to maintain your status.</p>
<h2>Arrival</h2>
<ul><li>Enter the United States no more than 30 days before your program begins.</li></ul>
<h2>Practical Training for M-1 Students</h2>
<p>M-1 students may only engage in practical training after completing their program.</p>
<h2>Upon Program Completion</h2>
<h3>Departure</h3>
<p>F-1 students have 60 days to depart.</p>
</div></article></div></body></html>"""

SRC = {"id": "sevp-test", "name": "Study in the States", "scope": ["f1"],
       "pages": {"students/maintaining-status": ["status"]}}


def words(text: str) -> int:
    return len(text.split())


def test_parse_page_sections_date_and_m1_skip():
    page = parse_page(PAGE, "students/maintaining-status", "https://example.gov/students/maintaining-status", ["M-1"])
    assert page.title == "Maintaining Status" and page.updated == "2025-04-29"
    assert [(p.path, p.text[:30]) for p in page.paragraphs] == [
        ((), "While studying in the United S"),
        (("arrival",), "- Enter the United States no m"),
        (("upon-program-completion", "departure"), "F-1 students have 60 days to d"),
    ]   # M-1 section skipped


def test_chunks_cite_page_and_section():
    page = parse_page(PAGE, "students/maintaining-status", "https://example.gov/students/maintaining-status", ["M-1"])
    chunks = {c["chunk_id"]: c for c in chunk_sevp([page], SRC, "now", count_tokens=words)}
    assert set(chunks) == {"sevp-maintaining-status-intro-001", "sevp-maintaining-status-arrival-001",
                           "sevp-maintaining-status-upon-program-completion-001"}
    c = chunks["sevp-maintaining-status-arrival-001"]
    assert c["citation"] == "Study in the States: Maintaining Status (Arrival)"
    assert c["source"] == "sevp" and c["doc_type"] == "policy_guidance"
    assert c["source_updated"] == "2025-04-29" and c["scope"] == ["f1", "status"]
    assert chunks["sevp-maintaining-status-intro-001"]["citation"] == "Study in the States: Maintaining Status"

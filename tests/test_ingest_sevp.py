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


USCIS_PAGE = """<html><head><title>H-1B Specialty Occupations | USCIS</title></head><body>
<div class="region-content"><article><div class="field--name-body">
<p>ALERT: A court issued an order.</p>
<h4>Changing Employers (Portability)</h4>
<p><strong>When can I begin working for a new employer?</strong></p>
<ul><li>As soon as the new employer properly files.</li></ul>
<h4>More Information</h4>
<ul><li>H-1B Cap Season</li></ul>
</div></article>
<div>Last Reviewed/Updated: <time>09/21/2026</time></div></div></body></html>"""

USCIS_SRC = {"id": "uscis-web-test", "name": "USCIS", "store_source": "uscis_web", "key_prefix": "uscis-web",
             "scope": ["h1b"], "pages": {"h-1b-specialty-occupations": ["portability"]}}


def test_uscis_page_h4_sections_bold_labels_and_date():
    page = parse_page(USCIS_PAGE, "h-1b-specialty-occupations", "https://www.uscis.gov/x",
                      ["More Information"], ["h2", "h3", "h4"], bold_subheadings=True)
    assert page.title == "H-1B Specialty Occupations" and page.updated == "2026-09-21"
    question = ("changing-employers-portability", "when-can-i-begin-working-for-a-new-employer")
    assert [(p.path, p.text) for p in page.paragraphs] == [
        ((), "ALERT: A court issued an order."),
        (question, "When can I begin working for a new employer?"),
        (question, "- As soon as the new employer properly files."),
    ]   # "More Information" link list skipped
    chunks = chunk_sevp([page], USCIS_SRC, "now", count_tokens=words)
    assert {c["source"] for c in chunks} == {"uscis_web"}
    assert {c["section_key"] for c in chunks} == {"uscis-web-h-1b-specialty-occupations-intro",
                                                  "uscis-web-h-1b-specialty-occupations-changing-employers-portability"}
    assert chunks[1]["citation"] == "USCIS: H-1B Specialty Occupations (Changing Employers (Portability))"

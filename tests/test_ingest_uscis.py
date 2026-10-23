"""Offline tests for USCIS parsing and chunking (inline HTML mirroring uscis.gov markup)."""

from ingest.chunk_uscis import chunk_alerts, chunk_policy_alerts, chunk_policy_manual
from ingest.fetch_uscis import keep_alert
from ingest.parse_uscis import Alert, parse_alert_list, parse_pm_chapter

CHAPTER = """<html><body><h1>Chapter 5 - Practical Training</h1>
<div id="guidance" class="tabcontent"><div class="field--name-body">
<h2><a id="S-A"></a>A. General</h2>
<p>Practical training is employment related to the major.<sup>[1]</sup></p>
<ul><li>Curricular Practical Training (CPT);</li><li>Optional Practical Training (OPT).</li></ul>
<h2><a id="S-C"></a>C. F-1 Student Optional Practical Training</h2>
<h3><a id="S-C-3"></a>3. STEM OPT Extension</h3>
<p>A student may apply for a 24-month extension.</p>
<h2><a id="S-E"></a>E. M-1 Practical Training</h2>
<p>M-1 students may engage in practical training after completing their course.</p>
<h2>Footnotes</h2><p>[1] See 8 CFR 214.2(f)(10).</p>
</div></div>
<div id="updates" class="tabcontent">
 <div class="pm-updates"><div class="pm-resource__update_header">POLICY ALERT - STEM OPT</div>
  <time datetime="2024-08-27T12:00:00Z">August 27, 2024</time>
  <div class="pm-resource__content"><p>Guidance on STEM OPT for F-1 students. This policy became effective August 27, 2024.</p></div></div>
 <div class="pm-updates"><div class="pm-resource__update_header">Technical Update - Replacing the Term</div>
  <time datetime="2025-02-26T12:00:00Z">February 26, 2025</time>
  <div class="pm-resource__content"><p>This technical update replaces all instances of the term with another.</p></div></div>
</div></body></html>"""

PM_SRC = {"id": "uscis-pm-v2-pF", "type": "uscis_pm", "name": "USCIS Policy Manual, Vol. 2, Part F",
          "chapter_url": "https://www.uscis.gov/policy-manual/volume-2-part-f-chapter-{n}",
          "scope": ["f1"], "chapter_scope": {5: ["opt"]}, "alert_include_terms": ["F-1", "OPT"]}
ALERT_SRC = {"scope": ["f1"], "include_terms": ["F-1", "student", "OPT"],
             "court_order_terms": ["court order", "enjoining", "vacating"],
             "court_order_relevance_terms": ["benefit application", "immigration applications"]}


def words(text: str) -> int:
    return len(text.split())


def test_parse_chapter_sections_footnotes_and_updates():
    ch = parse_pm_chapter(CHAPTER, 5, skip_headings=["M-1"])
    assert ch.title == "Practical Training"
    assert [(p.path, p.text) for p in ch.paragraphs] == [
        (("A",), "Practical training is employment related to the major."),   # footnote marker dropped
        (("A",), "- Curricular Practical Training (CPT);"),
        (("A",), "- Optional Practical Training (OPT)."),
        (("C", "3"), "A student may apply for a 24-month extension."),
    ]   # M-1 section and footnotes skipped
    assert ch.anchors[("C", "3")] == "S-C-3" and ch.headings[("C", "3")] == "STEM OPT Extension"
    assert [(u.date, u.effective) for u in ch.updates] == [("2024-08-27", "2024-08-27"), ("2025-02-26", None)]


def test_policy_manual_chunks_cite_and_link_sections():
    ch = parse_pm_chapter(CHAPTER, 5, skip_headings=["M-1"])
    chunks = {c["chunk_id"]: c for c in chunk_policy_manual([ch], PM_SRC, "now", count_tokens=words)}
    c = chunks["uscis-pm-v2-pF-ch5-sC-001"]
    assert c["citation"] == "USCIS Policy Manual, Vol. 2, Part F, Ch. 5.C.3"
    assert c["url"].endswith("volume-2-part-f-chapter-5#S-C-3")
    assert c["doc_type"] == "policy_guidance" and c["source"] == "uscis_pm"
    assert c["source_updated"] == "2025-02-26" and c["scope"] == ["f1", "opt"]
    assert c["text"].startswith("USCIS Policy Manual, Vol. 2, Part F, Ch. 5.C.3 — Ch. 5 Practical Training > C.")


def test_policy_alerts_skip_wording_only_updates():
    ch = parse_pm_chapter(CHAPTER, 5)
    alerts = chunk_policy_alerts([ch], PM_SRC, "now", count_tokens=words)
    assert [a["title"] for a in alerts] == ["POLICY ALERT - STEM OPT"]
    assert alerts[0]["effective_date"] == "2024-08-27"
    assert alerts[0]["citation"] == "USCIS Policy Manual update, August 27, 2024"


def test_alert_list_and_court_order_filter():
    html = """<div class="views-row"><div class="views-field-title"><a href="/newsroom/alerts/court-order">Court Order on Hold Policies</a></div>
      <time datetime="2026-06-12T12:00:00Z">June 12, 2026</time><div class="views-field-body">On June 5 a court ruled.</div></div>"""
    [row] = parse_alert_list(html)
    assert row.url == "https://www.uscis.gov/newsroom/alerts/court-order" and row.date == "2026-06-12"
    relevant = Alert("Court Order on Hold Policies", row.url, row.date, "", "A court order vacating hold policies on immigration applications.")
    unrelated = Alert("Court Order on Parole", row.url, row.date, "", "A court order vacating a parole program.")
    adopted = Alert("Agency adopted a new system", row.url, row.date, "", "The agency adopted FLAG.")   # 'OPT' inside a word
    assert keep_alert(relevant, ALERT_SRC) and not keep_alert(unrelated, ALERT_SRC) and not keep_alert(adopted, ALERT_SRC)
    [chunk] = chunk_alerts([relevant], ALERT_SRC, "now", count_tokens=words)
    assert chunk["doc_type"] == "court_order" and chunk["source"] == "uscis_alert"
    assert chunk["citation"] == "USCIS alert, June 12, 2026"

"""Offline tests for retrieval helpers, prompt formatting, and link checking."""

from app import prompts, retrieve
from app.chat import _strip_unknown_links

URL = "https://www.ecfr.gov/current/title-8/section-214.2#p-214.2(f)(10)(ii)(E)"


def test_tokenize_keeps_codes_and_stems():
    assert retrieve.tokenize("F-1 students on OPT under 8 CFR 214.2") == [
        "f-1", "student", "opt", "under", "8", "cfr", "214.2"]
    assert retrieve.tokenize("unemployed")[0] == retrieve.tokenize("unemployment")[0]
    assert retrieve.tokenize("status") == ["status"]


def test_expand_query_adds_regulatory_terms():
    q = retrieve.expand_query("What is CPT and the grace period?")
    assert "curricular practical training" in q
    assert "preparation for departure" in q
    assert retrieve.expand_query("Tell me about the deadline") == "Tell me about the deadline"
    assert "optional practical" not in retrieve.expand_query("adopt")   # whole words only


def test_split_question():
    assert retrieve.split_question(
        "How long is the grace period, and how many days can I be unemployed?") == [
        "How long is the grace period", "how many days can I be unemployed?"]
    assert retrieve.split_question(
        "What is CPT? How many days of unemployment are allowed on STEM OPT?") == [
        "What is CPT?", "How many days of unemployment are allowed on STEM OPT?"]
    assert retrieve.split_question("Can I travel and come back on OPT?") == [
        "Can I travel and come back on OPT?"]
    # Follow-on parts with no subject of their own stay attached.
    for q in ["Can I work on campus? What about off campus?",
              "How many months of OPT can I get, and can they get more later?"]:
        assert retrieve.split_question(q) == [q]


def test_strip_unknown_links_keeps_sources_with_parentheses():
    text = f"See [8 CFR 214.2(f)(10)(ii)(E)]({URL}) and [made up](https://example.com/a(b))."
    fixed, removed = _strip_unknown_links(text, {URL})
    assert f"]({URL})" in fixed
    assert "made up." in fixed and "example.com" not in fixed
    assert removed == ["https://example.com/a(b)"]


def test_user_message_has_date_headers_and_question():
    chunk = {"citation": "8 CFR 214.2(f)(9)(i)", "title": "§ 214.2(f)(9) Employment",
             "url": URL, "doc_type": "regulation", "source_updated": "2026-09-30",
             "effective_date": "", "text": "On-campus employment ..."}
    msg = prompts.user_message("Can I work?", [chunk], "2026-10-07")
    assert msg.startswith("Today's date: 2026-10-07")
    assert "[1] 8 CFR 214.2(f)(9)(i)" in msg and f"URL: {URL}" in msg
    assert "Updated: 2026-09-30" in msg and "Effective" not in msg
    assert msg.endswith("Question: Can I work?")

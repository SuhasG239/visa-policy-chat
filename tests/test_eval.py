"""Phase 1 eval, driven by tests/eval_questions.yaml.

    pytest tests/test_eval.py             # schema + retrieval checks (no LLM, free)
    pytest tests/test_eval.py --e2e       # also ask Claude every question (about $0.015 each on Sonnet)

Retrieval passes when every expected citation is covered by one of the top-k
chunks. End-to-end passes when the answer contains each must_include item, none of
the must_not_include items, shows the expected behavior, and invents no links.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app import retrieve

EVAL_FILE = Path(__file__).with_name("eval_questions.yaml")
CASES = yaml.safe_load(EVAL_FILE.read_text(encoding="utf-8"))["questions"]
TOP_K = retrieve.DEFAULT_K   # what chat.ask sends to the model

CATEGORIES = {"rule_lookup", "out_of_scope", "not_covered", "future_or_proposed", "personal_situation"}
BEHAVIORS = {"answer", "decline", "not_covered", "recommend_professional"}
PHASE1_TOPICS = {"on_campus_work", "off_campus_work", "cpt", "opt", "stem_opt", "travel",
                 "transfer", "grace_period", "change_of_status", "maintaining_status"}

# Behavior checks: every pattern in the list must match (case-insensitive).
BEHAVIOR_PATTERNS = {
    "answer": [r"\]\(https?://"],   # at least one cited source link
    "decline": [r"can.?t help|cannot help|can.?t assist|outside (?:of )?what|only (?:answer|help|cover)|not able to help|unable to help"],
    "not_covered": [r"(?:don.?t|do not|doesn.?t|does not)\s+(?:state|say|cover|address|include|specify|mention|explain|list|give|answer)"
                    r"|none (?:of them |of the excerpts )?(?:mention|cover|address|say|state)"
                    r"|not (?:covered|in the excerpts|stated)|no (?:information|excerpt)|can.?t (?:give|tell|say|answer)"],
    "recommend_professional": [r"attorney|accredited representative", r"\bDSO\b|designated school official"],
}


# ------------------------------------------------------------------ citation matching

def _order(token: str, roman: bool) -> int:
    if token.isdigit():
        return int(token)
    if roman:
        return _roman_value(token)
    value = 0
    for ch in token.lower():
        value = value * 26 + (ord(ch) - 96)
    return value


def _roman_value(token: str) -> int:
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
    total = 0
    for a, b in zip(token, token[1:] + " "):
        total += -values[a] if values.get(b, 0) > values[a] else values[a]
    return total


def _is_roman(token: str) -> bool:
    return bool(re.fullmatch(r"[ivxlc]+", token))


def parse_citation(citation: str) -> tuple[str, list[str], str | None]:
    """'8 CFR 214.2(f)(9)(ii)(A)–(C)' -> ('214.2', ['f','9','ii','A'], 'C'); '91 FR 64566' -> ('91 FR 64566', [], None)."""
    fr = re.match(r"^\s*(\d+)\s+FR\s+(\d+)\s*$", citation)
    if fr:
        return f"{fr.group(1)} FR {fr.group(2)}", [], None
    m = re.match(r"^\s*\d+\s+CFR\s+([\d.]+)(.*)$", citation)
    if not m:
        return citation.strip(), [], None   # Policy Manual, alerts: compared as text
    section, rest = m.groups()
    parts = re.split(r"\s*[–-]\s*(?=\()", rest, maxsplit=1)
    end = re.findall(r"\(([^()]+)\)", parts[1])[0] if len(parts) > 1 else None
    return section, re.findall(r"\(([^()]+)\)", parts[0]), end


def _kinds(token: str) -> set[str]:
    if token.isdigit():
        return {"number"}
    if token.isupper():
        return {"upper"}
    return {"roman", "letter"} if _is_roman(token) else {"letter"}


def _range_level(path: list[str], end: str) -> int:
    """Index of the path element the range end pairs with: '(ii)(E)–(iii)' pairs iii with ii."""
    for j in range(len(path) - 1, -1, -1):
        if _kinds(path[j]) & _kinds(end):
            return j
    return len(path) - 1


def covers(retrieved: str, expected: str) -> bool:
    """True if the retrieved chunk's citation covers the expected citation.

    A leading "=" demands that exact paragraph (not a sub-paragraph), for rules
    stated in a parent paragraph's own text, e.g. "=8 CFR 214.2(f)(10)".
    """
    exact = expected.startswith("=")
    if "CFR" not in expected and " FR " not in expected:
        # Policy Manual or alert citation: the same section, or a subsection of it ("Ch. 5.C" covers "Ch. 5.C.2").
        e = expected.lstrip("=").strip()
        return retrieved == e or (not exact and retrieved.startswith(e + "."))
    r_sec, r_path, r_end = parse_citation(retrieved)
    e_sec, e_path, _ = parse_citation(expected.lstrip("="))
    if not r_sec or r_sec != e_sec:
        return False
    if exact:
        return r_end is None and r_path == e_path
    if r_end is None:
        # The chunk is the expected paragraph or one of its sub-paragraphs.
        return r_path[: len(e_path)] == e_path
    j = _range_level(r_path, r_end)
    prefix, first, tail = r_path[:j], r_path[j], r_path[j + 1:]
    n = min(len(e_path), j)
    if e_path[:n] != prefix[:n]:
        return False
    if len(e_path) <= j:
        return True   # the whole range sits inside the expected paragraph
    roman = _is_roman(first) and _is_roman(r_end)
    target = e_path[j]
    if not _order(first, roman) <= _order(target, roman) <= _order(r_end, roman):
        return False
    if target != first or not tail or len(e_path) <= j + 1:
        return True
    # The chunk starts partway through `first` (e.g. at (ii)(E)): earlier parts aren't in it.
    tail_roman = _is_roman(tail[0])
    return _order(e_path[j + 1], tail_roman) >= _order(tail[0], tail_roman)


# ------------------------------------------------------------------ answer checks

def normalize(text: str) -> str:
    text = text.replace(" ", " ").replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"\s+", " ", text).lower()


def states(norm_text: str, phrase: str) -> bool:
    """True if the answer asserts `phrase`, not just mentions it as an open question.

    "I can't tell you whether you will be deported" mentions the phrase without
    claiming it, so an occurrence right after "whether" or "if" doesn't count.
    """
    for m in re.finditer(re.escape(phrase), norm_text):
        if not re.search(r"\b(?:whether|if)\s+$", norm_text[max(0, m.start() - 12): m.start()]):
            return True
    return False


def answer_problems(case: dict, text: str, removed_links: list[str]) -> list[str]:
    """Everything wrong with an answer, as readable messages (empty list = pass)."""
    norm = normalize(text)
    problems = []
    for item in case["must_include"]:
        options = item if isinstance(item, list) else [item]
        if not any(normalize(o) in norm for o in options):
            problems.append(f"missing one of {options}")
    for bad in case["must_not_include"]:
        if states(norm, normalize(bad)):
            problems.append(f"contains forbidden {bad!r}")
    for pattern in BEHAVIOR_PATTERNS[case["expected_behavior"]]:
        if not re.search(pattern, text, re.IGNORECASE):
            problems.append(f"behavior {case['expected_behavior']!r}: no match for /{pattern[:40]}.../")
    if case["expected_behavior"] == "decline" and re.search(r"\]\(https?://", text):
        problems.append("declined question should not cite sources")
    if removed_links:
        problems.append(f"model wrote links not in sources: {removed_links}")
    return problems


# ------------------------------------------------------------------ tests

def test_eval_file_is_well_formed():
    ids = [c["id"] for c in CASES]
    assert 30 <= len(CASES) <= 40, f"{len(CASES)} questions; CLAUDE.md asks for 30 to 40"
    assert len(ids) == len(set(ids)), "duplicate ids"
    required = {"id", "category", "topic", "question", "expected_citations",
                "must_include", "must_not_include", "expected_behavior", "notes"}
    for c in CASES:
        assert required <= set(c), f"{c.get('id')}: missing {required - set(c)}"
        assert c["category"] in CATEGORIES, c["id"]
        assert c["expected_behavior"] in BEHAVIORS, c["id"]
        for entry in c["expected_citations"]:
            for cit in entry if isinstance(entry, list) else [entry]:
                assert parse_citation(cit.lstrip("="))[0], f"{c['id']}: unparseable citation {cit!r}"
    assert CATEGORIES <= {c["category"] for c in CASES}, "every category needs at least one question"
    assert PHASE1_TOPICS <= {c["topic"] for c in CASES}, "every phase 1 topic needs a question"


@pytest.mark.parametrize(("retrieved", "expected", "result"), [
    ("8 CFR 214.2(f)(9)(i)", "8 CFR 214.2(f)(9)(i)", True),
    ("8 CFR 214.2(f)(9)(i)", "8 CFR 214.2(f)(9)", True),         # more specific chunk
    ("8 CFR 214.2(f)(10)", "8 CFR 214.2(f)(1)", False),           # (10) is not (1)
    ("8 CFR 214.2(f)(9)(ii)(A)–(C)", "8 CFR 214.2(f)(9)(ii)(C)", True),
    ("8 CFR 214.2(f)(9)(ii)(A)–(C)", "8 CFR 214.2(f)(9)(ii)(D)", False),
    ("8 CFR 214.2(f)(5)(ii)–(iii)", "8 CFR 214.2(f)(5)(ii)(E)", True),
    ("8 CFR 248.1(c)(1)–(3)", "8 CFR 248.1(c)", True),
    ("8 CFR 248.2(a)(4)–(6)", "8 CFR 248.2(a)(6)", True),
    ("8 CFR 214.1(m)(1)(i)", "8 CFR 214.2(f)(5)(v)", False),
    ("8 CFR 214.2(f)(10)", "=8 CFR 214.2(f)(10)", True),          # exact paragraph
    ("8 CFR 214.2(f)(10)(ii)(E)", "=8 CFR 214.2(f)(10)", False),  # sub-paragraph is not enough
    ("8 CFR 214.2(f)(16)(i)–(ii)", "=8 CFR 214.2(f)(16)(i)", False),
    ("8 CFR 214.2(f)(5)(ii)(E)–(iii)", "8 CFR 214.2(f)(5)(ii)(E)", True),   # range starting mid-paragraph
    ("8 CFR 214.2(f)(5)(ii)(E)–(iii)", "8 CFR 214.2(f)(5)(iii)", True),
    ("8 CFR 214.2(f)(5)(ii)(E)–(iii)", "8 CFR 214.2(f)(5)(ii)(C)", False),  # (C) comes before the start
    ("8 CFR 214.2(f)(15)(ii)(A)(2)–(C)", "8 CFR 214.2(f)(15)(ii)(C)", True),
    ("91 FR 64566", "91 FR 64566", True),                                    # Federal Register citations
    ("91 FR 64566", "91 FR 65119", False),
    ("USCIS Policy Manual, Vol. 2, Part F, Ch. 5.C.2", "USCIS Policy Manual, Vol. 2, Part F, Ch. 5.C", True),
    ("USCIS Policy Manual, Vol. 2, Part F, Ch. 5.C.2", "USCIS Policy Manual, Vol. 2, Part F, Ch. 5.C.3", False),
    ("USCIS alert, August 28, 2026", "USCIS alert, August 28, 2026", True),
])
def test_citation_matching(retrieved, expected, result):
    assert covers(retrieved, expected) is result


RETRIEVAL_CASES = [c for c in CASES if c["expected_citations"]]


@pytest.mark.slow
@pytest.mark.parametrize("case", RETRIEVAL_CASES, ids=[c["id"] for c in RETRIEVAL_CASES])
def test_retrieval(case):
    hits = [h["citation"] for h in retrieve.retrieve(case["question"], k=TOP_K)]
    # An entry may be a list of alternatives: any one of them satisfies it.
    missing = [e for e in case["expected_citations"]
               if not any(covers(h, alt) for h in hits for alt in (e if isinstance(e, list) else [e]))]
    assert not missing, f"not in top {TOP_K}: {missing}; got {hits}"


@pytest.mark.llm
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_end_to_end(case, request):
    if not request.config.getoption("--e2e"):
        pytest.skip("end-to-end eval runs only with --e2e (calls Claude)")
    from app import chat

    ans = chat.ask(case["question"])
    problems = answer_problems(case, ans.text, ans.removed_links)
    assert not problems, "; ".join(problems) + f"\n--- answer ---\n{ans.text[:1500]}"


@pytest.mark.parametrize(("text", "result"), [
    ("I can't tell you whether you will be deported, and I won't guess.", False),
    ("I can't say if you will be deported.", False),
    ("Based on these facts, you will be deported.", True),
])
def test_forbidden_phrase_needs_a_claim(text, result):
    assert states(normalize(text), "you will be deported") is result

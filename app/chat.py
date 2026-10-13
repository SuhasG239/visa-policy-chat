"""Question -> retrieve -> prompt -> answer with citations.

    python -m app.chat "Can F-1 students work on campus?"   # one question
    python -m app.chat                                      # interactive, with follow-ups
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app import llm, prompts, retrieve

log = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 3       # earlier question/answer pairs sent with a follow-up
# URL may contain balanced parentheses, e.g. ...section-214.2#p-214.2(f)(10)(ii)(E)
LINK = re.compile(r"\[([^\]]+)\]\((https?://(?:[^()\s]|\([^()\s]*\))+)\)")
DISCLAIMER = (
    "General legal information from official sources, not legal advice. Not a substitute "
    "for an immigration attorney, a DOJ-accredited representative, or your school's DSO. "
    "Your question and the retrieved excerpts are sent to Anthropic to generate an answer."
)


@dataclass
class Answer:
    """An answer plus the excerpts it was based on."""

    text: str
    sources: list[dict[str, Any]]
    model: str
    removed_links: list[str] = field(default_factory=list)   # URLs not in any source


def _strip_unknown_links(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Replace markdown links whose URL isn't from a retrieved source with plain text."""
    removed: list[str] = []

    def fix(m: re.Match) -> str:
        if m.group(2) in allowed:
            return m.group(0)
        removed.append(m.group(2))
        return m.group(1)

    return LINK.sub(fix, text), removed


EMPTY_INDEX = "The knowledge base is empty, so I can't answer yet. Run `python -m ingest.sync` first."
COVERAGE = "F-1 students"


def _prepare(question: str, history: list[dict[str, str]] | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Retrieve excerpts and build the messages for one question."""
    history = (history or [])[-2 * MAX_HISTORY_TURNS:]
    # A follow-up like "what about STEM OPT?" needs the previous question to retrieve well.
    previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    chunks = retrieve.retrieve(f"{previous} {question}".strip() if previous else question)
    messages = history + [
        {"role": "user", "content": prompts.user_message(question, chunks, date.today().isoformat())}
    ]
    return chunks, messages


def _finalize(text: str, chunks: list[dict[str, Any]]) -> Answer:
    text, removed = _strip_unknown_links(text, {c["url"] for c in chunks})
    if removed:
        log.warning("Removed %d link(s) not found in sources: %s", len(removed), removed)
    return Answer(text, chunks, llm.model_name(), removed)


def ask(question: str, history: list[dict[str, str]] | None = None) -> Answer:
    """Answer a question. `history` is earlier turns as {"role", "content"} dicts.

    Raises llm.LLMError if the model call fails.
    """
    chunks, messages = _prepare(question, history)
    if not chunks:
        return Answer(EMPTY_INDEX, [], llm.model_name())
    text = llm.chat(messages, system=prompts.SYSTEM_PROMPT, max_tokens=8000)
    return _finalize(text, chunks)


def ask_stream(
    question: str, history: list[dict[str, str]] | None = None
) -> Iterator[tuple[str, Any]]:
    """Streaming version of ask(): yields ("token", str) pieces, then ("done", Answer).

    The final Answer's text has unknown links removed, so it can differ from the
    concatenated tokens; clients should replace the streamed text with it.
    Raises llm.LLMError if the model call fails.
    """
    chunks, messages = _prepare(question, history)
    if not chunks:
        yield "token", EMPTY_INDEX
        yield "done", Answer(EMPTY_INDEX, [], llm.model_name())
        return
    parts: list[str] = []
    for piece in llm.chat_stream(messages, system=prompts.SYSTEM_PROMPT, max_tokens=8000):
        parts.append(piece)
        yield "token", piece
    yield "done", _finalize("".join(parts), chunks)


def warm_up() -> None:
    """Load the embedding model and keyword index so the first question isn't slow."""
    retrieve.retrieve("F-1 student status")


def _print_answer(ans: Answer) -> None:
    print(ans.text)
    print("\nSources:")
    for c in ans.sources:
        print(f"  - {c['citation']} (updated {c['source_updated'] or 'unknown'}): {c['url']}")
    if ans.removed_links:
        print(f"\n[!] Removed {len(ans.removed_links)} link(s) that did not match any source.")
    print(f"\n{DISCLAIMER}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # section signs and dashes on Windows
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) > 1:
        try:
            _print_answer(ask(" ".join(sys.argv[1:])))
        except llm.LLMError as exc:
            raise SystemExit(f"Error: {exc}") from exc
        return

    print(f"{DISCLAIMER}\nAsk about F-1 student rules. Empty line to quit.\n")
    history: list[dict[str, str]] = []
    while True:
        try:
            question = input("You: ").strip()
        except EOFError:
            break
        if not question:
            break
        try:
            ans = ask(question, history)
        except llm.LLMError as exc:
            print(f"Error: {exc}\n")
            continue
        print()
        _print_answer(ans)
        print()
        history += [{"role": "user", "content": question}, {"role": "assistant", "content": ans.text}]


if __name__ == "__main__":
    main()

# Visa Policy Chat

A local RAG chatbot that answers questions about U.S. immigration rules for people
in the United States on nonimmigrant visas. Answers are grounded only in official
government sources, cite the exact section with a link and date, and are written
in plain language.

This is a legal-information tool, not legal advice. See "Legal guardrails" below;
those rules are not optional.

## Scope (phase 1)

Start with **F-1 students** only: maintaining status, on-campus and off-campus work,
CPT, OPT, STEM OPT, travel and re-entry, transfers, grace periods, and change of status.
Do not expand scope until the phase 1 test set passes. Planned next: H-1B, then
dependents (F-2, H-4), then green card basics (Visa Bulletin, I-485).

## Tech stack

- Python 3.11+, virtual environment in `.venv/`
- PyMuPDF for PDFs; `requests` + BeautifulSoup for HTML; `lxml` for eCFR XML
- `sentence-transformers` for embeddings (default `BAAI/bge-small-en-v1.5`, runs on CPU)
- `rank_bm25` for keyword search alongside vector search
- ChromaDB, persistent, stored in `db/`
- Claude for the LLM (default `claude-opus-5-5`), called through the official `anthropic` SDK
- `pyyaml` for `ingest/sources.yaml`
- Streamlit for the UI
- `python-dotenv` for configuration
- `pytest` for tests

## Architecture rules

These rules exist so pieces can be swapped later without rewrites.

1. **All LLM calls go through `app/llm.py`.** It uses the `anthropic` package with
   `ANTHROPIC_API_KEY`, `LLM_MODEL`, and `LLM_EFFORT` from `.env`. No other file imports
   `anthropic`, `openai`, or `ollama` directly. Changing model is a `.env` edit; changing
   provider must only touch `llm.py`.
2. **All vector database code goes through `app/store.py`.** It exposes
   `upsert_chunks()`, `delete_by_section()`, `search()`, `all_chunks()`, `stats()`, and `reset()`.
   No other file imports `chromadb`. Swapping to Postgres + pgvector later must only
   touch this file.
3. **All embedding code goes through `app/embed.py`.** The embedding model name lives in
   `.env` as `EMBED_MODEL`. Store the model name in the index metadata; if it changes,
   the sync script must require a full re-index and never mix embeddings from two models.
4. Secrets and settings live in `.env`. Commit `.env.example`, never `.env`.
   `.gitignore` must include `.env`, `.venv/`, `db/`, and `data/raw/`.

## Project layout

```
visa-policy-chat/
  CLAUDE.md
  .env.example
  requirements.txt
  app/
    llm.py          # LLM provider wrapper (only place that talks to a model)
    embed.py        # embedding wrapper
    store.py        # ChromaDB wrapper (only place that talks to the DB)
    retrieve.py     # hybrid retrieval: vector + BM25, merge, optional rerank
    prompts.py      # system prompt and answer template
    chat.py         # question -> retrieve -> prompt -> answer with citations
    ui.py           # Streamlit app
  ingest/
    sources.yaml    # every source: id, type, URL/API params, scope tags
    fetch_ecfr.py
    fetch_federal_register.py
    fetch_uscis.py  # Policy Manual chapters, Policy Alerts, newsroom alerts
    fetch_sevp.py   # Study in the States pages
    fetch_state.py  # 9 FAM sections, Visa Bulletin (later phases)
    parse.py        # raw file -> clean text sections
    chunk.py        # sections -> chunks with metadata and stable IDs
    sync.py         # fetch -> diff -> parse -> chunk -> replace in DB
  data/
    raw/            # fetched files, named by source id and fetch date
    manifest.json   # per source: last fetched, content hash, source-updated date
  db/               # ChromaDB persistence
  tests/
    eval_questions.yaml
    test_eval.py
    test_store.py
```

## Data sources

Use only official government sources. Do not ingest law firm blogs, news articles,
forums, or other secondhand material into the knowledge base.

Before writing a fetcher, read the source's current developer documentation and confirm
the endpoints and parameters. Do not rely on remembered URLs.

| Source | What it covers | Access |
| --- | --- | --- |
| USCIS Policy Manual (uscis.gov/policy-manual) | USCIS's official policy guidance | HTML pages per volume/part/chapter |
| USCIS Policy Manual updates (uscis.gov/policy-manual/updates) | Policy Alerts announcing changes | HTML list, check on every sync |
| USCIS Alerts (uscis.gov/newsroom/alerts) | Fees, form editions, caps, court orders affecting policy | HTML list, check on every sync |
| eCFR (ecfr.gov) | Regulations in force. Phase 1: 8 CFR 214.1, 8 CFR 214.2(f), 8 CFR part 248 | Free REST API, no key. Use title dates (`latest_amended_on`, `up_to_date_as_of`) to detect changes |
| Federal Register (federalregister.gov) | New and proposed rules, proclamations | Free REST API, no key. Filter by agency (USCIS, DHS, State, ICE) and CFR title 8 |
| Study in the States (studyinthestates.dhs.gov) | SEVP guidance for F-1/M-1 students and DSOs | HTML pages |
| State Dept 9 FAM (fam.state.gov), Visa Bulletin (travel.state.gov) | Consular visa rules, priority dates | Later phases |

Every source is listed in `ingest/sources.yaml` with an id, type, location, and scope
tags (e.g. `f1`, `opt`, `h1b`). Fetchers read from that file; nothing is hardcoded elsewhere.

Be a polite client: set a descriptive User-Agent, add delays between requests,
cache raw downloads, and only re-download what may have changed.

## Chunk metadata

Every chunk stored in the database must carry:

- `chunk_id`: stable and deterministic, built from source and section, e.g.
  `ecfr-8-214.2-f-5` or `uscis-pm-v2-pF-ch5-s3-001`. Never random.
- `section_key`: the section the chunk belongs to (used to replace all of a section's chunks at once)
- `source`: `uscis_pm`, `ecfr`, `federal_register`, `uscis_alert`, `sevp`, `fam`, `visa_bulletin`
- `citation`: human-readable, e.g. "8 CFR 214.2(f)(10)" or "USCIS Policy Manual, Vol. 2, Part F, Ch. 5"
- `title`: section heading
- `url`: direct link to the official page or section
- `effective_date`: when the rule or guidance takes effect, if stated
- `source_updated`: the date the source says it was last updated
- `fetched_at`: when we downloaded it
- `doc_type`: `regulation`, `policy_guidance`, `proposed_rule`, `final_rule`, `alert`, `court_order`
- `scope`: tags, e.g. `["f1", "opt"]`

Chunk by the source's own structure (CFR paragraph, Policy Manual section), not by
fixed character counts. Keep chunks under roughly 400 tokens; split long sections
on sub-paragraph boundaries and keep the parent citation on every piece.

## Sync rules

`python -m ingest.sync` must be safe to run any time (idempotent) and is meant to run
daily on a schedule.

1. Fetch each source, compute a content hash, and compare with `data/manifest.json`.
2. Skip unchanged sources.
3. For a changed section, **delete its old chunks first** (`delete_by_section`), then
   upsert the new ones. Two versions of the same section must never coexist in the database.
4. Proposed rules are stored with `doc_type: proposed_rule` and must never be presented
   as current law.
5. Court orders and USCIS alerts that suspend, enjoin, or vacate a policy are high
   priority: store them and tag the affected policy chunks so answers can warn about it.
6. Write a sync log listing what changed, then update the manifest.
7. Record the last successful sync time so the UI can show it.

## Answer rules (system prompt requirements)

The model must:

- Answer **only** from the retrieved excerpts. If they don't cover the question, say so
  plainly and point to the official source or a professional. Never fill gaps from memory.
- Cite every factual claim with the chunk's `citation`, linked to its `url`, and show
  `source_updated`.
- Use plain language, and give the exact rule where precision matters
  (deadlines, day counts, unemployment limits).
- Clearly separate current rules, rules with a future effective date, and proposed rules.
- Flag when a cited policy is affected by a court order or recent alert.
- End with two or three suggested follow-up questions.
- Stay within immigration-rule topics and politely decline unrelated requests.

Keep the system prompt short, explicit, and only in `app/prompts.py`. Retrieve 5 to 8 chunks and put each in the prompt with its
citation and dates as a header.

## Legal guardrails

- The UI shows a persistent notice: this tool gives general legal information from
  official sources, not legal advice, and is not a substitute for an immigration
  attorney or DOJ-accredited representative.
- When a question is about the user's own situation (e.g. "what should I do",
  "will I be denied", overstays, arrests, removal proceedings, missed deadlines), the bot
  explains the relevant general rules, then clearly recommends an immigration attorney or
  accredited representative, and the school's DSO for F-1 students.
- Never tell a user to take or skip a specific legal action, and never predict the
  outcome of their case.
- Never invent forms, fees, deadlines, or URLs. Fees and form editions change often and
  must come from retrieved, dated sources.

## Privacy

Users may describe sensitive details about their immigration status.

- Do not log conversation content by default. Any logging is opt-in and local.
  `LLM_USAGE_LOG` records only timestamps, model, and token counts (`app/usage.py`).
- No telemetry or analytics.
- The LLM is hosted (Anthropic), so the UI must say that questions are sent to Anthropic.
  Only the question and retrieved excerpts go to the API.

## Testing

- `tests/eval_questions.yaml` holds 30 to 40 questions for the current scope. Fewer
  questions, but each one detailed enough to grade on its own:
  - `category` (rule lookup, out-of-scope, not-covered, future-effective/proposed, personal situation)
  - `expected_citations`: the citation(s) retrieval must return in the top results
  - `must_include`: specific facts the answer must state (exact day counts, hour limits, deadlines)
  - `must_not_include`: wrong or invented claims the answer must avoid
  - `expected_behavior`: e.g. answer, decline, say not covered, recommend attorney/DSO
  - `notes`: why the question is there and what failure it catches
  Cover every phase 1 topic, plus out-of-scope questions (must be declined), questions the
  sources don't answer (must say so), future-effective or proposed rules, and
  personal-situation questions (must recommend a professional).
- `pytest tests/test_eval.py` runs retrieval checks (expected citation in the top results)
  without the LLM, plus an optional slower end-to-end mode with the LLM.
- Run the eval after every sync and after any change to chunking, retrieval, or prompts.
- Verify changes by running the code; don't assume they work.

## Build order

Work in this order, and confirm each step runs before starting the next:

1. Skeleton, `requirements.txt`, `.env.example`, `.gitignore`, and the three wrappers
   (`llm.py`, `embed.py`, `store.py`) with a smoke test for each.
2. eCFR fetcher for 8 CFR 214.1, 214.2(f), and part 248 -> parse -> chunk -> index.
3. Retrieval and `chat.py` working from the command line with citations.
4. Streamlit UI with clickable citations, disclaimer, and last-synced indicator.
5. Initial eval set and `test_eval.py`.
6. Federal Register fetcher (immigration agencies, title 8).
7. USCIS Policy Manual chapters relevant to F-1, plus Policy Alerts and newsroom alerts.
8. Study in the States pages.
9. `sync.py` with the change-detection and replace rules above, then scheduling.

## Conventions

- Type hints and short docstrings on public functions.
- Small modules; no file over about 300 lines.
- Use `logging`, not `print`, except for a script's final output.
- Ask before adding a new dependency or a new data source.

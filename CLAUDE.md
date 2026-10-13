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
- **Backend API: FastAPI** (`uvicorn` to serve it) exposing the RAG pipeline over HTTP. This replaces the earlier plan to use Streamlit for the UI.
- **Frontend: a separate React app (Next.js, App Router, TypeScript)** in `web/`, styled with Tailwind. It is a pure client of the API and shares no Python.
- `python-dotenv` for configuration
- `pytest` for tests (backend); the frontend uses its own tooling in `web/`

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
4. **All HTTP/transport code goes through `app/api.py`.** It is the only file that imports
   `fastapi`. It calls `app.chat.ask(...)` (and, for streaming, a new `llm.chat_stream`)
   and does no retrieval, prompting, or model work of its own. The web frontend talks only
   to this API and never imports anything under `app/`. Swapping the frontend, or putting
   the API behind a different web framework later, must only touch this file.
5. Secrets and settings live in `.env`. Commit `.env.example`, never `.env`.
   `.gitignore` must include `.env`, `.venv/`, `db/`, `data/raw/`, and `web/node_modules/`,
   `web/.next/`. The frontend reads its own config from `web/.env.local` (e.g.
   `NEXT_PUBLIC_API_BASE`); never put the Anthropic key in any `web/` file.

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
    api.py          # FastAPI app: /ask (JSON), /ask/stream (SSE), /meta, /health
  web/              # Next.js + TypeScript + Tailwind frontend (pure API client)
    app/            # App Router pages; chat view is the home route
    components/     # ChatView, MessageList, AnswerProse, SourceCard, DisclaimerBar, Composer
    lib/            # api.ts (typed fetch wrapper + SSE), types.ts (mirrors the /ask schema)
    .env.local      # NEXT_PUBLIC_API_BASE (gitignored)
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

## Web frontend

The UI is a standalone React app in `web/` (Next.js, App Router, TypeScript, Tailwind)
that talks to `app/api.py` over HTTP. It holds no immigration logic of its own: every
rule, citation, and disclaimer comes from the API response. Keeping the frontend "dumb"
is what preserves the legal guardrails — it must not paraphrase, summarize, or re-rank
answers, only render what the backend sends.

### API contract (`app/api.py`)

The API wraps the existing pipeline; it does not reimplement it. `ask()` already returns
an `Answer(text, sources, model, removed_links)` where each item in `sources` is a chunk
dict with `citation`, `title`, `url`, `source_updated`, `effective_date`, `doc_type`,
`scope`, and `chunk_id`. Expose exactly that shape:

- `POST /ask` → body `{ "question": str, "history": [{ "role", "content" }] }`.
  Returns JSON `{ "text", "sources": [...], "model", "removed_links" }`. `text` keeps the
  model's inline citation markers and markdown links so the frontend can render them.
- `POST /ask/stream` → same body, **Server-Sent Events**. Stream `token` events as the
  answer text is generated, then one final `done` event carrying `sources`, `model`, and
  `removed_links` (these are known only after retrieval/formatting, so they arrive last).
  Streaming requires a new `llm.chat_stream(...)` generator in `app/llm.py` — **add it
  there, not in `api.py`** (architecture rule 1 still holds: nothing else imports
  `anthropic`). `chat.ask` keeps its current non-streaming behavior for `/ask` and tests.
- `GET /meta` → `{ "model", "last_synced", "chunk_count", "scope" }` for the header's
  model badge and last-synced indicator (`last_synced` from the sync record; rule 7 under
  "Sync rules"). `GET /health` → `{ "ok": true }`.
- CORS: allow only the frontend origin, from an env var. The Anthropic key never leaves
  the backend; the browser only ever calls this API.

### What the frontend must render

- **Answer prose** with inline citation markers turned into superscript links that point
  to the matching source. Render the model's markdown; do not strip the `removed_links`
  handling — if the backend removed links, it already cleaned the text.
- **Source cards**, one per `sources` item: the `citation` (monospace), `title`, a
  `doc_type` badge in a semantic color (regulation / policy_guidance / proposed_rule /
  court_order / alert), the `scope` tags, `source_updated`, and the `url` as an external
  link. Current, future-effective, and proposed rules must stay visually distinct —
  never let a `proposed_rule` read as current law.
- **Court-order / alert flag**: when an answer cites a `court_order` or `alert` doc_type,
  surface a warning callout, matching the backend's duty to flag affected policy.
- **Persistent disclaimer bar** (general legal information, not legal advice; recommends
  an attorney/DSO) and the "questions are sent to Anthropic" notice. These are required
  by "Legal guardrails" and "Privacy" and must always be visible, not dismissible.
- **Suggested follow-ups** from the two or three questions the model appends, as clickable
  chips. **Last-synced indicator** and **model badge** in the header from `/meta`.
- Loading/streaming state, keyboard send (Enter), and an accessible, responsive layout
  that works on mobile.

### Visual target and design direction

A styled, interactive prototype of the target UI has been built as a reference (ask the
project owner for the "Visa Policy Chat" artifact link). Match its structure and spirit:
a calm, trustworthy, document-forward look — serif answer prose over a clean sans UI,
restrained palette with a single confident accent, semantic colors reserved for doc-type
badges and warnings. It is a design reference, not code to copy; rebuild it properly in
React + Tailwind components. Build real light and dark themes.

### Skills to use (Claude Code)

- `vercel-react-best-practices` and `vercel-composition-patterns` when writing the React
  components and data fetching.
- `vercel-react-view-transitions` if adding route or state transitions.
- A design-direction skill (e.g. Taste Skill, or a brand `DESIGN.md` such as Linear /
  Mercury / Stripe) to set the aesthetic before building components.
- `web-design-guidelines` to audit the result for accessibility and polish once it runs.
- `deploy-to-vercel` for the frontend. The **backend deploys separately** (it needs
  Python, ChromaDB, and the embedding model) on a host that allows long-lived processes —
  Vercel is not suitable for the API. Point `NEXT_PUBLIC_API_BASE` at wherever the API runs.

### Guardrails for the frontend

- Do not cache or persist answer content or questions in the browser (localStorage,
  analytics, etc.). "Privacy" applies to the frontend too: no telemetry, no analytics.
- Never render a link that wasn't in a source; the backend strips unknown links, so the
  frontend trusts `sources` for the citation targets.
- Keep the disclaimer and the "sent to Anthropic" notice visible on every view.

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
4. `app/api.py` (FastAPI) exposing `/ask`, `/ask/stream`, `/meta`, `/health` over the
   existing `chat.ask(...)`. Confirm with `curl` before touching the frontend.
5. `web/` frontend (Next.js) calling the API: chat view with streamed answers, citation
   source cards, the persistent disclaimer, and the last-synced indicator. See the
   "Web frontend" section for the contract and the visual target.
6. Initial eval set and `test_eval.py`.
7. Federal Register fetcher (immigration agencies, title 8).
8. USCIS Policy Manual chapters relevant to F-1, plus Policy Alerts and newsroom alerts.
9. Study in the States pages.
10. `sync.py` with the change-detection and replace rules above, then scheduling.

## Conventions

- Type hints and short docstrings on public functions.
- Small modules; no file over about 300 lines.
- Use `logging`, not `print`, except for a script's final output.
- Ask before adding a new dependency or a new data source.

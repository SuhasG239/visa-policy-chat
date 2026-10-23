"""Fetch new and proposed rules from the Federal Register API.

Endpoint (https://www.federalregister.gov/developers/documentation/api/v1):
  /api/v1/documents.json   search, filtered by agency, CFR title/part, type, and date

Which documents are kept (all settings in sources.yaml):
  - title or abstract mentions one of `include_terms` (phase 1 is F-1 only)
  - proposed rules, unless a final rule with the same RIN has been published
  - final rules published within `final_rule_window_days`, or not yet effective;
    older final rules are already reflected in eCFR

The day's search results are cached in data/raw/ as <source id>_<date>.json.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ingest.config import RAW_DIR, PoliteClient, load_sources

log = logging.getLogger(__name__)

API = "https://www.federalregister.gov/api/v1/documents.json"
FIELDS = [
    "document_number", "type", "title", "action", "abstract", "dates", "citation",
    "publication_date", "effective_on", "comments_close_on", "html_url",
    "regulation_id_numbers", "agency_names", "cfr_references",
]
PER_PAGE = 100


@dataclass
class FrFetch:
    """Result of fetching one Federal Register source."""

    source: dict[str, Any]
    documents: list[dict[str, Any]]   # kept documents, newest first
    fetched_at: str
    raw_file: str


def search(client: PoliteClient, src: dict[str, Any]) -> list[dict[str, Any]]:
    """All documents matching the source's filters, across result pages."""
    docs: list[dict[str, Any]] = []
    for part in src["cfr_parts"]:
        params: dict[str, Any] = {
            "conditions[agencies][]": src["agencies"],
            "conditions[cfr][title]": src["cfr_title"],
            "conditions[cfr][part]": part,
            "conditions[type][]": src["document_types"],
            "conditions[publication_date][gte]": src["published_since"],
            "fields[]": FIELDS,
            "per_page": PER_PAGE,
            "order": "newest",
        }
        page = 1
        while True:
            data = client.get(API, {**params, "page": page}).json()
            docs.extend(data.get("results", []))
            if page * PER_PAGE >= data.get("count", 0):
                break
            page += 1
    unique = {d["document_number"]: d for d in docs}   # a rule can touch both parts
    return sorted(unique.values(), key=lambda d: d["publication_date"], reverse=True)


def is_relevant(doc: dict[str, Any], terms: list[str]) -> bool:
    """True if the title or abstract mentions any include term."""
    text = f"{doc.get('title') or ''} {doc.get('abstract') or ''}".lower()
    return any(t.lower() in text for t in terms)


def select(docs: list[dict[str, Any]], src: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Apply the relevance, superseded-proposal, and final-rule-window rules."""
    relevant = [d for d in docs if is_relevant(d, src["include_terms"])]
    final_rins = {
        rin for d in relevant if d["type"] == "Rule" for rin in d.get("regulation_id_numbers") or []
    }
    window_start = (today - timedelta(days=int(src["final_rule_window_days"]))).isoformat()
    kept = []
    for d in relevant:
        if d["type"] == "Proposed Rule":
            if final_rins & set(d.get("regulation_id_numbers") or []):
                log.info("Dropping %s: proposal superseded by a final rule", d["document_number"])
                continue
        elif d["type"] == "Rule":
            not_yet_effective = (d.get("effective_on") or "") > today.isoformat()
            if d["publication_date"] < window_start and not not_yet_effective:
                continue
        else:
            continue
        kept.append(d)
    return kept


def fetch(src: dict[str, Any], client: PoliteClient, today: date | None = None) -> FrFetch:
    """Search (or reuse today's cached search) and select documents for one source."""
    today = today or date.today()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{src['id']}_{today.isoformat()}.json"
    if raw_path.exists():
        cached = json.loads(raw_path.read_text(encoding="utf-8"))
        docs, fetched_at = cached["documents"], cached["fetched_at"]
        log.info("%s: using cached %s", src["id"], raw_path.name)
    else:
        docs = search(client, src)
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        raw_path.write_text(json.dumps({"fetched_at": fetched_at, "documents": docs}, indent=1), encoding="utf-8")
        log.info("%s: %d documents from the API", src["id"], len(docs))
    kept = select(docs, src, today)
    log.info("%s: kept %d of %d documents", src["id"], len(kept), len(docs))
    return FrFetch(src, kept, fetched_at, raw_path.name)


def fetch_all(source_id: str | None = None) -> list[FrFetch]:
    """Fetch every Federal Register source in sources.yaml (or just `source_id`)."""
    client = PoliteClient()
    return [
        fetch(src, client)
        for src in load_sources("federal_register")
        if not source_id or src["id"] == source_id
    ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for r in fetch_all():
        for d in r.documents:
            print(f"{d['publication_date']}  {d['type']:<13} {d['citation']:<12} {d['title'][:70]}")

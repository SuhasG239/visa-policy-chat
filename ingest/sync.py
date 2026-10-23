"""Fetch -> parse -> chunk -> replace in the vector store. Safe to re-run.

    python -m ingest.sync                 # all sources
    python -m ingest.sync --source ID     # one source
    python -m ingest.sync --reindex       # drop the index and rebuild everything

A source is skipped when its chunks hash the same as in data/manifest.json.
For a changed source, every old and new section_key is deleted before the new
chunks are upserted, so two versions of a section never coexist.

Handles eCFR, Federal Register, USCIS, and Study in the States sources.
Change logs and court-order tagging come in build step 10.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app import embed, store
from ingest import chunk, chunk_sevp, chunk_uscis, fetch_ecfr, fetch_federal_register, fetch_sevp, fetch_uscis, parse
from ingest.config import MANIFEST_FILE

log = logging.getLogger("ingest.sync")


def _load_manifest() -> dict[str, Any]:
    if MANIFEST_FILE.exists():
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    return {"sources": {}}


def _save_manifest(manifest: dict[str, Any]) -> None:
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _content_hash(chunks: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(f"{c['chunk_id']}\n{c['text']}\n{c['source_updated']}\n".encode())
    return h.hexdigest()


def _replace(chunks: list[dict[str, Any]], old_keys: list[str]) -> None:
    keys = sorted(set(old_keys) | {c["section_key"] for c in chunks})
    for key in keys:
        store.delete_by_section(key)
    if chunks:   # a source can legitimately shrink to nothing (e.g. every proposal finalized)
        store.upsert_chunks(chunks, embed.embed_documents([c["text"] for c in chunks]))


def _jobs(source_id: str | None):
    """Yield (source, chunks, manifest details) for every source: eCFR, Federal Register, USCIS, SEVP."""
    for f in fetch_ecfr.fetch_all(source_id):
        sections = parse.parse_ecfr(f.raw_path.read_bytes())
        yield f.source, chunk.chunk_ecfr(sections, f.source, f.fetched_at, f.amended), {
            "last_fetched": f.fetched_at,
            "source_date": f.date,
            "source_updated": max(f.amended.values(), default=""),
            "raw_file": f.raw_path.name,
        }
    for f in fetch_federal_register.fetch_all(source_id):
        yield f.source, chunk.chunk_federal_register(f.documents, f.source, f.fetched_at), {
            "last_fetched": f.fetched_at,
            "source_updated": max((d["publication_date"] for d in f.documents), default=""),
            "raw_file": f.raw_file,
            "documents": [f"{d['citation']} ({d['type']})" for d in f.documents],
        }
    for f in fetch_uscis.fetch_all(source_id):
        if f.source["type"] == "uscis_pm":
            chunks = (chunk_uscis.chunk_policy_manual(f.chapters, f.source, f.fetched_at)
                      + chunk_uscis.chunk_policy_alerts(f.chapters, f.source, f.fetched_at))
            updated = max((u.date for c in f.chapters for u in c.updates), default="")
        else:
            chunks = chunk_uscis.chunk_alerts(f.alerts, f.source, f.fetched_at)
            updated = max((a.date for a in f.alerts), default="")
        yield f.source, chunks, {"last_fetched": f.fetched_at, "source_updated": updated, "raw_file": f.raw_dir}
    for f in fetch_sevp.fetch_all(source_id):
        yield f.source, chunk_sevp.chunk_sevp(f.pages, f.source, f.fetched_at), {
            "last_fetched": f.fetched_at,
            "source_updated": max((p.updated for p in f.pages), default=""),
            "raw_file": f.raw_dir,
        }


def sync(source_id: str | None = None, reindex: bool = False) -> dict[str, str]:
    """Sync every source. Returns {source id: "updated" | "unchanged"}."""
    if reindex:
        store.reset()
    manifest = _load_manifest()
    force = reindex or store.stats()["count"] == 0
    results: dict[str, str] = {}

    for src, chunks, details in _jobs(source_id):
        digest = _content_hash(chunks)
        entry = manifest["sources"].get(src["id"], {})
        if not force and entry.get("content_hash") == digest:
            log.info("%s: unchanged (%d chunks)", src["id"], len(chunks))
            results[src["id"]] = "unchanged"
            continue
        _replace(chunks, entry.get("section_keys", []))
        manifest["sources"][src["id"]] = {
            "type": src["type"],
            **details,
            "content_hash": digest,
            "section_keys": sorted({c["section_key"] for c in chunks}),
            "chunk_count": len(chunks),
        }
        log.info("%s: indexed %d chunks", src["id"], len(chunks))
        results[src["id"]] = "updated"

    manifest["embed_model"] = embed.model_name()
    if not source_id:
        manifest["last_successful_sync"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_manifest(manifest)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", help="only sync this source id")
    parser.add_argument("--reindex", action="store_true", help="drop the index and rebuild")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        results = sync(args.source, args.reindex)
    except store.EmbeddingModelMismatch as exc:
        raise SystemExit(f"{exc}\nRe-run with --reindex.") from exc
    print(json.dumps({**results, "stats": store.stats()}, indent=2))


if __name__ == "__main__":
    main()

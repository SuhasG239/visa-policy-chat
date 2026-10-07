"""Fetch -> parse -> chunk -> replace in the vector store. Safe to re-run.

    python -m ingest.sync                 # all sources
    python -m ingest.sync --source ID     # one source
    python -m ingest.sync --reindex       # drop the index and rebuild everything

A source is skipped when its chunks hash the same as in data/manifest.json.
For a changed source, every old and new section_key is deleted before the new
chunks are upserted, so two versions of a section never coexist.

Currently handles eCFR sources (build step 2). Change logs, alerts, and other
source types come in later build steps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app import embed, store
from ingest import chunk, fetch_ecfr, parse
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
    vectors = embed.embed_documents([c["text"] for c in chunks])
    store.upsert_chunks(chunks, vectors)


def sync(source_id: str | None = None, reindex: bool = False) -> dict[str, str]:
    """Sync eCFR sources. Returns {source id: "updated" | "unchanged"}."""
    if reindex:
        store.reset()
    manifest = _load_manifest()
    force = reindex or store.stats()["count"] == 0
    results: dict[str, str] = {}

    for fetched in fetch_ecfr.fetch_all(source_id):
        src = fetched.source
        sections = parse.parse_ecfr(fetched.raw_path.read_bytes())
        chunks = chunk.chunk_ecfr(sections, src, fetched.fetched_at, fetched.amended)
        digest = _content_hash(chunks)
        entry = manifest["sources"].get(src["id"], {})
        if not force and entry.get("content_hash") == digest:
            log.info("%s: unchanged (%d chunks)", src["id"], len(chunks))
            results[src["id"]] = "unchanged"
            continue
        _replace(chunks, entry.get("section_keys", []))
        manifest["sources"][src["id"]] = {
            "type": src["type"],
            "last_fetched": fetched.fetched_at,
            "source_date": fetched.date,
            "source_updated": max(fetched.amended.values(), default=""),
            "content_hash": digest,
            "raw_file": fetched.raw_path.name,
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

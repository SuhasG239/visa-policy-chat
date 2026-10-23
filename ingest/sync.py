"""Fetch -> parse -> chunk -> tag -> replace changed sections in the vector store.

    python -m ingest.sync                 # all sources
    python -m ingest.sync --source ID     # one source
    python -m ingest.sync --reindex       # drop the index and rebuild everything
    python -m ingest.sync --eval          # afterwards, run the free retrieval eval

Safe to run any time (CLAUDE.md "Sync rules"):
  1-2. Every source is fetched and chunked; a section whose chunks hash the same as
       in data/manifest.json is left alone.
  3.   A changed section's old chunks are deleted before the new ones are added, so
       two versions of a section never coexist; vanished sections are deleted.
  5.   Court orders and suspending alerts tag the chunks they affect (court_orders.py).
  6.   Each run appends a record to data/sync-log.jsonl, then updates the manifest.
  7.   last_successful_sync is recorded only when every source synced without error.
A source that fails (e.g. the site is down) keeps its previous chunks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app import embed, store
from ingest import (chunk, chunk_sevp, chunk_uscis, court_orders, fetch_ecfr, fetch_federal_register,
                    fetch_sevp, fetch_uscis, parse)
from ingest.config import MANIFEST_FILE, RAW_DIR, ROOT, PoliteClient, load_sources

log = logging.getLogger("ingest.sync")

SYNC_LOG = ROOT / "data" / "sync-log.jsonl"
RAW_KEEP_DAYS = 14           # cached raw downloads older than this are pruned


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_manifest() -> dict[str, Any]:
    if MANIFEST_FILE.exists():
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    return {"sources": {}}


def _section_hashes(chunks: list[dict[str, Any]]) -> dict[str, str]:
    """One hash per section over its chunks' ids, text, dates, and court-order tags."""
    hashes: dict[str, Any] = {}
    for c in sorted(chunks, key=lambda c: c["chunk_id"]):
        h = hashes.setdefault(c["section_key"], hashlib.sha256())
        h.update(f"{c['chunk_id']}\n{c['text']}\n{c['source_updated']}\n{c.get('affected_by')}\n".encode())
    return {k: h.hexdigest() for k, h in hashes.items()}


class _Fetchers:
    """Builds chunks for one source; shares an HTTP client and eCFR title dates across sources."""

    def __init__(self) -> None:
        self.client = PoliteClient()
        self.ecfr_dates: dict[str, str] = {}
        self.today = date.today()

    def build(self, src: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        t = src["type"]
        if t == "ecfr":
            if src["title"] not in self.ecfr_dates:
                self.ecfr_dates[src["title"]] = fetch_ecfr.title_info(self.client, src["title"])["up_to_date_as_of"]
            f = fetch_ecfr.fetch(src, self.client, self.ecfr_dates[src["title"]])
            chunks = chunk.chunk_ecfr(parse.parse_ecfr(f.raw_path.read_bytes()), src, f.fetched_at, f.amended)
            return chunks, {"last_fetched": f.fetched_at, "source_date": f.date, "raw_file": f.raw_path.name,
                            "source_updated": max(f.amended.values(), default="")}
        if t == "federal_register":
            f = fetch_federal_register.fetch(src, self.client)
            return chunk.chunk_federal_register(f.documents, src, f.fetched_at), {
                "last_fetched": f.fetched_at, "raw_file": f.raw_file,
                "source_updated": max((d["publication_date"] for d in f.documents), default=""),
                "documents": [f"{d['citation']} ({d['type']})" for d in f.documents]}
        if t == "uscis_pm":
            f = fetch_uscis.fetch_policy_manual(src, self.today)
            chunks = (chunk_uscis.chunk_policy_manual(f.chapters, src, f.fetched_at)
                      + chunk_uscis.chunk_policy_alerts(f.chapters, src, f.fetched_at))
            return chunks, {"last_fetched": f.fetched_at, "raw_file": f.raw_dir,
                            "source_updated": max((u.date for c in f.chapters for u in c.updates), default="")}
        if t == "uscis_alert":
            f = fetch_uscis.fetch_alerts(src, self.today)
            return chunk_uscis.chunk_alerts(f.alerts, src, f.fetched_at), {
                "last_fetched": f.fetched_at, "raw_file": f.raw_dir,
                "source_updated": max((a.date for a in f.alerts), default="")}
        if t == "sevp":
            f = fetch_sevp.fetch(src, self.today)
            return chunk_sevp.chunk_sevp(f.pages, src, f.fetched_at), {
                "last_fetched": f.fetched_at, "raw_file": f.raw_dir,
                "source_updated": max((p.updated for p in f.pages), default="")}
        raise ValueError(f"Unknown source type {t!r}")


def _apply(src_id: str, chunks: list[dict[str, Any]], old: dict[str, str | None]) -> dict[str, list[str]]:
    """Replace changed sections, add new ones, delete vanished ones. Returns the diff."""
    new = _section_hashes(chunks)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(new) & set(old) if new[k] != old[k])
    for key in removed + changed:
        store.delete_by_section(key)          # old version out first (sync rule 3)
    fresh = [c for c in chunks if c["section_key"] in set(added + changed)]
    if fresh:
        store.upsert_chunks(fresh, embed.embed_documents([c["text"] for c in fresh]))
    log.info("%s: %d added, %d changed, %d removed sections", src_id, len(added), len(changed), len(removed))
    return {"added": added, "changed": changed, "removed": removed}


def _prune_raw(today: date) -> int:
    """Delete cached downloads (data/raw/*_YYYY-MM-DD[...]) older than RAW_KEEP_DAYS."""
    cutoff = (today - timedelta(days=RAW_KEEP_DAYS)).isoformat()
    pruned = 0
    for path in RAW_DIR.glob("*_20??-??-??*") if RAW_DIR.exists() else []:
        m = re.search(r"_(\d{4}-\d{2}-\d{2})", path.name)
        if m and m.group(1) < cutoff:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            pruned += 1
    return pruned


def _run_eval() -> dict[str, Any]:
    """The free retrieval eval (CLAUDE.md: run the eval after every sync). No API calls."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_eval.py", "-m", "not llm", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    failed = [l.split(" - ")[0].replace("FAILED ", "") for l in lines if l.startswith("FAILED")]
    return {"ok": proc.returncode == 0, "summary": lines[-1] if lines else "", "failed": failed}


def sync(source_id: str | None = None, reindex: bool = False, run_eval: bool = False) -> dict[str, Any]:
    """Run one sync. Returns the log record that is also appended to data/sync-log.jsonl."""
    record: dict[str, Any] = {"started": _now(), "sources": {}}
    if reindex:
        store.reset()
    manifest = _load_manifest()
    force = reindex or store.stats()["count"] == 0
    stored = None if force else store.all_chunks()
    fetchers = _Fetchers()

    def previous(src_id: str) -> list[dict[str, Any]]:
        keys = set(manifest["sources"].get(src_id, {}).get("section_keys", []))
        return [c for c in (stored or []) if c["section_key"] in keys]

    built: dict[str, tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]] = {}
    for src in load_sources():
        if source_id and src["id"] != source_id:
            built[src["id"]] = (src, previous(src["id"]), None)    # kept as is; used only for tagging
            continue
        try:
            chunks, details = fetchers.build(src)
            built[src["id"]] = (src, chunks, details)
        except Exception as exc:   # keep the source's previous chunks and report the failure
            log.error("%s: sync failed, keeping previous chunks: %s", src["id"], exc)
            built[src["id"]] = (src, previous(src["id"]), None)
            record["sources"][src["id"]] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    tagged = court_orders.tag([c for _, chunks, _ in built.values() for c in chunks])
    record["court_order_tags"] = {order: len(ids) for order, ids in tagged.items()}

    for src_id, (src, chunks, details) in built.items():
        if details is None:
            continue
        entry = manifest["sources"].get(src_id, {})
        old = {} if force else (entry.get("section_hashes") or dict.fromkeys(entry.get("section_keys", [])))
        diff = _apply(src_id, chunks, old)
        changed = any(diff.values())
        manifest["sources"][src_id] = {
            "type": src["type"], **details,
            "section_hashes": _section_hashes(chunks),
            "section_keys": sorted({c["section_key"] for c in chunks}),
            "chunk_count": len(chunks),
        }
        record["sources"][src_id] = {"status": "updated" if changed else "unchanged",
                                     **{k: v for k, v in diff.items() if v}}

    errors = [s for s, r in record["sources"].items() if r["status"] == "error"]
    manifest["embed_model"] = embed.model_name()
    if not source_id and not errors:
        manifest["last_successful_sync"] = _now()
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    record.update(finished=_now(), ok=not errors, raw_pruned=_prune_raw(fetchers.today),
                  stats={k: v for k, v in store.stats().items() if k != "collection"})
    if run_eval:
        record["eval"] = _run_eval()
    with SYNC_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", help="only sync this source id")
    parser.add_argument("--reindex", action="store_true", help="drop the index and rebuild")
    parser.add_argument("--eval", action="store_true", help="run the free retrieval eval afterwards")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        record = sync(args.source, args.reindex, args.eval)
    except store.EmbeddingModelMismatch as exc:
        raise SystemExit(f"{exc}\nRe-run with --reindex.") from exc
    print(json.dumps(record, indent=2))
    if not record["ok"] or not record.get("eval", {}).get("ok", True):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

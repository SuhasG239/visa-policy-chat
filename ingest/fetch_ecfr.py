"""Fetch regulation XML from the eCFR Versioner API.

Endpoints (https://www.ecfr.gov/developers/documentation/api/v1):
  /api/versioner/v1/titles.json                      title dates (up_to_date_as_of, latest_amended_on)
  /api/versioner/v1/full/{date}/title-{t}.xml        XML for a part/section
  /api/versioner/v1/versions/title-{t}.json          per-section amendment dates

Raw XML is cached in data/raw/ as <source id>_<date>.xml, where <date> is the
title's up_to_date_as_of date, so nothing is re-downloaded until eCFR updates.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest.config import RAW_DIR, PoliteClient, load_sources

log = logging.getLogger(__name__)

API = "https://www.ecfr.gov/api/versioner/v1"


@dataclass
class EcfrFetch:
    """Result of fetching one eCFR source."""

    source: dict[str, Any]
    raw_path: Path
    date: str                       # eCFR point-in-time date the XML reflects
    fetched_at: str                 # when we downloaded it (UTC ISO)
    amended: dict[str, str]         # section number -> latest amendment date


def title_info(client: PoliteClient, title: str) -> dict[str, Any]:
    """titles.json entry for one title."""
    titles = client.get(f"{API}/titles.json").json()["titles"]
    return next(t for t in titles if str(t["number"]) == str(title))


def amendment_dates(client: PoliteClient, src: dict[str, Any], date: str) -> dict[str, str]:
    """Latest amendment date per section in the source, as of `date`."""
    params: dict[str, Any] = {"issue_date[lte]": date, "part": src["part"]}
    if src.get("section"):
        params["section"] = src["section"]
    latest: dict[str, str] = {}
    page = 1
    while True:
        data = client.get(f"{API}/versions/title-{src['title']}.json", {**params, "page": page}).json()
        versions = data["content_versions"]
        for v in versions:
            if v["type"] == "section" and v["amendment_date"] > latest.get(v["identifier"], ""):
                latest[v["identifier"]] = v["amendment_date"]
        if len(versions) < 1000:
            return latest
        page += 1


def fetch(src: dict[str, Any], client: PoliteClient, date: str) -> EcfrFetch:
    """Download (or reuse cached) XML and metadata for one source at `date`."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{src['id']}_{date}.xml"
    meta_path = raw_path.with_suffix(".meta.json")
    if raw_path.exists() and meta_path.exists():
        log.info("%s: using cached %s", src["id"], raw_path.name)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return EcfrFetch(src, raw_path, date, meta["fetched_at"], meta["amended"])

    params = {"part": src["part"]}
    if src.get("section"):
        params["section"] = src["section"]
    resp = client.get(f"{API}/full/{date}/title-{src['title']}.xml", params)
    raw_path.write_bytes(resp.content)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    amended = amendment_dates(client, src, date)
    meta_path.write_text(
        json.dumps({"date": date, "fetched_at": fetched_at, "amended": amended}, indent=2),
        encoding="utf-8",
    )
    log.info("%s: downloaded %d bytes (eCFR date %s)", src["id"], len(resp.content), date)
    return EcfrFetch(src, raw_path, date, fetched_at, amended)


def fetch_all(source_id: str | None = None) -> list[EcfrFetch]:
    """Fetch every eCFR source in sources.yaml (or just `source_id`)."""
    client = PoliteClient()
    results = []
    dates: dict[str, str] = {}
    for src in load_sources("ecfr"):
        if source_id and src["id"] != source_id:
            continue
        if src["title"] not in dates:
            dates[src["title"]] = title_info(client, src["title"])["up_to_date_as_of"]
        results.append(fetch(src, client, dates[src["title"]]))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for r in fetch_all():
        print(f"{r.source['id']}: {r.raw_path.name} amended={r.amended}")

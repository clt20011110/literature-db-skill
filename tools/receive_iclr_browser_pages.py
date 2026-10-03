#!/usr/bin/env python3
"""Append JSONL page envelopes received from the visible ICLR Browser UI.

The input is deliberately a narrow public-evidence contract.  It accepts only
page metadata and visible card records; it does not accept cookies, headers,
tokens, or arbitrary paths.  The receiver is used through a local terminal
process so Browser evidence never leaves the machine.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("page envelope must be an object")
    required = {"schema_version", "venue_id", "year", "page", "source_url", "observed_at", "records"}
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"missing fields: {','.join(missing)}")
    if value["schema_version"] != "iclr-visible-openreview-page-v1":
        raise ValueError("unexpected schema_version")
    if value["venue_id"] != "iclr" or int(value["year"]) != 2026:
        raise ValueError("only ICLR 2026 is accepted")
    if not isinstance(value["page"], int) or value["page"] < 1:
        raise ValueError("page must be a positive integer")
    source_url = str(value["source_url"])
    if not source_url.startswith("https://openreview.net/group?id=ICLR.cc/2026/Conference#tab-accept-poster"):
        raise ValueError("source_url is outside the accepted official page")
    if not isinstance(value["records"], list):
        raise ValueError("records must be a list")
    for record in value["records"]:
        if not isinstance(record, dict):
            raise ValueError("record must be an object")
        for key in ("source_native_id", "native_id", "title", "authors", "landing_url", "pdf_url", "published_text", "track"):
            if key not in record:
                raise ValueError(f"record missing {key}")
        if not isinstance(record["authors"], list) or not all(isinstance(x, str) for x in record["authors"]):
            raise ValueError("record authors must be a string list")
        if not str(record["landing_url"]).startswith("https://openreview.net/forum?id="):
            raise ValueError("record landing_url is outside official OpenReview")
        if record["pdf_url"] is not None and not str(record["pdf_url"]).startswith("https://openreview.net/attachment?id="):
            raise ValueError("record pdf_url is outside official OpenReview")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    accepted_pages: set[int] = set()
    if output.is_file():
        with output.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    accepted_pages.add(int(json.loads(line)["page"]))
    for line in os.sys.stdin:
        if not line.strip():
            continue
        try:
            value = validate(json.loads(line))
            page = value["page"]
            if page in accepted_pages:
                raise ValueError(f"duplicate page {page}")
            value = dict(value)
            value["received_at"] = now()
            value["record_count"] = len(value["records"])
            with output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            accepted_pages.add(page)
            print(json.dumps({"status": "stored", "page": page, "records": len(value["records"])}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"status": "rejected", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), flush=True)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

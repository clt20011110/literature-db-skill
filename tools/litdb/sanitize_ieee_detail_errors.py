#!/usr/bin/env python3
"""Create a compact, secret-safe error evidence JSONL for IEEE detail runs.

The Browser client may place a very large data:text crash shell in an error
message.  Keep the original error artifact for audit, but give finalizers and
security scans a bounded representation containing only the identity, the
canonical official URL, an error class, and timestamps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def classify(error: object) -> str:
    text = str(error or "")
    upper = text.upper()
    if "DATA:TEXT/HTML" in upper or "PAGE CRASHED" in upper:
        if "ERR_CONNECTION_CLOSED" in upper:
            return "ERR_CONNECTION_CLOSED_DATA_TEXT_SHELL"
        return "BROWSER_CRASH_DATA_TEXT_SHELL"
    if "ERR_BLOCKED_BY_CLIENT" in upper:
        return "ERR_BLOCKED_BY_CLIENT"
    if "ERR_CONNECTION_CLOSED" in upper:
        return "ERR_CONNECTION_CLOSED"
    if "CAPTCHA" in upper or "HUMAN" in upper:
        return "ACCESS_OR_CAPTCHA_BLOCK"
    return "BROWSER_EXTRACTION_ERROR"


def canonical_url(value: object, identity: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname != "ieeexplore.ieee.org":
        return None
    if parsed.path.rstrip("/") != f"/document/{identity}":
        return None
    return f"https://ieeexplore.ieee.org/document/{identity}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = []
    for line_no, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        identity = str(raw.get("source_native_id") or raw.get("source_item_id") or "").strip()
        if not identity:
            raise SystemExit(f"missing source identity at line {line_no}")
        url = canonical_url(raw.get("url"), identity)
        if not url:
            raise SystemExit(f"non-canonical or non-official URL at line {line_no}")
        error_text = str(raw.get("error") or "")
        rows.append(
            {
                "schema_version": "ieee-detail-error-evidence-v1",
                "venue_id": raw.get("venue_id"),
                "source_native_id": identity,
                "url": url,
                "error_class": classify(error_text),
                "hard_stop": bool(raw.get("hard_stop")),
                "observed_at": raw.get("observed_at"),
                "raw_error_sha256": hashlib.sha256(error_text.encode("utf-8")).hexdigest(),
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({"status": "PASS", "rows": len(rows), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

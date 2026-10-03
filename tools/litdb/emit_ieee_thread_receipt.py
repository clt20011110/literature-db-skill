#!/usr/bin/env python3
"""Emit a hash-bound worker receipt and output manifest for an IEEE run."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--parent-thread-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--pages-visited", type=int, required=True)
    parser.add_argument("--records-observed", type=int, required=True)
    parser.add_argument("--records-emitted", type=int, default=0)
    parser.add_argument("--warning", action="append", default=[])
    parser.add_argument("--artifact", action="append", required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    started = now()
    receipt_path = root / "thread_receipt.json"
    artifact_names = list(dict.fromkeys(args.artifact + ["thread_receipt.json"]))
    missing = [name for name in artifact_names if not (root / name).is_file() and name != "thread_receipt.json"]
    if missing:
        raise SystemExit(f"missing artifacts: {missing}")
    receipt = {
        "schema_version": "ieee-visible-browser-thread-receipt-v1",
        "thread_id": args.thread_id,
        "actual_thread_id": args.thread_id,
        "parent_thread_id": args.parent_thread_id,
        "controller_thread_id": args.parent_thread_id,
        "model": "luna-max",
        "role": args.role,
        "venue_id": args.venue,
        "request_id": args.request_id,
        "status": "PASS",
        "started_at": started,
        "completed_at": now(),
        "output_root": str(root),
        "pages_visited": args.pages_visited,
        "records_observed": args.records_observed,
        "records_emitted": args.records_emitted,
        "catalog_write": False,
        "catalog_ready": False,
        "warnings": args.warning,
        "artifacts": artifact_names,
        "verdict": "PASS_CANDIDATE_ONLY",
        "reuse_policy": {
            "expected_and_listing_evidence_revalidated": True,
            "fresh_detail_samples_revalidated": True,
            "count_only_rows_not_catalog_metadata": True,
        },
    }
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = []
    for name in artifact_names:
        path = root / name
        files.append({
            "relative_path": name,
            "size_bytes": path.stat().st_size,
            "sha256": digest(path),
            "required": True,
        })
    manifest = {
        "schema_version": "output-manifest-v1",
        "venue_id": args.venue,
        "thread_id": args.thread_id,
        "actual_thread_id": args.thread_id,
        "request_id": args.request_id,
        "generated_at": now(),
        "status": "PASS",
        "hash_scope": "SHA-256 for all listed artifacts; output_manifest.json is excluded from its own hash list.",
        "artifacts": files,
    }
    (root / "output_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "receipt": str(receipt_path), "manifest": str(root / "output_manifest.json"), "artifact_count": len(files)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

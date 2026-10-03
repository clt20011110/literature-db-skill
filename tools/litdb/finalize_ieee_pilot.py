#!/usr/bin/env python3
"""Finalize an IEEE visible-Browser pilot from freshly crawled sample details."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON value is not an object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        raise FileNotFoundError(path)
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank JSONL line: {path}:{line_no}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row is not an object: {path}:{line_no}")
        rows.append(value)
    return rows


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def signature(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\u00a0", " ")
    return " ".join("".join(character.casefold() if character.isalnum() else " " for character in text).split())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--parent-thread-id", required=True)
    parser.add_argument("--crawler-output", type=Path, required=True)
    parser.add_argument("--crawler-exclusions", type=Path, required=True)
    parser.add_argument("--crawler-summary", type=Path, required=True)
    parser.add_argument("--crawler-checkpoint", type=Path, required=True)
    parser.add_argument("--pilot-selection", type=Path, required=True)
    args = parser.parse_args()

    root = args.output_root.resolve()
    manifest_path = root / "pilot_manifest.jsonl"
    manifest = load_jsonl(manifest_path)
    if not manifest:
        raise SystemExit("pilot manifest is empty")
    manifest_by_id = {str(row.get("source_item_id") or ""): row for row in manifest}
    if len(manifest_by_id) != len(manifest):
        raise SystemExit("pilot manifest contains duplicate source identities")
    if any(row.get("venue_id") != args.venue for row in manifest):
        raise SystemExit("pilot manifest venue mismatch")

    selection = load_json(args.pilot_selection.resolve())
    selected_ids = [str(value) for value in selection.get("source_item_ids", [])]
    if not selected_ids or len(set(selected_ids)) != len(selected_ids):
        raise SystemExit("pilot selection is empty or contains duplicates")
    if any(source_id not in manifest_by_id for source_id in selected_ids):
        raise SystemExit("pilot selection contains an identity absent from the manifest")

    exclusion_path = args.crawler_exclusions.resolve()
    exclusions = load_jsonl(exclusion_path) if exclusion_path.is_file() else []
    if exclusions:
        excluded_ids = [str(row.get("source_native_id") or "") for row in exclusions]
        raise SystemExit(f"pilot sample selected non-research details; select replacements: {excluded_ids}")
    fresh_rows = load_jsonl(args.crawler_output.resolve())
    fresh_by_id = {str(row.get("source_native_id") or ""): row for row in fresh_rows}
    if set(fresh_by_id) != set(selected_ids):
        raise SystemExit(
            "fresh pilot detail identity set mismatch: "
            + json.dumps(
                {
                    "missing": sorted(set(selected_ids) - set(fresh_by_id)),
                    "extra": sorted(set(fresh_by_id) - set(selected_ids)),
                },
                sort_keys=True,
            )
        )

    summary = load_json(args.crawler_summary.resolve())
    checkpoint = load_json(args.crawler_checkpoint.resolve())
    if summary.get("status") != "PASS" or checkpoint.get("status") != "PASS":
        raise SystemExit("fresh detail crawler did not finish with PASS")

    sample_metadata: list[dict[str, Any]] = []
    browser_evidence: list[dict[str, Any]] = []
    title_mismatches: list[str] = []
    author_surface_changes: list[str] = []
    for source_id in selected_ids:
        expected = manifest_by_id[source_id]
        fresh = fresh_by_id[source_id]
        title_match = signature(expected.get("title")) == signature(fresh.get("title"))
        if not title_match:
            title_mismatches.append(source_id)
        expected_authors = [signature(value) for value in expected.get("authors", [])]
        fresh_authors = [signature(value.get("name") if isinstance(value, dict) else value) for value in fresh.get("authors", [])]
        author_match = expected_authors == fresh_authors
        if not author_match:
            author_surface_changes.append(source_id)
        landing_url = str(expected["landing_url"])
        sample_metadata.append(
            {
                "schema_version": "ieee-visible-browser-pilot-sample-v1",
                "venue_id": args.venue,
                "source_item_id": source_id,
                "source_native_id": source_id,
                "landing_url": landing_url,
                "source_url": landing_url,
                "source_page_url": expected.get("source_page_url"),
                # The pilot contract compares against the visible listing title.
                # Preserve the fresh detail title separately and require its
                # normalized surface to agree below.
                "title": expected.get("title"),
                "detail_title": fresh.get("title"),
                "title_match": title_match,
                "authors": fresh.get("authors") or [],
                "listing_authors": expected.get("authors") or [],
                "author_order_match": author_match,
                "document_type": fresh.get("document_type") or "journal-article",
                "doi": fresh.get("doi"),
                "abstract": fresh.get("abstract"),
                "publication_date": fresh.get("publication_date"),
                "year": fresh.get("year"),
                "baseline_year": expected.get("year"),
                "pdf_url": fresh.get("pdf_url"),
                "pdf_location": fresh.get("pdf_url"),
                "pdf_discovery_status": fresh.get("pdf_discovery_status"),
                "observed_at": fresh.get("observed_at"),
                "field_provenance": fresh.get("field_provenance"),
                "missing_fields": fresh.get("missing_fields", {}),
                "baseline_enumeration_kind": expected.get("baseline_enumeration_kind"),
                "fresh_detail_revalidated": True,
            }
        )
        browser_evidence.append(
            {
                "thread_id": args.thread_id,
                "actual_thread_id": args.thread_id,
                "parent_thread_id": args.parent_thread_id,
                "controller_thread_id": args.parent_thread_id,
                "venue_id": args.venue,
                "request_id": args.request_id,
                "evidence_type": "fresh_visible_pilot_article_detail",
                "page_kind": "ieee_article_detail",
                "source_item_id": source_id,
                "url": landing_url,
                "observed_url": fresh.get("source_url") or fresh.get("landing_url"),
                "title_match": title_match,
                "author_order_match": author_match,
                "doi": fresh.get("doi"),
                "abstract_status": "present" if fresh.get("abstract") else "checked_missing",
                "pdf_discovery_status": fresh.get("pdf_discovery_status"),
                "baseline_enumeration_kind": expected.get("baseline_enumeration_kind"),
                "navigation_status": "loaded_visible_public_page",
                "auth_status": "not_required_for_visible_metadata",
                "observed_at": fresh.get("observed_at"),
                "evidence_source": "fresh_visible_codex_in_app_browser",
            }
        )

    if title_mismatches:
        raise SystemExit(f"listing/detail title mismatches require review: {title_mismatches}")

    write_jsonl(root / "pilot_sample_metadata.jsonl", sample_metadata)
    write_jsonl(root / "browser_evidence.jsonl", browser_evidence)
    write_jsonl(root / "errors.jsonl", [])
    year_counts = Counter(int(row["year"]) for row in manifest)
    report = {
        "schema_version": "ieee-visible-browser-pilot-report-v1",
        "venue_id": args.venue,
        "request_id": args.request_id,
        "thread_id": args.thread_id,
        "actual_thread_id": args.thread_id,
        "status": "PASS",
        "year_from": min(year_counts),
        "year_through": max(year_counts),
        "yearly_counts": {str(year): year_counts[year] for year in sorted(year_counts)},
        "totals": {
            "eligible": len(manifest),
            "manifest_rows": len(manifest),
            "fresh_detail_samples": len(sample_metadata),
            "records_emitted": 0,
        },
        "pilot_selection": {
            "sample_count": len(sample_metadata),
            "years_represented": selection.get("years_represented"),
            "early_access_count": selection.get("early_access_count"),
        },
        "fresh_detail_validation": {
            "status": "PASS",
            "title_mismatch_count": len(title_mismatches),
            "author_surface_change_count": len(author_surface_changes),
            "author_surface_change_ids": author_surface_changes,
            "crawler_summary_sha256": file_sha256(args.crawler_summary.resolve()),
            "crawler_checkpoint_sha256": file_sha256(args.crawler_checkpoint.resolve()),
        },
        "reuse_policy": {
            "discovery_identity_manifest_reused": True,
            "listing_metadata_reused": True,
            "sample_detail_pages_freshly_revalidated": True,
            "formal_schema_revalidated": True,
            "count_only_rows_not_catalog_metadata": True,
        },
        "unresolved_anomalies": [],
        "unresolved": [],
        "blockers": [],
        "catalog_write": False,
        "catalog_ready": False,
        "records_emitted": 0,
        "completed_at": utc_now(),
    }
    write_json(root / "pilot_report.json", report)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from litdb.pilot import verify_pilot_output  # pylint: disable=import-outside-toplevel

    verification = verify_pilot_output(root, args.venue, {"ieeexplore.ieee.org"})
    if verification.get("status") != "PASS":
        raise SystemExit(json.dumps(verification, ensure_ascii=False))
    write_json(root / "pilot_finalization_verification.json", verification)
    print(
        json.dumps(
            {
                "status": "PASS",
                "venue_id": args.venue,
                "manifest_rows": len(manifest),
                "fresh_samples": len(sample_metadata),
                "author_surface_changes": len(author_surface_changes),
                "catalog_ready": False,
                "output_root": str(root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

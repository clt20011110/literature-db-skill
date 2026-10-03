#!/usr/bin/env python3
"""Finalize an IEEE replay and compare it with the accepted Browser pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from finalize_ieee_pilot import (
    file_sha256,
    load_json,
    load_jsonl,
    signature,
    utc_now,
    write_json,
    write_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--parent-thread-id", required=True)
    parser.add_argument("--accepted-pilot-manifest", type=Path, required=True)
    parser.add_argument("--accepted-pilot-samples", type=Path, required=True)
    parser.add_argument("--crawler-output", type=Path, required=True)
    parser.add_argument("--crawler-exclusions", type=Path, required=True)
    parser.add_argument("--crawler-summary", type=Path, required=True)
    parser.add_argument("--crawler-checkpoint", type=Path, required=True)
    parser.add_argument("--replay-selection", type=Path, required=True)
    args = parser.parse_args()

    root = args.output_root.resolve()
    replay_manifest_path = root / "replay_manifest.jsonl"
    replay_manifest = load_jsonl(replay_manifest_path)
    pilot_manifest_path = args.accepted_pilot_manifest.resolve()
    pilot_samples_path = args.accepted_pilot_samples.resolve()
    pilot_manifest = load_jsonl(pilot_manifest_path)
    pilot_samples = load_jsonl(pilot_samples_path)
    replay_urls = {str(row.get("landing_url") or "") for row in replay_manifest}
    pilot_urls = {str(row.get("landing_url") or "") for row in pilot_manifest}
    if replay_urls != pilot_urls or len(replay_urls) != len(replay_manifest) or len(pilot_urls) != len(pilot_manifest):
        raise SystemExit("replay manifest identity set differs from the accepted pilot")
    if any(row.get("venue_id") != args.venue for row in replay_manifest + pilot_manifest + pilot_samples):
        raise SystemExit("pilot/replay venue mismatch")

    selection = load_json(args.replay_selection.resolve())
    selected_ids = [str(value) for value in selection.get("source_item_ids", [])]
    if not selected_ids or len(set(selected_ids)) != len(selected_ids):
        raise SystemExit("replay selection is empty or contains duplicates")
    pilot_by_id = {str(row.get("source_item_id") or row.get("source_native_id") or ""): row for row in pilot_samples}
    if set(selected_ids) != set(pilot_by_id):
        raise SystemExit("replay selection identity set differs from accepted pilot samples")

    exclusion_path = args.crawler_exclusions.resolve()
    exclusions = load_jsonl(exclusion_path) if exclusion_path.is_file() else []
    if exclusions:
        raise SystemExit(
            "replay sample changed to an excluded class: "
            + json.dumps([row.get("source_native_id") for row in exclusions])
        )
    fresh_rows = load_jsonl(args.crawler_output.resolve())
    fresh_by_id = {str(row.get("source_native_id") or ""): row for row in fresh_rows}
    if set(fresh_by_id) != set(selected_ids):
        raise SystemExit(
            "fresh replay detail identity set mismatch: "
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
        raise SystemExit("fresh replay detail crawler did not finish with PASS")

    replay_samples: list[dict[str, Any]] = []
    browser_evidence: list[dict[str, Any]] = []
    detail_title_mismatches: list[str] = []
    for source_id in selected_ids:
        accepted = pilot_by_id[source_id]
        fresh = fresh_by_id[source_id]
        title_match = signature(accepted.get("title")) == signature(fresh.get("title"))
        if not title_match:
            detail_title_mismatches.append(source_id)
        landing_url = str(accepted["landing_url"])
        replay_samples.append(
            {
                "schema_version": "ieee-visible-browser-replay-sample-v1",
                "venue_id": args.venue,
                "source_item_id": source_id,
                "source_native_id": source_id,
                "landing_url": landing_url,
                "source_url": landing_url,
                "source_page_url": accepted.get("source_page_url"),
                "title": accepted.get("title"),
                "detail_title": fresh.get("title"),
                "title_match": title_match,
                "authors": fresh.get("authors") or [],
                "document_type": fresh.get("document_type") or "journal-article",
                "doi": fresh.get("doi"),
                "abstract": fresh.get("abstract"),
                "publication_date": fresh.get("publication_date"),
                "year": fresh.get("year"),
                "baseline_year": accepted.get("baseline_year"),
                "pdf_url": fresh.get("pdf_url"),
                "pdf_location": fresh.get("pdf_url"),
                "pdf_discovery_status": fresh.get("pdf_discovery_status"),
                "observed_at": fresh.get("observed_at"),
                "field_provenance": fresh.get("field_provenance"),
                "missing_fields": fresh.get("missing_fields", {}),
                "baseline_enumeration_kind": accepted.get("baseline_enumeration_kind"),
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
                "evidence_type": "fresh_visible_replay_article_detail",
                "page_kind": "ieee_article_detail",
                "source_item_id": source_id,
                "url": landing_url,
                "observed_url": fresh.get("source_url") or fresh.get("landing_url"),
                "title_match": title_match,
                "doi": fresh.get("doi"),
                "abstract_status": "present" if fresh.get("abstract") else "checked_missing",
                "pdf_discovery_status": fresh.get("pdf_discovery_status"),
                "navigation_status": "loaded_visible_public_page",
                "auth_status": "not_required_for_visible_metadata",
                "observed_at": fresh.get("observed_at"),
                "evidence_source": "fresh_visible_codex_in_app_browser",
            }
        )
    if detail_title_mismatches:
        raise SystemExit(f"accepted/detail title mismatch in replay: {detail_title_mismatches}")

    write_jsonl(root / "replay_sample_metadata.jsonl", replay_samples)
    write_jsonl(root / "browser_evidence.jsonl", browser_evidence)
    write_jsonl(root / "errors.jsonl", [])
    report: dict[str, Any] = {
        "schema_version": "ieee-visible-browser-replay-report-v1",
        "venue_id": args.venue,
        "request_id": args.request_id,
        "thread_id": args.thread_id,
        "actual_thread_id": args.thread_id,
        "status": "PASS",
        "pilot_manifest_sha256": file_sha256(pilot_manifest_path),
        "replay_manifest_sha256": file_sha256(replay_manifest_path),
        "pilot_samples_sha256": file_sha256(pilot_samples_path),
        "replay_samples_sha256": file_sha256(root / "replay_sample_metadata.jsonl"),
        "fresh_detail_samples": len(replay_samples),
        "fresh_detail_title_mismatch_count": len(detail_title_mismatches),
        "reuse_policy": {
            "accepted_pilot_identity_manifest_reused": True,
            "fresh_replay_detail_pages_loaded": True,
            "field_agreement_recomputed": True,
            "count_only_rows_not_catalog_metadata": True,
        },
        "unresolved_anomalies": [],
        "catalog_write": False,
        "catalog_ready": False,
        "records_emitted": 0,
        "completed_at": utc_now(),
    }
    write_json(root / "replay_report.json", report)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from litdb.replay import verify_replay_output  # pylint: disable=import-outside-toplevel

    verification = verify_replay_output(
        root,
        args.venue,
        pilot_manifest_path,
        pilot_samples_path,
        {"ieeexplore.ieee.org"},
    )
    if verification.get("status") != "PASS":
        write_json(root / "replay_finalization_verification.json", verification)
        raise SystemExit(json.dumps(verification, ensure_ascii=False))
    report.update(
        {
            "set_agreement": verification["set_agreement"],
            "field_agreement": verification["field_agreement"],
            "overall": verification["overall"],
            "per_year": verification["per_year"],
            "sample_metadata": {
                "overall": {"agreement": verification["field_agreement"]},
                "field_comparison": verification["field_comparison"],
            },
        }
    )
    write_json(root / "replay_report.json", report)
    verification = verify_replay_output(
        root,
        args.venue,
        pilot_manifest_path,
        pilot_samples_path,
        {"ieeexplore.ieee.org"},
    )
    if verification.get("status") != "PASS":
        raise SystemExit(json.dumps(verification, ensure_ascii=False))
    write_json(root / "replay_finalization_verification.json", verification)
    print(
        json.dumps(
            {
                "status": "PASS",
                "venue_id": args.venue,
                "manifest_rows": len(replay_manifest),
                "fresh_samples": len(replay_samples),
                "set_agreement": verification["set_agreement"],
                "field_agreement": verification["field_agreement"],
                "catalog_ready": False,
                "output_root": str(root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

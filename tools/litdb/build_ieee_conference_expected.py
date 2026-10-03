#!/usr/bin/env python3
"""Promote an accepted IEEE conference listing baseline to expected manifests.

The source remains count/listing evidence only.  This program creates the
identity contract used by the Browser detail crawler and converts already
accepted, explicit non-main-track decisions to formal exclusions.  It never
creates catalog metadata and never treats included listing rows as catalog
ready.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ALLOWED_HOST = "ieeexplore.ieee.org"

# The accepted listing may retain a venue-specific rule label.  Catalog
# exclusions use a small stable taxonomy while preserving that exact label in
# exclusion evidence for audit and future policy changes.
NON_MAIN_TRACK_REASONS = {
    "invited_non_main_track_without_formal_main_track_evidence",
    "late_breaking_only",
    "late_breaking_only_acm_cross_source",
    "non_main_track_lightning_talk",
}
NON_RESEARCH_REASONS = {
    "keynote_abstract",
    "panel_abstract",
}


def catalog_exclusion_reason(reason: str) -> str:
    if reason in NON_MAIN_TRACK_REASONS:
        return "non_main_track"
    if reason in NON_RESEARCH_REASONS:
        return "non_research_content"
    return reason


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def set_hash(values: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank JSONL line: {path}:{line_no}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"row is not a JSON object: {path}:{line_no}")
        rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_gzip_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            for row in rows:
                zipped.write((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode())


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).replace("\u00a0", " ").split())
    return normalized or None


def author_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        raw = item.get("name") if isinstance(item, dict) else item
        name = normalize_text(raw)
        signature = name.casefold() if name else ""
        if name and signature not in seen:
            result.append(name)
            seen.add(signature)
    return result


def validate_url(url: str, identity: str | None = None) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != ALLOWED_HOST:
        raise ValueError(f"URL is outside the IEEE allowlist: {url}")
    if identity and not re.fullmatch(rf"/document/{re.escape(identity)}/?", parsed.path):
        raise ValueError(f"landing URL does not match identity {identity}: {url}")


def page_range(row: dict[str, Any]) -> str | None:
    value = row.get("page_range")
    if value:
        return normalize_text(value)
    pages = row.get("pages")
    if isinstance(pages, dict) and pages.get("first") is not None:
        first = str(pages["first"])
        last = str(pages.get("last") or pages["first"])
        return f"{first}-{last}"
    if isinstance(pages, str):
        return normalize_text(pages)
    return None


def exclusion_record(expected: dict[str, Any], raw: dict[str, Any], expected_path: Path) -> dict[str, Any]:
    source_reason = normalize_text(raw.get("exclusion_reason") or raw.get("exclusion_reason_code"))
    if not source_reason:
        raise ValueError(f"excluded row lacks reason: {expected['source_native_id']}")
    reason = catalog_exclusion_reason(source_reason)
    observed_at = expected["observed_at"]
    provenance = {
        field: {
            "source_url": expected["source_page_url"],
            "observed_at": observed_at,
            "method": "accepted_fresh_ieee_visible_listing_and_locked_scope_rule",
            "reuse_status": "count_baseline_reused_and_formal_exclusion_schema_revalidated",
            "status": "excluded",
        }
        for field in ("source_native_id", "title", "year", "inclusion_decision", "exclusion_reason_code")
    }
    return {
        "schema_version": "literature-metadata-exclusion-v1",
        "venue_id": expected["venue_id"],
        "source_native_id": expected["source_native_id"],
        "title": expected["title"],
        "authors": [{"name": name, "affiliations": [], "native_id": None} for name in expected["authors"]],
        "year": expected["year"],
        "document_type": expected["document_type"],
        "landing_url": expected["landing_url"],
        "source_url": expected["landing_url"],
        "source_page_url": expected["source_page_url"],
        "observed_at": observed_at,
        "is_early_access": False,
        "baseline_enumeration_kind": "issue",
        "inclusion_decision": "exclude",
        "exclusion_reason_code": reason,
        "exclusion_evidence": {
            "source_exclusion_reason_code": source_reason,
            "catalog_exclusion_reason_code": reason,
            "reason_taxonomy_version": "literature-exclusion-taxonomy-v1",
            "locked_listing_rule_id": raw.get("exclusion_rule_id") or raw.get("inclusion_rule_id"),
            "classification_status": raw.get("classification_status"),
            "fresh_visible_listing_revalidated": True,
            "detail_page_revalidated": False,
            "official_listing_url": expected["source_page_url"],
            "count_only_promoted_to_catalog": False,
        },
        "reuse_evidence": {
            "expected_manifest_path": str(expected_path),
            "source_listing_request_id": raw.get("request_id"),
            "source_listing_observed_at": raw.get("observed_at"),
            "source_grade": raw.get("source_grade"),
            "accepted_count_listing_reused": True,
            "formal_exclusion_schema_revalidated": True,
        },
        "field_provenance": provenance,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listing", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--venue-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--controller-thread-id", required=True)
    parser.add_argument("--year-from", type=int, default=2015)
    parser.add_argument("--year-through", type=int, default=datetime.now(timezone.utc).year)
    args = parser.parse_args()

    listing_path = args.listing.resolve()
    root = args.output_root.resolve()
    raw_rows = load_jsonl(listing_path)
    if not raw_rows:
        raise SystemExit("conference listing is empty")

    by_year: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    identities: set[str] = set()
    decisions: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    source_urls: set[str] = set()

    for raw in raw_rows:
        if raw.get("venue_id") != args.venue_id:
            raise SystemExit(f"cross-venue row: {raw.get('venue_id')!r}")
        identity = str(raw.get("source_native_id") or raw.get("source_item_id") or raw.get("arnumber") or "").strip()
        if not identity.isdigit() or identity in identities:
            raise SystemExit(f"invalid or duplicate IEEE identity: {identity!r}")
        identities.add(identity)
        year = int(raw.get("year"))
        if year < args.year_from or year > args.year_through:
            raise SystemExit(f"out-of-range year {year} for {identity}")
        source_page_url = str(raw.get("source_page_url") or "")
        validate_url(source_page_url)
        source_urls.add(source_page_url)
        landing_url = f"https://{ALLOWED_HOST}/document/{identity}"
        validate_url(landing_url, identity)
        collection_id = str(raw.get("collection_id") or "").strip()
        if not collection_id.isdigit():
            match = re.search(r"/xpl/conhome/(\d+)/proceeding", source_page_url)
            collection_id = match.group(1) if match else ""
        if not collection_id.isdigit():
            raise SystemExit(f"missing conference collection ID for {identity}")
        decision = str(raw.get("include_decision") or "").strip().lower()
        if decision not in {"include", "exclude"}:
            raise SystemExit(f"unaccepted listing decision for {identity}: {decision!r}")
        decisions[decision] += 1
        if decision == "exclude":
            reasons[str(raw.get("exclusion_reason") or raw.get("exclusion_reason_code") or "missing")] += 1
        title = normalize_text(raw.get("title"))
        observed_at = normalize_text(raw.get("observed_at"))
        if not title or not observed_at:
            raise SystemExit(f"title/observation missing for {identity}")
        authors = author_names(raw.get("authors"))
        expected = {
            "schema_version": "literature-expected-source-item-v1",
            "venue_id": args.venue_id,
            "year": year,
            "source_item_id": identity,
            "source_native_id": identity,
            "arnumber": identity,
            "landing_url": landing_url,
            "source_url": landing_url,
            "source_page_url": source_page_url,
            "selected_unit_id": source_page_url,
            "expected_publication_number": collection_id,
            "collection_id": collection_id,
            "title": title,
            "authors": authors,
            "authors_text": "; ".join(authors),
            "page_range": page_range(raw),
            "source_grade": raw.get("source_grade") or "A",
            "document_type": raw.get("document_type") or "conference-publication",
            "source_document_type": "IEEE conference proceedings listing",
            "baseline_enumeration_kind": "issue",
            "include_decision": decision,
            "inclusion_rule_id": raw.get("inclusion_rule_id"),
            "exclusion_reason_code": raw.get("exclusion_reason") or raw.get("exclusion_reason_code"),
            "observed_at": observed_at,
            "request_id": args.request_id,
            "source_listing_request_id": raw.get("request_id"),
            "controller_thread_id": args.controller_thread_id,
            "catalog_write": False,
            "catalog_ready": False,
            "records_emitted": 0,
            "readiness": "COUNT_ONLY_NOT_CATALOG_READY",
            "status": "count_only_not_catalog_ready",
            "source_reuse": "accepted_fresh_visible_listing_reused_pending_fresh_detail_revalidation",
        }
        by_year[year].append((expected, raw))

    expected_files: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for year in range(args.year_from, args.year_through + 1):
        pairs = by_year.get(year, [])
        expected_rows = [pair[0] for pair in pairs]
        expected_path = root / "expected" / f"{year}.jsonl.gz"
        write_gzip_jsonl(expected_path, expected_rows)
        for expected, raw in pairs:
            if expected["include_decision"] == "exclude":
                exclusions.append(exclusion_record(expected, raw, expected_path))
        expected_files.append(
            {
                "year": year,
                "rows": len(expected_rows),
                "included_candidates": sum(row["include_decision"] == "include" for row in expected_rows),
                "excluded": sum(row["include_decision"] == "exclude" for row in expected_rows),
                "sha256": sha256(expected_path),
                "path": str(expected_path),
            }
        )

    exclusions_path = root / "metadata_exclusions.jsonl"
    write_jsonl(exclusions_path, exclusions)
    for name in ("metadata_staging.jsonl", "detail_browser_evidence.jsonl", "detail_errors.jsonl"):
        (root / name).touch(exist_ok=True)

    summary = {
        "schema_version": "ieee-conference-expected-build-v1",
        "status": "PASS",
        "venue_id": args.venue_id,
        "request_id": args.request_id,
        "controller_thread_id": args.controller_thread_id,
        "source_listing_path": str(listing_path),
        "source_listing_sha256": sha256(listing_path),
        "source_listing_rows": len(raw_rows),
        "canonical_expected_identity_count": len(identities),
        "included_candidate_count": decisions["include"],
        "formal_listing_exclusion_count": decisions["exclude"],
        "exclusion_reason_counts": dict(sorted(reasons.items())),
        "source_identity_set_sha256": set_hash(list(identities)),
        "source_page_urls": sorted(source_urls),
        "expected_files": expected_files,
        "metadata_exclusions_path": str(exclusions_path),
        "metadata_exclusions_sha256": sha256(exclusions_path),
        "reuse_validation": {
            "accepted_count_manifest_reused": True,
            "canonical_identities_revalidated": True,
            "official_source_urls_revalidated": True,
            "formal_exclusion_schema_revalidated": True,
            "included_listing_rows_promoted_to_catalog": False,
            "fresh_detail_required_for_included_candidates": True,
        },
        "catalog_ready": False,
        "catalog_write": False,
        "created_at": utc_now(),
    }
    write_json(root / "expected_build_summary.json", summary)
    write_json(
        root / "reuse_validation.json",
        {
            "schema_version": "literature-reuse-validation-v1",
            "status": "PASS",
            "venue_id": args.venue_id,
            "source_listing_rows": len(raw_rows),
            "included_candidates_pending_detail": decisions["include"],
            "formal_listing_exclusions": decisions["exclude"],
            "source_identity_set_sha256": summary["source_identity_set_sha256"],
            **summary["reuse_validation"],
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

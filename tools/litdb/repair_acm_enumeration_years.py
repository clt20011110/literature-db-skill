#!/usr/bin/env python3
"""Align ACM catalog year with the accepted issue/listing enumeration year.

The official ACM detail page can report the online publication date months before
the issue year.  Preserve that date in publication_date/publication_year while
keeping the catalog ``year`` aligned with the accepted listing identity used by
coverage validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no} is not an object")
            rows.append(value)
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_doi(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "https://dl.acm.org/doi/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text if text.startswith("10.") and "/" in text else None


def enumeration_kind(source_url: str) -> str:
    return "justaccepted" if "/justaccepted" in source_url.lower() else "issue"


def expected_rows(expected_root: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(expected_root.glob("*.jsonl")):
        yield from read_jsonl(path)


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def repair_rows(
    rows: list[dict[str, Any]],
    listing_by_doi: dict[str, dict[str, Any]],
    expected_by_native_id: dict[str, int],
) -> tuple[int, int]:
    changed = 0
    publication_year_differences = 0
    for row in rows:
        doi = normalize_doi(row.get("doi"))
        native_id = str(row.get("source_native_id") or row.get("native_id") or "").strip()
        if not doi or doi not in listing_by_doi:
            raise ValueError(f"output row is absent from accepted ACM listing: {native_id or doi or 'unknown'}")
        listing = listing_by_doi[doi]
        year = int(listing.get("year") or listing.get("publication_year"))
        expected_year = expected_by_native_id.get(native_id)
        if expected_year is None:
            raise ValueError(f"output identity is absent from expected manifests: {native_id}")
        if expected_year != year:
            raise ValueError(f"listing/expected year conflict for {native_id}: {year} != {expected_year}")
        old_year = int(row.get("year")) if row.get("year") not in (None, "") else None
        if old_year != year:
            changed += 1
        if row.get("publication_year") not in (None, "") and int(row["publication_year"]) != year:
            publication_year_differences += 1
        row["year"] = year
        row["year_semantics"] = "official_issue_or_listing_enumeration_year"
        source_url = str(listing.get("source_page_url") or row.get("source_page_url") or "")
        row["source_listing_row"] = {
            **(row.get("source_listing_row") if isinstance(row.get("source_listing_row"), dict) else {}),
            "doi": doi,
            "source_page_url": source_url or None,
            "enumeration_year": year,
            "enumeration_kind": enumeration_kind(source_url),
            "listing_metadata_reused": True,
        }
        provenance = row.setdefault("field_provenance", {})
        provenance["year"] = {
            "source_url": source_url or None,
            "selector": "accepted_acm_toc_listing_year",
            "method": "accepted_acm_toc_listing_metadata_reused_after_detail_navigation",
            "observed_at": row.get("observed_at"),
            "status": "present",
            "reuse_status": "listing_enumeration_year_retained_detail_publication_date_preserved",
        }
        reuse = row.setdefault("source_reuse_policy", {})
        reuse["listing_identity_reused"] = True
        reuse["enumeration_year_revalidated_against_expected_manifest"] = True
        reuse["detail_publication_date_preserved"] = True
        reuse["catalog_write"] = False
    return changed, publication_year_differences


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listing", type=Path, required=True)
    parser.add_argument("--expected-root", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--exclusions", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    listing_rows = read_jsonl(args.listing)
    listing_by_doi: dict[str, dict[str, Any]] = {}
    for row in listing_rows:
        doi = normalize_doi(row.get("doi"))
        if not doi:
            raise ValueError("accepted listing row lacks DOI")
        if doi in listing_by_doi:
            raise ValueError(f"duplicate DOI in accepted listing: {doi}")
        listing_by_doi[doi] = row
    expected_by_native_id: dict[str, int] = {}
    for row in expected_rows(args.expected_root):
        native_id = str(row.get("source_native_id") or "").strip()
        year = int(row.get("year"))
        if not native_id or native_id in expected_by_native_id:
            raise ValueError(f"missing or duplicate expected source_native_id: {native_id}")
        expected_by_native_id[native_id] = year
    if len(listing_by_doi) != len(expected_by_native_id):
        raise ValueError("accepted listing and expected manifest counts differ")

    staging = read_jsonl(args.staging)
    exclusions = read_jsonl(args.exclusions)
    if len(staging) + len(exclusions) != len(expected_by_native_id):
        raise ValueError("staging plus exclusions do not account for every expected identity")
    before = {"staging": sha256(args.staging), "exclusions": sha256(args.exclusions)}
    changed_staging, differing_staging = repair_rows(staging, listing_by_doi, expected_by_native_id)
    changed_exclusions, differing_exclusions = repair_rows(exclusions, listing_by_doi, expected_by_native_id)
    atomic_write_jsonl(args.staging, staging)
    atomic_write_jsonl(args.exclusions, exclusions)
    report = {
        "schema_version": "acm-enumeration-year-repair-v1",
        "status": "PASS",
        "listing_count": len(listing_by_doi),
        "expected_count": len(expected_by_native_id),
        "staging_count": len(staging),
        "exclusion_count": len(exclusions),
        "changed_year_count": changed_staging + changed_exclusions,
        "detail_publication_year_differs_count": differing_staging + differing_exclusions,
        "publication_date_preserved": True,
        "catalog_ready": False,
        "catalog_write": False,
        "before_sha256": before,
        "after_sha256": {"staging": sha256(args.staging), "exclusions": sha256(args.exclusions)},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

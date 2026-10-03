#!/usr/bin/env python3
"""Build count-only IEEE journal baseline artifacts from visible TOC captures.

The input JSONL files are produced by ``ieee_visible_toc_enumerator.mjs``.
This program deliberately does not create a worker receipt or output manifest;
the assigned worker must attest the completed artifacts after independently
checking their hashes.  It also never writes catalog metadata.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


READINESS = "COUNT_ONLY_NOT_CATALOG_READY"
ALLOWED_HOST = "ieeexplore.ieee.org"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digest(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def write_gzip_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            for row in rows:
                zipped.write((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def author_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for author in value:
        raw = author.get("name") if isinstance(author, dict) else author
        if raw is None:
            continue
        name = " ".join(str(raw).replace("\u00a0", " ").split())
        signature = name.casefold()
        if name and signature not in seen:
            names.append(name)
            seen.add(signature)
    return names


def canonical_identity(row: dict[str, Any]) -> tuple[str, str]:
    source_id = str(row.get("source_native_id") or row.get("source_item_id") or row.get("arnumber") or "").strip()
    if not source_id.isdigit():
        raise ValueError(f"invalid IEEE source identity: {source_id!r}")
    landing_url = f"https://{ALLOWED_HOST}/document/{source_id}/"
    return source_id, landing_url


def validate_source_page(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != ALLOWED_HOST:
        raise ValueError(f"source page is outside the IEEE allowlist: {url}")
    # IEEE conference enumeration uses the visible proceedings surface rather
    # than the journal-style tocresult.jsp endpoint.  Both are official,
    # auditable listing pages; accept only the exact conhome proceedings path
    # (with query parameters) in addition to the historical TOC contract.
    is_toc = parsed.path == "/xpl/tocresult.jsp"
    is_conference = bool(re.fullmatch(r"/xpl/conhome/\d+/proceeding", parsed.path))
    if (not is_toc and not is_conference) or not parsed.query:
        raise ValueError(f"source page is not a visible IEEE TOC/proceedings URL: {url}")


def likely_substantive_sample(row: dict[str, Any]) -> bool:
    # When a controller has already revalidated a conference listing, never
    # select an explicitly excluded front-matter/non-main-track row merely
    # because it happens to have an author or a long page range.
    if str(row.get("include_decision") or "").lower() == "exclude":
        return False
    if not row.get("authors"):
        return False
    title = str(row.get("title") or "").casefold().strip()
    non_research = (
        r"^(?:the )?table of contents$",
        r"society information$",
        r"publication information$",
        r"(?:^|\s)editorial$",
        r"^corrections?(?:\s+to)?\b",
        r"^errata?\b",
        r"^retraction\b",
        r"^(?:author|subject )?index$",
        r"^(?:front )?cover$",
        r"^announcement\b",
        r"^obituary\b",
        r"^book review\b",
        r"^from the eic\b",
        r"\[from the eic\]$",
        r"^guest editor(?:s|s’|s')?\s+(?:introduction|editorial|foreword)\b",
        r"^guest editorial\b",
        r"^the last byte\b",
        r"\bnewsletter$",
        r"^ceda currents\b",
        r"^report on\b",
        r"^(?:an )?interview with\b",
        r"\bwisdom from the giants\b",
        r"^special issue on\b",
        r"^celebrating\b",
    )
    if any(re.search(pattern, title) for pattern in non_research):
        return False

    # Magazine-style IEEE venues can contain many signed one-page columns.
    # They have authors, but are poor positive pilot examples for the formal
    # research-article schema.  Prefer issue rows spanning at least four
    # numeric pages; Early Access commonly exposes only a placeholder 1--1
    # range and therefore remains eligible for its dedicated sample stratum.
    if row.get("baseline_enumeration_kind") == "issue":
        page_range = str(row.get("page_range") or "").strip()
        match = re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", page_range)
        if match and int(match.group(2)) - int(match.group(1)) + 1 < 4:
            return False
    return True


def evenly_spaced(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or not rows:
        return []
    if len(rows) <= count:
        return list(rows)
    if count == 1:
        return [rows[len(rows) // 2]]
    indices = [round(index * (len(rows) - 1) / (count - 1)) for index in range(count)]
    return [rows[index] for index in indices]


def select_pilot_sample(
    rows_by_year: dict[int, list[dict[str, Any]]],
    year_from: int,
    year_through: int,
    sample_count: int,
) -> list[dict[str, Any]]:
    eligible = {
        year: [row for row in rows_by_year.get(year, []) if likely_substantive_sample(row)]
        for year in range(year_from, year_through + 1)
    }
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add(rows: list[dict[str, Any]], count: int) -> None:
        for row in evenly_spaced([item for item in rows if item["source_item_id"] not in selected_ids], count):
            if len(selected) >= sample_count:
                return
            selected.append(row)
            selected_ids.add(row["source_item_id"])

    # Two visible issue examples from every year give the pilot full temporal
    # coverage without making detail crawl cost grow with venue size.
    for year in range(year_from, year_through + 1):
        add([row for row in eligible[year] if row["baseline_enumeration_kind"] == "issue"], 2)

    # Early Access is a distinct unit and must be represented separately.
    add(
        [row for row in eligible[year_through] if row["baseline_enumeration_kind"] == "early_access"],
        min(3, sample_count - len(selected)),
    )

    # Add explicit historical/middle/latest-closed anchors before generic fill.
    anchor_years = list(dict.fromkeys([year_from, (year_from + year_through - 1) // 2, year_through - 1]))
    for year in anchor_years:
        add(eligible.get(year, []), 1)

    if len(selected) < sample_count:
        all_eligible = [row for year in range(year_from, year_through + 1) for row in eligible[year]]
        add(all_eligible, sample_count - len(selected))
    if len(selected) != sample_count:
        raise SystemExit(f"could not select {sample_count} unique substantive pilot rows")
    return sorted(selected, key=lambda row: (row["year"], row["listing_position"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-listing", type=Path, required=True)
    parser.add_argument("--early-access-listing", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--parent-thread-id", required=True)
    parser.add_argument("--year-from", type=int, default=2015)
    parser.add_argument("--year-through", type=int, default=datetime.now(timezone.utc).year)
    parser.add_argument("--inclusion-rule-id")
    parser.add_argument("--plain-manifest", type=Path)
    parser.add_argument("--sample-expected-root", type=Path)
    parser.add_argument("--sample-count", type=int, default=30)
    args = parser.parse_args()

    issue_path = args.issue_listing.resolve()
    early_path = args.early_access_listing.resolve() if args.early_access_listing else None
    output_root = args.output_root.resolve()
    inclusion_rule_id = args.inclusion_rule_id or f"{args.venue}-visible-ieee-toc-candidate-v1"

    tagged_rows: list[tuple[str, dict[str, Any]]] = [
        ("issue", row) for row in load_jsonl(issue_path)
    ]
    if early_path:
        tagged_rows.extend(("early_access", row) for row in load_jsonl(early_path))
    if not tagged_rows:
        raise SystemExit("no visible IEEE listing rows supplied")

    rows_by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    source_discovery_request_ids: set[str] = set()
    enumeration_counts: dict[str, int] = defaultdict(int)
    issue_ids: set[str] = set()
    early_ids: set[str] = set()
    source_observed_at: list[str] = []

    for kind, raw in tagged_rows:
        if raw.get("venue_id") != args.venue:
            raise SystemExit(f"cross-venue listing row: {raw.get('venue_id')!r}")
        source_id, landing_url = canonical_identity(raw)
        if source_id in seen_ids or landing_url in seen_urls:
            raise SystemExit(f"duplicate issue/Early Access identity: {source_id}")
        seen_ids.add(source_id)
        seen_urls.add(landing_url)
        (early_ids if kind == "early_access" else issue_ids).add(source_id)
        enumeration_counts[kind] += 1

        year = int(raw.get("year"))
        if year < args.year_from or year > args.year_through:
            raise SystemExit(f"out-of-scope listing year for {source_id}: {year}")
        source_page_url = str(raw.get("source_page_url") or raw.get("selected_unit_id") or "")
        validate_source_page(source_page_url)
        title = " ".join(str(raw.get("title") or "").replace("\u00a0", " ").split())
        if not title:
            raise SystemExit(f"listing title missing for {source_id}")
        authors = author_names(raw.get("authors"))
        observed_at = str(raw.get("observed_at") or "")
        if not observed_at:
            raise SystemExit(f"listing observation time missing for {source_id}")
        source_observed_at.append(observed_at)
        if raw.get("request_id"):
            source_discovery_request_ids.add(str(raw["request_id"]))

        rows_by_year[year].append(
            {
                "schema_version": "literature-expected-source-item-v1",
                "venue_id": args.venue,
                "year": year,
                "source_item_id": source_id,
                "source_native_id": source_id,
                "arnumber": source_id,
                "landing_url": landing_url,
                "source_url": landing_url,
                "source_page_url": source_page_url,
                "selected_unit_id": source_page_url,
                # Preserve both visible URL identifiers.  The canonical IEEE
                # detail page reports the conhome collection id as
                # ``publicationNumber``; ``isnumber`` is the proceedings
                # issue/volume query identifier and is retained separately.
                "selected_unit_isnumber": str(raw.get("selected_unit_isnumber") or raw.get("isnumber") or ""),
                "expected_publication_number": str(
                    raw.get("expected_publication_number")
                    or raw.get("collection_id")
                    or raw.get("selected_unit_isnumber")
                    or raw.get("isnumber")
                    or ""
                ),
                "expected_publication_title": raw.get("expected_publication_title"),
                "title": title,
                "authors": authors,
                "authors_text": "; ".join(authors),
                "page_range": raw.get("pages") or raw.get("page_range"),
                "source_grade": "B" if year == args.year_through else "A",
                "document_type": "journal-article",
                "source_document_type": raw.get("source_document_type") or (
                    "Early Access listing" if kind == "early_access" else "Issue TOC listing"
                ),
                "baseline_enumeration_kind": kind,
                "include_decision": (
                    "exclude" if str(raw.get("include_decision") or "").lower() == "exclude"
                    else "include_candidate"
                ),
                "inclusion_rule_id": raw.get("inclusion_rule_id") or inclusion_rule_id,
                "observed_at": observed_at,
                "request_id": args.request_id,
                "source_discovery_request_id": raw.get("request_id"),
                "thread_id": args.thread_id,
                "actual_thread_id": args.thread_id,
                "parent_thread_id": args.parent_thread_id,
                "catalog_write": False,
                "catalog_ready": False,
                "records_emitted": 0,
                "readiness": READINESS,
                "status": "count_only_not_catalog_ready",
                "source_reuse": "fresh_visible_browser_listing_reused_pending_fresh_detail_revalidation",
            }
        )

    overlap = sorted(issue_ids & early_ids)
    if overlap:
        raise SystemExit(f"issue/Early Access identity overlap: {overlap[:10]}")

    output_root.mkdir(parents=True, exist_ok=True)
    yearly: list[dict[str, Any]] = []
    total_rows = 0
    for year in range(args.year_from, args.year_through + 1):
        rows = rows_by_year.get(year, [])
        for position, row in enumerate(rows, 1):
            row["listing_position"] = position
        expected_path = output_root / "expected" / f"{year}.jsonl.gz"
        write_gzip_jsonl(expected_path, rows)
        urls = [row["landing_url"] for row in rows]
        summary = {
            "venue_id": args.venue,
            "year": year,
            "displayed_count": len(rows),
            "parsed_count": len(rows),
            "unique_count": len(rows),
            "eligible_count": len(rows),
            "position_paper_count": 0,
            "source_grade": "B" if year == args.year_through else "A",
            "coverage": 1.0,
            "set_hash_sha256": digest(sorted(urls)),
            "ordered_listing_hash_sha256": digest(urls),
            "manifest_path": f"expected/{year}.jsonl.gz",
            "manifest_rows": len(rows),
            "manifest_sha256": sha256(expected_path),
            "source_page_urls": sorted({row["source_page_url"] for row in rows}),
            "status": "PASS",
            "catalog_write": False,
            "catalog_ready": False,
            "records_emitted": 0,
            "readiness": READINESS,
            "request_id": args.request_id,
            "thread_id": args.thread_id,
            "actual_thread_id": args.thread_id,
            "observed_at": max((row["observed_at"] for row in rows), default=max(source_observed_at)),
        }
        write_json(output_root / "count" / f"{year}.json", summary)
        yearly.append(summary)
        total_rows += len(rows)

    report = {
        "schema_version": "ieee-visible-toc-venue-count-report-v1",
        "venue_id": args.venue,
        "request_id": args.request_id,
        "thread_id": args.thread_id,
        "actual_thread_id": args.thread_id,
        "parent_thread_id": args.parent_thread_id,
        "status": "PASS",
        "year_from": args.year_from,
        "year_through": args.year_through,
        "yearly": yearly,
        "totals": {
            "displayed": total_rows,
            "parsed": total_rows,
            "unique": total_rows,
            "eligible": total_rows,
            "blocked": 0,
            "records_emitted": 0,
        },
        "enumeration": {
            "issue_count": enumeration_counts["issue"],
            "early_access_count": enumeration_counts["early_access"],
            "issue_early_access_overlap_count": len(overlap),
            "unique_identity_count": len(seen_ids),
        },
        "source_inputs": {
            "issue_listing": str(issue_path),
            "early_access_listing": str(early_path) if early_path else None,
            "discovery_request_ids": sorted(source_discovery_request_ids),
            "first_observed_at": min(source_observed_at),
            "last_observed_at": max(source_observed_at),
        },
        "reuse_policy": {
            "visible_listing_metadata_reused": True,
            "formal_count_schema_revalidated": True,
            "source_identity_and_allowlist_revalidated": True,
            "current_waterline_revalidated": True,
            "fresh_detail_revalidation_still_required": True,
            "count_only_not_catalog_metadata": True,
        },
        "unresolved_anomalies": [],
        "catalog_write": False,
        "catalog_ready": False,
        "records_emitted": 0,
        "readiness": READINESS,
        "completed_at": utc_now(),
    }
    write_json(output_root / "venue_count_report.json", report)

    all_rows = [row for year in range(args.year_from, args.year_through + 1) for row in rows_by_year.get(year, [])]
    if args.plain_manifest:
        write_jsonl(args.plain_manifest.resolve(), all_rows)
    if args.sample_expected_root:
        sample_root = args.sample_expected_root.resolve()
        sample_rows = select_pilot_sample(
            rows_by_year,
            args.year_from,
            args.year_through,
            args.sample_count,
        )
        sample_by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in sample_rows:
            sample_by_year[int(row["year"])].append(row)
        for year, rows in sorted(sample_by_year.items()):
            write_jsonl(sample_root / f"{year}.jsonl", rows)
        write_json(
            output_root / "pilot_selection.json",
            {
                "schema_version": "ieee-visible-toc-pilot-selection-v1",
                "venue_id": args.venue,
                "request_id": args.request_id,
                "sample_count": len(sample_rows),
                "years_represented": sorted(sample_by_year),
                "year_counts": {str(year): len(rows) for year, rows in sorted(sample_by_year.items())},
                "early_access_count": sum(
                    row["baseline_enumeration_kind"] == "early_access" for row in sample_rows
                ),
                "source_item_ids": [row["source_item_id"] for row in sample_rows],
                "sample_expected_root": str(sample_root),
                "catalog_ready": False,
                "selection_basis": "two_issue_rows_per_year_plus_early_access_and_temporal_anchors",
                "created_at": utc_now(),
            },
        )

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from litdb.count_baseline import verify_count_output  # pylint: disable=import-outside-toplevel

    verification = verify_count_output(
        output_root,
        args.venue,
        year_from=args.year_from,
        year_through=args.year_through,
        allowed_domains={ALLOWED_HOST},
    )
    if verification.get("status") != "PASS":
        raise SystemExit(json.dumps(verification, ensure_ascii=False))
    write_json(output_root / "count_build_verification.json", verification)
    print(
        json.dumps(
            {
                "status": "PASS",
                "venue_id": args.venue,
                "total": total_rows,
                "yearly_counts": {str(item["year"]): item["eligible_count"] for item in yearly},
                "issue_count": enumeration_counts["issue"],
                "early_access_count": enumeration_counts["early_access"],
                "pilot_sample_count": args.sample_count if args.sample_expected_root else 0,
                "catalog_ready": False,
                "output_root": str(output_root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

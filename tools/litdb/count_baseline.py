from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .io import load_json

REQUIRED_LISTING_FIELDS = {
    "venue_id", "year", "source_item_id", "landing_url", "title",
    "listing_position", "source_page_url", "source_grade", "document_type",
    "include_decision", "inclusion_rule_id", "observed_at",
}

CURRENT_YEAR_ZERO_WATERLINE_STATUSES = {
    "NO_FORMAL_PMLR_VOLUME_OBSERVED",
    "NO_FORMAL_PROCEEDINGS_OBSERVED",
    "NO_FORMAL_ISSUE_OBSERVED",
}

HISTORICAL_ZERO_INACTIVE_STATUSES = {
    "NOT_YET_ACTIVE",
    "VENUE_NOT_YET_ESTABLISHED",
}


def _digest(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _host_allowed(host: str, allowed_domains: set[str]) -> bool:
    return not allowed_domains or any(
        host == domain or host.endswith(f".{domain}") for domain in allowed_domains
    )


def verify_count_output(
    output_root: Path,
    venue_id: str,
    year_from: int = 2015,
    year_through: int | None = None,
    allowed_domains: set[str] | None = None,
) -> dict[str, Any]:
    if year_through is None:
        year_through = datetime.now(timezone.utc).year
    errors: list[str] = []
    yearly_results: list[dict[str, Any]] = []
    report_path = output_root / "venue_count_report.json"
    if not report_path.is_file():
        return {"status": "FAIL", "errors": ["venue_count_report.json missing"]}
    report = load_json(report_path)
    report_yearly = {item.get("year"): item for item in report.get("yearly", [])}
    expected_years = list(range(year_from, year_through + 1))
    if sorted(report_yearly) != expected_years:
        errors.append("venue report does not contain exactly every expected year")
    allowed_domains = {domain.lower() for domain in (allowed_domains or set())}
    total = 0
    for year in expected_years:
        manifest_path = output_root / "expected" / f"{year}.jsonl.gz"
        count_path = output_root / "count" / f"{year}.json"
        if not manifest_path.is_file() or not count_path.is_file():
            errors.append(f"{year}: expected manifest or count receipt missing")
            continue
        rows: list[dict[str, Any]] = []
        try:
            with gzip.open(manifest_path, "rt", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, 1):
                    if not line.strip():
                        errors.append(f"{year}:{line_no}: blank JSONL line")
                        continue
                    value = json.loads(line)
                    rows.append(value)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{year}: invalid gzip JSONL: {exc}")
            continue
        ids = [row.get("source_item_id") for row in rows]
        urls = [row.get("landing_url") for row in rows]
        positions = [row.get("listing_position") for row in rows]
        for index, row in enumerate(rows, 1):
            missing = REQUIRED_LISTING_FIELDS - set(row)
            if missing:
                errors.append(f"{year}:{index}: missing fields {sorted(missing)}")
                break
            if row.get("venue_id") != venue_id or row.get("year") != year:
                errors.append(f"{year}:{index}: venue/year scope mismatch")
                break
            for key in ("landing_url", "source_page_url"):
                host = (urlsplit(row.get(key, "")).hostname or "").lower()
                if not _host_allowed(host, allowed_domains):
                    errors.append(f"{year}:{index}: {key} host outside allowlist: {host}")
                    break
        if positions != list(range(1, len(rows) + 1)):
            errors.append(f"{year}: listing positions are not continuous 1..N")
        if len(set(ids)) != len(rows):
            errors.append(f"{year}: duplicate source_item_id")
        if len(set(urls)) != len(rows):
            errors.append(f"{year}: duplicate landing_url")
        receipt = load_json(count_path)
        summary = report_yearly.get(year, {})
        for label, value in (("count receipt", receipt), ("venue report", summary)):
            if value.get("venue_id") != venue_id or value.get("year") != year:
                errors.append(f"{year}: {label} scope mismatch")
            for key in ("displayed_count", "parsed_count", "unique_count", "eligible_count"):
                if value.get(key) != len(rows):
                    errors.append(f"{year}: {label} {key} != manifest rows")
        position_count = sum(row.get("document_type") == "position-paper" for row in rows)
        if summary.get("position_paper_count") != position_count:
            errors.append(f"{year}: position-paper count mismatch")
        set_hash = _digest(sorted(urls))
        ordered_hash = _digest(urls)
        if summary.get("set_hash_sha256") != set_hash:
            errors.append(f"{year}: set hash mismatch")
        if summary.get("ordered_listing_hash_sha256") != ordered_hash:
            errors.append(f"{year}: ordered hash mismatch")
        source_grade = "A" if year < year_through else "B"
        if any(row.get("source_grade") != source_grade for row in rows) or summary.get("source_grade") != source_grade:
            errors.append(f"{year}: source grade mismatch")
        displayed_count = summary.get("displayed_count")
        if displayed_count == 0 and not rows and year == year_through:
            report_status = summary.get("waterline_status")
            receipt_status = receipt.get("waterline_status")
            evidence_urls = summary.get("waterline_evidence_urls")
            if not isinstance(evidence_urls, list) or not evidence_urls:
                evidence_urls = receipt.get("waterline_evidence_urls")
            zero_waterline_valid = (
                report_status in CURRENT_YEAR_ZERO_WATERLINE_STATUSES
                and receipt_status == report_status
                and isinstance(evidence_urls, list)
                and bool(evidence_urls)
            )
            for evidence_url in evidence_urls if isinstance(evidence_urls, list) else []:
                host = (urlsplit(evidence_url).hostname or "").lower()
                if not _host_allowed(host, allowed_domains):
                    errors.append(f"{year}: zero-waterline evidence URL outside allowlist: {host}")
                    zero_waterline_valid = False
            if not zero_waterline_valid:
                errors.append(f"{year}: zero current-year manifest lacks validated negative-waterline evidence")
            coverage = 1.0 if zero_waterline_valid else 0.0
        elif displayed_count == 0 and not rows and year < year_through:
            report_status = summary.get("waterline_status")
            receipt_status = receipt.get("waterline_status")
            evidence_urls = summary.get("waterline_evidence_urls")
            if not isinstance(evidence_urls, list) or not evidence_urls:
                evidence_urls = receipt.get("waterline_evidence_urls")
            venue_start_year = summary.get("venue_start_year")
            if not isinstance(venue_start_year, int):
                venue_start_year = receipt.get("venue_start_year")
            historical_zero_valid = (
                report_status in HISTORICAL_ZERO_INACTIVE_STATUSES
                and receipt_status == report_status
                and isinstance(evidence_urls, list)
                and bool(evidence_urls)
                and isinstance(venue_start_year, int)
                and year < venue_start_year
            )
            for evidence_url in evidence_urls if isinstance(evidence_urls, list) else []:
                host = (urlsplit(evidence_url).hostname or "").lower()
                if not _host_allowed(host, allowed_domains):
                    errors.append(f"{year}: historical-zero evidence URL outside allowlist: {host}")
                    historical_zero_valid = False
            if not historical_zero_valid:
                errors.append(f"{year}: zero historical manifest lacks validated inactive-era evidence")
            coverage = 1.0 if historical_zero_valid else 0.0
        else:
            coverage = len(rows) / displayed_count if displayed_count else 0.0
        if source_grade == "A" and coverage != 1.0:
            errors.append(f"{year}: grade-A coverage is not 100%")
        if source_grade == "B" and coverage < 0.995:
            errors.append(f"{year}: grade-B coverage below 99.5%")
        yearly_results.append({
            "year": year, "rows": len(rows), "unique_ids": len(set(ids)),
            "unique_urls": len(set(urls)), "position_papers": position_count,
            "source_grade": source_grade, "coverage": coverage,
            "set_hash_sha256": set_hash, "ordered_hash_sha256": ordered_hash,
        })
        total += len(rows)
    totals = report.get("totals", {})
    for key in ("displayed", "parsed", "unique", "eligible"):
        if totals.get(key) != total:
            errors.append(f"venue total {key} != summed manifests")
    if totals.get("blocked") != 0:
        errors.append("blocked count is not zero")
    if report.get("status") != "PASS":
        errors.append("venue report status is not PASS")
    if report.get("unresolved_anomalies"):
        errors.append("unresolved anomalies remain")
    return {
        "status": "PASS" if not errors else "FAIL",
        "venue_id": venue_id,
        "year_from": year_from,
        "year_through": year_through,
        "yearly": yearly_results,
        "total": total,
        "errors": errors,
    }

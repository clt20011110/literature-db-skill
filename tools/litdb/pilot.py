from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .io import load_json


REQUIRED_MANIFEST_FIELDS = {
    "venue_id",
    "selected_unit_id",
    "year",
    "listing_position",
    "source_item_id",
    "landing_url",
    "title",
    "authors",
    "document_type",
    "include_decision",
    "inclusion_rule_id",
    "observed_at",
}

REQUIRED_SAMPLE_FIELDS = {
    "venue_id",
    "source_item_id",
    "landing_url",
    "title",
    "authors",
    "document_type",
    "doi",
    "abstract",
}


def _load_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not path.is_file():
        errors.append(f"{path.name} missing")
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            errors.append(f"{path.name}:{line_no}: blank JSONL line")
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_no}: invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name}:{line_no}: row is not an object")
            continue
        rows.append(value)
    return rows


def _host_allowed(url: str, allowed_domains: set[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def _report_total(report: dict[str, Any]) -> int | None:
    totals = report.get("totals")
    if isinstance(totals, dict):
        for key in ("eligible", "eligible_count", "records", "manifest_rows"):
            if isinstance(totals.get(key), int):
                return totals[key]
    for key in ("eligible_count", "manifest_row_count", "records_emitted"):
        if isinstance(report.get(key), int):
            return report[key]
    return None


def verify_pilot_output(output_root: Path, venue_id: str, allowed_domains: set[str]) -> dict[str, Any]:
    errors: list[str] = []
    allowed_domains = {item.lower() for item in allowed_domains}
    manifest = _load_jsonl(output_root / "pilot_manifest.jsonl", errors)
    samples = _load_jsonl(output_root / "pilot_sample_metadata.jsonl", errors)
    report_path = output_root / "pilot_report.json"
    report: dict[str, Any] = {}
    if not report_path.is_file():
        errors.append("pilot_report.json missing")
    else:
        report = load_json(report_path)
        if report.get("venue_id") != venue_id:
            errors.append("pilot report venue mismatch")
        if report.get("status") != "PASS":
            errors.append("pilot report status is not PASS")
        for key in ("unresolved_anomalies", "unresolved", "blockers"):
            if report.get(key):
                errors.append(f"pilot report has non-empty {key}")

    ids: list[str] = []
    urls: list[str] = []
    positions_by_year: dict[int, list[int]] = defaultdict(list)
    counts_by_year: dict[int, int] = defaultdict(int)
    manifest_by_url: dict[str, dict[str, Any]] = {}
    for line_no, row in enumerate(manifest, 1):
        missing = REQUIRED_MANIFEST_FIELDS - set(row)
        if missing:
            errors.append(f"pilot_manifest.jsonl:{line_no}: missing fields {sorted(missing)}")
            continue
        if row.get("venue_id") != venue_id:
            errors.append(f"pilot_manifest.jsonl:{line_no}: venue mismatch")
        year = row.get("year")
        position = row.get("listing_position")
        if not isinstance(year, int) or not isinstance(position, int):
            errors.append(f"pilot_manifest.jsonl:{line_no}: year/listing_position must be integers")
        else:
            positions_by_year[year].append(position)
            counts_by_year[year] += 1
        source_item_id = row.get("source_item_id")
        landing_url = row.get("landing_url")
        if not isinstance(source_item_id, str) or not source_item_id:
            errors.append(f"pilot_manifest.jsonl:{line_no}: invalid source_item_id")
        else:
            ids.append(source_item_id)
        if not isinstance(landing_url, str) or not landing_url:
            errors.append(f"pilot_manifest.jsonl:{line_no}: invalid landing_url")
        else:
            urls.append(landing_url)
            if allowed_domains and not _host_allowed(landing_url, allowed_domains):
                errors.append(f"pilot_manifest.jsonl:{line_no}: landing_url outside allowlist")
        source_url = row.get("source_page_url") or row.get("source_url")
        if not isinstance(source_url, str) or not source_url:
            errors.append(f"pilot_manifest.jsonl:{line_no}: source page URL missing")
        elif allowed_domains and not _host_allowed(source_url, allowed_domains):
            errors.append(f"pilot_manifest.jsonl:{line_no}: source page URL outside allowlist")
        if row.get("include_decision") not in {"include", "include_candidate"}:
            errors.append(f"pilot_manifest.jsonl:{line_no}: row is not an included candidate")
        if isinstance(landing_url, str):
            manifest_by_url[landing_url] = row

    if not manifest:
        errors.append("pilot manifest is empty")
    if len(set(ids)) != len(ids):
        errors.append("pilot manifest has duplicate source_item_id")
    if len(set(urls)) != len(urls):
        errors.append("pilot manifest has duplicate landing_url")
    for year, positions in sorted(positions_by_year.items()):
        if positions != list(range(1, len(positions) + 1)):
            errors.append(f"{year}: listing positions are not continuous and ordered 1..N")

    total = _report_total(report)
    if total is None:
        errors.append("pilot report has no machine-readable eligible total")
    elif total != len(manifest):
        errors.append("pilot report eligible total does not match manifest")

    expected_sample_count = 60 if any(value > 2000 for value in counts_by_year.values()) else 30
    if len(samples) != expected_sample_count:
        errors.append(f"pilot sample count {len(samples)} != required {expected_sample_count}")
    sample_urls: list[str] = []
    for line_no, row in enumerate(samples, 1):
        missing = REQUIRED_SAMPLE_FIELDS - set(row)
        if missing:
            errors.append(f"pilot_sample_metadata.jsonl:{line_no}: missing fields {sorted(missing)}")
            continue
        if row.get("venue_id") != venue_id:
            errors.append(f"pilot_sample_metadata.jsonl:{line_no}: venue mismatch")
        if "publication_date" not in row and "date" not in row:
            errors.append(f"pilot_sample_metadata.jsonl:{line_no}: publication date evidence missing")
        if "pdf_location" not in row and "pdf_url" not in row:
            errors.append(f"pilot_sample_metadata.jsonl:{line_no}: PDF location evidence missing")
        landing_url = row.get("landing_url")
        if isinstance(landing_url, str):
            sample_urls.append(landing_url)
        source = manifest_by_url.get(landing_url) if isinstance(landing_url, str) else None
        if source is None:
            errors.append(f"pilot_sample_metadata.jsonl:{line_no}: sample not found in manifest")
            continue
        if row.get("title") != source.get("title"):
            errors.append(f"pilot_sample_metadata.jsonl:{line_no}: title differs from manifest")
        for key in ("landing_url", "source_url"):
            value = row.get(key)
            if isinstance(value, str) and allowed_domains and not _host_allowed(value, allowed_domains):
                errors.append(f"pilot_sample_metadata.jsonl:{line_no}: {key} outside allowlist")
    if len(set(sample_urls)) != len(sample_urls):
        errors.append("pilot metadata samples are not unique")

    return {
        "status": "PASS" if not errors else "FAIL",
        "venue_id": venue_id,
        "manifest_rows": len(manifest),
        "samples": len(samples),
        "counts_by_year": dict(sorted(counts_by_year.items())),
        "errors": errors,
    }

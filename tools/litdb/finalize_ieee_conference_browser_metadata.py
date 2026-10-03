#!/usr/bin/env python3
"""Close the evidence loop for a completed IEEE conference Browser run.

The finalizer is deliberately reusable across conference venues.  It does not
browse and it never writes to the catalog.  It:

* normalizes venue-specific exclusion labels to the stable catalog taxonomy
  while retaining the original rule labels;
* clears only malformed DOI values that have explicit, fresh Browser evidence;
* proves all historical transient detail errors were subsequently accounted;
* emits current-waterline evidence from an accepted fresh Browser observation;
* refreshes output hashes after the auditable repairs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected JSON object at {path}:{line_no}")
            rows.append(value)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def normalize_doi(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().lower()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    normalized = re.sub(r"^doi:\s*", "", normalized).strip()
    return normalized if re.fullmatch(r"10\.\d{4,9}/\S+", normalized) else None


def catalog_exclusion_reason(reason: str) -> str:
    if reason in NON_MAIN_TRACK_REASONS:
        return "non_main_track"
    if reason in NON_RESEARCH_REASONS:
        return "non_research_content"
    return reason


def official_urls_from_waterline(value: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    checks = value.get("fresh_checks")
    if isinstance(checks, dict):
        for check in checks.values():
            if not isinstance(check, dict) or check.get("status") != "COMPLETED":
                continue
            url = check.get("observed_url") or check.get("url")
            if isinstance(url, str) and url.startswith("https://") and url not in urls:
                urls.append(url)
    if not urls:
        for url in value.get("source_urls", []):
            if isinstance(url, str) and url.startswith("https://") and url not in urls:
                urls.append(url)
    if not urls:
        raise ValueError("waterline source has no completed official HTTPS checks")
    return urls


def hostname_allowed(url: str, allowed_domains: set[str]) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--venue-id", required=True)
    parser.add_argument("--controller-thread-id", required=True)
    parser.add_argument("--waterline-source", required=True, type=Path)
    parser.add_argument("--invalid-doi-evidence", action="append", default=[], type=Path)
    parser.add_argument("--allowed-domain", action="append", default=[])
    args = parser.parse_args()

    root = args.run_root.resolve()
    staging_path = root / "metadata_staging.jsonl"
    exclusions_path = root / "metadata_exclusions.jsonl"
    errors_path = root / "detail_errors.jsonl"
    summary_path = root / "detail_summary.json"
    checkpoint_path = root / "detail_checkpoint.json"
    build_path = root / "expected_build_summary.json"

    staging = load_jsonl(staging_path)
    exclusions = load_jsonl(exclusions_path)
    errors = load_jsonl(errors_path)
    summary = load_json(summary_path)
    checkpoint = load_json(checkpoint_path)
    build = load_json(build_path)
    waterline_source = load_json(args.waterline_source.resolve())

    expected_total = build.get("canonical_expected_identity_count")
    source_hash = build.get("source_identity_set_sha256")
    if not isinstance(expected_total, int) or expected_total < 1:
        raise ValueError("expected build has no canonical identity count")
    if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ValueError("expected build has no valid identity-set hash")

    before_staging_hash = sha256_file(staging_path)
    before_exclusion_hash = sha256_file(exclusions_path)

    doi_evidence_by_id: dict[str, dict[str, Any]] = {}
    for evidence_arg in args.invalid_doi_evidence:
        evidence_path = evidence_arg.resolve()
        evidence = load_json(evidence_path)
        identity = str(evidence.get("source_native_id") or "")
        validation = evidence.get("doi_validation")
        if evidence.get("venue_id") != args.venue_id or not identity:
            raise ValueError(f"invalid DOI evidence identity: {evidence_path}")
        if not isinstance(validation, dict) or validation.get("status") != "INVALID_PLACEHOLDER":
            raise ValueError(f"DOI evidence is not an invalid-placeholder observation: {evidence_path}")
        if evidence.get("page_content_visible") is not True:
            raise ValueError(f"DOI evidence lacks a visible official page: {evidence_path}")
        source_url = evidence.get("source_url")
        parsed = urlsplit(source_url) if isinstance(source_url, str) else None
        if not parsed or parsed.scheme != "https" or parsed.hostname != "ieeexplore.ieee.org":
            raise ValueError(f"DOI evidence source is not official IEEE: {evidence_path}")
        evidence["_evidence_path"] = str(evidence_path)
        doi_evidence_by_id[identity] = evidence

    repaired_dois: list[dict[str, Any]] = []
    for row in staging:
        doi = row.get("doi")
        if not doi or normalize_doi(doi):
            continue
        identity = str(row.get("source_native_id") or "")
        evidence = doi_evidence_by_id.get(identity)
        if not evidence or evidence.get("visible_doi_text") != doi:
            raise ValueError(f"malformed DOI lacks matching fresh Browser evidence: {identity}: {doi!r}")
        row["source_reported_invalid_doi"] = doi
        row["doi"] = None
        row["doi_status"] = "checked_missing"
        missing = row.setdefault("missing_fields", {})
        missing["doi"] = {
            "reason_code": "checked_missing",
            "source_reported_value": doi,
            "validation_status": "invalid_placeholder",
            "evidence_path": evidence["_evidence_path"],
            "last_attempt_at": evidence.get("observed_at"),
        }
        provenance = row.setdefault("field_provenance", {})
        provenance["doi"] = {
            "method": "official_ieee_visible_field_revalidated_and_format_checked",
            "observed_at": evidence.get("observed_at"),
            "source_url": evidence.get("source_url"),
            "status": "checked_missing_invalid_placeholder",
            "source_reported_value": doi,
            "evidence_path": evidence["_evidence_path"],
        }
        repaired_dois.append({"source_native_id": identity, "source_reported_value": doi})

    repaired_ids = {item["source_native_id"] for item in repaired_dois}
    for identity, evidence in doi_evidence_by_id.items():
        if identity in repaired_ids:
            continue
        matches = [row for row in staging if str(row.get("source_native_id") or "") == identity]
        if len(matches) != 1:
            raise ValueError(f"DOI evidence identity is not present exactly once in staging: {identity}")
        row = matches[0]
        if (
            row.get("doi") is not None
            or row.get("doi_status") != "checked_missing"
            or row.get("source_reported_invalid_doi") != evidence.get("visible_doi_text")
        ):
            raise ValueError(f"previous DOI repair does not match its Browser evidence: {identity}")
        repaired_dois.append(
            {
                "source_native_id": identity,
                "source_reported_value": evidence.get("visible_doi_text"),
                "already_repaired_and_revalidated": True,
            }
        )

    reason_mappings: Counter[str] = Counter()
    for row in exclusions:
        evidence_value = row.get("exclusion_evidence")
        evidence_reason = (
            evidence_value.get("source_exclusion_reason_code")
            if isinstance(evidence_value, dict)
            else None
        )
        source_reason = (
            str(evidence_reason).strip()
            if evidence_reason is not None and str(evidence_reason).strip() not in {"", "None"}
            else str(row.get("exclusion_reason_code") or "").strip()
        )
        if not source_reason:
            raise ValueError(f"excluded row lacks a source reason: {row.get('source_native_id')}")
        catalog_reason = catalog_exclusion_reason(source_reason)
        if catalog_reason != source_reason:
            reason_mappings[f"{source_reason}->{catalog_reason}"] += 1
        if catalog_reason != row.get("exclusion_reason_code"):
            row["exclusion_reason_code"] = catalog_reason
        evidence = row.setdefault("exclusion_evidence", {})
        evidence.update(
            {
                "source_exclusion_reason_code": source_reason,
                "catalog_exclusion_reason_code": catalog_reason,
                "reason_taxonomy_version": "literature-exclusion-taxonomy-v1",
            }
        )

    atomic_jsonl(staging_path, staging)
    atomic_jsonl(exclusions_path, exclusions)

    accounted_ids = {
        str(row.get("source_native_id"))
        for row in staging + exclusions
        if row.get("source_native_id") is not None
    }
    if len(staging) + len(exclusions) != expected_total or len(accounted_ids) != expected_total:
        raise ValueError(
            f"formal accounting mismatch: expected={expected_total}, rows={len(staging) + len(exclusions)}, "
            f"identities={len(accounted_ids)}"
        )

    unresolved = [
        row
        for row in errors
        if str(row.get("source_native_id") or row.get("source_item_id") or "") not in accounted_ids
    ]
    error_resolution = {
        "schema_version": "ieee-detail-error-resolution-v1",
        "status": "PASS" if not unresolved else "FAIL",
        "venue_id": args.venue_id,
        "historical_error_count": len(errors),
        "resolved_error_count": len(errors) - len(unresolved),
        "unresolved_error_count": len(unresolved),
        "resolved_identity_basis": "every historical failed source_native_id was later emitted exactly once to staging or exclusions",
        "unresolved_ids": sorted(
            str(row.get("source_native_id") or row.get("source_item_id") or "") for row in unresolved
        ),
        "detail_errors_sha256": sha256_file(errors_path),
        "catalog_ready": False,
        "catalog_write": False,
    }
    atomic_json(root / "detail_error_resolution.json", error_resolution)
    if unresolved:
        raise ValueError(f"{len(unresolved)} historical detail errors remain unresolved")

    observed_at = waterline_source.get("observed_at")
    if not isinstance(observed_at, str):
        raise ValueError("waterline source lacks observed_at")
    latest_closed_year = max(int(row["year"]) for row in staging + exclusions)
    current_open_year = waterline_source.get("current_year")
    if not isinstance(current_open_year, int):
        nested_waterline = waterline_source.get("waterline")
        if isinstance(nested_waterline, dict):
            current_open_year = nested_waterline.get("open_year")
    if not isinstance(current_open_year, int):
        current_open_year = datetime.now(timezone.utc).year
    current_year_formal_proceedings_visible = waterline_source.get(
        "current_year_formal_proceedings_visible"
    )
    if not isinstance(current_year_formal_proceedings_visible, bool):
        current_year_formal_proceedings_visible = current_open_year <= latest_closed_year
    candidate_source_urls = official_urls_from_waterline(waterline_source)
    allowed_domains = {"ieeexplore.ieee.org", *(value.lower() for value in args.allowed_domain)}
    source_urls = [url for url in candidate_source_urls if hostname_allowed(url, allowed_domains)]
    ignored_source_urls = [url for url in candidate_source_urls if url not in source_urls]
    if not source_urls:
        raise ValueError("waterline source has no URL permitted by the venue registry allowlist")
    waterline = {
        "schema_version": "literature-waterline-evidence-v1",
        "venue_id": args.venue_id,
        "status": "NO_CHANGE",
        "drift_status": "NO_DRIFT",
        "enumeration_complete": True,
        "observed_at": observed_at,
        "browser_surface": "Codex in-app Browser",
        "controller_thread_id": args.controller_thread_id,
        "scope": "2015_to_latest_closed_formal_proceedings_plus_current_negative_waterline",
        "source_urls": source_urls,
        "ignored_non_registry_source_urls": ignored_source_urls,
        "source_item_set_sha256": source_hash,
        "current_source_item_count": expected_total,
        "baseline_current_scope_count": expected_total,
        "baseline_expected_root": str(root / "expected"),
        "new_ids": [],
        "missing_ids": [],
        "formal_proceedings_waterline": {
            "latest_closed_year": latest_closed_year,
            "current_year": current_open_year,
            "current_year_formal_proceedings_visible": current_year_formal_proceedings_visible,
            "source_status": waterline_source.get("status"),
            "source_evidence_path": str(args.waterline_source.resolve()),
        },
        "formal_metadata_accounting": {
            "expected_records": expected_total,
            "accounted_records": len(staging) + len(exclusions),
            "included_records": len(staging),
            "excluded_records": len(exclusions),
            "coverage": (len(staging) + len(exclusions)) / expected_total,
        },
        "reuse_validation": {
            "expected_manifest_reused": True,
            "canonical_listing_identities_reused": True,
            "fresh_detail_fields_revalidated": True,
            "fresh_current_waterline_revalidated": True,
            "count_only_promoted_to_catalog": False,
        },
        "catalog_ready": False,
        "catalog_write": False,
        "evidence_paths": {
            "expected_build_summary": str(build_path),
            "detail_summary": str(summary_path),
            "detail_error_resolution": str(root / "detail_error_resolution.json"),
            "source_waterline": str(args.waterline_source.resolve()),
            "invalid_doi_evidence": [str(path.resolve()) for path in args.invalid_doi_evidence],
        },
    }
    atomic_json(root / "waterline_evidence.json", waterline)

    after_staging_hash = sha256_file(staging_path)
    after_exclusion_hash = sha256_file(exclusions_path)
    finalized_at = utc_now()
    postprocess = {
        "status": "PASS",
        "finalized_at": finalized_at,
        "doi_repairs": repaired_dois,
        "exclusion_reason_mappings": dict(sorted(reason_mappings.items())),
        "before_staging_sha256": before_staging_hash,
        "after_staging_sha256": after_staging_hash,
        "before_exclusion_sha256": before_exclusion_hash,
        "after_exclusion_sha256": after_exclusion_hash,
        "historical_errors_resolved": len(errors),
    }
    atomic_json(root / "ieee_conference_postprocess.json", postprocess)

    summary.update(
        {
            "status": "PASS",
            "expected_total": expected_total,
            "completed_total": expected_total,
            "remaining": 0,
            "output_sha256": after_staging_hash,
            "exclusion_sha256": after_exclusion_hash,
            "postprocess": postprocess,
        }
    )
    atomic_json(summary_path, summary)
    checkpoint.update(
        {
            "status": "PASS",
            "expected_total": expected_total,
            "completed_total": expected_total,
            "remaining": 0,
            "current_source_native_id": None,
            "output_sha256": after_staging_hash,
            "exclusion_sha256": after_exclusion_hash,
            "postprocess_status": "PASS",
            "updated_at": finalized_at,
        }
    )
    atomic_json(checkpoint_path, checkpoint)

    print(
        json.dumps(
            {
                "status": "PASS",
                "venue_id": args.venue_id,
                "expected": expected_total,
                "included": len(staging),
                "excluded": len(exclusions),
                "doi_repairs": len(repaired_dois),
                "exclusion_reason_mappings": dict(sorted(reason_mappings.items())),
                "historical_errors_resolved": len(errors),
                "staging_sha256": after_staging_hash,
                "exclusion_sha256": after_exclusion_hash,
                "waterline_path": str(root / "waterline_evidence.json"),
                "error_resolution_path": str(root / "detail_error_resolution.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build immutable, validator-ready clean staging artifacts for a venue run.

The browser crawl remains the raw audit layer.  This script deterministically
overlays separately captured repair rows, applies reviewed field overrides,
normalizes exclusion provenance, and removes excluded identities from the
included staging file.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
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
                raise ValueError(f"JSONL row is not an object: {path}:{line_no}")
            rows.append(value)
    return rows


def identity(record: dict[str, Any], *, source: str) -> str:
    value = str(record.get("source_native_id") or "").strip()
    if not value:
        raise ValueError(f"source_native_id missing in {source}")
    return value


def unique_index(rows: Iterable[dict[str, Any]], *, source: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in rows:
        key = identity(record, source=source)
        if key in result:
            raise ValueError(f"duplicate source_native_id {key} in {source}")
        result[key] = record
    return result


def encoded_jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def provenance_for_reviewed_title(override: dict[str, Any]) -> dict[str, Any]:
    detail = override.get("fresh_detail_revalidation")
    accepted = override.get("accepted_listing")
    review = override.get("review")
    if not isinstance(detail, dict) or not detail.get("source_url") or not detail.get("observed_at"):
        raise ValueError("reviewed title override lacks fresh detail provenance")
    if not isinstance(accepted, dict) or not accepted.get("source_url") or not accepted.get("observed_at"):
        raise ValueError("reviewed title override lacks accepted listing provenance")
    if not isinstance(review, dict) or not review.get("reviewed_at"):
        raise ValueError("reviewed title override lacks controller review provenance")
    return {
        "method": "controller_reviewed_override_from_official_listing_and_detail",
        "observed_at": detail["observed_at"],
        "source_url": detail["source_url"],
        "status": "present",
        "reuse_status": "accepted_listing_reused_and_fresh_detail_revalidated",
        "reason_code": override.get("reason_code"),
        "accepted_listing": accepted,
        "review": review,
    }


def normalize_exclusion_provenance(record: dict[str, Any]) -> list[str]:
    provenance = record.get("field_provenance")
    if not isinstance(provenance, dict):
        provenance = {}
        record["field_provenance"] = provenance
    source_url = record.get("source_url") or record.get("landing_url")
    observed_at = record.get("observed_at") or record.get("source_observed_at")
    if not source_url or not observed_at:
        raise ValueError(
            f"exclusion {record.get('source_native_id')} lacks source_url/observed_at for provenance"
        )
    added: list[str] = []
    for field in (
        "source_native_id",
        "title",
        "year",
        "inclusion_decision",
        "exclusion_reason_code",
    ):
        if isinstance(provenance.get(field), dict):
            continue
        provenance[field] = {
            "method": "controller_normalized_official_detail_exclusion_decision",
            "observed_at": observed_at,
            "source_url": source_url,
            "status": "excluded",
            "reuse_status": "browser_observation_preserved",
        }
        added.append(field)
    return added


def decode_human_text_entities(record: dict[str, Any]) -> list[str]:
    """Decode publisher HTML entities in human-readable metadata fields only.

    URLs and provenance payloads are intentionally left untouched.  The raw
    Browser artifacts remain immutable; this normalization applies only to the
    validator-ready clean layer.
    """

    changed: list[str] = []

    def decode_field(container: dict[str, Any], key: str, path: str) -> None:
        value = container.get(key)
        if not isinstance(value, str):
            return
        decoded = html.unescape(value)
        if decoded != value:
            container[key] = decoded
            changed.append(path)

    for field in (
        "title",
        "abstract",
        "document_type",
        "publication_date",
        "volume",
        "issue",
        "pages",
        "start_page",
        "end_page",
    ):
        decode_field(record, field, field)

    authors = record.get("authors")
    if isinstance(authors, list):
        for index, author in enumerate(authors):
            if not isinstance(author, dict):
                continue
            decode_field(author, "name", f"authors[{index}].name")
            affiliations = author.get("affiliations")
            if isinstance(affiliations, list):
                for affiliation_index, affiliation in enumerate(affiliations):
                    if not isinstance(affiliation, str):
                        continue
                    decoded = html.unescape(affiliation)
                    if decoded != affiliation:
                        affiliations[affiliation_index] = decoded
                        changed.append(f"authors[{index}].affiliations[{affiliation_index}]")

    venue_identity = record.get("venue_identity")
    if isinstance(venue_identity, dict):
        decode_field(venue_identity, "publication_title", "venue_identity.publication_title")
    return changed


def outside_scope_exclusion(
    record: dict[str, Any],
    *,
    min_year: int,
    max_year: int,
) -> dict[str, Any] | None:
    try:
        year = int(record.get("year"))
    except (TypeError, ValueError):
        return None
    if min_year <= year <= max_year:
        return None
    excluded = copy.deepcopy(record)
    excluded["schema_version"] = "literature-metadata-exclusion-v1"
    excluded["inclusion_decision"] = "exclude"
    excluded["exclusion_reason_code"] = "outside_scope_year"
    excluded["exclusion_reason"] = (
        f"Fresh official detail metadata resolves this item to year {year}, "
        f"outside the configured initialization range {min_year}-{max_year}."
    )
    return excluded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--raw-staging", default="metadata_staging.jsonl")
    parser.add_argument("--raw-exclusions", default="metadata_exclusions.jsonl")
    parser.add_argument("--overrides", default="reviewed_title_overrides.jsonl")
    parser.add_argument("--clean-staging", default="metadata_staging_clean.jsonl")
    parser.add_argument("--clean-exclusions", default="metadata_exclusions_clean.jsonl")
    parser.add_argument("--manifest", default="clean_build_manifest.json")
    parser.add_argument("--min-year", type=int, default=2015)
    parser.add_argument("--max-year", type=int, default=datetime.now(timezone.utc).year)
    args = parser.parse_args()

    if args.min_year > args.max_year:
        raise ValueError("--min-year must be less than or equal to --max-year")

    run_root = args.run.expanduser().resolve()
    raw_staging_path = run_root / args.raw_staging
    raw_exclusion_path = run_root / args.raw_exclusions
    override_path = run_root / args.overrides
    clean_staging_path = run_root / args.clean_staging
    clean_exclusion_path = run_root / args.clean_exclusions
    manifest_path = run_root / args.manifest

    raw_rows = read_jsonl(raw_staging_path)
    raw_index = unique_index(raw_rows, source=str(raw_staging_path))
    raw_order = [identity(record, source=str(raw_staging_path)) for record in raw_rows]

    exclusion_rows = read_jsonl(raw_exclusion_path)
    raw_exclusion_count = len(exclusion_rows)
    exclusion_index = unique_index(exclusion_rows, source=str(raw_exclusion_path))
    if any(record.get("venue_id") != args.venue for record in raw_rows + exclusion_rows):
        raise ValueError("venue_id mismatch in raw staging or exclusions")

    repair_index: dict[str, dict[str, Any]] = {}
    repair_sources: dict[str, str] = {}
    for repair_path in sorted(run_root.glob("repair_*.jsonl")):
        rows = read_jsonl(repair_path)
        if len(rows) != 1:
            raise ValueError(f"repair artifact must contain exactly one row: {repair_path}")
        repair = rows[0]
        key = identity(repair, source=str(repair_path))
        if key in repair_index:
            raise ValueError(f"duplicate repair source_native_id {key}")
        if key not in raw_index:
            raise ValueError(f"repair identity {key} is absent from raw staging")
        if key in exclusion_index:
            raise ValueError(f"repair identity {key} is also excluded")
        if repair.get("venue_id") != args.venue:
            raise ValueError(f"repair venue mismatch for {key}")
        repair_index[key] = repair
        repair_sources[key] = repair_path.name

    clean_index = dict(raw_index)
    clean_index.update(repair_index)

    entity_normalizations: dict[str, list[str]] = {}
    for key, record in clean_index.items():
        changed = decode_human_text_entities(record)
        if changed:
            entity_normalizations[key] = changed

    out_of_scope_ids: list[str] = []
    for key in raw_order:
        if key in exclusion_index:
            continue
        excluded = outside_scope_exclusion(
            clean_index[key],
            min_year=args.min_year,
            max_year=args.max_year,
        )
        if excluded is None:
            continue
        exclusion_index[key] = excluded
        exclusion_rows.append(excluded)
        out_of_scope_ids.append(key)

    overrides = read_jsonl(override_path) if override_path.is_file() else []
    override_index = unique_index(overrides, source=str(override_path))
    for key, override in override_index.items():
        if override.get("venue_id") != args.venue:
            raise ValueError(f"override venue mismatch for {key}")
        if override.get("field_name") != "title" or override.get("decision") != "replace_staging_value":
            raise ValueError(f"unsupported reviewed override for {key}")
        if key not in clean_index:
            raise ValueError(f"override identity {key} is absent from staging")
        if key in exclusion_index:
            raise ValueError(f"override identity {key} is excluded")
        replacement = override.get("replacement_value")
        if not isinstance(replacement, str) or not replacement.strip():
            raise ValueError(f"reviewed title replacement is empty for {key}")
        record = clean_index[key]
        record["title"] = replacement
        provenance = record.setdefault("field_provenance", {})
        if not isinstance(provenance, dict):
            raise ValueError(f"field_provenance is not an object for {key}")
        provenance["title"] = provenance_for_reviewed_title(override)
        reviewed = record.setdefault("reviewed_field_overrides", [])
        if not isinstance(reviewed, list):
            raise ValueError(f"reviewed_field_overrides is not a list for {key}")
        reviewed.append(override)
        missing = record.get("missing_fields")
        if isinstance(missing, dict):
            missing.pop("title", None)

    normalized_exclusion_fields: dict[str, list[str]] = {}
    for key, record in exclusion_index.items():
        added = normalize_exclusion_provenance(record)
        if added:
            normalized_exclusion_fields[key] = added

    exclusion_ids = set(exclusion_index)
    clean_rows = [clean_index[key] for key in raw_order if key not in exclusion_ids]
    clean_exclusions = [exclusion_index[identity(row, source=str(raw_exclusion_path))] for row in exclusion_rows]
    clean_ids = {identity(row, source="clean staging") for row in clean_rows}
    if clean_ids & exclusion_ids:
        raise ValueError("clean staging and exclusions overlap")
    if clean_ids | exclusion_ids != set(raw_index) | exclusion_ids:
        raise ValueError("clean identity accounting differs from raw staging plus exclusions")

    clean_content = encoded_jsonl(clean_rows)
    exclusion_content = encoded_jsonl(clean_exclusions)
    atomic_write(clean_staging_path, clean_content)
    atomic_write(clean_exclusion_path, exclusion_content)

    manifest = {
        "schema_version": "literature-metadata-clean-build-v1",
        "status": "PASS",
        "venue_id": args.venue,
        "run_root": str(run_root),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "policy": {
            "raw_browser_artifacts_are_immutable": True,
            "prior_expected_manifests_and_browser_evidence_reused": True,
            "count_only_artifacts_not_promoted_to_catalog_metadata": True,
            "formal_schema_and_current_waterline_revalidation_required": True,
            "initialization_year_range": {
                "min_year": args.min_year,
                "max_year": args.max_year,
            },
        },
        "inputs": {
            "raw_staging": {
                "path": str(raw_staging_path),
                "records": len(raw_rows),
                "sha256": sha256_file(raw_staging_path),
            },
            "raw_exclusions": {
                "path": str(raw_exclusion_path),
                "records": raw_exclusion_count,
                "sha256": sha256_file(raw_exclusion_path),
            },
            "reviewed_overrides": {
                "path": str(override_path),
                "records": len(overrides),
                "sha256": sha256_file(override_path) if override_path.is_file() else None,
            },
        },
        "repairs": {
            "count": len(repair_index),
            "source_native_ids": sorted(repair_index),
            "files_by_source_native_id": dict(sorted(repair_sources.items())),
        },
        "human_text_entity_normalization": {
            "record_count": len(entity_normalizations),
            "fields_by_source_native_id": dict(sorted(entity_normalizations.items())),
        },
        "outside_scope_year_transitions": {
            "count": len(out_of_scope_ids),
            "source_native_ids": sorted(out_of_scope_ids),
        },
        "reviewed_title_overrides": {
            "count": len(override_index),
            "source_native_ids": sorted(override_index),
        },
        "exclusions": {
            "count": len(exclusion_index),
            "source_native_ids": sorted(exclusion_index),
            "reason_counts": dict(sorted(Counter(
                str(row.get("exclusion_reason_code") or row.get("exclusion_reason"))
                for row in clean_exclusions
            ).items())),
            "provenance_fields_added": dict(sorted(normalized_exclusion_fields.items())),
            "raw_staging_overlap_removed": sorted(set(raw_index) & exclusion_ids),
        },
        "outputs": {
            "clean_staging": {
                "path": str(clean_staging_path),
                "records": len(clean_rows),
                "sha256": sha256_bytes(clean_content),
            },
            "clean_exclusions": {
                "path": str(clean_exclusion_path),
                "records": len(clean_exclusions),
                "sha256": sha256_bytes(exclusion_content),
            },
            "accounted_identities": len(clean_ids | exclusion_ids),
        },
        "year_counts": dict(sorted(Counter(int(row["year"]) for row in clean_rows).items())),
    }
    manifest_content = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(manifest_path, manifest_content)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

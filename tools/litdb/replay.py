from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .io import load_json
from .normalize import (
    math_rendering_equivalent_for_replay,
    normalize_abstract_for_replay,
    normalize_publication_date_for_replay,
    normalize_scalar,
)


COMPARISON_FIELDS = (
    "title", "authors", "publication_date", "document_type",
    "doi", "landing_url", "abstract", "pdf_location",
)


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


def _identity(row: dict[str, Any]) -> str:
    landing_url = normalize_scalar(row.get("landing_url")) or ""
    return str(landing_url)


def _agreement(expected: set[Any], observed: set[Any]) -> dict[str, Any]:
    intersection = expected & observed
    union = expected | observed
    return {
        "expected": len(expected),
        "observed": len(observed),
        "intersection": len(intersection),
        "union": len(union),
        "missing": len(expected - observed),
        "extra": len(observed - expected),
        "jaccard": len(intersection) / len(union) if union else 1.0,
    }


def _value(row: dict[str, Any], field: str) -> Any:
    aliases = {
        "publication_date": ("publication_date", "date"),
        "pdf_location": ("pdf_location", "pdf_url"),
    }
    for key in aliases.get(field, (field,)):
        if key in row:
            value = row[key]
            if field == "pdf_location" and isinstance(value, dict):
                return value.get("url")
            return value
    return None


def _normalized(value: Any, field: str) -> Any:
    if field == "abstract":
        return normalize_abstract_for_replay(value)
    if field == "publication_date":
        return normalize_publication_date_for_replay(value)
    value = normalize_scalar(value)
    if field == "doi" and isinstance(value, str):
        return value.casefold().removeprefix("https://doi.org/").removeprefix("doi:").strip()
    return value


def verify_replay_output(
    output_root: Path,
    venue_id: str,
    pilot_manifest_path: Path,
    pilot_samples_path: Path,
    allowed_domains: set[str],
) -> dict[str, Any]:
    errors: list[str] = []
    pilot_manifest = _load_jsonl(pilot_manifest_path, errors)
    replay_manifest = _load_jsonl(output_root / "replay_manifest.jsonl", errors)
    pilot_samples = _load_jsonl(pilot_samples_path, errors)
    replay_samples = _load_jsonl(output_root / "replay_sample_metadata.jsonl", errors)
    report_path = output_root / "replay_report.json"
    if not report_path.is_file():
        errors.append("replay_report.json missing")
    else:
        report = load_json(report_path)
        if report.get("venue_id") != venue_id:
            errors.append("replay report venue mismatch")
        if report.get("status") != "PASS":
            errors.append("replay report status is not PASS")

    allowed_domains = {item.lower() for item in allowed_domains}
    for label, rows in (("pilot", pilot_manifest), ("replay", replay_manifest)):
        for line_no, row in enumerate(rows, 1):
            if row.get("venue_id") != venue_id:
                errors.append(f"{label} manifest:{line_no}: venue mismatch")
            for key in ("landing_url", "source_page_url", "source_url"):
                value = row.get(key)
                if not isinstance(value, str):
                    continue
                host = (urlsplit(value).hostname or "").lower()
                if allowed_domains and not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
                    errors.append(f"{label} manifest:{line_no}: {key} outside allowlist")

    pilot_set = {_identity(row) for row in pilot_manifest}
    replay_set = {_identity(row) for row in replay_manifest}
    if "" in pilot_set or "" in replay_set:
        errors.append("manifest contains an empty identity")
    overall = _agreement(pilot_set, replay_set)
    if overall["jaccard"] < 0.999:
        errors.append("overall replay set agreement below 99.9%")

    pilot_by_year: dict[int, set[str]] = defaultdict(set)
    replay_by_year: dict[int, set[str]] = defaultdict(set)
    for row in pilot_manifest:
        if isinstance(row.get("year"), int):
            pilot_by_year[row["year"]].add(_identity(row))
    for row in replay_manifest:
        if isinstance(row.get("year"), int):
            replay_by_year[row["year"]].add(_identity(row))
    years = sorted(set(pilot_by_year) | set(replay_by_year))
    per_year = {year: _agreement(pilot_by_year[year], replay_by_year[year]) for year in years}
    for year, result in per_year.items():
        if result["jaccard"] < 0.999:
            errors.append(f"{year}: replay set agreement below 99.9%")

    pilot_sample_map = {_identity(row): row for row in pilot_samples}
    replay_sample_map = {_identity(row): row for row in replay_samples}
    sample_sets = _agreement(set(pilot_sample_map), set(replay_sample_map))
    if sample_sets["jaccard"] != 1.0:
        errors.append("replay metadata sample identity set differs from pilot")
    matches = 0
    comparisons = 0
    field_matches: dict[str, int] = defaultdict(int)
    field_totals: dict[str, int] = defaultdict(int)
    comparison_modes: dict[str, int] = defaultdict(int)
    for identity in sorted(set(pilot_sample_map) & set(replay_sample_map)):
        expected = pilot_sample_map[identity]
        observed = replay_sample_map[identity]
        for field in COMPARISON_FIELDS:
            comparisons += 1
            field_totals[field] += 1
            expected_value = _value(expected, field)
            observed_value = _value(observed, field)
            matched = _normalized(expected_value, field) == _normalized(observed_value, field)
            if (
                not matched
                and field == "abstract"
                and math_rendering_equivalent_for_replay(expected_value, observed_value)
            ):
                matched = True
                comparison_modes["math_rendering_equivalent"] += 1
            if matched:
                matches += 1
                field_matches[field] += 1
    field_agreement = matches / comparisons if comparisons else 0.0
    if field_agreement < 0.995:
        errors.append("replay metadata field agreement below 99.5%")

    return {
        "status": "PASS" if not errors else "FAIL",
        "venue_id": venue_id,
        "set_agreement": overall["jaccard"],
        "field_agreement": field_agreement,
        "overall": overall,
        "per_year": per_year,
        "sample_identity_agreement": sample_sets,
        "field_comparison": {
            "samples": len(set(pilot_sample_map) & set(replay_sample_map)),
            "fields_per_sample": len(COMPARISON_FIELDS),
            "matches": matches,
            "total": comparisons,
            "by_field": {
                field: {
                    "matches": field_matches[field],
                    "total": field_totals[field],
                    "agreement": field_matches[field] / field_totals[field] if field_totals[field] else 0.0,
                }
                for field in COMPARISON_FIELDS
            },
            "comparison_modes": dict(sorted(comparison_modes.items())),
            "normalization": [
                "Unicode NFKC", "NBSP/whitespace collapse", "URL terminal slash",
                "DOI prefix/case normalization", "complete numeric date separator normalization",
                "comparison-only MathJax duplicate-token normalization for abstracts",
                "guarded MathJax/BibTeX math-surface equivalence: >=50 tokens, sequence>=0.85, token-Jaccard>=0.90",
            ],
            "raw_values_modified": False,
        },
        "errors": errors,
    }

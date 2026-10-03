from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import CONFIG_ROOT, EXPECTED_CONFERENCES, EXPECTED_JOURNALS, EXPECTED_VENUES
from .io import atomic_json, load_json, sha256_file, utc_now
from .paths import LitDBPaths

REQUIRED_FIELDS = {
    "id", "canonical_name", "venue_type", "profiles", "active_from", "active_to",
    "crawl_from", "publisher_family", "aliases", "issn", "eissn",
    "conference_frequency", "calendar_model", "allowed_domains", "expected_access",
    "preferred_entry_hints", "main_track_rules", "exclude_rules", "status",
}


def source_files() -> list[Path]:
    return sorted((CONFIG_ROOT / "venues").glob("*.yml"))


def load_source_venues() -> list[dict[str, Any]]:
    venues: list[dict[str, Any]] = []
    for path in source_files():
        value = load_json(path)
        if not isinstance(value, list):
            raise ValueError(f"{path} must contain a list")
        venues.extend(_normalize_venue(item) for item in value)
    return venues


def _normalize_venue(source: dict[str, Any]) -> dict[str, Any]:
    venue = dict(source)
    venue_type = venue["venue_type"]
    venue.setdefault("aliases", [])
    venue.setdefault("issn", [])
    venue.setdefault("eissn", [])
    venue.setdefault("active_to", None)
    venue.setdefault("crawl_from", 2015)
    venue.setdefault("conference_frequency", "annual" if venue_type == "conference" else None)
    venue.setdefault("calendar_model", "annual_proceedings" if venue_type == "conference" else "issue")
    venue.setdefault("expected_access", "public_html")
    venue.setdefault("preferred_entry_hints", [])
    venue.setdefault("main_track_rules", ["official_main_research_track"] if venue_type == "conference" else ["official_research_article_types"])
    venue.setdefault("exclude_rules", [
        "editorial", "news", "correction", "retraction", "front_matter", "index",
        "committee", "keynote_abstract", "workshop", "poster_only", "demo_only",
        "tutorial", "doctoral_consortium",
    ])
    venue.setdefault("status", "UNSEEN")
    return venue


def validate_venues(venues: list[dict[str, Any]], strict: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    ids: list[str] = []
    for index, venue in enumerate(venues):
        missing = sorted(REQUIRED_FIELDS - set(venue))
        if missing:
            errors.append(f"venue[{index}] missing fields: {', '.join(missing)}")
        venue_id = venue.get("id")
        if not isinstance(venue_id, str) or not venue_id:
            errors.append(f"venue[{index}] invalid id")
        else:
            ids.append(venue_id)
        if venue.get("venue_type") not in {"conference", "journal"}:
            errors.append(f"{venue_id}: invalid venue_type")
        domains = venue.get("allowed_domains")
        if not isinstance(domains, list) or not domains or any(not isinstance(d, str) or "." not in d for d in domains):
            errors.append(f"{venue_id}: allowed_domains must be a non-empty domain list")
        if venue.get("crawl_from") != 2015:
            errors.append(f"{venue_id}: crawl_from must be 2015")
        if venue.get("status") != "UNSEEN":
            errors.append(f"{venue_id}: source status must be UNSEEN")
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        errors.append(f"duplicate ids: {', '.join(duplicates)}")
    conferences = sum(v.get("venue_type") == "conference" for v in venues)
    journals = sum(v.get("venue_type") == "journal" for v in venues)
    if strict:
        if len(venues) != EXPECTED_VENUES:
            errors.append(f"venue count {len(venues)} != {EXPECTED_VENUES}")
        if conferences != EXPECTED_CONFERENCES:
            errors.append(f"conference count {conferences} != {EXPECTED_CONFERENCES}")
        if journals != EXPECTED_JOURNALS:
            errors.append(f"journal count {journals} != {EXPECTED_JOURNALS}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "venue_count": len(venues),
        "conference_count": conferences,
        "journal_count": journals,
        "errors": errors,
    }


def install_registry(paths: LitDBPaths) -> dict[str, Any]:
    venues = load_source_venues()
    result = validate_venues(venues, strict=True)
    if result["errors"]:
        raise ValueError("; ".join(result["errors"]))
    paths.venues.mkdir(parents=True, exist_ok=True)
    for venue in venues:
        atomic_json(paths.venues / f"{venue['id']}.yml", venue)
    aggregate = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "venue_count": len(venues),
        "conference_count": result["conference_count"],
        "journal_count": result["journal_count"],
        "venue_ids": [venue["id"] for venue in venues],
        "source_files": [str(path.relative_to(CONFIG_ROOT)) for path in source_files()],
    }
    atomic_json(paths.registry / "venue_registry.yml", aggregate)
    aggregate["registry_sha256"] = sha256_file(paths.registry / "venue_registry.yml")
    return aggregate


def validate_runtime(paths: LitDBPaths, strict: bool = False) -> dict[str, Any]:
    venue_files = sorted(paths.venues.glob("*.yml"))
    venues: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in venue_files:
        try:
            venue = load_json(path)
            if venue.get("id") != path.stem:
                errors.append(f"{path}: filename/id mismatch")
            venues.append(venue)
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            errors.append(f"{path}: {exc}")
    result = validate_venues(venues, strict=strict)
    result["errors"] = errors + result["errors"]
    result["status"] = "PASS" if not result["errors"] else "FAIL"
    return result

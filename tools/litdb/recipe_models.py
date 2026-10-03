from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import load_json

REQUIRED = {
    "venue_id", "recipe_version", "venue_type", "allowed_domains", "entry_urls",
    "access_mode", "login_required", "manual_auth_checkpoint", "calendar_model",
    "start_year", "edition_or_issue_enumeration", "article_enumeration", "pagination",
    "metadata_extractors", "content_inclusion_rules", "content_exclusion_rules",
    "rate_limit", "retry_policy", "drift_signals", "validation_anchors", "redaction_rules",
}
EXTRACTOR_FIELDS = {"title", "authors", "abstract", "doi", "landing_url", "pdf_url", "publication_date", "document_type"}


def validate(path: Path, expected_venue: str | None = None) -> dict[str, Any]:
    value = load_json(path)
    errors: list[str] = []
    missing = sorted(REQUIRED - set(value))
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
    if expected_venue and value.get("venue_id") != expected_venue:
        errors.append("venue scope mismatch")
    extractors = value.get("metadata_extractors", {})
    missing_extractors = sorted(EXTRACTOR_FIELDS - set(extractors))
    if missing_extractors:
        errors.append(f"missing metadata extractors: {', '.join(missing_extractors)}")
    domains = value.get("allowed_domains", [])
    if not domains:
        errors.append("allowed_domains cannot be empty")
    for url in value.get("entry_urls", []):
        if not any(url.startswith(f"https://{domain}") or url.startswith(f"https://www.{domain}") for domain in domains):
            errors.append(f"entry URL outside allowlist: {url}")
    rate = value.get("rate_limit", {})
    if not isinstance(rate.get("max_pages_per_minute"), (int, float)) or rate.get("max_pages_per_minute", 0) <= 0:
        errors.append("rate_limit.max_pages_per_minute must be positive")
    if not value.get("drift_signals"):
        errors.append("drift_signals cannot be empty")
    enumeration = value.get("edition_or_issue_enumeration", {})
    articles = value.get("article_enumeration", {})
    has_primary = bool(enumeration.get("primary") or value.get("primary_path")) or bool(
        enumeration.get("archive_url")
        and (enumeration.get("volume_page_rule") or enumeration.get("archive_entry_rule"))
        and articles.get("primary_page_kind")
    ) or bool(
        value.get("canonical_expected_set", {}).get("components")
        and enumeration.get("archive_url")
        and value.get("research_listing_union_enumeration")
        and articles
    )
    if not has_primary:
        errors.append("primary enumeration path required")
    has_fallback = bool(
        enumeration.get("fallback")
        or any(str(key).startswith("fallback") for key in articles)
        or value.get("fallback_path")
        or value.get("fallbacks")
        or any(str(key).startswith("fallback") for key in value.get("pagination", {}))
    )
    if not has_fallback:
        errors.append("fallback enumeration path required")
    pagination = value.get("pagination", {})
    has_termination = bool(pagination.get("termination_condition")) or any(
        isinstance(item, dict) and bool(item.get("termination") or item.get("termination_signal"))
        for item in pagination.values()
    ) or bool(enumeration.get("termination"))
    if not has_termination:
        errors.append("pagination termination condition required")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "venue_id": value.get("venue_id")}

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from .db import initialize as initialize_db
from .io import atomic_json, load_json, sha256_file, utc_now
from .normalize import normalize_scalar
from .paths import LitDBPaths
from .security_scan import scan_path, scan_text
from .state import transition


SCHEMA_VERSION = "literature-metadata-staging-v1"
EXCLUSION_SCHEMA_VERSION = "literature-metadata-exclusion-v1"
REQUIRED_PROVENANCE_FIELDS = (
    "source_native_id",
    "title",
    "authors",
    "year",
    "document_type",
    "landing_url",
    "abstract",
    "doi",
    "publication_date",
    "pdf_discovery_status",
)
CATALOG_TABLES = (
    "source_item",
    "source_item_field",
    "canonical_work",
    "work_identifier",
    "work_version",
    "work_location",
    "field_provenance",
    "venue_year_coverage",
    "update_watermark",
    "repair_task",
)
ALLOWED_PDF_STATUSES = {
    "direct_public",
    "visible_url",
    "landing_page_action",
    "not_visible",
    "access_restricted",
    "not_available",
    "checked_missing",
    "metadata_only",
}
ALLOWED_MISSING_REASONS = {
    "not_present_on_official_page",
    "not_assigned",
    "not_visible",
    "access_restricted",
    "publisher_does_not_supply",
    "source_unavailable",
    "checked_missing",
}
ALLOWED_EXCLUSION_REASONS = {
    "advertisement",
    "blank_page",
    "call_for_papers",
    "copyright_form",
    "promotional_content",
    "front_matter",
    "editorial",
    "news",
    "correction",
    "erratum",
    "retraction",
    "table_of_contents",
    "society_information",
    "publication_information",
    "acknowledgment",
    "index",
    "information_for_authors",
    "masthead",
    "cover",
    "announcement",
    "obituary",
    "book_review",
    "non_research_content",
    "non_main_track",
    "outside_scope_year",
}


def _read_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row is not an object: {path}:{line_no}")
            yield line_no, value


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _hostname_allowed(
    url: str,
    allowed_domains: set[str],
    allowed_path_prefixes: dict[str, list[str]] | None = None,
) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower().rstrip(".")
    domains = {domain.lower().rstrip(".") for domain in allowed_domains}
    # Scoped hosts are separate approvals, so older hostname-only consumers
    # cannot accidentally treat them as permission for the entire domain.
    domains.update(domain.lower().rstrip(".") for domain in (allowed_path_prefixes or {}))
    if not any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains):
        return False
    for domain, prefixes in (allowed_path_prefixes or {}).items():
        domain = domain.lower().rstrip(".")
        if hostname != domain and not hostname.endswith(f".{domain}"):
            continue
        # Path-scoped approvals apply to the exact host, without URL forms
        # that a downstream HTTP client could normalize outside the scope.
        # A trailing slash means a path prefix; without it, the path must match
        # exactly (needed for narrow API resource endpoints).
        if hostname != domain or parsed.username or parsed.password:
            return False
        try:
            if parsed.port not in (None, 443):
                return False
        except ValueError:
            return False
        # Percent escapes in a query are routine (for example, encoded
        # Europe PMC search syntax). Keep path-scoped approvals strict: an
        # escaped path could be decoded or normalized outside its prefix.
        if "\\" in url or "%" in parsed.path or any(ord(char) < 32 for char in url):
            return False
        path_parts = parsed.path.split("/")
        if any(part in {".", ".."} for part in path_parts[1:]):
            return False
        if any(part == "" for part in path_parts[1:-1]):
            return False
        return any(
            parsed.path.startswith(prefix) if prefix.endswith("/") else parsed.path == prefix
            for prefix in prefixes
        )
    return True


def _iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _normalize_doi(value: Any) -> str | None:
    value = normalize_scalar(value)
    if not isinstance(value, str) or not value:
        return None
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^doi:\s*", "", value, flags=re.IGNORECASE).strip().lower()
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else None


def _title_signature(value: Any) -> str:
    value = normalize_scalar(value)
    if not isinstance(value, str):
        return ""
    return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def _author_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for author in value:
        if isinstance(author, str):
            name = normalize_scalar(author)
        elif isinstance(author, dict):
            name = normalize_scalar(author.get("name"))
        else:
            name = None
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _missing_reason(record: dict[str, Any], field: str) -> str | None:
    missing = record.get("missing_fields")
    if not isinstance(missing, dict):
        return None
    value = missing.get(field)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        reason = value.get("reason_code") or value.get("reason")
        return str(reason) if reason else None
    return None


def _provenance_map(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    provenance = record.get("field_provenance")
    if isinstance(provenance, dict):
        return {str(key): value for key, value in provenance.items() if isinstance(value, dict)}
    if isinstance(provenance, list):
        mapped: dict[str, dict[str, Any]] = {}
        for item in provenance:
            if isinstance(item, dict) and item.get("field"):
                mapped[str(item["field"])] = item
        return mapped
    return {}


def _record_identity(record: dict[str, Any]) -> str:
    return str(record.get("source_native_id") or record.get("source_item_id") or record.get("arnumber") or "").strip()


def _early_access_reclassification_allowed(record: dict[str, Any], expected_row: dict[str, Any]) -> bool:
    reuse = record.get("reuse_evidence")
    if isinstance(reuse, dict) and reuse.get("expected_enumeration_kind") == "early_access":
        return True
    if record.get("baseline_enumeration_kind") == "early_access":
        return True
    source_type = str(expected_row.get("source_document_type") or "").casefold()
    return bool(record.get("is_early_access")) and "early access" in source_type


def _forbidden_key_paths(value: Any, prefix: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in {
                "authorization",
                "cookie",
                "setcookie",
                "ctoken",
                "accesstoken",
                "sessiontoken",
                "sessionid",
                "csrftoken",
                "password",
                "userinfo",
                "purchaseoptions",
                "rightslink",
            }:
                findings.append(path)
            findings.extend(_forbidden_key_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_forbidden_key_paths(child, f"{prefix}[{index}]"))
    return findings


def _canonical_key(venue_id: str, record: dict[str, Any]) -> str:
    doi = _normalize_doi(record.get("doi"))
    return f"doi:{doi}" if doi else f"source:{venue_id}:{_record_identity(record)}"


def resolve_staging_file(run_root: Path, venue_id: str, explicit: Path | None = None) -> Path:
    if explicit:
        result = explicit.expanduser().resolve()
        if not result.is_file():
            raise ValueError(f"staging file does not exist: {result}")
        return result
    candidates = [
        run_root / "metadata_staging.jsonl.gz",
        run_root / "metadata_staging.jsonl",
        run_root / "staging.jsonl.gz",
        run_root / "staging.jsonl",
        run_root / "staging" / f"{venue_id}.jsonl.gz",
        run_root / "staging" / f"{venue_id}.jsonl",
    ]
    found = [path for path in candidates if path.is_file()]
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValueError(f"no metadata staging JSONL found under {run_root}")
    raise ValueError(f"multiple metadata staging files found: {[str(path) for path in found]}")


def resolve_exclusion_file(run_root: Path, explicit: Path | None = None) -> Path | None:
    if explicit:
        result = explicit.expanduser().resolve()
        if not result.is_file():
            raise ValueError(f"metadata exclusion file does not exist: {result}")
        return result
    candidates = [
        run_root / "metadata_exclusions.jsonl.gz",
        run_root / "metadata_exclusions.jsonl",
        run_root / "exclusions.jsonl.gz",
        run_root / "exclusions.jsonl",
    ]
    found = [path for path in candidates if path.is_file()]
    if len(found) == 1:
        return found[0]
    if not found:
        return None
    raise ValueError(f"multiple metadata exclusion files found: {[str(path) for path in found]}")


def load_expected(expected_root: Path) -> tuple[dict[str, dict[str, Any]], dict[int, set[str]], list[str]]:
    identities: dict[str, dict[str, Any]] = {}
    by_year: dict[int, set[str]] = defaultdict(set)
    errors: list[str] = []
    for path in sorted(expected_root.glob("*.jsonl.gz")) + sorted(expected_root.glob("*.jsonl")):
        try:
            path_year = int(path.name.split(".", 1)[0])
        except ValueError:
            continue
        for line_no, row in _read_jsonl(path):
            identity = _record_identity(row)
            if not identity:
                errors.append(f"expected row missing source identity: {path}:{line_no}")
                continue
            year = row.get("year", path_year)
            try:
                year = int(year)
            except (TypeError, ValueError):
                errors.append(f"expected row has invalid year: {path}:{line_no}")
                continue
            if identity in identities and identities[identity].get("year") != year:
                errors.append(f"expected identity crosses years: {identity}")
                continue
            identities[identity] = {"year": year, "row": row, "path": str(path)}
            by_year[year].add(identity)
    if not identities:
        errors.append(f"no expected identities found under {expected_root}")
    return identities, by_year, errors


def _validate_waterline_evidence(
    run_root: Path,
    venue_id: str,
    allowed_domains: set[str],
    observed_ids: set[str],
    expected_ids: set[str],
    *,
    required: bool,
    allowed_path_prefixes: dict[str, list[str]] | None = None,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    path = run_root / "waterline_evidence.json"
    errors: list[str] = []
    warnings: list[str] = []
    if not path.is_file():
        message = "waterline_evidence.json is required to prove current enumeration"
        (errors if required else warnings).append(message)
        return None, errors, warnings
    try:
        evidence = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"invalid waterline evidence: {exc}"], warnings
    if evidence.get("venue_id") != venue_id:
        errors.append("waterline evidence venue_id mismatch")
    if evidence.get("status") not in {"PASS", "NO_CHANGE", "UPDATED"}:
        errors.append("waterline evidence status is not accepted")
    if evidence.get("drift_status") not in {"NO_DRIFT", "WITHIN_THRESHOLD"}:
        errors.append("waterline evidence has unresolved recipe drift")
    if evidence.get("enumeration_complete") is not True:
        errors.append("waterline enumeration is not complete")
    observed_at = evidence.get("observed_at")
    if not _iso_timestamp(observed_at):
        errors.append("waterline observed_at is missing or invalid")
    else:
        observed_time = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observed_time.tzinfo is None:
            observed_time = observed_time.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - observed_time.astimezone(timezone.utc)).total_seconds() / 86400
        if age_days > 7:
            errors.append(f"waterline evidence is stale ({age_days:.1f} days old)")
        if age_days < -1:
            errors.append("waterline evidence timestamp is in the future")
    urls = evidence.get("source_urls")
    if not isinstance(urls, list) or not urls:
        errors.append("waterline evidence has no source_urls")
    elif any(not isinstance(url, str) or not _hostname_allowed(url, allowed_domains, allowed_path_prefixes) for url in urls):
        errors.append("waterline evidence contains a non-allowlisted source URL")
    set_hash = evidence.get("source_item_set_sha256")
    if not isinstance(set_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", set_hash):
        errors.append("waterline source_item_set_sha256 is missing or invalid")
    for field in ("new_ids", "missing_ids"):
        if not isinstance(evidence.get(field), list):
            errors.append(f"waterline evidence {field} must be a list")
    new_ids = {str(value) for value in evidence.get("new_ids", [])}
    missing_ids = {str(value) for value in evidence.get("missing_ids", [])}
    if new_ids - observed_ids:
        errors.append(f"{len(new_ids - observed_ids)} waterline new IDs are absent from staging")
    unexplained_missing = missing_ids & expected_ids
    if unexplained_missing:
        errors.append(f"waterline reports {len(unexplained_missing)} accepted IDs missing from the current source")
    count = evidence.get("current_source_item_count")
    if not isinstance(count, int) or count < 0:
        errors.append("waterline current_source_item_count is invalid")
    return evidence, errors, warnings


def _validate_record(
    record: dict[str, Any],
    line_no: int,
    venue_id: str,
    allowed_domains: set[str],
    allowed_path_prefixes: dict[str, list[str]] | None = None,
) -> list[str]:
    prefix = f"row {line_no}"
    errors: list[str] = []
    if record.get("schema_version") not in {None, SCHEMA_VERSION}:
        errors.append(f"{prefix}: unsupported schema_version")
    if record.get("venue_id") != venue_id:
        errors.append(f"{prefix}: venue_id mismatch")
    identity = _record_identity(record)
    if not identity:
        errors.append(f"{prefix}: source_native_id missing")
    title = normalize_scalar(record.get("title"))
    if not isinstance(title, str) or not title:
        errors.append(f"{prefix}: title missing")
    if not _author_names(record.get("authors")):
        errors.append(f"{prefix}: ordered authors missing")
    try:
        year = int(record.get("year"))
        if year < 2015 or year > datetime.now(timezone.utc).year:
            errors.append(f"{prefix}: year outside initialization range")
    except (TypeError, ValueError):
        errors.append(f"{prefix}: invalid year")
    if not normalize_scalar(record.get("document_type")):
        errors.append(f"{prefix}: document_type missing")
    if record.get("inclusion_decision") not in {"include", "included"}:
        errors.append(f"{prefix}: inclusion_decision must be include")
    for field in ("landing_url", "source_url"):
        value = record.get(field)
        if not isinstance(value, str) or not _hostname_allowed(value, allowed_domains, allowed_path_prefixes):
            errors.append(f"{prefix}: {field} is not an allowed official HTTPS URL")
    observed_at = record.get("observed_at") or record.get("source_observed_at")
    if not _iso_timestamp(observed_at):
        errors.append(f"{prefix}: observed_at missing or invalid")
    abstract = normalize_scalar(record.get("abstract"))
    abstract_reason = _missing_reason(record, "abstract")
    if not abstract and abstract_reason not in ALLOWED_MISSING_REASONS:
        errors.append(f"{prefix}: abstract missing without structured reason")
    publication_date = normalize_scalar(record.get("publication_date"))
    date_reason = _missing_reason(record, "publication_date")
    if not publication_date and date_reason not in ALLOWED_MISSING_REASONS:
        errors.append(f"{prefix}: publication_date missing without structured reason")
    doi = record.get("doi")
    if doi and not _normalize_doi(doi):
        errors.append(f"{prefix}: DOI is malformed")
    if not doi:
        doi_status = record.get("doi_status") or _missing_reason(record, "doi")
        if doi_status not in ALLOWED_MISSING_REASONS:
            errors.append(f"{prefix}: DOI missing without checked status")
    pdf_status = record.get("pdf_discovery_status")
    if pdf_status not in ALLOWED_PDF_STATUSES:
        errors.append(f"{prefix}: pdf_discovery_status missing or invalid")
    pdf_url = record.get("pdf_url")
    if pdf_url and not _hostname_allowed(str(pdf_url), allowed_domains, allowed_path_prefixes):
        errors.append(f"{prefix}: pdf_url is not an allowed official HTTPS URL")
    provenance = _provenance_map(record)
    for field in REQUIRED_PROVENANCE_FIELDS:
        item = provenance.get(field)
        if not item:
            errors.append(f"{prefix}: field provenance missing for {field}")
            continue
        source_url = item.get("source_url")
        observed = item.get("observed_at")
        if not isinstance(source_url, str) or not _hostname_allowed(source_url, allowed_domains, allowed_path_prefixes):
            errors.append(f"{prefix}: provenance source invalid for {field}")
        if not _iso_timestamp(observed):
            errors.append(f"{prefix}: provenance timestamp invalid for {field}")
    return errors


def _validate_exclusion(
    record: dict[str, Any],
    line_no: int,
    venue_id: str,
    allowed_domains: set[str],
    allowed_path_prefixes: dict[str, list[str]] | None = None,
) -> list[str]:
    prefix = f"exclusion row {line_no}"
    errors: list[str] = []
    if record.get("schema_version") not in {None, EXCLUSION_SCHEMA_VERSION}:
        errors.append(f"{prefix}: unsupported schema_version")
    if record.get("venue_id") != venue_id:
        errors.append(f"{prefix}: venue_id mismatch")
    if not _record_identity(record):
        errors.append(f"{prefix}: source_native_id missing")
    if not normalize_scalar(record.get("title")):
        errors.append(f"{prefix}: title missing")
    reason = record.get("exclusion_reason_code") or record.get("exclusion_reason")
    try:
        year = int(record.get("year"))
        if reason == "outside_scope_year":
            if year < 1900 or year > datetime.now(timezone.utc).year:
                errors.append(f"{prefix}: implausible outside-scope year")
        elif year < 2015 or year > datetime.now(timezone.utc).year:
            errors.append(f"{prefix}: year outside initialization range")
    except (TypeError, ValueError):
        errors.append(f"{prefix}: invalid year")
    if record.get("inclusion_decision") not in {"exclude", "excluded"}:
        errors.append(f"{prefix}: inclusion_decision must be exclude")
    if reason not in ALLOWED_EXCLUSION_REASONS:
        errors.append(f"{prefix}: exclusion_reason_code missing or invalid")
    for field in ("source_url", "landing_url"):
        value = record.get(field)
        if field == "landing_url" and not value:
            continue
        if not isinstance(value, str) or not _hostname_allowed(value, allowed_domains, allowed_path_prefixes):
            errors.append(f"{prefix}: {field} is not an allowed official HTTPS URL")
    observed_at = record.get("observed_at") or record.get("source_observed_at")
    if not _iso_timestamp(observed_at):
        errors.append(f"{prefix}: observed_at missing or invalid")
    provenance = _provenance_map(record)
    for field in ("source_native_id", "title", "year", "inclusion_decision", "exclusion_reason_code"):
        item = provenance.get(field)
        if not item:
            errors.append(f"{prefix}: field provenance missing for {field}")
            continue
        if not isinstance(item.get("source_url"), str) or not _hostname_allowed(item["source_url"], allowed_domains, allowed_path_prefixes):
            errors.append(f"{prefix}: provenance source invalid for {field}")
        if not _iso_timestamp(item.get("observed_at")):
            errors.append(f"{prefix}: provenance timestamp invalid for {field}")
    return errors


def validate_staging(
    paths: LitDBPaths,
    venue_id: str,
    run_root: Path,
    *,
    staging_file: Path | None = None,
    exclusion_file: Path | None = None,
    expected_root: Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    venue_path = paths.venues / f"{venue_id}.yml"
    if not venue_path.is_file():
        return {"status": "FAIL", "errors": [f"unknown venue: {venue_id}"]}
    venue = load_json(venue_path)
    allowed_domains = set(venue.get("allowed_domains", []))
    allowed_path_prefixes = venue.get("allowed_path_prefixes", {})
    if not isinstance(allowed_path_prefixes, dict) or any(
        not isinstance(domain, str) or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain)
        or not isinstance(prefixes, list) or not prefixes
        or any(not isinstance(prefix, str) or not prefix.startswith("/")
               or prefix == "/"
               or any(char in prefix for char in ("%", "\\", "?", "#"))
               or any(
                   part in {".", "..", ""}
                   for part in (
                       prefix.split("/")[1:-1]
                       if prefix.endswith("/")
                       else prefix.split("/")[1:]
                   )
               )
               for prefix in prefixes)
        for domain, prefixes in allowed_path_prefixes.items()
    ):
        return {"status": "FAIL", "errors": ["invalid allowed_path_prefixes configuration"]}
    try:
        staging_path = resolve_staging_file(run_root, venue_id, staging_file)
        exclusion_path = resolve_exclusion_file(run_root, exclusion_file)
    except ValueError as exc:
        return {"status": "FAIL", "errors": [str(exc)]}
    expected_root = (expected_root or (paths.home / "manifests" / "expected" / venue_id)).expanduser().resolve()
    expected, expected_by_year, errors = load_expected(expected_root)
    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    excluded_seen: dict[str, int] = {}
    observed_by_year: dict[int, set[str]] = defaultdict(set)
    excluded_by_year: dict[int, set[str]] = defaultdict(set)
    effective_expected_by_year: dict[int, set[str]] = {
        year: set(identities) for year, identities in expected_by_year.items()
    }
    year_reclassifications: list[dict[str, Any]] = []
    field_counts: Counter[str] = Counter()
    missing_reason_counts: Counter[str] = Counter()
    exclusion_reason_counts: Counter[str] = Counter()
    doi_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed_times: list[str] = []
    parse_errors = 0
    try:
        rows = list(_read_jsonl(staging_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "errors": [str(exc)], "staging_file": str(staging_path)}
    for line_no, record in rows:
        row_errors = _validate_record(record, line_no, venue_id, allowed_domains, allowed_path_prefixes)
        forbidden = _forbidden_key_paths(record)
        if forbidden:
            row_errors.append(f"row {line_no}: forbidden secret/session or non-metadata fields: {forbidden[:20]}")
        text_findings = scan_text(json.dumps(record, ensure_ascii=False))
        if text_findings:
            row_errors.append(f"row {line_no}: security scan findings: {[item['kind'] for item in text_findings[:20]]}")
        if row_errors:
            parse_errors += len(row_errors)
            if len(errors) < 500:
                errors.extend(row_errors[: 500 - len(errors)])
        identity = _record_identity(record)
        if identity:
            if identity in seen:
                errors.append(f"duplicate source_native_id {identity} at rows {seen[identity]} and {line_no}")
            else:
                seen[identity] = line_no
            try:
                year = int(record.get("year"))
                observed_by_year[year].add(identity)
                if identity in expected and expected[identity]["year"] != year:
                    expected_row = expected[identity]["row"]
                    if _early_access_reclassification_allowed(record, expected_row):
                        old_year = int(expected[identity]["year"])
                        effective_expected_by_year.setdefault(old_year, set()).discard(identity)
                        effective_expected_by_year.setdefault(year, set()).add(identity)
                        year_reclassifications.append({
                            "source_native_id": identity,
                            "baseline_year": old_year,
                            "detail_year": year,
                            "reason": "early_access_baseline_year_reclassified_by_fresh_official_detail",
                        })
                    else:
                        errors.append(f"source identity year differs from expected: {identity}")
            except (TypeError, ValueError):
                pass
        for field in (
            "title", "authors", "abstract", "document_type", "publication_date", "volume", "issue",
            "pages", "doi", "landing_url", "pdf_url", "pdf_discovery_status",
        ):
            value = record.get(field)
            if value not in (None, "", []):
                field_counts[field] += 1
            elif _missing_reason(record, field):
                missing_reason_counts[field] += 1
        observed_at = record.get("observed_at") or record.get("source_observed_at")
        if _iso_timestamp(observed_at):
            observed_times.append(observed_at)
        normalized_doi = _normalize_doi(record.get("doi"))
        if normalized_doi:
            doi_groups[normalized_doi].append(record)
        records.append(record)
    exclusions: list[dict[str, Any]] = []
    if exclusion_path:
        try:
            exclusion_rows = list(_read_jsonl(exclusion_path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            exclusion_rows = []
        for line_no, record in exclusion_rows:
            row_errors = _validate_exclusion(record, line_no, venue_id, allowed_domains, allowed_path_prefixes)
            forbidden = _forbidden_key_paths(record)
            if forbidden:
                row_errors.append(
                    f"exclusion row {line_no}: forbidden secret/session or non-metadata fields: {forbidden[:20]}"
                )
            text_findings = scan_text(json.dumps(record, ensure_ascii=False))
            if text_findings:
                row_errors.append(
                    f"exclusion row {line_no}: security scan findings: {[item['kind'] for item in text_findings[:20]]}"
                )
            if row_errors:
                parse_errors += len(row_errors)
                if len(errors) < 500:
                    errors.extend(row_errors[: 500 - len(errors)])
            identity = _record_identity(record)
            if identity:
                if identity in excluded_seen:
                    errors.append(
                        f"duplicate excluded source_native_id {identity} at rows {excluded_seen[identity]} and {line_no}"
                    )
                else:
                    excluded_seen[identity] = line_no
                if identity in seen:
                    errors.append(f"source_native_id appears in both metadata staging and exclusions: {identity}")
                try:
                    year = int(record.get("year"))
                    excluded_by_year[year].add(identity)
                    if identity in expected and expected[identity]["year"] != year:
                        expected_row = expected[identity]["row"]
                        if _early_access_reclassification_allowed(record, expected_row):
                            old_year = int(expected[identity]["year"])
                            effective_expected_by_year.setdefault(old_year, set()).discard(identity)
                            effective_expected_by_year.setdefault(year, set()).add(identity)
                            year_reclassifications.append({
                                "source_native_id": identity,
                                "baseline_year": old_year,
                                "detail_year": year,
                                "reason": "excluded_early_access_baseline_year_reclassified_by_fresh_official_detail",
                            })
                        else:
                            errors.append(f"excluded source identity year differs from expected: {identity}")
                except (TypeError, ValueError):
                    pass
            reason = record.get("exclusion_reason_code") or record.get("exclusion_reason")
            if reason:
                exclusion_reason_counts[str(reason)] += 1
            observed_at = record.get("observed_at") or record.get("source_observed_at")
            if _iso_timestamp(observed_at):
                observed_times.append(observed_at)
            exclusions.append(record)
    expected_ids = set(expected)
    included_ids = set(seen)
    excluded_ids = set(excluded_seen)
    observed_ids = included_ids | excluded_ids
    if year_reclassifications:
        warnings.append(
            f"reclassified {len(year_reclassifications)} Early Access identities from count-baseline years using fresh official detail metadata"
        )
    waterline, waterline_errors, waterline_warnings = _validate_waterline_evidence(
        run_root,
        venue_id,
        allowed_domains,
        observed_ids,
        expected_ids,
        required=strict,
        allowed_path_prefixes=allowed_path_prefixes,
    )
    errors.extend(waterline_errors)
    warnings.extend(waterline_warnings)
    missing_ids = sorted(expected_ids - observed_ids)
    extra_ids = sorted(observed_ids - expected_ids)
    if missing_ids:
        errors.append(f"staging is missing {len(missing_ids)} expected identities")
    if extra_ids:
        warnings.append(f"staging contains {len(extra_ids)} identities newer than or outside the accepted baseline")
    doi_clusters: list[dict[str, Any]] = []
    for doi, grouped in sorted(doi_groups.items()):
        if len(grouped) < 2:
            continue
        identities = sorted({_record_identity(record) for record in grouped})
        signatures = {_title_signature(record.get("title")) for record in grouped}
        cluster = {"doi": doi, "source_native_ids": identities, "title_signature_count": len(signatures)}
        doi_clusters.append(cluster)
        if len(signatures) > 1:
            errors.append(f"DOI {doi} maps to conflicting titles across {len(identities)} source identities")
    if doi_clusters and not any(cluster["title_signature_count"] > 1 for cluster in doi_clusters):
        warnings.append(f"clustered {len(doi_clusters)} repeated DOI version groups without fuzzy matching")
    if strict and parse_errors:
        errors.append(f"strict schema validation found {parse_errors} field errors")
    yearly: list[dict[str, Any]] = []
    for year in sorted(set(effective_expected_by_year) | set(observed_by_year) | set(excluded_by_year)):
        expected_set = effective_expected_by_year.get(year, set())
        included_set = observed_by_year.get(year, set())
        excluded_set = excluded_by_year.get(year, set())
        observed_set = included_set | excluded_set
        yearly.append({
            "year": year,
            "expected": len(expected_set),
            "included": len(included_set),
            "excluded": len(excluded_set),
            "observed": len(observed_set),
            "missing": len(expected_set - observed_set),
            "extra": len(observed_set - expected_set),
            "coverage": (len(expected_set & observed_set) / len(expected_set)) if expected_set else 1.0,
            "observed_set_sha256": hashlib.sha256("\n".join(sorted(observed_set)).encode()).hexdigest(),
        })
    total = len(records)
    field_coverage = {
        field: {
            "present": field_counts[field],
            "missing_with_reason": missing_reason_counts[field],
            "rate": field_counts[field] / total if total else 0.0,
        }
        for field in sorted(set(field_counts) | set(missing_reason_counts))
    }
    waterline_times = list(observed_times)
    if waterline and _iso_timestamp(waterline.get("observed_at")):
        waterline_times.append(waterline["observed_at"])
    result = {
        "schema_version": "metadata-staging-validation-v1",
        "status": "PASS" if not errors else "FAIL",
        "catalog_ready": not errors,
        "venue_id": venue_id,
        "run_root": str(run_root),
        "staging_file": str(staging_path),
        "staging_sha256": sha256_file(staging_path),
        "expected_root": str(expected_root),
        "records": total,
        "included_records": total,
        "excluded_records": len(exclusions),
        "accounted_records": total + len(exclusions),
        "expected_records": len(expected_ids),
        "matched_expected_records": len(expected_ids & observed_ids),
        "missing_expected_count": len(missing_ids),
        "extra_count": len(extra_ids),
        "missing_expected_sample": missing_ids[:100],
        "extra_sample": extra_ids[:100],
        "exclusion_file": str(exclusion_path) if exclusion_path else None,
        "exclusion_sha256": sha256_file(exclusion_path) if exclusion_path else None,
        "exclusion_reason_counts": dict(exclusion_reason_counts),
        "excluded_sample": sorted(excluded_ids)[:100],
        "year_reclassification_count": len(year_reclassifications),
        "year_reclassifications": year_reclassifications,
        "doi_version_clusters": doi_clusters,
        "yearly": yearly,
        "field_coverage": field_coverage,
        "waterline_observed_at": max(waterline_times, default=None),
        "waterline_evidence": waterline,
        "errors": errors,
        "warnings": warnings,
        "validated_at": utc_now(),
    }
    atomic_json(run_root / "staging_validation.json", result)
    return result


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _upsert(
    connection: sqlite3.Connection,
    table: str,
    key_fields: dict[str, Any],
    payload: dict[str, Any],
    now: str,
) -> str:
    if table not in CATALOG_TABLES and table not in {"venue", "venue_state", "thread_run", "merge_event"}:
        raise ValueError(f"unsupported catalog table: {table}")
    clauses: list[str] = []
    params: list[Any] = []
    for field, value in key_fields.items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", field):
            raise ValueError(f"unsafe payload key: {field}")
        clauses.append(f"json_extract(payload_json, '$.{field}') = ?")
        params.append(value)
    row = connection.execute(
        f"SELECT id, payload_json FROM {table} WHERE {' AND '.join(clauses)} ORDER BY id LIMIT 1",
        params,
    ).fetchone()
    encoded = _json_dump(payload)
    if row is None:
        connection.execute(
            f"INSERT INTO {table}(payload_json, created_at) VALUES(?, ?)",
            (encoded, now),
        )
        return "added"
    if row[1] == encoded:
        return "unchanged"
    connection.execute(f"UPDATE {table} SET payload_json=? WHERE id=?", (encoded, row[0]))
    return "updated"


def _normalized_authors(value: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return normalized
    for author in value:
        if isinstance(author, str):
            name = normalize_scalar(author)
            item: dict[str, Any] = {"name": name}
        elif isinstance(author, dict):
            name = normalize_scalar(author.get("name"))
            item = {"name": name}
            affiliations = author.get("affiliations") or author.get("affiliation")
            if isinstance(affiliations, list):
                item["affiliations"] = [normalize_scalar(entry) for entry in affiliations if normalize_scalar(entry)]
            native_id = author.get("native_id") or author.get("id")
            if native_id:
                item["native_id"] = normalize_scalar(native_id)
        else:
            continue
        if item.get("name"):
            normalized.append(item)
    return normalized


def _canonical_source_payload(venue_id: str, record: dict[str, Any], now: str) -> dict[str, Any]:
    identity = _record_identity(record)
    doi = _normalize_doi(record.get("doi"))
    observed_at = record.get("observed_at") or record.get("source_observed_at")
    payload = {
        "schema_version": "source-item-v1",
        "catalog_ready": True,
        "venue_id": venue_id,
        "source_item_id": f"{venue_id}:{identity}",
        "source_native_id": identity,
        "canonical_key": _canonical_key(venue_id, record),
        "year": int(record["year"]),
        "title": normalize_scalar(record.get("title")),
        "authors": _normalized_authors(record.get("authors")),
        "abstract": normalize_scalar(record.get("abstract")),
        "document_type": normalize_scalar(record.get("document_type")),
        "publication_date": normalize_scalar(record.get("publication_date")),
        "volume": normalize_scalar(record.get("volume")),
        "issue": normalize_scalar(record.get("issue")),
        "pages": normalize_scalar(record.get("pages")),
        "start_page": normalize_scalar(record.get("start_page")),
        "end_page": normalize_scalar(record.get("end_page")),
        "doi": doi,
        "landing_url": normalize_scalar(record.get("landing_url")),
        "pdf_url": normalize_scalar(record.get("pdf_url")),
        "pdf_discovery_status": record.get("pdf_discovery_status"),
        "is_early_access": bool(record.get("is_early_access")),
        "baseline_enumeration_kind": record.get("baseline_enumeration_kind"),
        "reuse_evidence": record.get("reuse_evidence", {}),
        "inclusion_decision": "include",
        "source_url": normalize_scalar(record.get("source_url")),
        "observed_at": observed_at,
        "missing_fields": record.get("missing_fields", {}),
        "venue_identity": record.get("venue_identity", {}),
        "updated_at": now,
    }
    return payload


def _exclusion_source_payload(venue_id: str, record: dict[str, Any], now: str) -> dict[str, Any]:
    identity = _record_identity(record)
    return {
        "schema_version": "source-item-v1",
        "catalog_ready": True,
        "venue_id": venue_id,
        "source_item_id": f"{venue_id}:{identity}",
        "source_native_id": identity,
        "canonical_key": None,
        "year": int(record["year"]),
        "title": normalize_scalar(record.get("title")),
        "authors": _normalized_authors(record.get("authors")),
        "document_type": normalize_scalar(record.get("document_type")),
        "landing_url": normalize_scalar(record.get("landing_url") or record.get("source_url")),
        "source_url": normalize_scalar(record.get("source_url")),
        "observed_at": record.get("observed_at") or record.get("source_observed_at"),
        "inclusion_decision": "exclude",
        "exclusion_reason_code": record.get("exclusion_reason_code") or record.get("exclusion_reason"),
        "exclusion_evidence": record.get("exclusion_evidence"),
        "is_early_access": bool(record.get("is_early_access")),
        "baseline_enumeration_kind": record.get("baseline_enumeration_kind"),
        "reuse_evidence": record.get("reuse_evidence", {}),
        "updated_at": now,
    }


def _version_key(venue_id: str, record: dict[str, Any]) -> str:
    if record.get("is_early_access"):
        return f"{venue_id}:early-access:{_record_identity(record)}"
    return ":".join(
        [venue_id, str(record.get("year") or ""), str(record.get("volume") or ""), str(record.get("issue") or ""), _record_identity(record)]
    )


def _load_records(path: Path) -> list[dict[str, Any]]:
    return [row for _, row in _read_jsonl(path)]


def _catalog_snapshot(connection: sqlite3.Connection, venue_id: str) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for table in CATALOG_TABLES:
        rows = connection.execute(
            f"SELECT payload_json FROM {table} WHERE json_extract(payload_json, '$.venue_id')=? ORDER BY payload_json",
            (venue_id,),
        ).fetchall()
        digest = hashlib.sha256()
        for row in rows:
            digest.update(row[0].encode("utf-8"))
            digest.update(b"\n")
        tables[table] = {"rows": len(rows), "sha256": digest.hexdigest()}
    return {"venue_id": venue_id, "tables": tables, "created_at": utc_now()}


def _transition_after_merge(paths: LitDBPaths, venue_id: str, run_root: Path, merge_receipt: Path) -> None:
    if not paths.state.is_file():
        return
    state = load_json(paths.state)
    current = state.get("venues", {}).get(venue_id, {}).get("state")
    if current == "COUNT_BASELINED":
        start = run_root / "bootstrap_start_receipt.json"
        atomic_json(start, {"venue_id": venue_id, "status": "BOOTSTRAP_RUNNING", "started_at": utc_now()})
        transition(paths, venue_id, "COUNT_BASELINED", "BOOTSTRAP_RUNNING", start)
        current = "BOOTSTRAP_RUNNING"
    if current in {"PARTIAL", "AUTH_REQUIRED", "SOURCE_BLOCKED"}:
        resume = run_root / "bootstrap_resume_receipt.json"
        atomic_json(resume, {"venue_id": venue_id, "status": "BOOTSTRAP_RESUMED", "resumed_at": utc_now()})
        transition(paths, venue_id, current, "BOOTSTRAP_RUNNING", resume)
        current = "BOOTSTRAP_RUNNING"
    if current == "BOOTSTRAP_RUNNING":
        transition(paths, venue_id, "BOOTSTRAP_RUNNING", "BOOTSTRAP_STAGED", merge_receipt)


def merge_venue(
    paths: LitDBPaths,
    venue_id: str,
    run_root: Path,
    *,
    staging_file: Path | None = None,
    exclusion_file: Path | None = None,
    expected_root: Path | None = None,
    verify_before_commit: bool = True,
) -> tuple[int, dict[str, Any]]:
    run_root = run_root.expanduser().resolve()
    initialize_db(paths.catalog)
    security_code, security_report = scan_path(paths, run_root)
    if security_code:
        return 4, {
            "status": "SECURITY_FAIL",
            "venue_id": venue_id,
            "security": security_report,
            "committed": False,
        }
    validation = validate_staging(
        paths,
        venue_id,
        run_root,
        staging_file=staging_file,
        exclusion_file=exclusion_file,
        expected_root=expected_root,
        strict=verify_before_commit,
    )
    if validation.get("status") != "PASS":
        return 2, {"status": "FAIL", "venue_id": venue_id, "validation": validation, "committed": False}
    staging_path = Path(validation["staging_file"])
    records = _load_records(staging_path)
    exclusion_path = Path(validation["exclusion_file"]) if validation.get("exclusion_file") else None
    exclusions = _load_records(exclusion_path) if exclusion_path else []
    venue = load_json(paths.venues / f"{venue_id}.yml")
    now = utc_now()
    counters: Counter[str] = Counter()
    connection = sqlite3.connect(paths.catalog, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        counters[f"venue:{_upsert(connection, 'venue', {'venue_id': venue_id}, {'venue_id': venue_id, **venue, 'updated_at': now}, now)}"] += 1
        for record in records:
            identity = _record_identity(record)
            canonical_key = _canonical_key(venue_id, record)
            source = _canonical_source_payload(venue_id, record, now)
            result = _upsert(connection, "source_item", {"venue_id": venue_id, "source_native_id": identity}, source, now)
            counters[f"source_item:{result}"] += 1
            canonical = {
                "schema_version": "canonical-work-v1",
                "canonical_key": canonical_key,
                "venue_id": venue_id,
                "title": source["title"],
                "authors": source["authors"],
                "abstract": source["abstract"],
                "publication_year": source["year"],
                "document_type": source["document_type"],
                "primary_source_item_id": source["source_item_id"],
                "doi": source["doi"],
                "updated_at": now,
            }
            existing_canonical_row = connection.execute(
                "SELECT payload_json FROM canonical_work WHERE json_extract(payload_json, '$.canonical_key')=? ORDER BY id LIMIT 1",
                (canonical_key,),
            ).fetchone()
            if existing_canonical_row:
                existing_canonical = json.loads(existing_canonical_row[0])
                if _title_signature(existing_canonical.get("title")) != _title_signature(canonical.get("title")):
                    raise ValueError(f"canonical identity conflict for {canonical_key}: title signatures differ")
            result = _upsert(connection, "canonical_work", {"canonical_key": canonical_key}, canonical, now)
            counters[f"canonical_work:{result}"] += 1
            identifiers = [("source-native", identity)]
            if source["doi"]:
                identifiers.append(("doi", source["doi"]))
            for scheme, value in identifiers:
                identifier = {
                    "venue_id": venue_id,
                    "canonical_key": canonical_key,
                    "scheme": scheme,
                    "value": value,
                    "source_item_id": source["source_item_id"],
                    "updated_at": now,
                }
                result = _upsert(connection, "work_identifier", {"canonical_key": canonical_key, "scheme": scheme, "value": value}, identifier, now)
                counters[f"work_identifier:{result}"] += 1
            version = {
                "venue_id": venue_id,
                "canonical_key": canonical_key,
                "version_key": _version_key(venue_id, record),
                "source_item_id": source["source_item_id"],
                "year": source["year"],
                "volume": source["volume"],
                "issue": source["issue"],
                "is_early_access": bool(record.get("is_early_access")),
                "publication_date": source["publication_date"],
                "observed_at": source["observed_at"],
                "updated_at": now,
            }
            result = _upsert(connection, "work_version", {"version_key": version["version_key"]}, version, now)
            counters[f"work_version:{result}"] += 1
            locations = [("landing", source["landing_url"], "visible")]
            if source["pdf_url"]:
                locations.append(("pdf", source["pdf_url"], source["pdf_discovery_status"]))
            for location_type, url, access_status in locations:
                location = {
                    "venue_id": venue_id,
                    "canonical_key": canonical_key,
                    "source_item_id": source["source_item_id"],
                    "location_type": location_type,
                    "url": url,
                    "access_status": access_status,
                    "observed_at": source["observed_at"],
                    "updated_at": now,
                }
                result = _upsert(connection, "work_location", {"canonical_key": canonical_key, "location_type": location_type, "url": url}, location, now)
                counters[f"work_location:{result}"] += 1
            provenance = _provenance_map(record)
            for field_name, value in source.items():
                if field_name in {"schema_version", "catalog_ready", "updated_at", "missing_fields", "venue_identity"}:
                    continue
                field_payload = {
                    "venue_id": venue_id,
                    "source_native_id": identity,
                    "source_item_id": source["source_item_id"],
                    "field_name": field_name,
                    "value": value,
                    "updated_at": now,
                }
                result = _upsert(connection, "source_item_field", {"venue_id": venue_id, "source_native_id": identity, "field_name": field_name}, field_payload, now)
                counters[f"source_item_field:{result}"] += 1
            for field_name, item in provenance.items():
                provenance_payload = {
                    "venue_id": venue_id,
                    "source_native_id": identity,
                    "source_item_id": source["source_item_id"],
                    "field_name": field_name,
                    "source_url": item.get("source_url"),
                    "observed_at": item.get("observed_at"),
                    "method": item.get("method") or item.get("source_kind"),
                    "reuse_status": item.get("reuse_status"),
                    "status": item.get("status", "present" if source.get(field_name) not in (None, "", []) else "missing"),
                    "updated_at": now,
                }
                result = _upsert(
                    connection,
                    "field_provenance",
                    {"venue_id": venue_id, "source_native_id": identity, "field_name": field_name},
                    provenance_payload,
                    now,
                )
                counters[f"field_provenance:{result}"] += 1
            for missing_field, details in source["missing_fields"].items():
                reason = details if isinstance(details, str) else details.get("reason_code") or details.get("reason")
                task = {
                    "venue_id": venue_id,
                    "source_native_id": identity,
                    "field_name": missing_field,
                    "status": "OPEN",
                    "reason_code": reason,
                    "source_url": source["landing_url"],
                    "created_from_run": str(run_root),
                    "updated_at": now,
                }
                result = _upsert(connection, "repair_task", {"venue_id": venue_id, "source_native_id": identity, "field_name": missing_field}, task, now)
                counters[f"repair_task:{result}"] += 1
        for record in exclusions:
            identity = _record_identity(record)
            source = _exclusion_source_payload(venue_id, record, now)
            result = _upsert(
                connection,
                "source_item",
                {"venue_id": venue_id, "source_native_id": identity},
                source,
                now,
            )
            counters[f"source_item_excluded:{result}"] += 1
            for field_name, value in source.items():
                if field_name in {"schema_version", "catalog_ready", "updated_at", "reuse_evidence"}:
                    continue
                field_payload = {
                    "venue_id": venue_id,
                    "source_native_id": identity,
                    "source_item_id": source["source_item_id"],
                    "field_name": field_name,
                    "value": value,
                    "updated_at": now,
                }
                result = _upsert(
                    connection,
                    "source_item_field",
                    {"venue_id": venue_id, "source_native_id": identity, "field_name": field_name},
                    field_payload,
                    now,
                )
                counters[f"source_item_field_excluded:{result}"] += 1
            for field_name, item in _provenance_map(record).items():
                provenance_payload = {
                    "venue_id": venue_id,
                    "source_native_id": identity,
                    "source_item_id": source["source_item_id"],
                    "field_name": field_name,
                    "source_url": item.get("source_url"),
                    "observed_at": item.get("observed_at"),
                    "method": item.get("method") or item.get("source_kind"),
                    "reuse_status": item.get("reuse_status"),
                    "status": item.get("status", "excluded"),
                    "updated_at": now,
                }
                result = _upsert(
                    connection,
                    "field_provenance",
                    {"venue_id": venue_id, "source_native_id": identity, "field_name": field_name},
                    provenance_payload,
                    now,
                )
                counters[f"field_provenance_excluded:{result}"] += 1
        for yearly in validation["yearly"]:
            coverage = {
                "venue_id": venue_id,
                "year": yearly["year"],
                "expected": yearly["expected"],
                "observed": yearly["observed"],
                "included": yearly["included"],
                "excluded": yearly["excluded"],
                "missing": yearly["missing"],
                "extra": yearly["extra"],
                "coverage": yearly["coverage"],
                "observed_set_sha256": yearly["observed_set_sha256"],
                "staging_sha256": validation["staging_sha256"],
                "updated_at": now,
            }
            result = _upsert(connection, "venue_year_coverage", {"venue_id": venue_id, "year": yearly["year"]}, coverage, now)
            counters[f"venue_year_coverage:{result}"] += 1
        all_accounted = records + exclusions
        latest_year = max((int(record["year"]) for record in all_accounted), default=None)
        latest_item = max(
            all_accounted,
            key=lambda row: str(row.get("observed_at") or row.get("source_observed_at") or ""),
            default=None,
        )
        watermark = {
            "venue_id": venue_id,
            "mode": "initialize",
            "last_successful_fingerprint_at": validation["waterline_observed_at"],
            "last_successful_delta_at": now,
            "last_seen_source_date": normalize_scalar(latest_item.get("publication_date")) if latest_item else None,
            "last_seen_item_id": _record_identity(latest_item) if latest_item else None,
            "latest_year": latest_year,
            "overlap_days": 14,
            "staging_sha256": validation["staging_sha256"],
            "updated_at": now,
        }
        result = _upsert(connection, "update_watermark", {"venue_id": venue_id}, watermark, now)
        counters[f"update_watermark:{result}"] += 1
        venue_state = {
            "venue_id": venue_id,
            "state": "MERGED_PENDING_RECONCILE",
            "run_root": str(run_root),
            "staging_sha256": validation["staging_sha256"],
            "exclusion_sha256": validation.get("exclusion_sha256"),
            "records": len(records),
            "excluded_records": len(exclusions),
            "updated_at": now,
        }
        _upsert(connection, "venue_state", {"venue_id": venue_id}, venue_state, now)
        receipt_path = run_root / "thread_receipt.json"
        if receipt_path.is_file():
            receipt = load_json(receipt_path)
            thread_id = receipt.get("thread_id") or receipt.get("actual_thread_id") or run_root.name
            thread_payload = {"thread_id": thread_id, "venue_id": venue_id, "role": "venue-bootstrap", "receipt": receipt, "updated_at": now}
            _upsert(connection, "thread_run", {"thread_id": thread_id}, thread_payload, now)
        event_id = hashlib.sha256(
            f"{venue_id}\n{run_root}\n{validation['staging_sha256']}\n{validation.get('exclusion_sha256') or ''}".encode()
        ).hexdigest()
        merge_event = {
            "event_id": event_id,
            "venue_id": venue_id,
            "mode": "initialize",
            "run_root": str(run_root),
            "staging_file": str(staging_path),
            "exclusion_file": str(exclusion_path) if exclusion_path else None,
            "staging_sha256": validation["staging_sha256"],
            "exclusion_sha256": validation.get("exclusion_sha256"),
            "records": len(records),
            "excluded_records": len(exclusions),
            "counters": dict(counters),
            "merged_at": now,
        }
        result = _upsert(connection, "merge_event", {"event_id": event_id}, merge_event, now)
        counters[f"merge_event:{result}"] += 1
        if verify_before_commit:
            count = connection.execute(
                "SELECT COUNT(*) FROM source_item WHERE json_extract(payload_json, '$.venue_id')=?",
                (venue_id,),
            ).fetchone()[0]
            if count < validation["expected_records"]:
                raise ValueError(f"pre-commit source_item count {count} is below expected {validation['expected_records']}")
        snapshot = _catalog_snapshot(connection, venue_id)
        connection.commit()
    except Exception as exc:
        connection.rollback()
        return 2, {"status": "ROLLBACK", "venue_id": venue_id, "error": str(exc), "committed": False}
    finally:
        connection.close()
    atomic_json(run_root / "catalog_snapshot.json", snapshot)
    receipt = {
        "schema_version": "metadata-merge-receipt-v1",
        "status": "PASS",
        "venue_id": venue_id,
        "mode": "initialize",
        "run_root": str(run_root),
        "staging_file": str(staging_path),
        "staging_sha256": validation["staging_sha256"],
        "records_merged": len(records),
        "records_excluded": len(exclusions),
        "counters": dict(counters),
        "snapshot": str(run_root / "catalog_snapshot.json"),
        "committed": True,
        "watermark_advanced": True,
        "merged_at": now,
    }
    merge_receipt = run_root / "merge_receipt.json"
    atomic_json(merge_receipt, receipt)
    try:
        _transition_after_merge(paths, venue_id, run_root, merge_receipt)
    except ValueError as exc:
        receipt["state_transition_warning"] = str(exc)
        atomic_json(merge_receipt, receipt)
    return 0, receipt


def _query_payloads(connection: sqlite3.Connection, table: str, venue_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"SELECT payload_json FROM {table} WHERE json_extract(payload_json, '$.venue_id')=? ORDER BY id",
        (venue_id,),
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


def reconcile_venue(
    paths: LitDBPaths,
    venue_id: str,
    *,
    run_root: Path | None = None,
    expected_root: Path | None = None,
    strict: bool = False,
) -> tuple[int, dict[str, Any]]:
    run_root = (run_root or (paths.home / "reports" / "reconcile" / venue_id)).expanduser().resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    expected_root = (expected_root or (paths.home / "manifests" / "expected" / venue_id)).expanduser().resolve()
    expected, expected_by_year, errors = load_expected(expected_root)
    connection = sqlite3.connect(paths.catalog)
    try:
        sources = _query_payloads(connection, "source_item", venue_id)
        canonicals = _query_payloads(connection, "canonical_work", venue_id)
        locations = _query_payloads(connection, "work_location", venue_id)
        provenances = _query_payloads(connection, "field_provenance", venue_id)
        watermarks = _query_payloads(connection, "update_watermark", venue_id)
    finally:
        connection.close()
    source_ids = [str(row.get("source_native_id") or "") for row in sources]
    included_sources = [row for row in sources if row.get("inclusion_decision") == "include"]
    excluded_sources = [row for row in sources if row.get("inclusion_decision") == "exclude"]
    duplicate_ids = sorted(identity for identity, count in Counter(source_ids).items() if identity and count > 1)
    expected_ids = set(expected)
    observed_ids = set(source_ids)
    missing = sorted(expected_ids - observed_ids)
    extra = sorted(observed_ids - expected_ids)
    if duplicate_ids:
        errors.append(f"catalog contains {len(duplicate_ids)} duplicate source identities")
    if missing:
        errors.append(f"catalog is missing {len(missing)} expected identities")
    if any(not row.get("catalog_ready") for row in sources):
        errors.append("catalog contains source_item rows not marked catalog_ready")
    canonical_keys = {row.get("canonical_key") for row in canonicals}
    missing_canonical = [
        row["source_native_id"] for row in included_sources if row.get("canonical_key") not in canonical_keys
    ]
    if missing_canonical:
        errors.append(f"{len(missing_canonical)} source items lack a canonical work")
    landing_by_key = {row.get("canonical_key") for row in locations if row.get("location_type") == "landing"}
    missing_landing = [
        row["source_native_id"] for row in included_sources if row.get("canonical_key") not in landing_by_key
    ]
    if missing_landing:
        errors.append(f"{len(missing_landing)} source items lack a landing location")
    provenance_keys = {(row.get("source_native_id"), row.get("field_name")) for row in provenances}
    provenance_gaps = [
        {"source_native_id": row.get("source_native_id"), "field": field}
        for row in included_sources
        for field in REQUIRED_PROVENANCE_FIELDS
        if (row.get("source_native_id"), field) not in provenance_keys
    ]
    if provenance_gaps:
        errors.append(f"{len(provenance_gaps)} required field provenance entries are missing")
    exclusion_provenance_gaps = [
        {"source_native_id": row.get("source_native_id"), "field": field}
        for row in excluded_sources
        for field in ("source_native_id", "title", "year", "inclusion_decision", "exclusion_reason_code")
        if (row.get("source_native_id"), field) not in provenance_keys
    ]
    if exclusion_provenance_gaps:
        errors.append(f"{len(exclusion_provenance_gaps)} exclusion provenance entries are missing")
    invalid_exclusions = [
        row.get("source_native_id")
        for row in excluded_sources
        if row.get("exclusion_reason_code") not in ALLOWED_EXCLUSION_REASONS
    ]
    if invalid_exclusions:
        errors.append(f"{len(invalid_exclusions)} excluded source items lack a valid exclusion reason")
    if not watermarks:
        errors.append("update watermark is missing")
    elif not watermarks[-1].get("last_successful_fingerprint_at"):
        errors.append("update watermark lacks a successful fingerprint time")
    field_coverage: dict[str, dict[str, Any]] = {}
    for field in ("title", "authors", "abstract", "publication_date", "doi", "landing_url", "pdf_url"):
        present = sum(row.get(field) not in (None, "", []) for row in included_sources)
        reasoned = sum(bool(_missing_reason(row, field)) for row in included_sources)
        field_coverage[field] = {
            "present": present,
            "missing_with_reason": reasoned,
            "rate": present / len(included_sources) if included_sources else 0.0,
        }
    yearly: list[dict[str, Any]] = []
    observed_by_year: dict[int, set[str]] = defaultdict(set)
    included_by_year: dict[int, set[str]] = defaultdict(set)
    excluded_by_year: dict[int, set[str]] = defaultdict(set)
    effective_expected_by_year: dict[int, set[str]] = {
        year: set(identities) for year, identities in expected_by_year.items()
    }
    year_reclassifications: list[dict[str, Any]] = []
    for row in sources:
        try:
            year = int(row["year"])
            identity = str(row["source_native_id"])
            observed_by_year[year].add(identity)
            if row.get("inclusion_decision") == "exclude":
                excluded_by_year[year].add(identity)
            else:
                included_by_year[year].add(identity)
            if identity in expected and expected[identity]["year"] != year:
                if _early_access_reclassification_allowed(row, expected[identity]["row"]):
                    old_year = int(expected[identity]["year"])
                    effective_expected_by_year.setdefault(old_year, set()).discard(identity)
                    effective_expected_by_year.setdefault(year, set()).add(identity)
                    year_reclassifications.append({
                        "source_native_id": identity,
                        "baseline_year": old_year,
                        "detail_year": year,
                        "reason": "early_access_baseline_year_reclassified_by_fresh_official_detail",
                    })
                else:
                    errors.append(f"catalog source identity year differs from expected: {identity}")
        except (KeyError, TypeError, ValueError):
            errors.append("catalog source row has invalid year")
    for year in sorted(set(effective_expected_by_year) | set(observed_by_year)):
        expected_set = effective_expected_by_year.get(year, set())
        observed_set = observed_by_year.get(year, set())
        yearly.append({
            "year": year,
            "expected": len(expected_set),
            "observed": len(observed_set),
            "included": len(included_by_year.get(year, set())),
            "excluded": len(excluded_by_year.get(year, set())),
            "missing": len(expected_set - observed_set),
            "extra": len(observed_set - expected_set),
            "coverage": len(expected_set & observed_set) / len(expected_set) if expected_set else 1.0,
        })
    if strict and len(sources) < len(expected):
        errors.append("strict reconciliation source count is below baseline")
    report = {
        "schema_version": "venue-coverage-report-v1",
        "status": "PASS" if not errors else "FAIL",
        "venue_id": venue_id,
        "catalog_ready": not errors,
        "expected_records": len(expected),
        "catalog_source_items": len(sources),
        "included_source_items": len(included_sources),
        "excluded_source_items": len(excluded_sources),
        "canonical_works": len(canonicals),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "duplicate_count": len(duplicate_ids),
        "provenance_gap_count": len(provenance_gaps),
        "exclusion_provenance_gap_count": len(exclusion_provenance_gaps),
        "year_reclassification_count": len(year_reclassifications),
        "year_reclassifications": year_reclassifications,
        "field_coverage": field_coverage,
        "yearly": yearly,
        "watermark": watermarks[-1] if watermarks else None,
        "errors": errors,
        "reconciled_at": utc_now(),
    }
    atomic_json(run_root / "venue_coverage_report.json", report)
    _write_jsonl(run_root / "venue_gap_manifest.jsonl", ({"venue_id": venue_id, "source_native_id": identity} for identity in missing))
    _write_jsonl(run_root / "venue_extra_manifest.jsonl", ({"venue_id": venue_id, "source_native_id": identity} for identity in extra))
    repair_plan = {
        "venue_id": venue_id,
        "status": "NO_REPAIR_NEEDED" if not errors else "REPAIR_REQUIRED",
        "missing_expected": len(missing),
        "duplicate_ids": duplicate_ids[:100],
        "provenance_gaps": provenance_gaps[:100],
        "next_actions": errors,
        "created_at": utc_now(),
    }
    atomic_json(run_root / "venue_repair_plan.json", repair_plan)
    if errors:
        return 1, report
    receipt = run_root / "reconcile_receipt.json"
    atomic_json(receipt, {"venue_id": venue_id, "status": "PASS", "report": str(run_root / "venue_coverage_report.json"), "reconciled_at": utc_now()})
    if paths.state.is_file():
        state = load_json(paths.state)
        current = state.get("venues", {}).get(venue_id, {}).get("state")
        try:
            if current == "BOOTSTRAP_STAGED":
                dispatch = run_root / "reconcile_start_receipt.json"
                atomic_json(dispatch, {"venue_id": venue_id, "status": "RECONCILING", "started_at": utc_now()})
                transition(paths, venue_id, "BOOTSTRAP_STAGED", "RECONCILING", dispatch)
                current = "RECONCILING"
            if current == "RECONCILING":
                transition(paths, venue_id, "RECONCILING", "ACTIVE", receipt)
        except ValueError as exc:
            report["state_transition_warning"] = str(exc)
            atomic_json(run_root / "venue_coverage_report.json", report)
    return 0, report


def bootstrap_plan(paths: LitDBPaths, venue_id: str) -> dict[str, Any]:
    venue_path = paths.venues / f"{venue_id}.yml"
    if not venue_path.is_file():
        return {"status": "FAIL", "errors": [f"unknown venue: {venue_id}"]}
    expected_root = paths.home / "manifests" / "expected" / venue_id
    expected, by_year, errors = load_expected(expected_root)
    state = load_json(paths.state).get("venues", {}).get(venue_id, {}) if paths.state.is_file() else {}
    return {
        "status": "PASS" if not errors else "BLOCKED",
        "venue_id": venue_id,
        "mode": "initialize",
        "state": state,
        "expected_root": str(expected_root),
        "expected_records": len(expected),
        "yearly": [{"year": year, "expected": len(ids)} for year, ids in sorted(by_year.items())],
        "staging_schema_version": SCHEMA_VERSION,
        "required_provenance_fields": list(REQUIRED_PROVENANCE_FIELDS),
        "errors": errors,
        "created_at": utc_now(),
    }

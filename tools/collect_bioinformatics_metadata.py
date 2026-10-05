#!/usr/bin/env python3
"""Capture and stage Bioinformatics metadata observed in a normal OUP browser.

This program never requests an OUP URL. Its optional loopback form accepts a
small allowlisted JSON record copied from visible browser DOM metadata, stores
that record under a content hash, and appends an index row. ``collect`` turns
those captures into expected identities, provenance-complete staging,
exclusions, unresolved items and waterline evidence.

Capture envelope (JSON):
  {"schema_version":"bioinformatics-browser-capture-v1",
   "page_type":"archive|issue|advance|article", "source_url":"https://academic.oup.com/bioinformatics/...",
   "observed_at":"ISO-8601", "complete":true, "data":{...}}

Archive data contains year_links and issues. Issue/advance data contains
items with landing_url/title/DOI and only the authors preview, citation,
section, categories or paper-PDF link actually visible in that listing.
Listing data may also carry observed pagination evidence: the visible next-page
URL or an explicit observation that the page is terminal. For advance listings,
the saved chain must start at the official entry URL and reach a captured
terminal page; per-page completeness alone never closes the chain.
Article data contains citation_* values, repeated ordered citation_authors,
visible_publication_date, abstract text and an article document_type only when
the page exposes it. The capture server stores no HTML, cookies, headers,
tokens, or arbitrary input fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


VENUE_ID = "bioinformatics"
OUP_HOST = "academic.oup.com"
EUROPE_PMC_API_HOST = "www.ebi.ac.uk"
EUROPE_PMC_API_PATH = "/europepmc/webservices/rest/search"
EUROPE_PMC_HOST = "europepmc.org"
CROSSREF_API_HOST = "api.crossref.org"
CROSSREF_JOURNAL_PATH = "/journals/1367-4811/works"
CAPTURE_SCHEMA = "bioinformatics-browser-capture-v1"
STAGING_SCHEMA = "literature-metadata-staging-v1"
EXCLUSION_SCHEMA = "literature-metadata-exclusion-v1"
ARCHIVE_URL = "https://academic.oup.com/bioinformatics/issue-archive"
ADVANCE_URL = "https://academic.oup.com/bioinformatics/advance-articles"
SCOPE_START = 2015
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s<>\"']+", re.I)
YEAR_RE = re.compile(r"(?:19|20)\d{2}")

EXCLUDED_TYPE_REASONS = (
    (re.compile(r"\beditorial\b"), "editorial"),
    (re.compile(r"\bcorrections?\b|\bcorrigendum\b"), "correction"),
    (re.compile(r"\berratum\b"), "erratum"),
    (re.compile(r"\bretraction\b"), "retraction"),
    (re.compile(r"\bfront\s*matter\b|\bfrontmatter\b"), "front_matter"),
    (re.compile(r"\btable\s+of\s+contents\b"), "table_of_contents"),
    (re.compile(r"\bbook\s+review\b"), "book_review"),
    (re.compile(r"\bobituary\b"), "obituary"),
    (re.compile(r"\bannouncement\b"), "announcement"),
)
INCLUDED_TYPE_RE = re.compile(
    r"\b(original\s+(?:papers?|articles?)|research\s+articles?|"
    r"applications?\s+notes?|reviews?|review\s+articles?|research\s+supplements?)\b",
    re.I,
)
EXCLUDED_TITLE_PREFIXES = (
    (re.compile(r"^(?:editorial|editorial note)\s*[:—-]", re.I), "editorial"),
    (re.compile(r"^(?:correction|corrigendum)\s+(?:to|for)\b", re.I), "correction"),
    (re.compile(r"^erratum\s*[:—-]", re.I), "erratum"),
    (re.compile(r"^retraction\s*[:—-]", re.I), "retraction"),
    (re.compile(r"^(?:table of contents|cover|obituary)\s*$", re.I), "front_matter"),
)

CAPTURE_FIELDS = {
    "archive": {"year_links", "issues", "pagination", "directory"},
    "issue": {"year", "volume", "issue", "issue_state", "items", "pagination"},
    "advance": {"items", "pagination"},
    "article": {
        "citation_title", "citation_authors", "citation_doi", "citation_pmid",
        "citation_journal_title", "citation_volume", "citation_issue",
        "citation_publication_date", "citation_pdf_url", "visible_publication_date",
        "abstract", "document_type",
    },
}
ITEM_FIELDS = {"landing_url", "title", "authors_preview", "doi", "citation", "section", "categories", "pdf_url"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def clean_official_url(value: Any, *, allow_browse_by: bool = False) -> str | None:
    """Keep only a clean OUP URL; never retain auth, signature or tracking args."""
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.hostname != OUP_HOST or parsed.username or parsed.password:
        return None
    try:
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    if not parsed.path.startswith("/bioinformatics/") or any(ord(char) < 32 for char in parsed.path):
        return None
    query = ""
    if parsed.query:
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if allow_browse_by and pairs == [("browseBy", "volume")]:
            query = ""
        else:
            return None
    return urlunsplit(("https", OUP_HOST, parsed.path, query, ""))


PAGINATION_PARAM_RE = re.compile(r"^(?:page(?:number|no|index)?|page_no|page_index|offset|start|from)$", re.I)


def clean_listing_page_url(value: Any, *, expected_path: str | None = None) -> str | None:
    """Keep an observed OUP listing page and its harmless numeric page state."""
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.hostname != OUP_HOST or parsed.username or parsed.password or parsed.fragment:
        return None
    try:
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    if not parsed.path.startswith("/bioinformatics/") or any(ord(char) < 32 for char in parsed.path):
        return None
    if expected_path is not None and parsed.path != expected_path:
        return None
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    kept: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for key, value in pairs:
        key_folded = key.casefold()
        if key_folded == "browseby" and value.casefold() == "volume":
            continue
        if not PAGINATION_PARAM_RE.fullmatch(key) or not value.isdigit() or key_folded in seen_keys:
            return None
        seen_keys.add(key_folded)
        kept.append((key, value))
    query = urlencode(kept) if kept else ""
    return urlunsplit(("https", OUP_HOST, parsed.path, query, ""))


def normalize_doi(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = DOI_RE.search(value.strip())
    if not match:
        return None
    return match.group(0).rstrip(".,;:)]}").casefold()


def article_native_id(url: str) -> str | None:
    """Use OUP's terminal numeric article ID; use DOI only for advance-only URLs."""
    path = unquote(urlsplit(url).path)
    if not path.startswith("/bioinformatics/"):
        return None
    parts = [part for part in path.rstrip("/").split("/") if part]
    if len(parts) < 3 or parts[0] != "bioinformatics" or parts[1] not in {"article", "advance-article"}:
        return None
    terminal = parts[-1]
    if terminal.isdigit():
        return f"bioinformatics:{terminal}"
    doi = normalize_doi("/".join(parts[2:]))
    if doi:
        return f"bioinformatics:doi/{doi}"
    return None


def clean_europe_pmc_source_url(value: Any) -> str | None:
    """Validate an observed Europe PMC search request URL for provenance."""
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.hostname != EUROPE_PMC_API_HOST or parsed.path != EUROPE_PMC_API_PATH:
        return None
    if parsed.username or parsed.password or parsed.fragment:
        return None
    try:
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    if not parsed.query:
        return None
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    sensitive = re.compile(r"token|auth|signature|secret|session|cookie|credential|access.?key", re.I)
    if any(sensitive.search(key) for key, _ in pairs):
        return None
    return urlunsplit(("https", EUROPE_PMC_API_HOST, parsed.path, parsed.query, ""))


def clean_europe_pmc_pdf_url(value: Any) -> str | None:
    """Keep an actual Europe PMC PDF link only when its returned URL is scoped."""
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        return None
    try:
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    allowed = (
        parsed.hostname == EUROPE_PMC_HOST and parsed.path.startswith("/articles/")
    ) or (
        parsed.hostname == EUROPE_PMC_API_HOST
        and parsed.path.startswith("/europepmc/webservices/rest/")
        and parsed.path.endswith("/fullTextPDF")
    )
    if not allowed or not parsed.path or any(ord(char) < 32 for char in parsed.path):
        return None
    sensitive = re.compile(r"token|auth|signature|secret|session|cookie|credential|access.?key", re.I)
    if any(sensitive.search(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        return None
    return urlunsplit(("https", parsed.hostname or "", parsed.path, parsed.query, ""))


def _epmc_pdf_url(record: dict[str, Any]) -> str | None:
    values = record.get("pdf_links_as_supplied")
    if not isinstance(values, list):
        return None
    for item in values:
        if isinstance(item, str):
            raw_url = item
        elif isinstance(item, dict):
            style = item.get("documentStyle", item.get("document_style"))
            if style and str(style).casefold() != "pdf":
                continue
            raw_url = item.get("url") or item.get("value") or item.get("link")
        else:
            continue
        clean = clean_europe_pmc_pdf_url(raw_url)
        if clean:
            return clean
    return None


def _epmc_authors(record: dict[str, Any]) -> list[str]:
    values = record.get("authors")
    if not isinstance(values, list):
        return []
    ordered = sorted(
        (item for item in values if isinstance(item, dict)),
        key=lambda item: item.get("order") if isinstance(item.get("order"), int) else 1_000_000,
    )
    names = []
    for item in ordered:
        name = _clean_string(item.get("name") or item.get("full_name_as_supplied"), limit=500)
        if name:
            names.append(name)
    return names


def _epmc_date(record: dict[str, Any]) -> tuple[str | None, str | None]:
    dates = record.get("dates_as_supplied")
    if not isinstance(dates, dict):
        return None, None
    preference = (
        "firstPublicationDate",
        "journalFirstPublicationDate",
        "electronicPublicationDate",
        "publicationDate",
        "firstIndexDate",
    )
    keys = [key for key in preference if key in dates]
    keys.extend(key for key in dates if key not in keys)
    for key in keys:
        entry = dates.get(key)
        if isinstance(entry, dict):
            date_value = _clean_string(entry.get("value"), limit=100)
            precision = _clean_string(entry.get("precision"), limit=50)
        else:
            date_value, precision = _clean_string(entry, limit=100), None
        if date_value:
            return date_value, precision
    return None, None


def _normalized_match_title(value: Any) -> str:
    title = normalize_space(value or "").casefold()
    return title.rstrip(".").rstrip()


def _load_europe_pmc_records(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    records: list[dict[str, Any]] = []
    for row in read_jsonl(path):
        journal = row.get("journal") if isinstance(row.get("journal"), dict) else {}
        issn_values = {str(value).replace("-", "").casefold() for value in journal.get("issn_values", []) if isinstance(value, str)}
        eissn = str(journal.get("eissn") or "").replace("-", "").casefold()
        if row.get("matches_target_eissn") is not True and eissn != "13674811" and "13674811" not in issn_values:
            continue
        exact_identity_scope_candidate = (
            row.get("needs_expected_identity_join_for_scope") is True
            and row.get("within_requested_year_window") is True
        )
        if (
            row.get("within_collection_scope") is not True
            and row.get("year_window_margin_only") is not True
            and not exact_identity_scope_candidate
        ):
            continue
        source_url = clean_europe_pmc_source_url(row.get("source_url"))
        if not source_url:
            continue
        observed_at = _iso_time(row.get("observed_at"))
        pmid = _clean_string(row.get("pmid"), limit=100)
        doi = normalize_doi(row.get("doi"))
        publication_date, publication_date_precision = _epmc_date(row)
        records.append({
            "source_url": source_url,
            "observed_at": observed_at,
            "doi": doi,
            "pmid": pmid,
            "title": _clean_string(row.get("title"), limit=2000),
            "authors": _epmc_authors(row),
            "abstract": _clean_string(row.get("abstract"), limit=100000),
            "publication_date": publication_date,
            "publication_date_precision": publication_date_precision,
            "issue_year": row.get("issue_year") if isinstance(row.get("issue_year"), int) else None,
            "pdf_url": _epmc_pdf_url(row),
            "volume": _clean_string(row.get("volume"), limit=100),
            "issue": _clean_string(row.get("issue"), limit=100),
            "pmcid": _clean_string(row.get("pmcid"), limit=100),
            "native_id": row.get("europepmc_native_id") if isinstance(row.get("europepmc_native_id"), dict) else None,
            "year_window_margin_only": row.get("year_window_margin_only") is True,
            "needs_expected_identity_join_for_scope": exact_identity_scope_candidate,
        })
    return records


def _match_europe_pmc(
    doi: str | None,
    pmid: str | None,
    records: list[dict[str, Any]],
    *,
    title: str | None = None,
    volume: str | None = None,
    issue: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None, dict[str, Any]]:
    doi_matches = [row for row in records if doi and row.get("doi") == doi]
    pmid_matches = [row for row in records if pmid and str(row.get("pmid")) == str(pmid)]
    candidate_map = {
        (row.get("doi"), row.get("pmid"), row.get("pmcid"), row.get("title"), row.get("volume"), row.get("issue"), row["source_url"]): row
        for row in [*doi_matches, *pmid_matches]
    }
    candidates = list(candidate_map.values())
    audit: dict[str, Any] = {
        "doi_candidate_count": len(doi_matches),
        "pmid_candidate_count": len(pmid_matches),
        "exact_identifier_candidate_count": len(candidates),
        "title_match_count": 0,
        "volume_match_count": 0,
        "issue_match_count": 0,
        "selected_candidate": None,
        "selection_rule": None,
        "metadata_discrepancies": [],
        "candidates": [
            {
                "doi": candidate.get("doi"),
                "pmid": candidate.get("pmid"),
                "pmcid": candidate.get("pmcid"),
                "native_id": candidate.get("native_id"),
                "title": candidate.get("title"),
                "volume": candidate.get("volume"),
                "issue": candidate.get("issue"),
                "issue_year": candidate.get("issue_year"),
                "year_window_margin_only": candidate.get("year_window_margin_only"),
                "needs_expected_identity_join_for_scope": candidate.get("needs_expected_identity_join_for_scope"),
                "source_url": candidate.get("source_url"),
                "selected": False,
            }
            for candidate in candidates
        ],
    }
    if not candidates:
        return None, None, None, audit

    safe_candidates = [
        row for row in candidates
        if (not doi or not row.get("doi") or row.get("doi") == doi)
        and (not pmid or not row.get("pmid") or str(row.get("pmid")) == str(pmid))
    ]
    if not safe_candidates:
        audit["selection_rule"] = "exact identifiers conflict"
        return None, None, "doi_pmid_identifier_conflict", audit

    selected_pool = safe_candidates
    selection_steps: list[str] = []
    if len(safe_candidates) == 1:
        selected_pool = safe_candidates
        only = selected_pool[0]
        if title and _normalized_match_title(only.get("title")) != _normalized_match_title(title):
            audit["metadata_discrepancies"].append({"field": "title", "oup_value": title, "europe_pmc_value": only.get("title")})
        if volume and only.get("volume") and normalize_space(str(only["volume"])).casefold() != normalize_space(str(volume)).casefold():
            audit["metadata_discrepancies"].append({"field": "volume", "oup_value": volume, "europe_pmc_value": only.get("volume")})
        if issue and only.get("issue") and normalize_space(str(only["issue"])).casefold() != normalize_space(str(issue)).casefold():
            audit["metadata_discrepancies"].append({"field": "issue", "oup_value": issue, "europe_pmc_value": only.get("issue")})
        selection_steps.append("unique exact identifier candidate; OUP metadata discrepancies retained")
    else:
        if title:
            exact_title = [row for row in selected_pool if _normalized_match_title(row.get("title")) == _normalized_match_title(title)]
            audit["title_match_count"] = len(exact_title)
            if exact_title:
                selected_pool = exact_title
                selection_steps.append("exact normalized title")
            else:
                audit["selection_rule"] = "duplicate exact identifiers without exact normalized title match"
                return None, None, "exact_identifier_title_conflict", audit

        if volume:
            volume_matches = [row for row in selected_pool if row.get("volume") and normalize_space(str(row["volume"])).casefold() == normalize_space(str(volume)).casefold()]
            audit["volume_match_count"] = len(volume_matches)
            if volume_matches:
                selected_pool = volume_matches
                selection_steps.append("exact volume")
            else:
                audit["selection_rule"] = "duplicate exact identifiers without exact volume match"
                return None, None, "exact_identifier_volume_conflict", audit
        if issue:
            issue_matches = [row for row in selected_pool if row.get("issue") and normalize_space(str(row["issue"])).casefold() == normalize_space(str(issue)).casefold()]
            audit["issue_match_count"] = len(issue_matches)
            if issue_matches:
                selected_pool = issue_matches
                selection_steps.append("exact issue")
            else:
                audit["selection_rule"] = "duplicate exact identifiers without exact issue match"
                return None, None, "exact_identifier_issue_conflict", audit

    if len(selected_pool) != 1:
        audit["selection_rule"] = "exact identifiers and OUP metadata remain ambiguous"
        return None, None, "ambiguous_exact_identifier_title_volume_issue_match", audit

    selected = selected_pool[0]
    for candidate_audit in audit["candidates"]:
        candidate_key = (candidate_audit.get("doi"), candidate_audit.get("pmid"), candidate_audit.get("pmcid"), candidate_audit.get("title"), candidate_audit.get("volume"), candidate_audit.get("issue"), candidate_audit.get("source_url"))
        selected_key = (selected.get("doi"), selected.get("pmid"), selected.get("pmcid"), selected.get("title"), selected.get("volume"), selected.get("issue"), selected.get("source_url"))
        candidate_audit["selected"] = candidate_key == selected_key
    matched_by = "doi+pmid" if doi and pmid and selected.get("doi") == doi and str(selected.get("pmid")) == str(pmid) else "doi" if doi and selected.get("doi") == doi else "pmid"
    if selected.get("year_window_margin_only"):
        selection_steps.append("2014 year-window-margin record matched to in-scope OUP source item")
    audit["selected_candidate"] = {
        "doi": selected.get("doi"),
        "pmid": selected.get("pmid"),
        "pmcid": selected.get("pmcid"),
        "native_id": selected.get("native_id"),
        "source_url": selected.get("source_url"),
    }
    audit["selection_rule"] = "; ".join(selection_steps) or "unique exact identifier match"
    return selected, matched_by, None, audit


def clean_crossref_request_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.hostname != CROSSREF_API_HOST or parsed.username or parsed.password or parsed.fragment:
        return None
    try:
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    if parsed.path != CROSSREF_JOURNAL_PATH and not re.fullmatch(r"/works/10\.1093/bioinformatics/[^/]+", parsed.path, re.I):
        return None
    sensitive = re.compile(r"token|auth|signature|secret|session|cookie|credential|access.?key", re.I)
    if any(sensitive.search(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        return None
    return urlunsplit(("https", CROSSREF_API_HOST, parsed.path, parsed.query, ""))


def _crossref_date(row: dict[str, Any]) -> tuple[str | None, str | None]:
    field = row.get("published-online")
    date_parts = field.get("date-parts") if isinstance(field, dict) else None
    if not isinstance(date_parts, list) or not date_parts or not isinstance(date_parts[0], list):
        return None, None
    values = date_parts[0]
    if not 1 <= len(values) <= 3 or any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        return None, None
    if not 1 <= values[0] <= 9999 or (len(values) >= 2 and not 1 <= values[1] <= 12) or (len(values) == 3 and not 1 <= values[2] <= 31):
        return None, None
    result = f"{values[0]:04d}"
    if len(values) >= 2:
        result += f"-{values[1]:02d}"
    if len(values) == 3:
        result += f"-{values[2]:02d}"
    return result, {1: "year", 2: "month", 3: "day"}[len(values)]


def _crossref_link_candidates(row: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    links = row.get("link")
    if not isinstance(links, list):
        return [], []
    vor: list[dict[str, str]] = []
    am: list[dict[str, str]] = []
    for link in links:
        if not isinstance(link, dict) or str(link.get("content-type", "")).casefold() != "application/pdf":
            continue
        version = str(link.get("content-version", "")).casefold()
        if version not in {"vor", "am"}:
            continue
        raw_url = link.get("URL")
        clean_url = clean_official_url(raw_url)
        if not clean_url:
            continue
        candidate = {
            "url": clean_url,
            "content_version": version,
            "content_type": str(link.get("content-type")),
            "intended_application": str(link.get("intended-application", "")),
        }
        if version == "vor":
            vor.append(candidate)
        else:
            am.append(candidate)
    return vor, am


def _load_crossref_records(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    records = []
    for row in read_jsonl(path):
        doi = normalize_doi(row.get("DOI"))
        source_url = clean_crossref_request_url(row.get("request_url"))
        observed_at = row.get("observed_at_utc")
        issns = {str(value).replace("-", "").casefold() for value in row.get("ISSN", []) if isinstance(value, str)}
        # The journal-ISSN endpoint can contain neighboring Oxford journals
        # (for example Bioinformatics Advances). Require the target DOI prefix
        # as well as the eISSN; exact OUP DOI matching happens later.
        if not doi or not doi.startswith("10.1093/bioinformatics/") or not source_url or "13674811" not in issns:
            continue
        observed_at = _iso_time(observed_at)
        vor_links, am_links = _crossref_link_candidates(row)
        title_values = row.get("title") if isinstance(row.get("title"), list) else []
        resource = row.get("resource") if isinstance(row.get("resource"), dict) else {}
        primary_resource = resource.get("primary") if isinstance(resource.get("primary"), dict) else {}
        licenses = row.get("license_entries") if isinstance(row.get("license_entries"), list) else row.get("license") if isinstance(row.get("license"), list) else []
        records.append({
            "doi": doi,
            "title": _clean_string(title_values[0] if title_values else None, limit=2000),
            "volume": _clean_string(row.get("volume"), limit=100),
            "issue": _clean_string(row.get("issue"), limit=100),
            "resource_url": clean_official_url(primary_resource.get("URL")),
            "source_url": source_url,
            "observed_at": observed_at,
            "online_date": _crossref_date(row)[0],
            "online_date_precision": _crossref_date(row)[1],
            "vor_pdf_candidates": vor_links,
            "am_pdf_candidates": am_links,
            "licenses": licenses,
            "crossref_type": _clean_string(row.get("type"), limit=100),
        })
    return records


def _match_crossref(
    doi: str | None,
    records: list[dict[str, Any]],
    *,
    title: str | None = None,
    volume: str | None = None,
    issue: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    candidates = [row for row in records if doi and row.get("doi") == doi]
    audit: dict[str, Any] = {
        "doi": doi,
        "candidate_count": len(candidates),
        "selected_candidate": None,
        "selection_rule": None,
        "candidates": [
            {
                "doi": row.get("doi"),
                "title": row.get("title"),
                "volume": row.get("volume"),
                "issue": row.get("issue"),
                "source_url": row.get("source_url"),
                "vor_pdf_candidates": row.get("vor_pdf_candidates", []),
                "am_pdf_candidates": row.get("am_pdf_candidates", []),
                "selected": False,
            }
            for row in candidates
        ],
    }
    if not candidates:
        return None, audit
    pool = candidates
    rules: list[str] = []
    if len(pool) > 1 and title:
        title_matches = [row for row in pool if _normalized_match_title(row.get("title")) == _normalized_match_title(title)]
        if title_matches:
            pool = title_matches
            rules.append("exact normalized title")
        else:
            audit["selection_rule"] = "duplicate DOI rows lack an exact normalized title match"
            return None, audit
    if len(pool) > 1 and volume:
        volume_matches = [row for row in pool if row.get("volume") and normalize_space(str(row["volume"])).casefold() == normalize_space(str(volume)).casefold()]
        if volume_matches:
            pool = volume_matches
            rules.append("exact volume")
        else:
            audit["selection_rule"] = "duplicate DOI rows lack an exact volume match"
            return None, audit
    if len(pool) > 1 and issue:
        issue_matches = [row for row in pool if row.get("issue") and normalize_space(str(row["issue"])).casefold() == normalize_space(str(issue)).casefold()]
        if issue_matches:
            pool = issue_matches
            rules.append("exact issue")
        else:
            audit["selection_rule"] = "duplicate DOI rows lack an exact issue match"
            return None, audit
    if len(pool) != 1:
        audit["selection_rule"] = "duplicate DOI rows remain ambiguous"
        return None, audit
    selected = pool[0]
    selected_index = next(index for index, row in enumerate(candidates) if row is selected)
    audit["candidates"][selected_index]["selected"] = True
    audit["selected_candidate"] = {"doi": selected["doi"], "source_url": selected["source_url"], "observed_at": selected["observed_at"]}
    audit["selection_rule"] = "; ".join(rules) or "unique exact DOI"
    return selected, audit


def _clean_string(value: Any, *, limit: int = 50000) -> str | None:
    if not isinstance(value, str):
        return None
    value = normalize_space(value)
    return value[:limit] if value else None


def _clean_string_list(value: Any, *, limit: int = 1000) -> list[str]:
    if not isinstance(value, list):
        return []
    return [cleaned for item in value[:limit] if (cleaned := _clean_string(item, limit=5000))]


def _iso_time(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("observed_at is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    return value


def _scope_year(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not SCOPE_START <= value <= datetime.now(timezone.utc).year:
        raise ValueError("year is outside the requested 2015-to-current scope")
    return value


def _sanitize_pagination(data: dict[str, Any], source_url: str) -> dict[str, Any] | None:
    raw = data.get("pagination")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"next_page_url": None, "terminal_observed": None, "invalid_evidence_shape": True}
    raw_next = raw.get("next_page_url")
    next_url = clean_listing_page_url(raw_next, expected_path=urlsplit(source_url).path) if raw_next is not None else None
    terminal_value = raw.get("terminal_observed")
    return {
        "next_page_url": next_url,
        "terminal_observed": terminal_value if isinstance(terminal_value, bool) else None,
        "invalid_next_page_url": raw_next is not None and next_url is None,
        "invalid_terminal_state": "terminal_observed" in raw and not isinstance(terminal_value, bool),
    }


def _sanitize_archive_directory(raw: Any, source_url: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"kind": None, "complete": None, "invalid_evidence": True}
    kind = raw.get("kind")
    if kind not in {"year_index", "year", "volume"}:
        kind = None
    complete = raw.get("complete") if isinstance(raw.get("complete"), bool) else None
    directory: dict[str, Any] = {
        "kind": kind,
        "complete": complete,
        "invalid_evidence": raw.get("complete") is not None and complete is None,
    }
    invalid = bool(directory["invalid_evidence"])
    if kind in {"year", "volume"}:
        year = raw.get("year")
        if isinstance(year, int) and not isinstance(year, bool) and SCOPE_START <= year <= datetime.now(timezone.utc).year:
            directory["year"] = year
        else:
            directory["year"] = None
            invalid = True
    if kind == "volume":
        volume = _clean_string(raw.get("volume"), limit=100)
        directory["volume"] = volume
        if not volume:
            invalid = True
    if kind == "year":
        raw_links = raw.get("volume_links")
        if not isinstance(raw_links, list):
            directory["volume_links"] = []
            invalid = True
        else:
            links = []
            for item in raw_links:
                if not isinstance(item, dict):
                    invalid = True
                    continue
                volume = _clean_string(item.get("volume"), limit=100)
                url = clean_listing_page_url(item.get("url"))
                if not volume or not url:
                    invalid = True
                    continue
                links.append({"volume": volume, "url": url})
            directory["volume_links"] = links
    raw_entry = raw.get("entry_url")
    if raw_entry is not None:
        entry_url = clean_listing_page_url(raw_entry)
        if entry_url:
            directory["entry_url"] = entry_url
        else:
            invalid = True
    directory["invalid_evidence"] = invalid
    return directory


def sanitize_capture(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != CAPTURE_SCHEMA:
        raise ValueError("unsupported capture schema")
    page_type = value.get("page_type")
    if page_type not in CAPTURE_FIELDS:
        raise ValueError("page_type must be archive, issue, advance, or article")
    source_url = (
        clean_listing_page_url(value.get("source_url"))
        if page_type in {"archive", "issue", "advance"}
        else clean_official_url(value.get("source_url"))
    )
    if not source_url:
        raise ValueError("source_url must be a clean official Bioinformatics page URL")
    observed_at = _iso_time(value.get("observed_at"))
    if not isinstance(value.get("complete"), bool):
        raise ValueError("complete must state whether this page's visible listing was fully inspected")
    data = value.get("data")
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    clean: dict[str, Any] = {
        "schema_version": CAPTURE_SCHEMA,
        "page_type": page_type,
        "source_url": source_url,
        "observed_at": observed_at,
        "complete": value["complete"],
        "data": {},
    }
    clean_data = clean["data"]
    if page_type == "archive":
        year_links = data.get("year_links")
        issues = data.get("issues")
        if not isinstance(year_links, list) or not isinstance(issues, list):
            raise ValueError("archive data requires year_links and issues arrays")
        clean_data["year_links"] = []
        for item in year_links:
            if not isinstance(item, dict) or not isinstance(item.get("year"), int):
                raise ValueError("archive year_links entries require an integer year")
            url = clean_listing_page_url(item.get("url"))
            clean_data["year_links"].append({"year": item["year"], "url": url, "label": _clean_string(item.get("label"), limit=500)})
        clean_data["issues"] = []
        for item in issues:
            if not isinstance(item, dict):
                raise ValueError("archive issue entries must be objects")
            url = clean_listing_page_url(item.get("url"))
            if not url or not isinstance(item.get("year"), int):
                raise ValueError("archive issue entry requires a clean URL and integer year")
            clean_data["issues"].append({
                "year": item["year"], "volume": _clean_string(item.get("volume"), limit=100),
                "issue": _clean_string(item.get("issue"), limit=100), "url": url,
                "label": _clean_string(item.get("label"), limit=500),
            })
        clean_data["pagination"] = _sanitize_pagination(data, source_url)
        clean_data["directory"] = _sanitize_archive_directory(data.get("directory"), source_url)
    elif page_type in {"issue", "advance"}:
        if page_type == "issue":
            clean_data["year"] = _scope_year(data.get("year"))
            clean_data["volume"] = _clean_string(data.get("volume"), limit=100)
            clean_data["issue"] = _clean_string(data.get("issue"), limit=100)
            clean_data["issue_state"] = _clean_string(data.get("issue_state"), limit=200)
            if not clean_data["volume"] or not clean_data["issue"]:
                raise ValueError("issue data requires observed volume and issue labels")
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("listing data requires an items array")
        clean_data["items"] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("listing items must be objects")
            landing = clean_official_url(item.get("landing_url"))
            if not landing or not article_native_id(landing):
                raise ValueError("each listing item requires an observed OUP article URL with a publisher ID")
            record = {
                "landing_url": landing,
                "title": _clean_string(item.get("title"), limit=2000),
                "authors_preview": _clean_string_list(item.get("authors_preview"), limit=100),
                "doi": normalize_doi(item.get("doi")),
                "citation": _clean_string(item.get("citation"), limit=5000),
                "section": _clean_string(item.get("section"), limit=500),
                "categories": _clean_string_list(item.get("categories"), limit=100),
                "pdf_url": clean_official_url(item.get("pdf_url")),
            }
            clean_data["items"].append(record)
        clean_data["pagination"] = _sanitize_pagination(data, source_url)
    else:
        clean_data.update({
            "citation_title": _clean_string(data.get("citation_title"), limit=2000),
            "citation_authors": _clean_string_list(data.get("citation_authors"), limit=500),
            "citation_doi": normalize_doi(data.get("citation_doi")),
            "citation_pmid": _clean_string(data.get("citation_pmid"), limit=100),
            "citation_journal_title": _clean_string(data.get("citation_journal_title"), limit=300),
            "citation_volume": _clean_string(data.get("citation_volume"), limit=100),
            "citation_issue": _clean_string(data.get("citation_issue"), limit=100),
            "citation_publication_date": _clean_string(data.get("citation_publication_date"), limit=100),
            "citation_pdf_url": clean_official_url(data.get("citation_pdf_url")),
            "visible_publication_date": _clean_string(data.get("visible_publication_date"), limit=200),
            "abstract": _clean_string(data.get("abstract"), limit=100000),
            "document_type": _clean_string(data.get("document_type"), limit=500),
        })
    # A strict field allowlist keeps unrelated browser state and temporary
    # anti-bot/auth parameters out of run evidence.
    return clean


class CaptureHandler(BaseHTTPRequestHandler):
    server_version = "BioinformaticsLocalCapture/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _reply(self, code: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _local_host_ok(self) -> bool:
        return self.headers.get("Host", "") in {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }

    def do_GET(self) -> None:  # noqa: N802
        if not self._local_host_ok():
            return self._reply(403, b"local host required")
        if self.path == "/status":
            body = json.dumps({"status": "ready", "saved": self.server.saved_count}).encode()
            return self._reply(200, body, "application/json; charset=utf-8")
        if self.path != "/":
            return self._reply(404, b"not found")
        page = """<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>
<title>Bioinformatics browser capture</title>
<main style='font:16px system-ui;max-width:960px;margin:32px auto'>
<h1>Bioinformatics browser capture</h1>
<p>Paste one allowlisted JSON capture envelope. This local form stores selected public metadata only.</p>
<label for='capture-json'>Capture JSON</label>
<textarea id='capture-json' rows='24' style='display:block;width:100%;font:13px monospace;margin:8px 0'></textarea>
<button id='capture-submit' type='button'>Save capture</button>
<output id='capture-status' style='display:block;margin-top:12px' aria-live='polite'></output>
<script>
document.querySelector('#capture-submit').addEventListener('click', async () => {
  const out = document.querySelector('#capture-status');
  out.textContent = 'Saving...';
  try {
    const r = await fetch('/capture', {method:'POST', headers:{'Content-Type':'application/json'}, body:document.querySelector('#capture-json').value});
    const data = await r.json();
    out.textContent = r.ok ? `Saved ${data.capture_id}` : `Rejected: ${data.error}`;
  } catch (e) { out.textContent = `Failed: ${e.message}`; }
});
</script></main>""".encode("ascii")
        self._reply(200, page)

    def do_POST(self) -> None:  # noqa: N802
        if not self._local_host_ok():
            return self._reply(403, b'{"error":"local host required"}', "application/json; charset=utf-8")
        if self.path != "/capture":
            return self._reply(404, b'{"error":"not found"}', "application/json; charset=utf-8")
        origin = self.headers.get("Origin", "")
        allowed_origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if origin not in allowed_origins:
            return self._reply(403, b'{"error":"local origin required"}', "application/json; charset=utf-8")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size <= 0 or size > 2_000_000:
            return self._reply(413, b'{"error":"capture must be between 1 byte and 2 MB"}', "application/json; charset=utf-8")
        try:
            value = sanitize_capture(json.loads(self.rfile.read(size)))
            serialized = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            capture_id = hashlib.sha256(serialized).hexdigest()
            browser_root: Path = self.server.browser_root
            relpath = Path("raw") / "browser" / f"{capture_id}.json"
            destination = browser_root / f"{capture_id}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                temp = destination.with_suffix(".json.tmp")
                temp.write_bytes(serialized)
                temp.replace(destination)
                index = browser_root / "index.jsonl"
                index.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps({
                    "capture_id": capture_id,
                    "file": str(relpath),
                    "page_type": value["page_type"],
                    "source_url": value["source_url"],
                    "observed_at": value["observed_at"],
                    "complete": value["complete"],
                }, ensure_ascii=False, sort_keys=True) + "\n"
                with index.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                self.server.saved_count += 1
            body = json.dumps({"status": "saved", "capture_id": capture_id, "page_type": value["page_type"]}).encode()
            return self._reply(201, body, "application/json; charset=utf-8")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            # Sanitization errors are generic and never echo a submitted URL or token.
            body = json.dumps({"error": str(exc)}).encode()
            return self._reply(400, body, "application/json; charset=utf-8")


def serve_capture_form(run_root: Path, port: int) -> None:
    run_root = run_root.expanduser().resolve()
    browser_root = run_root / "raw" / "browser"
    browser_root.mkdir(parents=True, exist_ok=True)
    index = browser_root / "index.jsonl"
    server = ThreadingHTTPServer(("127.0.0.1", port), CaptureHandler)
    server.browser_root = browser_root  # type: ignore[attr-defined]
    server.saved_count = len(read_jsonl(index)) if index.exists() else 0  # type: ignore[attr-defined]
    print(f"Bioinformatics capture form: http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _load_captures(pages_index: Path, evidence_root: Path) -> list[dict[str, Any]]:
    captures: list[dict[str, Any]] = []
    base = evidence_root.resolve()
    for row in read_jsonl(pages_index):
        relative = Path(row.get("file", ""))
        path = (base / relative).resolve()
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise ValueError("capture path escapes the run directory") from exc
        value = sanitize_capture(json.loads(path.read_text(encoding="utf-8")))
        if value["page_type"] != row.get("page_type") or value["source_url"] != row.get("source_url"):
            raise ValueError("capture index does not match saved evidence")
        captures.append(value)
    return captures


def _archive_issue_map(captures: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    issues: dict[str, dict[str, Any]] = {}
    year_links: list[dict[str, Any]] = []
    for capture in captures:
        if capture["page_type"] != "archive":
            continue
        year_links.extend({**entry, "source_url": capture["source_url"], "observed_at": capture["observed_at"]} for entry in capture["data"]["year_links"])
        for entry in capture["data"]["issues"]:
            if SCOPE_START <= entry["year"] <= datetime.now(timezone.utc).year:
                issue_path = urlsplit(entry["url"]).path
                prior = issues.get(issue_path)
                current = {**entry, "source_url": capture["source_url"], "observed_at": capture["observed_at"]}
                if prior and (prior["year"], prior["volume"], prior["issue"]) != (current["year"], current["volume"], current["issue"]):
                    raise ValueError("archive contains conflicting identities for one issue URL")
                issues[issue_path] = current
    return issues, year_links


def _capture_signature(capture: dict[str, Any]) -> str:
    data = dict(capture.get("data", {}))
    # Directory attestations are additive annotations of the same captured
    # listing. A later visit may record that it was the complete year/volume
    # directory without changing the observed links; compare the substantive
    # page evidence while retaining the latest attestation below.
    if capture.get("page_type") == "archive":
        data.pop("directory", None)
    return json.dumps(
        [capture.get("page_type"), capture.get("source_url"), capture.get("complete"), data],
        ensure_ascii=False,
        sort_keys=True,
    )


def _listing_chain_state(
    captures: list[dict[str, Any]],
    page_type: str,
    *,
    require_navigation_evidence: bool = False,
    entry_url: str | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """Check observed next-page links from each listing entry through a terminal page."""
    if not captures:
        return (not require_navigation_evidence), []
    blockers: list[dict[str, Any]] = []
    expected_path = urlsplit(entry_url).path if entry_url else None
    pages_by_path: dict[str, list[dict[str, Any]]] = {}
    for capture in captures:
        source_url = capture["source_url"]
        path = urlsplit(source_url).path
        if expected_path and path != expected_path:
            blockers.append({"kind": "pagination_page_outside_entry_scope", "page_type": page_type, "source_url": source_url})
            continue
        pages_by_path.setdefault(path, []).append(capture)

    if require_navigation_evidence and expected_path and expected_path not in pages_by_path:
        return False, [*blockers, {"kind": "pagination_entry_page_not_captured", "page_type": page_type, "source_url": entry_url}]

    all_paths_complete = True
    for path, pages in sorted(pages_by_path.items()):
        by_url: dict[str, dict[str, Any]] = {}
        for capture in pages:
            source_url = capture["source_url"]
            prior = by_url.get(source_url)
            if prior and _capture_signature(prior) != _capture_signature(capture):
                blockers.append({"kind": "pagination_page_capture_conflict", "page_type": page_type, "source_url": source_url})
                all_paths_complete = False
            elif not prior or capture["observed_at"] > prior["observed_at"]:
                by_url[source_url] = capture

        has_navigation_evidence = any(
            capture["data"].get("pagination") is not None or bool(urlsplit(capture["source_url"]).query)
            for capture in pages
        )
        chain_required = require_navigation_evidence or has_navigation_evidence
        if not chain_required:
            continue

        root_url = entry_url if entry_url else urlunsplit(("https", OUP_HOST, path, "", ""))
        if root_url not in by_url:
            blockers.append({"kind": "pagination_entry_page_not_captured", "page_type": page_type, "source_url": root_url})
            all_paths_complete = False
            continue

        visited: set[str] = set()
        current_url = root_url
        closed = False
        while True:
            if current_url in visited:
                blockers.append({"kind": "pagination_chain_cycle", "page_type": page_type, "source_url": current_url})
                all_paths_complete = False
                break
            visited.add(current_url)
            capture = by_url.get(current_url)
            if not capture:
                blockers.append({"kind": "pagination_next_page_not_captured", "page_type": page_type, "source_url": current_url})
                all_paths_complete = False
                break
            if not capture["complete"]:
                blockers.append({"kind": "pagination_page_not_complete", "page_type": page_type, "source_url": current_url})
                all_paths_complete = False
            pagination = capture["data"].get("pagination")
            if not isinstance(pagination, dict):
                blockers.append({"kind": "pagination_evidence_missing", "page_type": page_type, "source_url": current_url})
                all_paths_complete = False
                break
            if pagination.get("invalid_evidence_shape") or pagination.get("invalid_next_page_url") or pagination.get("invalid_terminal_state"):
                blockers.append({"kind": "pagination_evidence_invalid", "page_type": page_type, "source_url": current_url})
                all_paths_complete = False
                break
            next_url = pagination.get("next_page_url")
            terminal = pagination.get("terminal_observed")
            if next_url and terminal is True:
                blockers.append({"kind": "pagination_next_terminal_conflict", "page_type": page_type, "source_url": current_url, "next_page_url": next_url})
                all_paths_complete = False
                break
            if next_url:
                if terminal is not False:
                    blockers.append({"kind": "pagination_next_terminal_state_missing", "page_type": page_type, "source_url": current_url, "next_page_url": next_url})
                    all_paths_complete = False
                    break
                current_url = next_url
                continue
            if terminal is True:
                closed = True
                break
            blockers.append({"kind": "pagination_terminal_not_observed", "page_type": page_type, "source_url": current_url})
            all_paths_complete = False
            break

        if closed:
            orphans = sorted(set(by_url) - visited)
            if orphans:
                blockers.extend({"kind": "pagination_page_not_reachable", "page_type": page_type, "source_url": url} for url in orphans)
                all_paths_complete = False
    return all_paths_complete and not blockers, blockers


def _archive_directory_state(
    captures: list[dict[str, Any]],
    target_years: set[int],
    year_links: list[dict[str, Any]],
    archive_issues: dict[str, dict[str, Any]],
) -> tuple[bool, list[dict[str, Any]], int, int]:
    """Require a complete year index, each annual directory, and every linked volume directory."""
    blockers: list[dict[str, Any]] = []
    archive_path = urlsplit(ARCHIVE_URL).path
    directories = [
        (capture, capture["data"].get("directory"))
        for capture in captures
        if isinstance(capture["data"].get("directory"), dict)
    ]
    year_indexes = [
        (capture, directory)
        for capture, directory in directories
        if directory.get("kind") == "year_index" and urlsplit(capture["source_url"]).path == archive_path
    ]
    if not any(
        directory.get("complete") is True
        and not directory.get("invalid_evidence")
        and capture.get("complete") is True
        for capture, directory in year_indexes
    ):
        blockers.append({"kind": "archive_directory_year_index_not_attested_complete", "source_url": ARCHIVE_URL})

    year_links_by_year: dict[int, set[str]] = {}
    for link in year_links:
        if isinstance(link.get("year"), int) and link.get("url"):
            year_links_by_year.setdefault(link["year"], set()).add(link["url"])
    year_directories: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    referenced_volumes: set[tuple[int, str, str]] = set()
    for year in sorted(target_years):
        candidates = [
            (capture, directory)
            for capture, directory in directories
            if directory.get("kind") == "year" and directory.get("year") == year
        ]
        if not candidates:
            blockers.append({"kind": "archive_directory_year_not_captured", "year": year})
            continue
        complete_candidates = [
            (capture, directory)
            for capture, directory in candidates
            if directory.get("complete") is True
            and not directory.get("invalid_evidence")
            and capture.get("complete") is True
        ]
        if not complete_candidates:
            blockers.append({"kind": "archive_directory_year_not_attested_complete", "year": year})
            continue
        linked_year_urls = year_links_by_year.get(year, set())
        if len(linked_year_urls) > 1:
            blockers.append({"kind": "archive_directory_year_link_conflict", "year": year, "urls": sorted(linked_year_urls)})
        linked_candidates = [
            (capture, directory)
            for capture, directory in complete_candidates
            if directory.get("entry_url", capture["source_url"]) in linked_year_urls
        ]
        if not linked_candidates:
            blockers.append({"kind": "archive_directory_year_entry_not_linked", "year": year})
        if len(linked_candidates) != len(complete_candidates):
            blockers.append({"kind": "archive_directory_year_page_not_linked", "year": year})
        year_directories[year] = linked_candidates
        volume_link_map: dict[str, set[str]] = {}
        for _capture, directory in linked_candidates:
            links = directory.get("volume_links")
            if not isinstance(links, list):
                blockers.append({"kind": "archive_directory_volume_links_missing", "year": year})
                continue
            for volume_link in links:
                volume = volume_link.get("volume")
                url = volume_link.get("url")
                if not isinstance(volume, str) or not volume or not isinstance(url, str):
                    blockers.append({"kind": "archive_directory_volume_link_invalid", "year": year})
                    continue
                volume_link_map.setdefault(volume, set()).add(url)
        for volume, urls in sorted(volume_link_map.items()):
            if len(urls) != 1:
                blockers.append({"kind": "archive_directory_volume_link_conflict", "year": year, "volume": volume, "urls": sorted(urls)})
                continue
            url = next(iter(urls))
            key = (year, volume, urlsplit(url).path)
            if key in referenced_volumes:
                blockers.append({"kind": "archive_directory_duplicate_volume_link", "year": year, "volume": volume})
                continue
            referenced_volumes.add(key)
            volume_dirs = [
                (volume_capture, volume_directory)
                for volume_capture, volume_directory in directories
                if volume_directory.get("kind") == "volume"
                and volume_directory.get("year") == year
                and volume_directory.get("volume") == volume
                and volume_directory.get("entry_url", volume_capture["source_url"]) == url
            ]
            if not volume_dirs:
                blockers.append({"kind": "archive_directory_volume_not_captured", "year": year, "volume": volume, "url": url})
                continue
            usable_volume_dirs = [
                (volume_capture, volume_directory)
                for volume_capture, volume_directory in volume_dirs
                if volume_directory.get("complete") is True
                and not volume_directory.get("invalid_evidence")
                and volume_capture.get("complete") is True
            ]
            if not usable_volume_dirs:
                blockers.append({"kind": "archive_directory_volume_not_attested_complete", "year": year, "volume": volume, "url": url})
                continue
            observed_issue_identities: set[tuple[str, str]] = set()
            for volume_capture, _volume_directory in usable_volume_dirs:
                rows = volume_capture["data"].get("issues", [])
                if any(row.get("year") != year or str(row.get("volume")) != volume for row in rows):
                    blockers.append({"kind": "archive_directory_volume_issue_identity_conflict", "year": year, "volume": volume, "source_url": volume_capture["source_url"]})
                for row in rows:
                    if row.get("url") and row.get("issue"):
                        observed_issue_identities.add((urlsplit(row["url"]).path, str(row["issue"])))
            expected_issue_identities = {
                (path, str(issue["issue"]))
                for path, issue in archive_issues.items()
                if issue.get("year") == year and str(issue.get("volume")) == volume and issue.get("issue")
            }
            if observed_issue_identities != expected_issue_identities:
                blockers.append({
                    "kind": "archive_directory_volume_issue_set_mismatch",
                    "year": year,
                    "volume": volume,
                    "missing_issue_count": len(expected_issue_identities - observed_issue_identities),
                    "unlinked_issue_count": len(observed_issue_identities - expected_issue_identities),
                })

    for capture, directory in directories:
        if directory.get("kind") != "volume":
            continue
        year = directory.get("year")
        volume = directory.get("volume")
        path = urlsplit(capture["source_url"]).path
        if (year, volume, path) not in referenced_volumes:
            blockers.append({"kind": "archive_directory_volume_not_linked", "year": year, "volume": volume, "source_url": capture["source_url"]})

    # Archive issue anchors are still retained as observed identities. The
    # directories above separately establish that each year's complete list
    # of volumes and each volume's issue list were inspected.
    for capture in captures:
        directory = capture["data"].get("directory")
        if capture["data"].get("issues") and not isinstance(directory, dict):
            blockers.append({"kind": "archive_directory_volume_evidence_missing", "source_url": capture["source_url"]})
    for path, issue in archive_issues.items():
        if not isinstance(issue.get("year"), int) or not issue.get("volume") or not issue.get("issue"):
            blockers.append({"kind": "archive_directory_issue_identity_incomplete", "url": issue.get("url") or path})

    year_count = len(year_directories)
    volume_count = len({
        (directory.get("year"), directory.get("volume"))
        for _capture, directory in directories
        if directory.get("kind") == "volume" and directory.get("complete") is True and not directory.get("invalid_evidence")
    })
    return not blockers, blockers, year_count, volume_count


def _capture_items(capture: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in capture["data"].get("items", []):
        native_id = article_native_id(item["landing_url"])
        if not native_id:
            continue
        items.append({
            **item,
            "source_native_id": native_id,
            "page_type": capture["page_type"],
            "enumeration_source_url": capture["source_url"],
            "enumeration_observed_at": capture["observed_at"],
            "year": capture["data"].get("year"),
            "volume": capture["data"].get("volume"),
            "issue": capture["data"].get("issue"),
            "issue_state": capture["data"].get("issue_state"),
            "page_complete": capture["complete"],
        })
    return items


def _article_detail(capture: dict[str, Any]) -> dict[str, Any]:
    data = capture["data"]
    return {
        "source_native_id": article_native_id(capture["source_url"]),
        "source_url": capture["source_url"],
        "observed_at": capture["observed_at"],
        "title": data.get("citation_title"),
        "authors": data.get("citation_authors", []),
        "doi": data.get("citation_doi"),
        "pmid": data.get("citation_pmid"),
        "journal_title": data.get("citation_journal_title"),
        "volume": data.get("citation_volume"),
        "issue": data.get("citation_issue"),
        "publication_date": data.get("citation_publication_date"),
        "visible_publication_date": data.get("visible_publication_date"),
        "abstract": data.get("abstract"),
        "document_type": data.get("document_type"),
        "pdf_url": data.get("citation_pdf_url"),
    }


def classify_scope(document_type: str | None, title: str | None) -> tuple[str, str | None]:
    type_text = normalize_space(document_type or "").casefold()
    for pattern, reason in EXCLUDED_TYPE_REASONS:
        if pattern.search(type_text):
            return "exclude", reason
    if title:
        for pattern, reason in EXCLUDED_TITLE_PREFIXES:
            if pattern.search(normalize_space(title)):
                return "exclude", reason
    if INCLUDED_TYPE_RE.search(type_text):
        return "include", None
    return "unresolved", None


def _prov(url: str, time: str, method: str, **extra: Any) -> dict[str, Any]:
    return {"source_url": url, "observed_at": time, "method": method, **extra}


def collect(
    pages_index: Path,
    evidence_root: Path,
    output_root: Path,
    previous_expected_root: Path | None = None,
    supplement_records_path: Path | None = None,
    crossref_records_path: Path | None = None,
) -> dict[str, Any]:
    captures = _load_captures(pages_index, evidence_root)
    supplement_records = _load_europe_pmc_records(supplement_records_path)
    crossref_records = _load_crossref_records(crossref_records_path)
    archive_issues, archive_year_links = _archive_issue_map(captures)
    issue_page_captures = [capture for capture in captures if capture["page_type"] == "issue"]
    issue_captures = {
        urlsplit(capture["source_url"]).path: capture
        for capture in issue_page_captures
    }
    archive_captures = [capture for capture in captures if capture["page_type"] == "archive"]
    advance_captures = [capture for capture in captures if capture["page_type"] == "advance"]
    detail_captures = [capture for capture in captures if capture["page_type"] == "article"]
    archive_chain_complete, archive_chain_blockers = _listing_chain_state(archive_captures, "archive")
    issue_chain_complete, issue_chain_blockers = _listing_chain_state(issue_page_captures, "issue")
    advance_chain_complete, advance_chain_blockers = _listing_chain_state(
        advance_captures,
        "advance",
        require_navigation_evidence=True,
        entry_url=ADVANCE_URL,
    )
    occurrences = [item for capture in captures if capture["page_type"] in {"issue", "advance"} for item in _capture_items(capture)]
    details = [_article_detail(capture) for capture in detail_captures]
    unresolved: list[dict[str, Any]] = []

    target_years = set(range(SCOPE_START, datetime.now(timezone.utc).year + 1))
    seen_archive_years = {item["year"] for item in archive_year_links}
    absent_archive_years = sorted(target_years - seen_archive_years)
    archive_directories_complete, archive_directory_blockers, archive_year_directory_count, archive_volume_directory_count = _archive_directory_state(
        archive_captures,
        target_years,
        archive_year_links,
        archive_issues,
    )
    archive_issue_years = {item["year"] for item in archive_issues.values()}
    absent_issue_years = sorted(target_years - archive_issue_years)
    missing_issue_paths = sorted(set(archive_issues) - set(issue_captures))
    unexpected_issue_paths = sorted(set(issue_captures) - set(archive_issues))
    if absent_archive_years:
        unresolved.append({"kind": "archive_year_links_missing", "years": absent_archive_years})
    if absent_issue_years:
        unresolved.append({"kind": "archive_issue_year_missing", "years": absent_issue_years})
    if missing_issue_paths:
        unresolved.append({"kind": "official_issue_pages_not_captured", "urls": [archive_issues[path]["url"] for path in missing_issue_paths]})
    if unexpected_issue_paths:
        unresolved.append({"kind": "issue_capture_not_listed_in_archive", "urls": [issue_captures[path]["source_url"] for path in unexpected_issue_paths]})
    if not advance_captures:
        unresolved.append({"kind": "advance_articles_not_captured"})
    unresolved.extend(archive_chain_blockers)
    unresolved.extend(issue_chain_blockers)
    unresolved.extend(advance_chain_blockers)
    unresolved.extend(archive_directory_blockers)
    for capture in captures:
        if capture["page_type"] in {"archive", "issue", "advance"} and not capture["complete"]:
            unresolved.append({"kind": "listing_page_not_attested_complete", "page_type": capture["page_type"], "source_url": capture["source_url"]})

    groups: dict[str, dict[str, list[Any]]] = {}
    for item in occurrences:
        native_id = item.get("source_native_id")
        if not native_id:
            unresolved.append({"kind": "listing_identity_missing", "enumeration_source_url": item["enumeration_source_url"], "landing_url": item["landing_url"]})
            continue
        groups.setdefault(native_id, {"occurrences": [], "details": []})["occurrences"].append(item)
    for detail in details:
        native_id = detail.get("source_native_id")
        if not native_id:
            unresolved.append({"kind": "detail_identity_missing", "source_url": detail["source_url"]})
            continue
        groups.setdefault(native_id, {"occurrences": [], "details": []})["details"].append(detail)

    source_occurrences: list[dict[str, Any]] = []
    expected_source_items: list[dict[str, Any]] = []
    expected_by_year: dict[int, dict[str, dict[str, Any]]] = {}
    staging: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    accounted_ids: set[str] = set()
    supplement_join_rows: list[dict[str, Any]] = []
    crossref_join_rows: list[dict[str, Any]] = []

    for source_id, group in sorted(groups.items()):
        members = group["occurrences"]
        group_details = group["details"]
        if not members:
            unresolved.append({"kind": "detail_without_enumeration_identity", "source_native_id": source_id, "source_url": group_details[0]["source_url"] if group_details else None})
            continue
        issue_members = [item for item in members if item["page_type"] == "issue"]
        issue_years = {item["year"] for item in issue_members if isinstance(item.get("year"), int)}
        listing_dois = {normalize_doi(item.get("doi")) for item in members if normalize_doi(item.get("doi"))}
        detail_dois = {normalize_doi(item.get("doi")) for item in group_details if normalize_doi(item.get("doi"))}
        detail_pmids = {str(item.get("pmid")) for item in group_details if item.get("pmid")}
        supplement_dois = sorted(listing_dois | detail_dois)
        supplement_pmids = sorted(detail_pmids)
        supplement_doi = supplement_dois[0] if len(supplement_dois) == 1 else None
        supplement_pmid = supplement_pmids[0] if len(supplement_pmids) == 1 else None
        match_representative = sorted(issue_members or members, key=lambda item: (item["enumeration_source_url"], item["landing_url"]))[0]
        match_title = next((item.get("title") for item in group_details if item.get("title")), match_representative.get("title"))
        match_volume = match_representative.get("volume") or next((item.get("volume") for item in group_details if item.get("volume")), None)
        match_issue = match_representative.get("issue") or next((item.get("issue") for item in group_details if item.get("issue")), None)
        supplement, supplement_matched_by, supplement_match_error, supplement_match_audit = _match_europe_pmc(
            supplement_doi,
            supplement_pmid,
            supplement_records,
            title=match_title,
            volume=match_volume,
            issue=match_issue,
        )
        if len(supplement_dois) > 1 or len(supplement_pmids) > 1:
            supplement_match_error = "multiple conflicting OUP DOI or PMID values"
        supplement_join_rows.append({
            "source_native_id": source_id,
            "oup_doi_values": supplement_dois,
            "oup_pmid_values": supplement_pmids,
            "matched": bool(supplement),
            "matched_by": supplement_matched_by,
            "supplement_source_url": supplement.get("source_url") if supplement else None,
            "margin_assisted": bool(supplement and supplement.get("year_window_margin_only")),
            "match_error": supplement_match_error,
            "match_audit": supplement_match_audit,
        })
        if supplement_match_error:
            unresolved.append({"kind": "europe_pmc_exact_identifier_match_conflict", "source_native_id": source_id, "reason": supplement_match_error, "doi": supplement_doi, "pmid": supplement_pmid})
        crossref_doi = supplement_doi or (supplement.get("doi") if supplement else None)
        crossref_record, crossref_match_audit = _match_crossref(
            crossref_doi,
            crossref_records,
            title=match_title,
            volume=match_volume,
            issue=match_issue,
        )
        crossref_join_rows.append({
            "source_native_id": source_id,
            "oup_or_verified_doi": crossref_doi,
            "matched": bool(crossref_record),
            "crossref_source_url": crossref_record.get("source_url") if crossref_record else None,
            "observed_vor_pdf_count": len(crossref_record.get("vor_pdf_candidates", [])) if crossref_record else 0,
            "observed_am_pdf_count": len(crossref_record.get("am_pdf_candidates", [])) if crossref_record else 0,
            "crossref_published_online": crossref_record.get("online_date") if crossref_record else None,
            "oup_visible_publication_date": next((item.get("visible_publication_date") for item in group_details if item.get("visible_publication_date")), None),
            "match_audit": crossref_match_audit,
        })
        if crossref_match_audit.get("candidate_count", 0) > 1 and not crossref_record:
            unresolved.append({"kind": "crossref_duplicate_doi_match_unresolved", "source_native_id": source_id, "doi": crossref_doi, "match_audit": crossref_match_audit})
        detail_years = {
            int(match.group(0))
            for detail in group_details
            for match in [YEAR_RE.search(detail.get("publication_date") or "")]
            if match
        }
        if not issue_years:
            issue_year = supplement.get("issue_year") if supplement else None
            if isinstance(issue_year, int):
                detail_years.add(issue_year)
        identity_years = sorted(issue_years or detail_years)
        identity_occurrences = [
            {
                "page_type": item["page_type"],
                "source_url": item["enumeration_source_url"],
                "observed_at": item["enumeration_observed_at"],
                "year": item.get("year"),
                "volume": item.get("volume"),
                "issue": item.get("issue"),
                "landing_url": item["landing_url"],
            }
            for item in members
        ]
        occurrence_representative = sorted(
            issue_members or members,
            key=lambda item: (item["enumeration_source_url"], item["landing_url"]),
        )[0] if members else None
        identity_dois = sorted({normalize_doi(row.get("doi")) for row in [*members, *group_details] if normalize_doi(row.get("doi"))})
        identity_titles = sorted({
            normalize_space(row.get("title") or "").casefold()
            for row in [*members, *group_details]
            if normalize_space(row.get("title") or "")
        })
        expected_source_items.append({
            "schema_version": "literature-expected-source-item-v1",
            "venue_id": VENUE_ID,
            "source_native_id": source_id,
            "years": identity_years,
            "year": identity_years[0] if len(identity_years) == 1 else None,
            "volume": occurrence_representative.get("volume") if occurrence_representative else None,
            "issue": occurrence_representative.get("issue") if occurrence_representative else None,
            "source_urls": sorted({item["enumeration_source_url"] for item in members}),
            "landing_urls": sorted({item["landing_url"] for item in members}),
            "doi_values": identity_dois,
            "title_values": identity_titles,
            "source_occurrence_count": len(members),
            "source_occurrences": identity_occurrences,
        })
        for expected_year in identity_years:
            if expected_year not in target_years:
                continue
            yearly_members = [item for item in issue_members if item["year"] == expected_year] or members
            representative = sorted(
                yearly_members,
                key=lambda item: (item["page_type"] != "issue", item["enumeration_source_url"], item["landing_url"]),
            )[0]
            expected_by_year.setdefault(expected_year, {})[source_id] = {
                "schema_version": "literature-expected-source-item-v1",
                "venue_id": VENUE_ID,
                "source_native_id": source_id,
                "year": expected_year,
                "volume": representative.get("volume"),
                "issue": representative.get("issue"),
                "source_url": representative["enumeration_source_url"],
                "landing_url": representative["landing_url"],
                "enumeration_kind": "issue" if issue_members else "advance_article",
                "doi_values": identity_dois,
                "title_values": identity_titles,
            }

        dois = sorted({normalize_doi(row.get("doi")) for row in [*members, *group_details] if normalize_doi(row.get("doi"))})
        titles = sorted({normalize_space(row.get("title") or "").casefold() for row in [*members, *group_details] if normalize_space(row.get("title") or "")})
        for item in members:
            source_occurrences.append({
                "source_native_id": item["source_native_id"],
                "doi": normalize_doi(item.get("doi")),
                "year": item.get("year"),
                "volume": item.get("volume"),
                "issue": item.get("issue"),
                "page_type": item["page_type"],
                "enumeration_source_url": item["enumeration_source_url"],
                "enumeration_observed_at": item["enumeration_observed_at"],
                "landing_url": item["landing_url"],
                "title": item.get("title"),
                "section": item.get("section"),
                "categories": item.get("categories", []),
                "issue_state": item.get("issue_state"),
                "capture_state": "enumerated",
            })
        if len(dois) > 1 or len(titles) > 1:
            unresolved.append({
                "kind": "source_identity_collision",
                "source_native_id": source_id,
                "dois": dois,
                "titles": titles,
                "source_urls": sorted({row["enumeration_source_url"] for row in members} | {row["source_url"] for row in group_details}),
            })
            continue
        canonical_id = source_id

        if len(issue_years) > 1:
            unresolved.append({"kind": "identity_assigned_to_multiple_issue_years", "source_native_id": canonical_id, "years": sorted(issue_years)})
            continue
        chosen = sorted(issue_members or members, key=lambda item: (item["enumeration_source_url"], item["landing_url"]))[0] if members else None
        details_for_entity = group_details
        if chosen and chosen["page_type"] == "issue":
            year = chosen["year"]
        else:
            year = identity_years[0] if len(identity_years) == 1 else None
        if not isinstance(year, int) or year not in target_years:
            unresolved.append({"kind": "source_year_unresolved", "source_native_id": canonical_id, "enumeration_sources": sorted({item["enumeration_source_url"] for item in members})})
            continue
        if len(details_for_entity) > 1:
            detail_signatures = {(row.get("title"), tuple(row.get("authors") or []), normalize_doi(row.get("doi")), row.get("publication_date")) for row in details_for_entity}
            if len(detail_signatures) > 1:
                unresolved.append({"kind": "conflicting_article_detail_captures", "source_native_id": canonical_id, "source_urls": sorted(row["source_url"] for row in details_for_entity)})
                continue
        detail = details_for_entity[0] if details_for_entity else {}

        archive_entry = archive_issues.get(urlsplit(chosen["enumeration_source_url"]).path) if chosen["page_type"] == "issue" else None
        if archive_entry:
            year_url, year_time, year_method = archive_entry["source_url"], archive_entry["observed_at"], "official_archive_volume_year"
        elif detail.get("publication_date"):
            year_url, year_time, year_method = detail["source_url"], detail["observed_at"], "official_advance_publication_date"
        elif supplement and supplement.get("issue_year") == year:
            year_url, year_time, year_method = supplement["source_url"], supplement["observed_at"], "Europe_PMC_issue_year_fallback_for_advance_item"
        else:
            year_url, year_time, year_method = chosen["enumeration_source_url"], chosen["enumeration_observed_at"], "official_advance_listing_year"
        list_urls = sorted({row["enumeration_source_url"] for row in members})
        title = detail.get("title") or chosen.get("title")
        type_candidates: list[tuple[str, str, str]] = []
        if detail.get("document_type"):
            type_candidates.append((detail["document_type"], detail["source_url"], detail["observed_at"]))
        if classify_scope(detail.get("document_type"), title)[0] == "unresolved":
            for occurrence in sorted(members, key=lambda item: (item["page_type"] != "issue", item["enumeration_source_url"])):
                if occurrence.get("section"):
                    type_candidates.append((occurrence["section"], occurrence["enumeration_source_url"], occurrence["enumeration_observed_at"]))
                if occurrence.get("categories"):
                    type_candidates.append(("; ".join(occurrence["categories"]), occurrence["enumeration_source_url"], occurrence["enumeration_observed_at"]))
            if archive_entry and archive_entry.get("label"):
                type_candidates.append((archive_entry["label"], archive_entry["source_url"], archive_entry["observed_at"]))
        selected_type = None
        type_source_url = detail.get("source_url") or chosen["enumeration_source_url"]
        type_observed_at = detail.get("observed_at") or chosen["enumeration_observed_at"]
        for candidate_type, candidate_url, candidate_time in type_candidates:
            if classify_scope(candidate_type, title)[0] != "unresolved":
                selected_type = candidate_type
                type_source_url, type_observed_at = candidate_url, candidate_time
                break
        if not selected_type and type_candidates:
            selected_type, type_source_url, type_observed_at = type_candidates[0]
        title_source_url = detail["source_url"] if detail.get("title") else chosen["enumeration_source_url"]
        title_time = detail["observed_at"] if detail.get("title") else chosen["enumeration_observed_at"]
        authors = detail.get("authors") or (supplement.get("authors") if supplement else []) or []
        authors_source_url = detail.get("source_url") if detail.get("authors") else (supplement.get("source_url") if supplement and supplement.get("authors") else chosen["enumeration_source_url"])
        authors_observed_at = detail.get("observed_at") if detail.get("authors") else (supplement.get("observed_at") if supplement and supplement.get("authors") else chosen["enumeration_observed_at"])
        abstract = detail.get("abstract") or (supplement.get("abstract") if supplement else None)
        abstract_source_url = detail.get("source_url") if detail.get("abstract") else (supplement.get("source_url") if supplement and supplement.get("abstract") else chosen["enumeration_source_url"])
        abstract_observed_at = detail.get("observed_at") if detail.get("abstract") else (supplement.get("observed_at") if supplement and supplement.get("abstract") else chosen["enumeration_observed_at"])
        doi = detail.get("doi") or (dois[0] if dois else normalize_doi(chosen.get("doi"))) or (supplement.get("doi") if supplement else None)
        doi_from_supplement = not detail.get("doi") and not dois and bool(supplement and supplement.get("doi"))
        doi_source_url = detail["source_url"] if detail.get("doi") else (supplement["source_url"] if doi_from_supplement else chosen["enumeration_source_url"])
        doi_time = detail["observed_at"] if detail.get("doi") else (supplement["observed_at"] if doi_from_supplement else chosen["enumeration_observed_at"])
        publication_date = detail.get("publication_date") or (supplement.get("publication_date") if supplement else None)
        publication_date_from_supplement = not detail.get("publication_date") and bool(supplement and supplement.get("publication_date"))
        publication_date_source_url = detail["source_url"] if detail.get("publication_date") else (supplement["source_url"] if publication_date_from_supplement else chosen["enumeration_source_url"])
        publication_date_observed_at = detail["observed_at"] if detail.get("publication_date") else (supplement["observed_at"] if publication_date_from_supplement else chosen["enumeration_observed_at"])
        publication_date_precision = supplement.get("publication_date_precision") if publication_date_from_supplement and supplement else None
        pdf_url = detail.get("pdf_url") or chosen.get("pdf_url") or (supplement.get("pdf_url") if supplement else None)
        crossref_vor = crossref_record["vor_pdf_candidates"][0] if crossref_record and crossref_record.get("vor_pdf_candidates") else None
        crossref_am_candidates = crossref_record.get("am_pdf_candidates", []) if crossref_record else []
        if not detail.get("pdf_url") and not chosen.get("pdf_url") and crossref_vor:
            pdf_url = crossref_vor["url"]
        elif not detail.get("pdf_url") and not chosen.get("pdf_url") and not crossref_vor and supplement and supplement.get("pdf_url"):
            pdf_url = supplement["pdf_url"]
        pdf_from_crossref = not detail.get("pdf_url") and not chosen.get("pdf_url") and bool(crossref_vor)
        pdf_from_supplement = not detail.get("pdf_url") and not chosen.get("pdf_url") and not crossref_vor and bool(supplement and supplement.get("pdf_url"))
        if detail.get("pdf_url"):
            pdf_source_url, pdf_time = detail["source_url"], detail["observed_at"]
        elif chosen.get("pdf_url"):
            pdf_source_url, pdf_time = chosen["enumeration_source_url"], chosen["enumeration_observed_at"]
        elif crossref_vor:
            pdf_source_url, pdf_time = crossref_record["source_url"], crossref_record["observed_at"]
        elif supplement:
            pdf_source_url, pdf_time = supplement["source_url"], supplement["observed_at"]
        else:
            pdf_source_url, pdf_time = chosen["enumeration_source_url"], chosen["enumeration_observed_at"]
        crossref_online_date = crossref_record.get("online_date") if crossref_record else None
        crossref_online_precision = crossref_record.get("online_date_precision") if crossref_record else None
        if detail.get("visible_publication_date"):
            online_publication_date = detail["visible_publication_date"]
            online_publication_date_precision = "publisher_visible_date"
            online_date_source_url, online_date_observed_at = detail["source_url"], detail["observed_at"]
            online_date_method = "official_OUP_visible_published_date"
        elif crossref_online_date:
            online_publication_date = crossref_online_date
            online_publication_date_precision = crossref_online_precision
            online_date_source_url, online_date_observed_at = crossref_record["source_url"], crossref_record["observed_at"]
            online_date_method = f"Crossref_published-online_date_precision_{crossref_online_precision}"
        else:
            online_publication_date = None
            online_publication_date_precision = None
            online_date_source_url = detail.get("source_url") or (supplement.get("source_url") if supplement else chosen["enumeration_source_url"])
            online_date_observed_at = detail.get("observed_at") or (supplement.get("observed_at") if supplement else chosen["enumeration_observed_at"])
            online_date_method = "checked_no_online_publication_date"
        source_occurrence_sources = sorted({item["enumeration_source_url"] for item in members})
        abstract_source_method = (
            "visible_abstract_section" if detail.get("abstract")
            else "Europe_PMC_abstract" if supplement and supplement.get("abstract")
            else "checked_no_abstract"
        )
        decision, reason = classify_scope(selected_type, title)
        if decision == "unresolved":
            unresolved.append({"kind": "research_scope_unresolved", "source_native_id": canonical_id, "year": year, "title": title, "document_type": selected_type, "source_url": type_source_url})
            continue

        if decision == "exclude":
            list_time = chosen["enumeration_observed_at"]
            exclusions.append({
                "schema_version": EXCLUSION_SCHEMA,
                "venue_id": VENUE_ID,
                "source_native_id": canonical_id,
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "year": year,
                "volume": chosen.get("volume"),
                "issue": chosen.get("issue"),
                "doi": doi,
                "document_type": selected_type or reason,
                "inclusion_decision": "exclude",
                "exclusion_reason_code": reason,
                "exclusion_reason_detail": f"Explicit official type or title classification: {selected_type or title}.",
                "source_url": chosen["enumeration_source_url"],
                "landing_url": chosen["landing_url"],
                "observed_at": max(list_time, detail.get("observed_at") or list_time),
                "field_provenance": {
                    "source_native_id": _prov(chosen["enumeration_source_url"], list_time, "official_issue_or_advance_article_link"),
                    "title": _prov(title_source_url, title_time, "official_citation_title" if detail.get("title") else "official_issue_listing_title"),
                    "year": _prov(year_url, year_time, year_method),
                    "inclusion_decision": _prov(type_source_url, type_observed_at, "explicit_nonresearch_scope_rule"),
                    "exclusion_reason_code": _prov(type_source_url, type_observed_at, "explicit_editorial_correction_or_frontmatter_type"),
                },
            })
            accounted_ids.add(canonical_id)
            continue

        if not detail and not supplement:
            unresolved.append({"kind": "article_detail_not_captured", "source_native_id": canonical_id, "year": year, "landing_url": chosen["landing_url"], "enumeration_source_url": chosen["enumeration_source_url"]})
            continue
        if not title or not authors or not chosen.get("landing_url"):
            unresolved.append({"kind": "required_article_metadata_missing", "source_native_id": canonical_id, "year": year, "missing": [name for name, val in (("title", title), ("authors", authors), ("landing_url", chosen.get("landing_url"))) if not val], "source_url": detail.get("source_url") or (supplement.get("source_url") if supplement else chosen["enumeration_source_url"])})
            continue

        list_time = chosen["enumeration_observed_at"]
        detail_url = detail.get("source_url") or (supplement.get("source_url") if supplement else chosen["enumeration_source_url"])
        detail_time = detail.get("observed_at") or (supplement.get("observed_at") if supplement else list_time)
        observed_times = [list_time]
        if detail.get("observed_at"):
            observed_times.append(detail["observed_at"])
        if supplement:
            observed_times.append(supplement["observed_at"])
        if crossref_record:
            observed_times.append(crossref_record["observed_at"])
        pdf_status = "visible_url" if pdf_url else ("not_visible" if detail.get("source_url") else "metadata_only")
        pdf_method = (
            "Crossref_publisher_deposited_VOR_PDF" if pdf_from_crossref
            else "Europe_PMC_documentStyle_pdf_url" if pdf_from_supplement
            else "observed_article_pdf_url" if pdf_url
            else "checked_no_article_pdf_url" if detail.get("source_url")
            else "Europe_PMC_documentStyle_pdf_not_returned; OUP_article_detail_pending"
        )
        field_provenance = {
            "source_native_id": _prov(chosen["enumeration_source_url"], list_time, "official_issue_or_advance_article_link", source_occurrence_sources=source_occurrence_sources),
            "title": _prov(title_source_url, title_time, "official_citation_title" if title_source_url == detail_url else "official_issue_listing_title"),
            "authors": _prov(authors_source_url, authors_observed_at, "ordered_citation_author_metadata" if detail.get("authors") else "Europe_PMC_ordered_authors"),
            "year": _prov(year_url, year_time, year_method),
            "document_type": _prov(type_source_url, type_observed_at, "official_article_type_or_issue_section"),
            "landing_url": _prov(chosen["enumeration_source_url"], list_time, "observed_official_article_anchor"),
            "abstract": _prov(abstract_source_url, abstract_observed_at, abstract_source_method),
            "doi": _prov(doi_source_url, doi_time, "official_citation_doi" if detail.get("doi") or dois else "Europe_PMC_DOI" if doi_from_supplement else "checked_absent_doi"),
            "publication_date": _prov(publication_date_source_url, publication_date_observed_at, "citation_publication_date_metadata" if detail.get("publication_date") else f"Europe_PMC_publication_date_precision_{publication_date_precision or 'unspecified'}" if publication_date_from_supplement else "checked_no_publication_date"),
            "online_publication_date": _prov(online_date_source_url, online_date_observed_at, online_date_method),
            "pdf_discovery_status": _prov(
                pdf_source_url,
                pdf_time,
                pdf_method,
                **({"content_version": "vor", "license": crossref_record.get("licenses", [])} if pdf_from_crossref and crossref_record else {}),
            ),
        }
        if crossref_record and crossref_vor:
            field_provenance["crossref_pdf_metadata"] = _prov(
                crossref_record["source_url"],
                crossref_record["observed_at"],
                "Crossref_publisher_deposited_VOR_link_and_license_metadata",
                content_version="vor",
                license=crossref_record.get("licenses", []),
            )
        missing: dict[str, Any] = {}
        for field, value, reason_code in (
            ("abstract", abstract, "not_present_on_official_page"),
            ("doi", doi, "not_present_on_official_page"),
            ("publication_date", publication_date, "not_present_on_official_page"),
            ("pdf_url", pdf_url, "not_visible" if detail.get("source_url") else "not_present_on_official_page"),
        ):
            if not value:
                checked_sources = list_urls.copy()
                if detail.get("source_url"):
                    checked_sources.insert(0, detail["source_url"])
                elif supplement:
                    checked_sources.insert(0, supplement["source_url"])
                missing[field] = {"reason_code": reason_code, "checked_sources": checked_sources}
        if not detail.get("source_url") and not pdf_url and (supplement or crossref_record):
            unresolved.append({
                "kind": "oup_article_detail_pdf_check_pending",
                "source_native_id": canonical_id,
                "year": year,
                "landing_url": chosen["landing_url"],
                "supplement_source_url": supplement.get("source_url") if supplement else None,
                "crossref_source_url": crossref_record.get("source_url") if crossref_record else None,
                "am_pdf_candidates_not_usable": crossref_am_candidates,
            })
        staging.append({
            "schema_version": STAGING_SCHEMA,
            "venue_id": VENUE_ID,
            "source_native_id": canonical_id,
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "document_type": selected_type,
            "publication_date": publication_date,
            "publication_date_precision": publication_date_precision,
            "visible_publication_date": detail.get("visible_publication_date"),
            "online_publication_date": online_publication_date,
            "online_publication_date_precision": online_publication_date_precision,
            "crossref_published_online_date": crossref_online_date,
            "crossref_published_online_precision": crossref_online_precision,
            "citation_pmid": detail.get("pmid") or (supplement.get("pmid") if supplement else None),
            "year": year,
            "volume": chosen.get("volume") or detail.get("volume"),
            "issue": chosen.get("issue") or detail.get("issue"),
            "doi": doi,
            "landing_url": chosen["landing_url"],
            "pdf_url": pdf_url,
            "pdf_discovery_status": pdf_status,
            "crossref_vor_pdf_url": crossref_vor.get("url") if crossref_vor else None,
            "crossref_pdf_content_version": "vor" if crossref_vor else None,
            "crossref_pdf_license": crossref_record.get("licenses", []) if crossref_record and crossref_vor else [],
            "inclusion_decision": "include",
            "source_url": chosen["enumeration_source_url"],
            "observed_at": max(observed_times),
            "field_provenance": field_provenance,
            "missing_fields": missing,
            "source_occurrences": [
                {"source_native_id": item["source_native_id"], "page_type": item["page_type"], "source_url": item["enumeration_source_url"], "year": item.get("year"), "volume": item.get("volume"), "issue": item.get("issue")}
                for item in members
            ],
        })
        accounted_ids.add(canonical_id)

    target_issue_paths = set(archive_issues)
    issue_pages_complete = (
        target_issue_paths == set(issue_captures)
        and all(issue_captures[path]["complete"] for path in target_issue_paths)
        and issue_chain_complete
    )
    archive_complete = (
        bool(archive_captures)
        and all(capture["complete"] for capture in archive_captures)
        and archive_chain_complete
        and archive_directories_complete
    )
    advance_complete = bool(advance_captures) and advance_chain_complete
    all_enumerated_ids = {item["source_native_id"] for item in occurrences if item.get("source_native_id")}
    enumeration_issue_kinds = {
        "archive_year_links_missing",
        "archive_issue_year_missing",
        "official_issue_pages_not_captured",
        "issue_capture_not_listed_in_archive",
        "advance_articles_not_captured",
        "listing_page_not_attested_complete",
        "listing_identity_missing",
    }
    enumeration_errors = any(
        row["kind"] in enumeration_issue_kinds
        or row["kind"].startswith("pagination_")
        or row["kind"].startswith("archive_directory_")
        for row in unresolved
    )
    enumeration_complete = archive_complete and issue_pages_complete and advance_complete and not enumeration_errors
    collection_complete = enumeration_complete and not unresolved and all_enumerated_ids <= accounted_ids

    output_root.mkdir(parents=True, exist_ok=True)
    expected_root = output_root / "expected"
    expected_root.mkdir(parents=True, exist_ok=True)
    for previous_file in expected_root.glob("*.jsonl"):
        previous_file.unlink()
    for year, rows in sorted(expected_by_year.items()):
        write_jsonl(expected_root / f"{year}.jsonl", sorted(rows.values(), key=lambda row: row["source_native_id"]))
    write_jsonl(output_root / "expected_source_items.jsonl", sorted(expected_source_items, key=lambda row: row["source_native_id"]))
    write_jsonl(output_root / "source_occurrences.jsonl", sorted(source_occurrences, key=lambda row: (row["source_native_id"], row["enumeration_source_url"])))
    write_jsonl(output_root / "supplement_join_report.jsonl", sorted(supplement_join_rows, key=lambda row: row["source_native_id"]))
    write_jsonl(output_root / "crossref_join_report.jsonl", sorted(crossref_join_rows, key=lambda row: row["source_native_id"]))
    write_jsonl(output_root / "metadata_staging.jsonl", sorted(staging, key=lambda row: (row["year"], row["source_native_id"])))
    write_jsonl(output_root / "metadata_exclusions.jsonl", sorted(exclusions, key=lambda row: (row["year"], row["source_native_id"])))
    write_jsonl(output_root / "unresolved.jsonl", sorted(unresolved, key=lambda row: (str(row.get("year", "")), str(row.get("source_native_id", "")), row["kind"])))

    current_ids = sorted(all_enumerated_ids)
    previous_ids: set[str] = set()
    if previous_expected_root and previous_expected_root.is_dir():
        for manifest in previous_expected_root.glob("*.jsonl*"):
            previous_ids.update(str(row["source_native_id"]) for row in read_jsonl(manifest) if row.get("source_native_id"))
    new_ids = sorted(set(current_ids) - previous_ids)
    missing_ids = sorted(previous_ids - set(current_ids))
    source_hash = hashlib.sha256("\n".join(current_ids).encode("utf-8")).hexdigest()
    source_urls = sorted({capture["source_url"] for capture in captures})
    latest_observed = max((capture["observed_at"] for capture in captures), default=utc_now())
    if not enumeration_complete or missing_ids:
        waterline_status, drift_status = "BLOCKED", "UNRESOLVED_DRIFT" if missing_ids else "NO_DRIFT"
    else:
        waterline_status = "UPDATED" if new_ids else "NO_CHANGE"
        drift_status = "WITHIN_THRESHOLD" if new_ids else "NO_DRIFT"
    waterline = {
        "venue_id": VENUE_ID,
        "status": waterline_status,
        "drift_status": drift_status,
        "enumeration_complete": enumeration_complete,
        "observed_at": latest_observed,
        "source_urls": source_urls,
        "source_item_set_sha256": source_hash,
        "source_item_set_hash_method": "sha256 over newline-joined sorted observed OUP source-native IDs; repeated issue/advance occurrences with the same numeric publisher ID share one identity, distinct publisher IDs remain separate, and raw occurrences are retained separately",
        "current_source_item_count": len(current_ids),
        "new_ids": new_ids,
        "missing_ids": missing_ids,
    }
    write_json(output_root / "waterline_evidence.json", waterline)
    stats = {
        "venue_id": VENUE_ID,
        "status": "READY_FOR_STRICT_VALIDATION" if collection_complete else "INCOMPLETE_SOURCE_EVIDENCE",
        "enumeration_complete": enumeration_complete,
        "metadata_complete": collection_complete,
        "requested_years": [SCOPE_START, datetime.now(timezone.utc).year],
        "archive_issue_count_in_scope": len(archive_issues),
        "archive_year_directories_captured": archive_year_directory_count,
        "archive_volume_directories_captured": archive_volume_directory_count,
        "archive_directories_complete": archive_directories_complete,
        "issue_pages_captured": len(issue_captures),
        "issue_listing_page_captures": len(issue_page_captures),
        "issue_pages_complete": issue_pages_complete,
        "advance_listing_pages": len(advance_captures),
        "advance_pagination_complete": advance_complete,
        "unique_source_identities": len(current_ids),
        "expected_source_item_records": len(expected_source_items),
        "europe_pmc_records_loaded": len(supplement_records),
        "europe_pmc_exact_matches": sum(bool(row["matched"]) for row in supplement_join_rows),
        "europe_pmc_margin_assisted_matches": sum(bool(row["margin_assisted"]) for row in supplement_join_rows),
        "crossref_records_loaded": len(crossref_records),
        "crossref_exact_matches": sum(bool(row["matched"]) for row in crossref_join_rows),
        "crossref_vor_pdf_records": sum(row["observed_vor_pdf_count"] > 0 for row in crossref_join_rows),
        "crossref_am_only_records": sum(row["observed_am_pdf_count"] > 0 and row["observed_vor_pdf_count"] == 0 for row in crossref_join_rows),
        "source_occurrence_count": len(source_occurrences),
        "staging_records": len(staging),
        "exclusions": len(exclusions),
        "unresolved": len(unresolved),
        "abstract_present": sum(bool(row.get("abstract")) for row in staging),
        "doi_present": sum(bool(row.get("doi")) for row in staging),
        "observed_pdf_links": sum(bool(row.get("pdf_url")) for row in staging),
        "yearly_expected": {str(year): len(rows) for year, rows in sorted(expected_by_year.items())},
        "waterline_status": waterline_status,
    }
    write_json(output_root / "collection_summary.json", stats)
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("serve-capture-form", help="serve the localhost-only JSON capture form")
    capture.add_argument("--run-root", type=Path, required=True)
    capture.add_argument("--port", type=int, default=8769)
    collect_parser = subparsers.add_parser("collect", help="build expected/staging files from saved JSON captures")
    collect_parser.add_argument("--pages-index", type=Path, required=True)
    collect_parser.add_argument("--evidence-root", type=Path, required=True)
    collect_parser.add_argument("--output-root", type=Path, required=True)
    collect_parser.add_argument("--previous-expected-root", type=Path)
    collect_parser.add_argument("--supplement-records", type=Path, help="normalized Europe PMC JSONL for exact-identifier field supplementation")
    collect_parser.add_argument("--crossref-records", type=Path, help="normalized Crossref JSONL for exact-DOI published-online/VOR metadata supplementation")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "serve-capture-form":
            serve_capture_form(args.run_root, args.port)
            return 0
        stats = collect(
            args.pages_index,
            args.evidence_root,
            args.output_root,
            args.previous_expected_root,
            args.supplement_records,
            args.crossref_records,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0 if stats["status"] == "READY_FOR_STRICT_VALIDATION" else 1


if __name__ == "__main__":
    raise SystemExit(main())

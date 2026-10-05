#!/usr/bin/env python3
"""Fetch Bioinformatics metadata supplements from the official Europe PMC API.

OUP issue/archive enumeration remains the scope authority. This tool only
downloads public Europe PMC core metadata for the exact Bioinformatics eISSN
and writes resumable, run-local evidence; it never follows full-text links.

Example: `python3 tools/fetch_bioinformatics_europepmc.py --run-dir "$litdb_run/supplement"`
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
from litdb.metadata_pipeline import _hostname_allowed
from litdb.registry import load_source_venues

API_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
EISSN = "1367-4811"
TARGET_DOI_PREFIX = "10.1093/bioinformatics/"
BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "ol", "p", "section",
    "table", "tr", "ul",
}
CHALLENGE_MARKERS = ("captcha", "cloudflare", "security verification", "verify you are human")
SENSITIVE_QUERY_NAME = re.compile(
    r"token|sig(?:nature)?|expires?|credential|authorization|auth(?:key)?|"
    r"access[_-]?key|api[_-]?key|(?:^|[_-])key(?:$|[_-])|x-amz-|x-goog-|jwt|session|cookie",
    re.IGNORECASE,
)
BIOINFORMATICS_VENUE = next(
    venue for venue in load_source_venues() if venue["id"] == "bioinformatics"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


class _AbstractText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_abstract(value: str | None) -> str | None:
    if not value:
        return None
    parser = _AbstractText()
    parser.feed(value)
    parser.close()
    text = html.unescape("".join(parser.parts)).replace("\r", "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line) or None


def redact_sensitive_url(url: str) -> tuple[str, list[str], str | None]:
    """Mask values for secret-like query parameter names without exposing them."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url, [], None
    if not parsed.scheme or not parsed.hostname or not parsed.query:
        return url, [], parsed.hostname.lower() if parsed.hostname else None
    redacted_keys: list[str] = []
    safe_parts = []
    for part in parsed.query.split("&"):
        key_part, separator, value_part = part.partition("=")
        key = unquote_plus(key_part)
        if separator and SENSITIVE_QUERY_NAME.search(key):
            redacted_keys.append(key)
            safe_parts.append(f"{key_part}=%5BREDACTED%5D")
        else:
            safe_parts.append(part)
    safe_url = urlunsplit(parsed._replace(query="&".join(safe_parts)))
    return safe_url, redacted_keys, parsed.hostname.lower()


def redact_urls_in_json(value: object) -> tuple[object, Counter[tuple[str, str]]]:
    """Recursively redact secret-like query values in API URL fields."""
    counts: Counter[tuple[str, str]] = Counter()
    def visit(node: object) -> object:
        if isinstance(node, dict):
            output = {}
            for key, item in node.items():
                if isinstance(item, str) and item.lower().startswith(("https://", "http://")):
                    safe_url, keys, host = redact_sensitive_url(item)
                    output[key] = safe_url
                    if host:
                        for name in keys:
                            counts[(host, name)] += 1
                    if keys:
                        output[f"{key}_redacted_query_parameters"] = keys
                else:
                    output[key] = visit(item)
            return output
        if isinstance(node, list):
            return [visit(item) for item in node]
        return node
    return visit(value), counts


def redact_set_cookie_headers(raw_headers: bytes) -> tuple[bytes, Counter[str]]:
    safe, cookies, _ = redact_response_headers(raw_headers)
    return safe, cookies


def redact_response_headers(raw_headers: bytes) -> tuple[bytes, Counter[str], Counter[tuple[str, str]]]:
    text = raw_headers.decode("utf-8", errors="replace")
    counts: Counter[str] = Counter()
    url_counts: Counter[tuple[str, str]] = Counter()
    safe_lines = []
    for line in text.splitlines():
        header_name, separator, header_value = line.partition(":")
        normalized_name = header_name.strip().lower()
        if separator and normalized_name in {
            "set-cookie", "set-cookie2", "cookie", "authorization", "proxy-authorization",
            "x-api-key", "x-auth-token", "x-access-token",
        }:
            if normalized_name.startswith("set-cookie"):
                cookie_pair = header_value.strip().split(";", 1)[0]
                name = cookie_pair.split("=", 1)[0].strip() or "unknown"
                counts[name] += 1
            else:
                counts[normalized_name] += 1
            safe_lines.append(f"{header_name}: [REDACTED]")
            continue
        def replace_url(match: re.Match[str]) -> str:
            safe_url, keys, host = redact_sensitive_url(match.group(0))
            if host:
                for key in keys:
                    url_counts[(host, key)] += 1
            return safe_url
        safe_lines.append(re.sub(r"https?://[^\s<>\"']+", replace_url, line, flags=re.IGNORECASE))
    safe_text = "\n".join(safe_lines) + ("\n" if raw_headers.endswith((b"\n", b"\r")) else "")
    return safe_text.encode("utf-8"), counts, url_counts


def _challenge_marker(raw_body: bytes) -> str | None:
    sample = raw_body[:8192].decode("utf-8", errors="replace").lstrip().lower()
    return next((marker for marker in CHALLENGE_MARKERS if marker in sample), None)


def date_precision(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"\d{4}", value):
        return "year"
    if re.fullmatch(r"\d{4}[-/]\d{2}", value) or re.fullmatch(r"\d{4}\s+[A-Za-z]{3,9}", value):
        return "month"
    if re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", value) or re.fullmatch(r"\d{8}", value):
        return "day"
    return "as_supplied"


def build_query(year_from: int, year_to: int) -> str:
    return f"ISSN:{EISSN} AND PUB_YEAR:[{year_from} TO {year_to}]"


def build_request_url(query: str, cursor_mark: str, page_size: int) -> str:
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": str(page_size),
        "cursorMark": cursor_mark,
    }
    return f"{API_BASE}/search?{urlencode(params)}"


def _authors(record: dict) -> list[dict]:
    source_authors = record.get("authorList", {}).get("author", []) or []
    result = []
    for index, author in enumerate(source_authors, 1):
        first = author.get("firstName")
        family = author.get("lastName")
        composed = " ".join(part.strip() for part in (first, family) if isinstance(part, str) and part.strip())
        result.append({
            "order": index,
            "name": composed or author.get("fullName") or author.get("collectiveName"),
            "first_name": first,
            "last_name": family,
            "full_name_as_supplied": author.get("fullName"),
            "collective_name": author.get("collectiveName"),
        })
    return result


def _dates(record: dict) -> dict:
    journal_info = record.get("journalInfo") or {}
    raw_values = {
        "dateOfCompletion": record.get("dateOfCompletion"),
        "dateOfCreation": record.get("dateOfCreation"),
        "dateOfRevision": record.get("dateOfRevision"),
        "firstPublicationDate": record.get("firstPublicationDate"),
        "firstIndexDate": record.get("firstIndexDate"),
        "fullTextReceivedDate": record.get("fullTextReceivedDate"),
        "journalInfo.dateOfPublication": journal_info.get("dateOfPublication"),
        "journalInfo.printPublicationDate": journal_info.get("printPublicationDate"),
    }
    return {
        key: {"value": value, "precision": date_precision(value)}
        for key, value in raw_values.items()
        if value is not None
    }


def normalize_record(
    record: dict,
    *,
    source_url: str,
    observed_at: str,
    page_number: int,
    requested_from_year: int,
    requested_to_year: int,
) -> dict:
    journal_info = record.get("journalInfo") or {}
    journal = journal_info.get("journal") or {}
    full_text = record.get("fullTextUrlList", {}).get("fullTextUrl", []) or []
    publication_year = journal_info.get("yearOfPublication") or record.get("pubYear")
    try:
        publication_year = int(publication_year) if publication_year is not None else None
    except (TypeError, ValueError):
        publication_year = None
    issns = [
        value.strip()
        for value in (journal.get("issn"), journal.get("essn"))
        if isinstance(value, str) and value.strip()
    ]
    pdf_links = []
    redacted_query_parameters: Counter[str] = Counter()
    for item in full_text:
        if str(item.get("documentStyle") or "").lower() != "pdf" or not item.get("url"):
            continue
        link = {key: item.get(key) for key in ("availability", "availabilityCode", "documentStyle", "site", "url")}
        redacted_keys = item.get("url_redacted_query_parameters")
        if isinstance(redacted_keys, list):
            link["url_redacted_query_parameters"] = redacted_keys
            redacted_query_parameters.update(str(key) for key in redacted_keys)
        pdf_links.append(link)
    native_id = record.get("id")
    native_source = record.get("source")
    normalized_doi = str(record.get("doi") or "").strip().lower()
    matches_target_doi_prefix = normalized_doi.startswith(TARGET_DOI_PREFIX)
    doi_scope_status = (
        "target_prefix" if matches_target_doi_prefix
        else "non_target_doi" if normalized_doi
        else "missing_doi_requires_identity_join"
    )
    within_requested_year_window = (
        requested_from_year <= publication_year <= requested_to_year
        if publication_year is not None
        else False
    )
    within_scope_year_window = (
        2015 <= publication_year <= requested_to_year
        if publication_year is not None
        else False
    )
    within_collection_scope = (
        within_scope_year_window and matches_target_doi_prefix
    )
    return {
        "schema_version": "europepmc-bioinformatics-supplement-v1",
        "venue_id": "bioinformatics",
        "supplement_source": "Europe PMC REST API",
        "source_url": source_url,
        "observed_at": observed_at,
        "source_page": page_number,
        "europepmc_native_id": {"source": native_source, "id": native_id},
        "pmid": record.get("pmid"),
        "pmcid": record.get("pmcid"),
        "doi": record.get("doi"),
        "matches_target_doi_prefix": matches_target_doi_prefix,
        "doi_scope_status": doi_scope_status,
        "journal": {
            "title": journal.get("title"),
            "medline_abbreviation": journal.get("medlineAbbreviation"),
            "issn": journal.get("issn"),
            "eissn": journal.get("essn"),
            "issn_values": issns,
            "matches_target_eissn": EISSN in issns,
        },
        "volume": journal_info.get("volume"),
        "issue": journal_info.get("issue"),
        "issue_year": publication_year,
        "requested_year_window": [requested_from_year, requested_to_year],
        "within_requested_year_window": within_requested_year_window,
        "within_collection_scope": within_collection_scope,
        "needs_expected_identity_join_for_scope": within_scope_year_window and doi_scope_status == "missing_doi_requires_identity_join",
        "year_window_margin_only": publication_year is not None and requested_from_year <= publication_year < 2015,
        "title": record.get("title"),
        "authors": _authors(record),
        "author_string_as_supplied": record.get("authorString"),
        "abstract": clean_abstract(record.get("abstractText")),
        "abstract_source_field": "abstractText",
        "dates_as_supplied": _dates(record),
        "publication_types": record.get("pubTypeList", {}).get("pubType", []) or [],
        "full_text_url_list_as_supplied": full_text,
        "pdf_links_as_supplied": pdf_links,
        "sensitive_url_query_values_redacted_by_parameter": dict(sorted(redacted_query_parameters.items())),
        "full_text_downloaded": False,
    }


def load_checkpoint(path: Path, query: str, years: tuple[int, int], page_size: int) -> dict:
    if not path.is_file():
        return {
            "schema_version": "europepmc-cursor-checkpoint-v1",
            "query": query,
            "year_window": list(years),
            "page_size": page_size,
            "status": "not_started",
            "next_cursor_mark": "*",
            "next_page_number": 1,
            "hit_count": None,
            "pages": [],
            "started_at": utc_now(),
            "updated_at": utc_now(),
        }
    checkpoint = json.loads(path.read_text())
    if checkpoint.get("query") != query or checkpoint.get("year_window") != list(years):
        raise ValueError("existing checkpoint query/year window differs; use a fresh run directory")
    if checkpoint.get("page_size") != page_size:
        raise ValueError("existing checkpoint page_size differs; resume with the same page size")
    if checkpoint.get("status", "").startswith("blocked_"):
        raise ValueError(f"checkpoint is blocked ({checkpoint['status']}); preserve it and review before resuming")
    return checkpoint


def curl_request(curl: str, url: str, body_path: Path, headers_path: Path, timeout: int) -> dict:
    observed_at = utc_now()
    with tempfile.TemporaryDirectory(prefix="litdb-europepmc-") as temporary_dir:
        temporary = Path(temporary_dir)
        raw_body_path = temporary / "response.body"
        raw_headers_path = temporary / "response.headers"
        command = [
            curl,
            "--silent", "--show-error", "--max-time", str(timeout),
            "--header", "Accept: application/json",
            "--dump-header", str(raw_headers_path),
            "--output", str(raw_body_path),
            "--write-out", "%{http_code}\t%{url_effective}\t%{content_type}\n",
            url,
        ]
        # Default system curl TLS verification stays enabled. Never add --insecure/-k.
        completed = subprocess.run(command, text=True, capture_output=True)
        if completed.returncode != 0 and not raw_body_path.exists():
            raise RuntimeError(f"curl failed before a response was saved: {completed.stderr.strip()}")
        output = completed.stdout.strip().split("\t", 2)
        if len(output) != 3:
            raise RuntimeError(f"curl did not return HTTP metadata: {completed.stderr.strip()}")
        status_text, effective_url, content_type = output
        raw_body = raw_body_path.read_bytes() if raw_body_path.exists() else b""
        raw_headers = raw_headers_path.read_bytes() if raw_headers_path.exists() else b""
        body_hash_before = hashlib.sha256(raw_body).hexdigest()
        headers_hash_before = hashlib.sha256(raw_headers).hexdigest()
        url_redactions: Counter[tuple[str, str]] = Counter()
        marker = _challenge_marker(raw_body)
        try:
            parsed_body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed_body = None
            body_kind = "challenge" if marker else "non_json"
            safe_body_value = {
                "response_body_redacted": True,
                "http_body_sha256": body_hash_before,
                "http_body_byte_count": len(raw_body),
                "reason": "challenge_body_not_retained" if marker else "non_json_or_invalid_json_response_body_not_retained",
                "challenge_marker_detected": bool(marker),
            }
        else:
            if marker and not (
                isinstance(parsed_body, dict) and isinstance(parsed_body.get("resultList"), dict)
            ):
                # Challenge responses can be JSON as well as HTML. Do not retain
                # challenge contents; only persist a boolean classification and hashes.
                body_kind = "challenge"
                safe_body_value = {
                    "response_body_redacted": True,
                    "http_body_sha256": body_hash_before,
                    "http_body_byte_count": len(raw_body),
                    "reason": "challenge_body_not_retained",
                    "challenge_marker_detected": True,
                }
            else:
                body_kind = "json"
                safe_body_value, url_redactions = redact_urls_in_json(parsed_body)
        safe_body = (json.dumps(safe_body_value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        safe_headers, cookie_names, header_url_redactions = redact_response_headers(raw_headers)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        headers_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(safe_body)
        headers_path.write_bytes(safe_headers)
        redaction_receipt = {
            "http_body_sha256_before_redaction": body_hash_before,
            "retained_body_sha256": hashlib.sha256(safe_body).hexdigest(),
            "http_body_byte_count_before_redaction": len(raw_body),
            "retained_body_byte_count": len(safe_body),
            "sensitive_url_query_value_redactions_by_host_and_key": {
                f"{host}::{key}": count for (host, key), count in sorted(url_redactions.items())
            },
            "response_headers_sha256_before_redaction": headers_hash_before,
            "retained_headers_sha256": hashlib.sha256(safe_headers).hexdigest(),
            "set_cookie_header_redactions_by_name": dict(sorted(cookie_names.items())),
            "set_cookie_header_redaction_count": sum(cookie_names.values()),
            "response_header_sensitive_url_query_value_redactions_by_host_and_key": {
                f"{host}::{key}": count for (host, key), count in sorted(header_url_redactions.items())
            },
        }
    try:
        status = int(status_text)
    except ValueError as exc:
        raise RuntimeError(f"invalid HTTP status from curl: {status_text!r}") from exc
    return {
        "requested_url": url,
        "source_url": effective_url,
        "observed_at": observed_at,
        "http_status": status,
        "content_type": content_type,
        "response_body_kind": body_kind,
        "challenge_marker_detected": body_kind == "challenge",
        "curl_exit_code": completed.returncode,
        "curl_stderr": completed.stderr.strip() or None,
        "response_file": str(body_path),
        "response_headers_file": str(headers_path),
        "sanitization_receipt": redaction_receipt,
    }


def _safe_json_response(body_path: Path, content_type: str) -> tuple[dict | None, str | None]:
    body = body_path.read_bytes()
    sample = body[:8192].decode("utf-8", errors="replace").lstrip().lower()
    if not ("json" in content_type.lower() or sample.startswith("{") or sample.startswith("[")):
        marker = next((item for item in CHALLENGE_MARKERS if item in sample), None)
        return None, "verification_challenge_or_non_json" if marker or sample.startswith("<") else "non_json_response"
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None, "invalid_json_response"
    if not isinstance(value, dict) or not isinstance(value.get("resultList"), dict):
        return None, "unexpected_api_response_shape"
    return value, None


def _verify_effective_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.ebi.ac.uk"
        and parsed.path.startswith("/europepmc/webservices/rest/")
    )


def _write_normalized(run_dir: Path, checkpoint: dict, years: tuple[int, int]) -> dict:
    unique: dict[tuple[str, str], tuple[dict, str]] = {}
    errors: list[dict] = []
    raw_count = 0
    for page in checkpoint["pages"]:
        page_file = run_dir / page["response_file"]
        response = json.loads(page_file.read_text())
        results = response.get("resultList", {}).get("result", []) or []
        raw_count += len(results)
        for record in results:
            source = str(record.get("source") or "")
            record_id = str(record.get("id") or "")
            if not source or not record_id:
                errors.append({"page": page["page_number"], "error": "missing Europe PMC source/id", "doi": record.get("doi")})
                continue
            key = (source, record_id)
            fingerprint = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            if key in unique:
                if unique[key][1] != fingerprint:
                    errors.append({"page": page["page_number"], "error": "conflicting duplicate source identity", "source": source, "id": record_id})
                continue
            normalized = normalize_record(
                record,
                source_url=page["source_url"],
                observed_at=page["observed_at"],
                page_number=page["page_number"],
                requested_from_year=years[0],
                requested_to_year=years[1],
            )
            unique[key] = (normalized, fingerprint)

    records = [item[0] for item in unique.values()]
    doi_groups: dict[str, list[dict]] = defaultdict(list)
    pmid_groups: dict[str, list[dict]] = defaultdict(list)
    for item in records:
        if item.get("doi"):
            doi_groups[item["doi"].strip().lower()].append(item)
        if item.get("pmid"):
            pmid_groups[str(item["pmid"])].append(item)
    duplicate_doi_values = {key: values for key, values in doi_groups.items() if len(values) > 1}
    duplicate_pmid_values = {key: values for key, values in pmid_groups.items() if len(values) > 1}
    year_eligible_records = [item for item in records if item.get("within_requested_year_window") and not item.get("year_window_margin_only")]
    scope_records = [item for item in records if item.get("within_collection_scope")]
    margin_records = [item for item in records if item.get("year_window_margin_only")]
    target_prefix_records = [item for item in records if item.get("matches_target_doi_prefix")]
    target_prefix_scope_records = [item for item in scope_records if item.get("matches_target_doi_prefix")]
    target_prefix_margin_records = [item for item in margin_records if item.get("matches_target_doi_prefix")]
    non_target_doi_records = [item for item in records if item.get("doi_scope_status") == "non_target_doi"]
    missing_doi_records = [item for item in records if item.get("doi_scope_status") == "missing_doi_requires_identity_join"]
    redactions_by_parameter: Counter[str] = Counter()
    for item in records:
        redactions_by_parameter.update(item.get("sensitive_url_query_values_redacted_by_parameter", {}))
    page_sanitization = [
        {"page_number": page.get("page_number"), **page["sanitization_receipt"]}
        for page in checkpoint["pages"]
        if isinstance(page.get("sanitization_receipt"), dict)
    ]
    redaction_receipt = {
        "schema_version": "europepmc-sensitive-data-redaction-receipt-v1",
        "generated_at": utc_now(),
        "policy": "Secret-like URL query values and every Set-Cookie response-header value are redacted before durable run evidence is written. Original and retained file hashes are recorded; no secret values are retained in the receipt.",
        "page_receipts": page_sanitization,
        "sensitive_url_query_value_redactions_by_parameter": dict(sorted(redactions_by_parameter.items())),
        "sensitive_url_query_value_redactions_by_host_and_key": {},
        "set_cookie_header_redaction_count": sum(
            int(receipt.get("set_cookie_header_redaction_count", 0)) for receipt in page_sanitization
        ),
        "set_cookie_header_redactions_by_name": dict(sorted(Counter({}).items())),
    }
    host_key_redactions: Counter[str] = Counter()
    header_host_key_redactions: Counter[str] = Counter()
    cookie_name_redactions: Counter[str] = Counter()
    for receipt in page_sanitization:
        host_key_redactions.update(receipt.get("sensitive_url_query_value_redactions_by_host_and_key", {}))
        header_host_key_redactions.update(receipt.get("response_header_sensitive_url_query_value_redactions_by_host_and_key", {}))
        cookie_name_redactions.update(receipt.get("set_cookie_header_redactions_by_name", {}))
    redaction_receipt["sensitive_url_query_value_redactions_by_host_and_key"] = dict(sorted(host_key_redactions.items()))
    redaction_receipt["response_header_sensitive_url_query_value_redactions_by_host_and_key"] = dict(sorted(header_host_key_redactions.items()))
    redaction_receipt["set_cookie_header_redactions_by_name"] = dict(sorted(cookie_name_redactions.items()))
    atomic_json(run_dir / "sensitive-data-redaction-receipt.json", redaction_receipt)
    def api_pdf_link(item: dict) -> bool:
        return bool(item.get("pdf_links_as_supplied"))

    def allowlisted_pdf_link(item: dict) -> bool:
        return any(
            not link.get("url_redacted_query_parameters")
            and _hostname_allowed(
                str(link.get("url") or ""),
                set(BIOINFORMATICS_VENUE.get("allowed_domains", [])),
                BIOINFORMATICS_VENUE.get("allowed_path_prefixes", {}),
            )
            for link in item.get("pdf_links_as_supplied", [])
        )

    def redacted_pdf_link(item: dict) -> bool:
        return any(link.get("url_redacted_query_parameters") for link in item.get("pdf_links_as_supplied", []))

    coverage_fields = {
        "title": lambda item: bool(item.get("title")),
        "authors": lambda item: bool(item.get("authors")),
        "abstract": lambda item: bool(item.get("abstract")),
        "doi": lambda item: bool(item.get("doi")),
        "pmid": lambda item: bool(item.get("pmid")),
        "pmcid": lambda item: bool(item.get("pmcid")),
        "volume": lambda item: bool(item.get("volume")),
        "issue": lambda item: bool(item.get("issue")),
        "publication_date": lambda item: bool(item.get("dates_as_supplied")),
        "pdf_link": api_pdf_link,
        "allowlisted_usable_pdf_link": allowlisted_pdf_link,
        "redacted_sensitive_query_pdf_link": redacted_pdf_link,
    }
    coverage = {}
    for name, present in coverage_fields.items():
        coverage[name] = {
            "year_eligible_query_present": sum(present(item) for item in year_eligible_records),
            "year_eligible_query_total": len(year_eligible_records),
            "margin_present": sum(present(item) for item in margin_records),
            "margin_total": len(margin_records),
        }
    target_prefix_coverage = {
        name: {
            "target_doi_prefix_present": sum(present(item) for item in target_prefix_scope_records),
            "target_doi_prefix_total": len(target_prefix_scope_records),
            "margin_present": sum(present(item) for item in target_prefix_margin_records),
            "margin_total": len(target_prefix_margin_records),
        }
        for name, present in coverage_fields.items()
    }
    output = run_dir / "normalized_records.jsonl"
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for item in records:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(output)
    year_counts = Counter(str(item.get("issue_year")) for item in records)
    page_hit_counts = {page.get("hit_count") for page in checkpoint.get("pages", [])}
    pagination_summary = {
        "checkpoint_page_count": len(checkpoint.get("pages", [])),
        "nonempty_result_page_count": sum(int(page.get("result_count", 0)) > 0 for page in checkpoint.get("pages", [])),
        "terminal_empty_page_count": sum(bool(page.get("terminal_empty_page")) for page in checkpoint.get("pages", [])),
        "page_record_count_sum": sum(int(page.get("result_count", 0)) for page in checkpoint.get("pages", [])),
        "reported_hit_count": checkpoint.get("hit_count"),
        "hit_count_stable_across_pages": len(page_hit_counts) <= 1,
    }
    summary = {
        "schema_version": "europepmc-supplement-normalization-report-v1",
        "generated_at": utc_now(),
        "raw_record_count": raw_count,
        "unique_source_identity_count": len(records),
        "cursor_pagination": pagination_summary,
        "target_doi_prefix_in_requested_collection_year_window_count": len(scope_records),
        "eissn_query_year_eligible_count": len(year_eligible_records),
        "margin_only_2014_count": len(margin_records),
        "target_doi_prefix_record_count": len(target_prefix_records),
        "target_doi_prefix_in_scope_year_count": len(target_prefix_scope_records),
        "target_doi_prefix_margin_2014_count": len(target_prefix_margin_records),
        "non_target_doi_record_count": len(non_target_doi_records),
        "non_target_dois": sorted({item.get("doi") for item in non_target_doi_records if item.get("doi")}),
        "missing_doi_record_count_requires_exact_identity_join": len(missing_doi_records),
        "missing_doi_year_eligible_count_requires_exact_identity_join": sum(
            item.get("within_requested_year_window") and not item.get("year_window_margin_only")
            for item in missing_doi_records
        ),
        "non_target_doi_year_eligible_count_excluded": sum(
            item.get("within_requested_year_window") and not item.get("year_window_margin_only")
            for item in non_target_doi_records
        ),
        "target_doi_prefix_candidate_policy": "A target DOI prefix is a Bioinformatics metadata-supplement candidate marker only; final use still requires an exact expected OUP DOI/verified PMID join. Rows with missing DOI need identity confirmation. Non-target DOI records are never target supplements.",
        "other_or_missing_year_count": len(records) - len(year_eligible_records) - len(margin_records),
        "unique_doi_count": len(doi_groups),
        "missing_doi_count": sum(not item.get("doi") for item in records),
        "missing_pmid_count": sum(not item.get("pmid") for item in records),
        "duplicate_source_identity_count": raw_count - len(records),
        "conflicting_source_identity_errors": errors,
        "duplicate_doi_values": {key: len(values) for key, values in sorted(duplicate_doi_values.items())},
        "duplicate_doi_excess_record_count": sum(len(values) - 1 for values in duplicate_doi_values.values()),
        "duplicate_pmid_values": {key: len(values) for key, values in sorted(duplicate_pmid_values.items())},
        "target_eissn_match_count": sum(item["journal"]["matches_target_eissn"] for item in records),
        "target_eissn_mismatch_count": sum(not item["journal"]["matches_target_eissn"] for item in records),
        "year_counts_all_results": dict(sorted(year_counts.items())),
        "field_coverage": coverage,
        "target_doi_prefix_field_coverage": target_prefix_coverage,
        "pdf_link_record_count": sum(bool(item.get("pdf_links_as_supplied")) for item in records),
        "pdf_link_count": sum(len(item.get("pdf_links_as_supplied", [])) for item in records),
        "api_reported_pdf_link_record_count": sum(api_pdf_link(item) for item in records),
        "api_reported_pdf_link_count": sum(len(item.get("pdf_links_as_supplied", [])) for item in records),
        "allowlisted_usable_pdf_link_record_count": sum(allowlisted_pdf_link(item) for item in records),
        "allowlisted_usable_pdf_link_count": sum(
            sum(allowlisted_pdf_link({"pdf_links_as_supplied": [link]}) for link in item.get("pdf_links_as_supplied", []))
            for item in records
        ),
        "redacted_sensitive_query_pdf_link_record_count": sum(redacted_pdf_link(item) for item in records),
        "redacted_sensitive_query_pdf_link_count": sum(
            bool(link.get("url_redacted_query_parameters"))
            for item in records
            for link in item.get("pdf_links_as_supplied", [])
        ),
        "target_doi_prefix_allowlisted_usable_pdf_record_count": sum(allowlisted_pdf_link(item) for item in target_prefix_scope_records),
        "target_doi_prefix_api_reported_pdf_record_count": sum(api_pdf_link(item) for item in target_prefix_scope_records),
        "pdf_link_status": "API-reported links only; sensitive query values are redacted; allowlisted count uses the Bioinformatics source/runtime URL policy; no link test or download performed",
        "sensitive_url_query_value_redactions_by_parameter": dict(sorted(redactions_by_parameter.items())),
        "normalized_records_file": output.name,
        "raw_api_responses": [page["response_file"] for page in checkpoint["pages"]],
        "out_of_scope_margin_policy": "Pre-2015 rows in the requested year window are preserved as margin-only. Rows from 2015 through the requested upper year are year-eligible; the target DOI prefix identifies Bioinformatics candidates, missing DOI rows require exact expected-identity confirmation, and non-target DOI rows are excluded.",
        "not_oup_scope_evidence": True,
    }
    atomic_json(run_dir / "normalization_report.json", summary)
    return summary


def _recover_empty_terminal_page(run_dir: Path, checkpoint: dict) -> bool:
    """Accept a saved empty cursor probe when it proves the hit count is exhausted."""
    if checkpoint.get("status") != "failed_missing_cursor":
        return False
    request = checkpoint.get("last_error")
    if not isinstance(request, dict) or request.get("http_status") != 200:
        return False
    response_path = Path(str(request.get("response_file") or ""))
    try:
        response_path = response_path.resolve(strict=True)
        response_path.relative_to(run_dir.resolve())
    except (OSError, ValueError):
        return False
    try:
        response = json.loads(response_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    results = response.get("resultList", {}).get("result", []) if isinstance(response, dict) else None
    hit_count = response.get("hitCount") if isinstance(response, dict) else None
    if (
        results != []
        or response.get("nextCursorMark") is not None
        or hit_count != checkpoint.get("hit_count")
        or checkpoint.get("records_fetched") != hit_count
        or not _verify_effective_url(str(request.get("source_url") or ""))
    ):
        return False

    page_number = checkpoint.get("next_page_number")
    page_entry = {
        "page_number": page_number,
        "cursor_mark": checkpoint.get("next_cursor_mark"),
        "next_cursor_mark": None,
        "terminal_empty_page": True,
        "request_url": request.get("requested_url"),
        "source_url": request.get("source_url"),
        "observed_at": request.get("observed_at"),
        "http_status": request.get("http_status"),
        "content_type": request.get("content_type"),
        "result_count": 0,
        "hit_count": hit_count,
        "response_file": str(response_path.relative_to(run_dir.resolve())),
        "response_headers_file": str(Path(str(request.get("response_headers_file") or "")).resolve().relative_to(run_dir.resolve())),
        "sanitization_receipt": request.get("sanitization_receipt"),
    }
    checkpoint["pages"].append(page_entry)
    checkpoint["pages_fetched"] = len(checkpoint["pages"])
    checkpoint["status"] = "complete"
    checkpoint["completed_at"] = utc_now()
    checkpoint["updated_at"] = utc_now()
    checkpoint["terminal_empty_page_observation"] = page_entry
    checkpoint.pop("last_error", None)
    return True


def fetch(args: argparse.Namespace) -> int:
    if not 1 <= args.page_size <= 500:
        raise ValueError("page-size must be between 1 and 500")
    if args.year_from > args.year_to:
        raise ValueError("year-from must be <= year-to")
    if args.delay_seconds < 1.0:
        raise ValueError("delay-seconds must be at least 1 second")
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("system curl is required")

    run_dir = args.run_dir.expanduser().resolve()
    pages_dir = run_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    query = build_query(args.year_from, args.year_to)
    years = (args.year_from, args.year_to)
    checkpoint_path = run_dir / "checkpoint.json"
    checkpoint = load_checkpoint(checkpoint_path, query, years, args.page_size)
    if _recover_empty_terminal_page(run_dir, checkpoint):
        atomic_json(checkpoint_path, checkpoint)
    atomic_json(run_dir / "collection_manifest.json", {
        "schema_version": "europepmc-bioinformatics-collection-v1",
        "venue_id": "bioinformatics",
        "api_docs_url": "https://europepmc.org/RestfulWebService",
        "api_base_url": API_BASE,
        "query": query,
        "year_window_requested": list(years),
        "collection_scope_years": [2015, args.year_to],
        "margin_only_years": list(range(args.year_from, min(args.year_to, 2014) + 1)),
        "page_size": args.page_size,
        "delay_seconds_between_requests": args.delay_seconds,
        "pagination": "cursorMark / nextCursorMark",
        "tls": "system curl default certificate verification; insecure mode is not enabled",
        "full_text_downloaded": False,
        "scope_authority": "OUP archive enumeration remains the expected-identity source; this API is supplementary metadata only.",
        "evidence_redaction_policy": "Sensitive URL query values and Set-Cookie response-header values are redacted before durable evidence is written; hashes and aggregate key/host counts are retained.",
        "created_at": checkpoint["started_at"],
    })
    if checkpoint.get("status") == "complete":
        summary = _write_normalized(run_dir, checkpoint, years)
        print(json.dumps({"status": "complete", "summary": summary}, ensure_ascii=False))
        return 0

    pages_this_run = 0
    previous_page_count = len(checkpoint.get("pages", []))
    checkpoint["status"] = "running"
    checkpoint["updated_at"] = utc_now()
    atomic_json(checkpoint_path, checkpoint)

    while True:
        if args.max_pages is not None and pages_this_run >= args.max_pages:
            checkpoint["status"] = "paused_after_max_pages"
            checkpoint["updated_at"] = utc_now()
            atomic_json(checkpoint_path, checkpoint)
            break
        page_number = checkpoint["next_page_number"]
        cursor_mark = checkpoint["next_cursor_mark"]
        if page_number > 1 or pages_this_run > 0:
            time.sleep(args.delay_seconds)
        request_url = build_request_url(query, cursor_mark, args.page_size)
        body_path = pages_dir / f"page-{page_number:05d}.json"
        headers_path = pages_dir / f"page-{page_number:05d}.headers.txt"
        try:
            request = curl_request(curl, request_url, body_path, headers_path, args.timeout)
        except Exception as exc:
            checkpoint.update(status="failed_request", updated_at=utc_now(), last_error=str(exc))
            atomic_json(checkpoint_path, checkpoint)
            raise

        if not _verify_effective_url(request["source_url"]):
            request["stop_reason"] = "redirect_outside_allowlist"
            checkpoint.update(status="blocked_redirect", updated_at=utc_now(), last_error=request)
            atomic_json(checkpoint_path, checkpoint)
            print(json.dumps({"status": checkpoint["status"], "request": request}, ensure_ascii=False))
            return 2
        if request["http_status"] in {403, 429}:
            request["stop_reason"] = f"http_{request['http_status']}"
            checkpoint.update(status=f"blocked_http_{request['http_status']}", updated_at=utc_now(), last_error=request)
            atomic_json(checkpoint_path, checkpoint)
            print(json.dumps({"status": checkpoint["status"], "request": request}, ensure_ascii=False))
            return 2
        if request["http_status"] != 200:
            request["stop_reason"] = "unexpected_http_status"
            checkpoint.update(status="failed_http_status", updated_at=utc_now(), last_error=request)
            atomic_json(checkpoint_path, checkpoint)
            print(json.dumps({"status": checkpoint["status"], "request": request}, ensure_ascii=False))
            return 2
        if request.get("challenge_marker_detected"):
            request["stop_reason"] = "challenge_marker_detected_before_redaction"
            checkpoint.update(status="blocked_challenge", updated_at=utc_now(), last_error=request)
            atomic_json(checkpoint_path, checkpoint)
            print(json.dumps({"status": checkpoint["status"], "request": request}, ensure_ascii=False))
            return 2
        if request.get("response_body_kind") != "json":
            request["stop_reason"] = "non_json_response_body_redacted"
            checkpoint.update(status="failed_response", updated_at=utc_now(), last_error=request)
            atomic_json(checkpoint_path, checkpoint)
            print(json.dumps({"status": checkpoint["status"], "request": request}, ensure_ascii=False))
            return 2
        response, problem = _safe_json_response(body_path, request["content_type"])
        if problem:
            request["stop_reason"] = problem
            checkpoint.update(status="blocked_challenge" if "challenge" in problem else "failed_response", updated_at=utc_now(), last_error=request)
            atomic_json(checkpoint_path, checkpoint)
            print(json.dumps({"status": checkpoint["status"], "request": request}, ensure_ascii=False))
            return 2

        results = response.get("resultList", {}).get("result", []) or []
        hit_count = response.get("hitCount")
        if checkpoint.get("hit_count") is None:
            checkpoint["hit_count"] = hit_count
        elif hit_count != checkpoint["hit_count"]:
            checkpoint.update(status="failed_hitcount_drift", updated_at=utc_now(), last_error={"previous": checkpoint["hit_count"], "current": hit_count, "request": request})
            atomic_json(checkpoint_path, checkpoint)
            print(json.dumps({"status": checkpoint["status"], "last_error": checkpoint["last_error"]}, ensure_ascii=False))
            return 2
        next_cursor = response.get("nextCursorMark")
        if not isinstance(next_cursor, str) or not next_cursor:
            checkpoint.update(status="failed_missing_cursor", updated_at=utc_now(), last_error=request)
            atomic_json(checkpoint_path, checkpoint)
            return 2

        page_entry = {
            "page_number": page_number,
            "cursor_mark": cursor_mark,
            "next_cursor_mark": next_cursor,
            "request_url": request["requested_url"],
            "source_url": request["source_url"],
            "observed_at": request["observed_at"],
            "http_status": request["http_status"],
            "content_type": request["content_type"],
            "result_count": len(results),
            "hit_count": hit_count,
            "response_file": str(body_path.relative_to(run_dir)),
            "response_headers_file": str(headers_path.relative_to(run_dir)),
            "sanitization_receipt": request["sanitization_receipt"],
        }
        checkpoint["pages"].append(page_entry)
        checkpoint["pages_fetched"] = len(checkpoint["pages"])
        checkpoint["records_fetched"] = sum(page["result_count"] for page in checkpoint["pages"])
        checkpoint["next_cursor_mark"] = next_cursor
        checkpoint["next_page_number"] = page_number + 1
        checkpoint["updated_at"] = utc_now()
        finished = next_cursor == cursor_mark or len(results) == 0
        if finished and checkpoint["records_fetched"] != hit_count:
            checkpoint.update(
                status="blocked_incomplete_hitcount",
                updated_at=utc_now(),
                last_error={
                    "reason": "terminal_cursor_before_hit_count_exhausted",
                    "termination_condition": "repeated_cursor" if next_cursor == cursor_mark else "empty_result_page",
                    "records_fetched": checkpoint["records_fetched"],
                    "hit_count": hit_count,
                    "page_number": page_number,
                    "cursor_mark": cursor_mark,
                    "next_cursor_mark": next_cursor,
                },
            )
            checkpoint.pop("completed_at", None)
        else:
            checkpoint["status"] = "complete" if finished else "running"
            checkpoint["completed_at"] = utc_now() if finished else None
        atomic_json(checkpoint_path, checkpoint)
        pages_this_run += 1
        summary = _write_normalized(run_dir, checkpoint, years)
        if checkpoint.get("status") == "blocked_incomplete_hitcount":
            print(json.dumps({
                "status": checkpoint["status"],
                "last_error": checkpoint["last_error"],
                "summary": summary,
            }, ensure_ascii=False))
            return 2
        print(json.dumps({
            "status": checkpoint["status"],
            "page_number": page_number,
            "page_records": len(results),
            "records_fetched": checkpoint["records_fetched"],
            "hit_count": hit_count,
            "next_cursor_mark": next_cursor,
            "source_url": request["source_url"],
            "observed_at": request["observed_at"],
        }, ensure_ascii=False))
        if finished:
            break

    checkpoint = json.loads(checkpoint_path.read_text())
    summary = _write_normalized(run_dir, checkpoint, years)
    if checkpoint.get("status") == "complete":
        print(json.dumps({"status": "complete", "summary": summary}, ensure_ascii=False))
    else:
        print(json.dumps({"status": checkpoint.get("status"), "pages_in_this_run": pages_this_run, "summary": summary}, ensure_ascii=False))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="run-local output directory; for example $litdb_run/supplement",
    )
    parser.add_argument("--year-from", type=int, default=2014)
    parser.add_argument("--year-to", type=int, default=datetime.now().year)
    parser.add_argument("--page-size", type=int, default=500, help="Europe PMC page size, maximum 500")
    parser.add_argument("--delay-seconds", type=float, default=1.5, help="minimum 1 second between API requests")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-pages", type=int, help="optional resumable cap, mainly for response-shape checks")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return fetch(args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

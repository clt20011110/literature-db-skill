#!/usr/bin/env python3
"""Fetch Crossref metadata for Bioinformatics (ISSN 1367-4811).

This is a metadata-only sidecar fetcher. It contacts only api.crossref.org,
never follows redirects, and never requests publisher landing pages or PDFs.
Raw page responses and checkpoints are kept in a run directory so pagination
can resume without repeating completed pages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from collections import Counter
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from fetch_bioinformatics_europepmc import (  # noqa: E402
    redact_response_headers,
    redact_set_cookie_headers,
    redact_urls_in_json,
)


API_ENDPOINT = "https://api.crossref.org/journals/1367-4811/works"
ISSN = "1367-4811"
DOI_PREFIX = "10.1093/bioinformatics/"
SELECT_FIELDS = (
    "DOI,ISSN,resource,title,type,issued,published,published-print,"
    "published-online,license,link,volume,issue,page,article-number"
)
USER_AGENT = "paper-metadata-litdb-crossref/0.1 (metadata-only journal supplement)"
CHECKPOINT_SCHEMA = "bioinformatics-crossref-cursor-v2"
DATE_KEYS = ("issued", "published", "published-print", "published-online")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def query_settings(year_from: int, year_to: int, rows: int) -> dict[str, Any]:
    if year_from < 1900 or year_to < 1900 or year_from > year_to:
        raise ValueError("publication year range must be ordered and at least 1900")
    date_from = f"{year_from}-01-01"
    date_to = f"{year_to}-12-31"
    return {
        "endpoint": API_ENDPOINT,
        "issn": ISSN,
        "doi_prefix": DOI_PREFIX,
        "year_from": year_from,
        "year_to": year_to,
        "date_from": date_from,
        "date_to": date_to,
        "filter": f"from-pub-date:{date_from},until-pub-date:{date_to}",
        "page_size": rows,
        "select_fields": SELECT_FIELDS,
    }


def validate_checkpoint_settings(
    checkpoint: dict[str, Any], expected: dict[str, Any]
) -> None:
    if checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError("unsupported checkpoint schema")
    if checkpoint.get("query_settings") != expected:
        raise ValueError(
            "run settings differ from the saved checkpoint; choose a new --run-dir"
        )


def query_url(cursor: str, settings: dict[str, Any]) -> str:
    query = urllib.parse.urlencode(
        {
            "filter": settings["filter"],
            "rows": str(settings["page_size"]),
            "select": settings["select_fields"],
            "cursor": cursor,
        }
    )
    return f"{settings['endpoint']}?{query}"


def run_curl(url: str) -> tuple[int, str, bytes, bytes, dict[str, Any]]:
    """Fetch into memory, redact URL query values/cookies, return only safe bytes."""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is required (with normal system TLS verification)")
    with tempfile.TemporaryDirectory(prefix="litdb-crossref-") as temporary:
        raw_headers_path = pathlib.Path(temporary) / "response.headers"
        completed = subprocess.run(
            [
                curl,
                "--fail",
                "--silent",
                "--show-error",
                "--max-redirs",
                "0",
                "--connect-timeout",
                "30",
                "--max-time",
                "240",
                "--retry",
                "4",
                "--retry-all-errors",
                "--retry-delay",
                "2",
                "--retry-max-time",
                "120",
                "--user-agent",
                USER_AGENT,
                "--dump-header",
                str(raw_headers_path),
                "--output",
                "-",
                "--write-out",
                "\n__CROSSREF_META__%{http_code}\t%{content_type}\n",
                url,
            ],
            check=False,
            capture_output=True,
            timeout=300,
        )
        marker = b"\n__CROSSREF_META__"
        if marker not in completed.stdout:
            raise RuntimeError(
                f"curl did not return response metadata (exit={completed.returncode})"
            )
        raw_body, metadata = completed.stdout.rsplit(marker, 1)
        metadata_parts = metadata.strip().split(b"\t", 1)
        status = (
            int(metadata_parts[0])
            if metadata_parts and metadata_parts[0].isdigit()
            else 0
        )
        content_type = (
            metadata_parts[1].decode("utf-8", errors="replace")
            if len(metadata_parts) > 1
            else ""
        )
        raw_headers = (
            raw_headers_path.read_bytes() if raw_headers_path.exists() else b""
        )

    safe_headers, header_redactions, header_url_redactions = redact_response_headers(
        raw_headers
    )
    _, cookie_names = redact_set_cookie_headers(raw_headers)
    try:
        parsed_body = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed_body = None
    if parsed_body is None:
        safe_body = b""
        url_redactions: Counter[tuple[str, str]] = Counter()
        body_retention_reason = "non_json_response_body_not_retained"
    else:
        safe_body_value, url_redactions = redact_urls_in_json(parsed_body)
        safe_body = (
            json.dumps(
                safe_body_value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        body_retention_reason = "JSON response retained after signed-query-value redaction"
    receipt = {
        "http_body_sha256_before_redaction": hashlib.sha256(raw_body).hexdigest(),
        "retained_body_sha256": hashlib.sha256(safe_body).hexdigest(),
        "http_body_byte_count_before_redaction": len(raw_body),
        "retained_body_byte_count": len(safe_body),
        "body_retention_reason": body_retention_reason,
        "sensitive_url_query_value_redactions_by_host_and_key": {
            f"{host}::{key}": count
            for (host, key), count in sorted(url_redactions.items())
        },
        "response_headers_sha256_before_redaction": hashlib.sha256(
            raw_headers
        ).hexdigest(),
        "retained_headers_sha256": hashlib.sha256(safe_headers).hexdigest(),
        "sensitive_response_header_redactions_by_name": dict(
            sorted(header_redactions.items())
        ),
        "sensitive_response_header_redaction_count": sum(
            header_redactions.values()
        ),
        "response_header_sensitive_url_query_value_redactions_by_host_and_key": {
            f"{host}::{key}": count
            for (host, key), count in sorted(header_url_redactions.items())
        },
        "set_cookie_header_redactions_by_name": dict(sorted(cookie_names.items())),
        "set_cookie_header_redaction_count": sum(cookie_names.values()),
        "sanitization_reason": (
            "sensitive response header values and secret-like URL query values are "
            "redacted before durable files are written; no sensitive values are retained"
        ),
    }
    if completed.returncode != 0 or status != 200:
        detail = f"curl exit={completed.returncode}, HTTP={status}"
        raise RuntimeError(detail)
    if not content_type.lower().startswith("application/json"):
        raise RuntimeError(f"unexpected Crossref content type: {content_type!r}")
    if parsed_body is None:
        raise RuntimeError("Crossref returned a non-JSON body; body was not retained")
    return status, content_type, safe_body, safe_headers, receipt


def normalize_item(item: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Keep all Crossref records, but expose usable PDFs only for target DOI/ISSN."""
    selected = {
        key: item[key]
        for key in (
            "DOI",
            "ISSN",
            "resource",
            "title",
            "type",
            *DATE_KEYS,
            "license",
            "link",
            "volume",
            "issue",
            "page",
            "article-number",
        )
        if key in item
    }
    pdf_candidates = []
    for link in item.get("link", []) if isinstance(item.get("link"), list) else []:
        if not isinstance(link, dict):
            continue
        if str(link.get("content-type", "")).casefold() != "application/pdf":
            continue
        version = str(link.get("content-version", "")).casefold()
        if version not in {"vor", "am"}:
            continue
        pdf_candidates.append(
            {
                "URL": link.get("URL"),
                "content-type": link.get("content-type"),
                "content-version": version,
                "intended-application": link.get("intended-application"),
                "url_redacted_query_parameters": link.get(
                    "URL_redacted_query_parameters", []
                ),
            }
        )
    pdf_candidates.sort(
        key=lambda entry: (
            0 if entry["content-version"] == "vor" else 1,
            entry.get("URL") or "",
        )
    )
    licenses = item.get("license") if isinstance(item.get("license"), list) else []
    doi = str(item.get("DOI") or "").strip().casefold()
    doi_prefix_match = doi.startswith(DOI_PREFIX)
    raw_issns = item.get("ISSN") or []
    if not isinstance(raw_issns, list):
        raw_issns = [raw_issns]
    issn_match = ISSN in {str(value).strip() for value in raw_issns}
    target_scope_match = doi_prefix_match and issn_match
    vor_candidates = [
        candidate
        for candidate in pdf_candidates
        if candidate["content-version"] == "vor"
        and not candidate["url_redacted_query_parameters"]
    ]
    return {
        **selected,
        "target_doi_prefix_match": doi_prefix_match,
        "target_issn_match": issn_match,
        "target_scope_match": target_scope_match,
        "target_scope_exclusion_reason": (
            None
            if target_scope_match
            else "doi_prefix_mismatch"
            if not doi_prefix_match
            else "exact_issn_missing"
        ),
        "pdf_link_candidates": pdf_candidates,
        "preferred_pdf_candidate": (
            vor_candidates[0] if target_scope_match and vor_candidates else None
        ),
        "source": "Crossref REST API",
        "request_url": request["request_url"],
        "requested_at_utc": request["requested_at_utc"],
        "observed_at_utc": request["observed_at_utc"],
        "http_status": request["http_status"],
        "license_entries": licenses,
    }


def record_fingerprint(record: dict[str, Any]) -> str:
    """Fingerprint DOI metadata while excluding page-specific request provenance."""
    provenance_fields = {
        "source",
        "request_url",
        "requested_at_utc",
        "observed_at_utc",
        "http_status",
    }
    academic_record = {
        key: value for key, value in record.items() if key not in provenance_fields
    }
    return hashlib.sha256(
        json.dumps(
            academic_record,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def validate_saved_pages(
    run_dir: pathlib.Path, checkpoint: dict[str, Any]
) -> int:
    """Validate retained pages and cursor/count state before resume or export."""
    pages = checkpoint.get("pages")
    settings = checkpoint.get("query_settings")
    reported_total = checkpoint.get("reported_total_results")
    if not isinstance(pages, list) or not isinstance(settings, dict):
        raise ValueError("checkpoint has invalid pages or query settings")
    if pages and (not isinstance(reported_total, int) or isinstance(reported_total, bool)):
        raise ValueError("checkpoint pages lack a valid reported_total_results")

    pages_dir = run_dir / "pages"
    total_items = 0
    expected_cursor = "*"
    for index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise ValueError(f"checkpoint page {index} is not an object")
        if page.get("request_url") != query_url(expected_cursor, settings):
            raise ValueError(f"checkpoint page {index} breaks cursor/query chaining")
        raw_path = pages_dir / str(page.get("raw_file") or "")
        response_bytes = raw_path.read_bytes()
        if hashlib.sha256(response_bytes).hexdigest() != page.get(
            "retained_response_sha256"
        ):
            raise ValueError(f"saved Crossref page hash mismatch: {raw_path}")
        payload = json.loads(response_bytes)
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("items"), list):
            raise ValueError(f"page response missing message.items: {raw_path}")
        items = message["items"]
        if len(items) != page.get("item_count"):
            raise ValueError(f"saved Crossref page item_count mismatch: {raw_path}")
        page_total = message.get("total-results")
        if not isinstance(page_total, int) or isinstance(page_total, bool):
            raise ValueError(f"page response has invalid total-results: {raw_path}")
        if page_total != reported_total:
            raise ValueError(f"Crossref total-results changed on page {index}")
        total_items += len(items)
        if total_items > reported_total:
            raise ValueError("saved Crossref pages exceed reported total-results")

        next_cursor = message.get("next-cursor")
        terminal = (
            not isinstance(next_cursor, str)
            or not next_cursor
            or len(items) < settings["page_size"]
            or next_cursor == expected_cursor
            or total_items == reported_total
        )
        if index < len(pages) and terminal:
            raise ValueError(f"checkpoint contains pages after terminal page {index}")
        if not terminal:
            expected_cursor = next_cursor

    status = checkpoint.get("status")
    if status == "complete":
        if checkpoint.get("next_cursor"):
            raise ValueError("completed Crossref checkpoint still has a next_cursor")
        if not pages or total_items != reported_total:
            raise ValueError(
                f"completed Crossref checkpoint has {total_items} of "
                f"{reported_total} reported items"
            )
    elif pages:
        last_page = pages[-1]
        last_payload = json.loads(
            (pages_dir / str(last_page.get("raw_file") or "")).read_text(
                encoding="utf-8"
            )
        )
        next_cursor = last_payload["message"].get("next-cursor")
        if (
            not isinstance(next_cursor, str)
            or not next_cursor
            or checkpoint.get("next_cursor") != next_cursor
        ):
            raise ValueError("resumable checkpoint next_cursor does not match saved page")
    return total_items


def write_outputs(run_dir: pathlib.Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    validate_saved_pages(run_dir, checkpoint)
    pages_dir = run_dir / "pages"
    records: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    duplicate_dois = 0
    conflicting_dois: list[str] = []
    version_counts: Counter[str] = Counter()
    target_version_counts: Counter[str] = Counter()
    intended_counts: Counter[str] = Counter()
    license_counts: Counter[str] = Counter()
    target_scope_counts: Counter[str] = Counter(
        {
            "total": 0,
            "doi_prefix_match": 0,
            "exact_issn_match": 0,
            "both_doi_prefix_and_issn_match": 0,
            "doi_prefix_only": 0,
            "exact_issn_only": 0,
            "neither": 0,
        }
    )
    records_path = run_dir / "records.jsonl"
    temporary_records = records_path.with_suffix(".jsonl.tmp")
    with temporary_records.open("w", encoding="utf-8") as output:
        for page in checkpoint["pages"]:
            raw_path = pages_dir / page["raw_file"]
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            message = payload.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("items"), list):
                raise ValueError(f"page response missing message.items: {raw_path}")
            request = {
                "request_url": page["request_url"],
                "requested_at_utc": page["requested_at_utc"],
                "observed_at_utc": page["observed_at_utc"],
                "http_status": page["http_status"],
            }
            for item in message["items"]:
                if not isinstance(item, dict) or not item.get("DOI"):
                    continue
                normalized = normalize_item(item, request)
                doi = str(normalized["DOI"]).casefold()
                digest = record_fingerprint(normalized)
                if doi in seen:
                    duplicate_dois += 1
                    if seen[doi] != digest:
                        conflicting_dois.append(str(normalized["DOI"]))
                    continue
                seen[doi] = digest
                records.append(normalized)
                target_scope_counts["total"] += 1
                target_scope_counts["doi_prefix_match"] += int(
                    normalized["target_doi_prefix_match"]
                )
                target_scope_counts["exact_issn_match"] += int(
                    normalized["target_issn_match"]
                )
                target_scope_counts["both_doi_prefix_and_issn_match"] += int(
                    normalized["target_scope_match"]
                )
                if normalized["target_doi_prefix_match"] and not normalized["target_issn_match"]:
                    target_scope_counts["doi_prefix_only"] += 1
                elif normalized["target_issn_match"] and not normalized["target_doi_prefix_match"]:
                    target_scope_counts["exact_issn_only"] += 1
                elif not normalized["target_doi_prefix_match"] and not normalized["target_issn_match"]:
                    target_scope_counts["neither"] += 1
                for candidate in normalized["pdf_link_candidates"]:
                    version_counts[candidate["content-version"]] += 1
                    intended_counts[str(candidate.get("intended-application") or "(missing)")] += 1
                    if normalized["target_scope_match"]:
                        target_version_counts[candidate["content-version"]] += 1
                for license_entry in normalized["license_entries"]:
                    version = str(license_entry.get("content-version") or "(missing)")
                    url = str(license_entry.get("URL") or "(missing)")
                    license_counts[f"{version}|{url}"] += 1
        records.sort(key=lambda record: str(record.get("DOI", "")).casefold())
        for record in records:
            output.write(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
    os.replace(temporary_records, records_path)
    receipt_path = run_dir / "sensitive-data-redaction-receipt.json"
    previous_receipt = (
        load_json(receipt_path) if receipt_path.exists() else {}
    )
    page_receipts = [
        {
            "page_number": page.get("page"),
            "response_file": page.get("raw_file"),
            "response_headers_file": page.get("headers_file"),
            **page.get("sanitization_receipt", {}),
        }
        for page in checkpoint["pages"]
        if isinstance(page.get("sanitization_receipt"), dict)
    ]
    url_redactions: Counter[str] = Counter()
    header_url_redactions: Counter[str] = Counter()
    sensitive_header_redactions: Counter[str] = Counter()
    cookie_redactions: Counter[str] = Counter()
    for receipt in page_receipts:
        url_redactions.update(
            receipt.get("sensitive_url_query_value_redactions_by_host_and_key", {})
        )
        header_url_redactions.update(
            receipt.get(
                "response_header_sensitive_url_query_value_redactions_by_host_and_key",
                {},
            )
        )
        sensitive_header_redactions.update(
            receipt.get("sensitive_response_header_redactions_by_name", {})
        )
        cookie_redactions.update(receipt.get("set_cookie_header_redactions_by_name", {}))
    external_receipts = previous_receipt.get("external_file_receipts", [])
    for receipt in external_receipts:
        url_redactions.update(
            receipt.get("sensitive_url_query_value_redactions_by_host_and_key", {})
        )
        header_url_redactions.update(
            receipt.get(
                "response_header_sensitive_url_query_value_redactions_by_host_and_key",
                {},
            )
        )
        sensitive_header_redactions.update(
            receipt.get("sensitive_response_header_redactions_by_name", {})
        )
        cookie_redactions.update(receipt.get("set_cookie_header_redactions_by_name", {}))
    redaction_receipt = {
        "schema_version": "crossref-sensitive-data-redaction-receipt-v1",
        "generated_at_utc": utc_now(),
        "policy": (
            "Crossref response bodies and headers are first held in memory/temporary files. "
            "Secret-like URL query values and sensitive response header values are redacted before "
            "durable evidence is written. Only file hashes, byte counts, parameter/host or "
            "header-name or cookie-name counts, and reasons are retained; no sensitive values are retained."
        ),
        "page_receipts": page_receipts,
        "external_file_receipts": external_receipts,
        "sensitive_url_query_value_redactions_by_host_and_key": dict(
            sorted(url_redactions.items())
        ),
        "response_header_sensitive_url_query_value_redactions_by_host_and_key": dict(
            sorted(header_url_redactions.items())
        ),
        "sensitive_response_header_redactions_by_name": dict(
            sorted(sensitive_header_redactions.items())
        ),
        "sensitive_response_header_redaction_count": sum(
            sensitive_header_redactions.values()
        ),
        "set_cookie_header_redactions_by_name": dict(sorted(cookie_redactions.items())),
        "set_cookie_header_redaction_count": sum(cookie_redactions.values()),
    }
    atomic_json(receipt_path, redaction_receipt)
    result = {
        "status": checkpoint["status"],
        "source": "Crossref REST API",
        "query_settings": checkpoint["query_settings"],
        "endpoint": checkpoint["query_settings"]["endpoint"],
        "issn": checkpoint["query_settings"]["issn"],
        "doi_prefix": checkpoint["query_settings"]["doi_prefix"],
        "publication_year_filter": {
            "from": checkpoint["query_settings"]["year_from"],
            "through": checkpoint["query_settings"]["year_to"],
        },
        "publication_date_filter": {
            "from": checkpoint["query_settings"]["date_from"],
            "through": checkpoint["query_settings"]["date_to"],
        },
        "select_fields": checkpoint["query_settings"]["select_fields"].split(","),
        "page_size": checkpoint["query_settings"]["page_size"],
        "page_count": len(checkpoint["pages"]),
        "records_unique_by_doi": len(records),
        "target_scope_counts": dict(sorted(target_scope_counts.items())),
        "out_of_scope_record_count": (
            len(records) - target_scope_counts["both_doi_prefix_and_issn_match"]
        ),
        "duplicate_doi_rows_skipped": duplicate_dois,
        "conflicting_duplicate_dois": sorted(set(conflicting_dois)),
        "crossref_reported_total_results": checkpoint.get("reported_total_results"),
        "pdf_candidate_link_count_by_version_all_records": dict(sorted(version_counts.items())),
        "pdf_candidate_link_count_by_version_target_scope": dict(
            sorted(target_version_counts.items())
        ),
        "usable_target_vor_candidate_count": sum(
            1 for record in records if record["preferred_pdf_candidate"] is not None
        ),
        "pdf_candidate_intended_application_count": dict(sorted(intended_counts.items())),
        "license_entries_by_version_and_url": dict(sorted(license_counts.items())),
        "next_cursor_present": bool(checkpoint.get("next_cursor")),
        "last_page_request_url": checkpoint["pages"][-1]["request_url"] if checkpoint["pages"] else None,
        "last_observed_at_utc": checkpoint["pages"][-1]["observed_at_utc"] if checkpoint["pages"] else None,
        "records_jsonl": str(records_path),
        "sensitive_data_redaction_receipt": str(receipt_path),
        "note": (
            "Crossref reported totals describe this registry query only and do not define or close "
            "the publisher/OUP expected set. Every Crossref row is retained with DOI-prefix and "
            "exact-ISSN scope flags. Only rows matching both may receive a preferred VOR PDF "
            "candidate; AM links remain audit candidates and are never relabeled as VOR. No "
            "publisher URL was requested and no PDF was downloaded."
        ),
    }
    atomic_json(run_dir / "summary.json", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=pathlib.Path, required=True)
    parser.add_argument("--year-from", type=int, default=2014)
    parser.add_argument(
        "--year-to",
        type=int,
        default=dt.datetime.now(dt.timezone.utc).year,
    )
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument(
        "--max-pages",
        type=int,
        help="pause after this many new pages; useful for a resumable pilot",
    )
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.page_size <= 1000:
        raise SystemExit("--page-size must be between 1 and 1000")
    if args.max_pages is not None and args.max_pages < 1:
        raise SystemExit("--max-pages must be at least 1")
    if args.delay_seconds < 0:
        raise SystemExit("--delay-seconds cannot be negative")
    try:
        settings = query_settings(args.year_from, args.year_to, args.page_size)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = args.run_dir.expanduser().resolve()
    pages_dir = run_dir / "pages"
    checkpoint_path = run_dir / "checkpoint.json"
    pages_dir.mkdir(parents=True, exist_ok=True)

    if checkpoint_path.exists():
        checkpoint = load_json(checkpoint_path)
        try:
            validate_checkpoint_settings(checkpoint, settings)
        except ValueError as exc:
            raise SystemExit(f"{exc}: {checkpoint_path}") from exc
        if checkpoint.get("status") == "complete":
            summary = write_outputs(run_dir, checkpoint)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
    else:
        checkpoint = {
            "schema": CHECKPOINT_SCHEMA,
            "source": "Crossref REST API",
            "query_settings": settings,
            "endpoint": settings["endpoint"],
            "issn": settings["issn"],
            "start_date": settings["date_from"],
            "end_date": settings["date_to"],
            "select_fields": settings["select_fields"],
            "page_size": settings["page_size"],
            "created_at_utc": utc_now(),
            "updated_at_utc": utc_now(),
            "status": "running",
            "pages": [],
            "next_cursor": "*",
            "reported_total_results": None,
        }
        atomic_json(checkpoint_path, checkpoint)

    saved_item_count = validate_saved_pages(run_dir, checkpoint)
    initial_page_count = len(checkpoint["pages"])
    new_pages = 0
    try:
        while checkpoint.get("next_cursor"):
            if args.max_pages is not None and new_pages >= args.max_pages:
                checkpoint["status"] = "paused"
                break
            cursor = str(checkpoint["next_cursor"])
            url = query_url(cursor, settings)
            page_number = len(checkpoint["pages"]) + 1
            basename = f"page-{page_number:05d}"
            raw_path = pages_dir / f"{basename}.json"
            headers_path = pages_dir / f"{basename}.headers.txt"
            requested_at = utc_now()
            status, content_type, safe_body, safe_headers, sanitization_receipt = run_curl(url)
            observed_at = utc_now()
            raw_path.write_bytes(safe_body)
            headers_path.write_bytes(safe_headers)
            payload = json.loads(safe_body)
            message = payload.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("items"), list):
                raise ValueError(f"invalid Crossref response structure in {raw_path}")
            page_total = message.get("total-results")
            if not isinstance(page_total, int) or isinstance(page_total, bool):
                raise ValueError("Crossref response has an invalid total-results value")
            if checkpoint["reported_total_results"] is None:
                checkpoint["reported_total_results"] = page_total
            elif page_total != checkpoint["reported_total_results"]:
                raise ValueError("Crossref total-results changed during cursor pagination")
            items = message["items"]
            next_cursor = message.get("next-cursor")
            total_after_page = saved_item_count + len(items)
            if total_after_page > page_total:
                raise ValueError("Crossref pages exceed reported total-results")
            cursor_missing = not isinstance(next_cursor, str) or not next_cursor
            terminal_signal = (
                cursor_missing
                or len(items) < settings["page_size"]
                or next_cursor == cursor
                or total_after_page == page_total
            )
            if terminal_signal and total_after_page != page_total:
                raise ValueError(
                    f"Crossref pagination ended after {total_after_page} of "
                    f"{page_total} reported items"
                )
            page_entry = {
                "page": page_number,
                "raw_file": raw_path.name,
                "headers_file": headers_path.name,
                "request_url": url,
                "requested_at_utc": requested_at,
                "observed_at_utc": observed_at,
                "http_status": status,
                "content_type": content_type,
                "item_count": len(items),
                "retained_response_sha256": hashlib.sha256(safe_body).hexdigest(),
                "sanitization_receipt": sanitization_receipt,
                "next_cursor_present": isinstance(next_cursor, str) and bool(next_cursor),
            }
            checkpoint["pages"].append(page_entry)
            saved_item_count = total_after_page
            # A terminal cursor/page signal is accepted only when all advertised
            # rows have been retained. The total count also safely closes a full page.
            if terminal_signal:
                checkpoint["next_cursor"] = None
                checkpoint["status"] = "complete"
            else:
                checkpoint["next_cursor"] = next_cursor
                checkpoint["status"] = "running"
            checkpoint["updated_at_utc"] = utc_now()
            atomic_json(checkpoint_path, checkpoint)
            new_pages += 1
            print(
                json.dumps(
                    {
                        "page": page_number,
                        "items": len(items),
                        "reported_total_results": checkpoint["reported_total_results"],
                        "status": checkpoint["status"],
                        "observed_at_utc": observed_at,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if checkpoint["status"] == "complete":
                break
            time.sleep(args.delay_seconds)
        if checkpoint["status"] == "running":
            checkpoint["status"] = "paused"
            checkpoint["updated_at_utc"] = utc_now()
            atomic_json(checkpoint_path, checkpoint)
    except Exception as exc:
        checkpoint["status"] = "failed"
        checkpoint["error"] = f"{type(exc).__name__}: {exc}"
        checkpoint["failed_at_utc"] = utc_now()
        checkpoint["updated_at_utc"] = utc_now()
        atomic_json(checkpoint_path, checkpoint)
        print(checkpoint["error"], file=sys.stderr)
        raise

    summary = write_outputs(run_dir, checkpoint)
    summary["new_pages_this_run"] = new_pages
    summary["previous_pages_before_run"] = initial_page_count
    atomic_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

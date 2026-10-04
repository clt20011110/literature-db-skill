#!/usr/bin/env python3
"""Collect CVPR/ICCV main-proceedings metadata from CVF Open Access.

The collector keeps official enumeration manifests, throttled detail-page
requests, a resumable SQLite cache, staging, waterline evidence, and unresolved
items under the selected literature-db run directory. It never downloads PDFs.
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import gzip
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit


CVF_ROOT = "https://openaccess.thecvf.com/"
CVF_MENU_URL = urljoin(CVF_ROOT, "menu")
SCHEMA_VERSION = "literature-metadata-staging-v1"
CHECKPOINT_VERSION = "cvf-open-access-checkpoint-v1"
USER_AGENT = "literature-db metadata collector (polite public-metadata requests)"
EXCLUSION_SCHEMA_VERSION = "literature-metadata-exclusion-v1"
NOTICE_TITLE_PREFIX = "Notice of Violation of IEEE Publication Principles:"
PROVENANCE_FIELDS = (
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


class RequestRateLimiter:
    """Thread-safe start-rate cap shared by listing and detail requests."""

    def __init__(self, minimum_spacing: float):
        self.minimum_spacing = max(minimum_spacing, 1.0 / 3.0)
        self._lock = threading.Lock()
        self._next_start = 0.0
        self._stop_event = threading.Event()

    def wait_turn(self) -> None:
        if self._stop_event.is_set():
            raise RuntimeError("request cancelled after official access barrier")
        with self._lock:
            now = time.monotonic()
            scheduled_start = max(now, self._next_start)
            self._next_start = scheduled_start + self.minimum_spacing
        wait_for = scheduled_start - now
        if wait_for > 0 and self._stop_event.wait(wait_for):
            raise RuntimeError("request cancelled after official access barrier")
        if self._stop_event.is_set():
            raise RuntimeError("request cancelled after official access barrier")

    def stop_after_access_barrier(self) -> None:
        self._stop_event.set()


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
    opener = gzip.open if path.suffix == ".gz" else open
    temporary = path.with_suffix(path.suffix + ".tmp")
    with opener(temporary, "wt", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_gzip_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            zipped.write(data)
    temporary.replace(path)


class CVFListingParser(HTMLParser):
    """Read paper identity, ordered author names, and observed PDF links."""

    def __init__(self, list_url: str):
        super().__init__(convert_charrefs=True)
        self.list_url = list_url
        self.rows: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.title_open = False
        self.anchor_mode: str | None = None
        self.title_parts: list[str] = []
        self.author_parts: list[str] = []
        self.author_anchor_names: list[str] = []
        self.dd_index = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "dt" and "ptitle" in (attributes.get("class") or "").split():
            self._finish_row()
            self.current = {"title": "", "landing_url": None, "authors": [], "pdf_url": None}
            self.title_open = True
            self.dd_index = 0
            return
        if self.current is None:
            return
        if tag == "dd" and not self.title_open:
            self.dd_index += 1
        elif tag == "input" and attributes.get("name") == "query_author":
            name = normalize_space(attributes.get("value") or "")
            if name:
                self.current["authors"].append(name)
        elif tag == "a":
            href = attributes.get("href") or ""
            if self.title_open and href:
                self.current["landing_url"] = urljoin(self.list_url, href)
                self.anchor_mode = "title"
                self.title_parts = []
            elif self.dd_index == 1 and href == "#" and not self.current["authors"]:
                self.anchor_mode = "author"
                self.author_parts = []
            else:
                full_url = urljoin(self.list_url, href)
                path = urlsplit(full_url).path.casefold()
                if path.endswith(".pdf") and "/papers/" in path and "/supplemental/" not in path:
                    self.current["pdf_url"] = full_url

    def handle_data(self, data: str) -> None:
        if self.anchor_mode == "title":
            self.title_parts.append(data)
        elif self.anchor_mode == "author":
            self.author_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor_mode:
            value = normalize_space("".join(self.title_parts if self.anchor_mode == "title" else self.author_parts))
            if self.current is not None and self.anchor_mode == "title":
                self.current["title"] = value
            elif value:
                self.author_anchor_names.append(value)
            self.anchor_mode = None
        elif tag == "dt":
            self.title_open = False

    def close(self) -> None:
        super().close()
        self._finish_row()

    def _finish_row(self) -> None:
        if self.current is None:
            return
        if not self.current["authors"]:
            self.current["authors"] = list(self.author_anchor_names)
        self.author_anchor_names = []
        if self.current.get("landing_url"):
            self.rows.append(self.current)
        self.current = None
        self.title_open = False
        self.anchor_mode = None


class CVFDetailParser(HTMLParser):
    """Read article-level citation metadata and the visible abstract."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.abstract_parts: list[str] = []
        self.abstract_depth = 0
        self.doi_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta":
            name = (attributes.get("name") or "").strip().casefold()
            content = normalize_space(attributes.get("content") or "")
            if name and content:
                self.meta.setdefault(name, []).append(content)
        elif tag == "div" and (attributes.get("id") or "").casefold() == "abstract":
            self.abstract_depth = 1
        elif self.abstract_depth and tag == "div":
            self.abstract_depth += 1
        elif self.abstract_depth and tag in {"br", "p", "li"}:
            self.abstract_parts.append(" ")
        elif tag == "a":
            href = attributes.get("href") or ""
            if "doi.org/" in href.casefold() or "ieeexplore.ieee.org" in href.casefold():
                self.doi_links.append(href)

    def handle_data(self, data: str) -> None:
        if self.abstract_depth:
            self.abstract_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.abstract_depth:
            self.abstract_depth -= 1

    @property
    def abstract(self) -> str | None:
        value = normalize_space("".join(self.abstract_parts))
        return value or None

    def first(self, name: str) -> str | None:
        values = self.meta.get(name.casefold(), [])
        return values[0] if values else None

    def authors(self) -> list[str]:
        result = []
        for author in self.meta.get("citation_author", []):
            if ", " in author:
                family, given = author.split(", ", 1)
                result.append(normalize_space(f"{given} {family}"))
            else:
                result.append(author)
        return result


class CVFAnchorParser(HTMLParser):
    """Capture visible links on official menu and edition landing pages."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href is not None:
            self.links.append((normalize_space("".join(self.parts)), self.href))
            self.href = None


def official_menu_editions(html: str, venue_id: str, start_year: int, end_year: int) -> dict[int, str]:
    parser = CVFAnchorParser()
    parser.feed(html)
    parser.close()
    prefix = venue_id.upper()
    editions: dict[int, str] = {}
    for label, href in parser.links:
        if label.casefold() != "main conference":
            continue
        match = re.fullmatch(rf"/?{prefix}(\d{{4}})(?:\.py)?", href.strip(), re.I)
        if not match:
            continue
        year = int(match.group(1))
        if start_year <= year <= end_year:
            editions[year] = urljoin(CVF_MENU_URL, href)
    return dict(sorted(editions.items()))


def official_listing_targets(page_url: str, page_body: bytes, root_rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Choose only pagination links visibly exposed by this edition page.

    Some CVF editions expose an all-paper link while older editions expose
    only day-level links. The link layout, rather than a guessed query value,
    determines which official listing pages are enumerated.
    """
    parser = CVFAnchorParser()
    parser.feed(page_body.decode("utf-8", "replace"))
    parser.close()
    links = [(label, urljoin(page_url, href)) for label, href in parser.links if href]
    all_pages = [(label, url) for label, url in links if label.casefold() == "all papers"]
    if all_pages:
        return all_pages
    day_pages = [(label, url) for label, url in links if re.match(r"day\s+\d+\s*:?", label, re.I)]
    if day_pages:
        return day_pages
    return [("Edition listing", page_url)] if root_rows else []


def source_identity(venue_id: str, year: int, landing_url: str) -> str:
    slug = unquote(Path(urlsplit(landing_url).path).name)
    if slug.casefold().endswith(".html"):
        slug = slug[:-5]
    return f"{venue_id.upper()}{year}:{slug}"


def fetch_public_html(url: str, delay_seconds: float, last_started: list[float] | RequestRateLimiter, timeout_seconds: int) -> tuple[bytes | None, str | None]:
    if isinstance(last_started, RequestRateLimiter):
        try:
            last_started.wait_turn()
        except RuntimeError:
            return None, "request_cancelled_after_official_access_barrier"
    else:
        wait_for = delay_seconds - (time.monotonic() - last_started[0]) if last_started[0] else 0
        if wait_for > 0:
            time.sleep(wait_for)
        last_started[0] = time.monotonic()
    command = [
        "curl", "--fail", "--silent", "--show-error", "--location", "--compressed",
        "--max-time", str(timeout_seconds),
        "--user-agent", USER_AGENT, url,
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=timeout_seconds + 15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"fetch_error:{type(exc).__name__}:{exc}"
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        return None, f"curl_exit_{result.returncode}:{message[:300]}"
    return result.stdout, None


def response_is_access_barrier(error: str | None, body: bytes | None) -> bool:
    if error and re.search(r"\b(403|429)\b", error):
        return True
    if error == "detail_page_missing_article_metadata":
        # A 200 response without CVF article metadata is not a paper detail
        # page; stop instead of walking the remaining venue through a gate.
        return True
    if body:
        sample = body[:100_000].decode("utf-8", "replace").casefold()
        if any(marker in sample for marker in ("cf-chl-captcha", "cf_chl_opt", "challenge-form", "captcha-container")):
            return True
        has_article_metadata = "citation_title" in sample and ("citation_author" in sample or "id=\"abstract\"" in sample)
        if not has_article_metadata and any(marker in sample for marker in ("verify you are human", "complete the captcha", "robot check")):
            return True
    return False


def parse_doi(parser: CVFDetailParser, html: str) -> str | None:
    for key in ("citation_doi", "dc.identifier", "dc.identifier.doi", "prism.doi", "doi"):
        for value in parser.meta.get(key, []):
            match = re.search(r"10\.\d{4,9}/[^\s<>\"{}]+", value, re.I)
            if match:
                return match.group(0).rstrip(".,;:)").lower()
    for href in parser.doi_links:
        decoded = unquote(href)
        match = re.search(r"10\.\d{4,9}/[^\s<>\"{}]+", decoded, re.I)
        if match:
            return match.group(0).rstrip(".,;:)").lower()
    bib_match = re.search(r"\bdoi\s*=\s*[\{\"]\s*(10\.\d{4,9}/[^\}\"\s]+)", html, re.I)
    if bib_match:
        return bib_match.group(1).rstrip(".,;:)").lower()
    return None


def make_staging_row(
    venue_id: str,
    expected: dict[str, Any],
    page: CVFDetailParser | None,
    observed_at: str,
    page_error: str | None,
) -> tuple[dict[str, Any], list[str]]:
    landing_url = str(expected["landing_url"])
    list_url = str(expected["source_url"])
    detail_url = landing_url
    successful_detail = page is not None
    source_url = detail_url if successful_detail else list_url
    source_observed = observed_at if successful_detail else str(expected["source_observed_at"])

    title = (page.first("citation_title") if page else None) or expected.get("title")
    authors = (page.authors() if page else []) or list(expected.get("authors") or [])
    if not authors and expected.get("authors"):
        authors = list(expected["authors"])
    if page and expected.get("authors") and len(page.authors()) and len(page.authors()) != len(expected["authors"]):
        authors = list(expected["authors"])
    year = int(expected["year"])
    publication_date = page.first("citation_publication_date") if page else None
    abstract = page.abstract if page else None
    html_text = getattr(page, "_html_for_doi", "") if page else ""
    doi = parse_doi(page, html_text) if page else None
    detail_pdf = page.first("citation_pdf_url") if page else None
    list_pdf = expected.get("pdf_url")
    pdf_url = list_pdf or detail_pdf
    pdf_evidence = []
    if list_pdf:
        pdf_evidence.append({
            "source_url": list_url,
            "observed_at": str(expected["source_observed_at"]),
            "method": "official_list_pdf_anchor",
            "url": list_pdf,
        })
    if detail_pdf:
        pdf_evidence.append({
            "source_url": detail_url,
            "observed_at": observed_at if successful_detail else str(expected["source_observed_at"]),
            "method": "citation_pdf_url_meta",
            "url": detail_pdf,
        })
    pdf_status = "visible_url" if pdf_url else "not_visible"

    missing: dict[str, dict[str, str]] = {}
    unresolved: list[str] = []
    unavailable_reason = "source_unavailable" if page_error else "not_present_on_official_page"
    missing_check_url = detail_url if page_error else source_url
    missing_check_observed = observed_at if page_error else source_observed
    for field, value in (("abstract", abstract), ("publication_date", publication_date), ("doi", doi)):
        if not value:
            reason = "source_unavailable" if page_error else unavailable_reason
            missing[field] = {"reason_code": reason, "source_url": missing_check_url, "observed_at": missing_check_observed}
            if field == "abstract":
                unresolved.append("abstract")
    if not pdf_url:
        missing["pdf_url"] = {"reason_code": "not_visible", "source_url": source_url, "observed_at": source_observed}
    if not title:
        unresolved.append("title")
    if not authors:
        unresolved.append("authors")

    detail_time = observed_at if successful_detail or page_error else str(expected["source_observed_at"])
    title_from_detail = bool(page and page.first("citation_title"))
    authors_from_detail = bool(page and page.authors() and len(page.authors()) == len(authors))
    field_sources = {
        "source_native_id": (list_url, str(expected["source_observed_at"]), "official_cvf_list_link"),
        "title": (detail_url, detail_time, "citation_title_meta") if title_from_detail else (list_url, str(expected["source_observed_at"]), "official_cvf_list_title"),
        "authors": (detail_url, detail_time, "citation_author_meta") if authors_from_detail else (list_url, str(expected["source_observed_at"]), "official_cvf_list_author_forms"),
        "year": (list_url, str(expected["source_observed_at"]), "official_cvf_edition_heading"),
        "document_type": list_url,
        "landing_url": (list_url, str(expected["source_observed_at"]), "official_cvf_main_proceedings_link"),
        "abstract": (detail_url, detail_time, "official_abstract_div" if successful_detail else "detail_fetch_unavailable"),
        "doi": (detail_url, detail_time, "official_detail_metadata_check" if successful_detail else "detail_fetch_unavailable"),
        "publication_date": (detail_url, detail_time, "citation_publication_date_meta" if successful_detail else "detail_fetch_unavailable"),
        "pdf_discovery_status": (list_url, str(expected["source_observed_at"]), "official_list_pdf_anchor") if list_pdf else ((detail_url, detail_time, "citation_pdf_url_meta") if detail_pdf else (list_url, str(expected["source_observed_at"]), "official_list_pdf_link_or_checked_absence")),
    }
    field_sources["document_type"] = (list_url, str(expected["source_observed_at"]), "official_main_proceedings_list_entry")
    provenance = {
        field: {
            "source_url": field_sources[field][0],
            "observed_at": field_sources[field][1],
            "method": field_sources[field][2],
        }
        for field in PROVENANCE_FIELDS
    }
    row = {
        "schema_version": SCHEMA_VERSION,
        "venue_id": venue_id,
        "source_native_id": expected["source_native_id"],
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "document_type": "conference-paper",
        "publication_date": publication_date,
        "year": year,
        "doi": doi,
        "landing_url": landing_url,
        "pdf_url": pdf_url,
        "pdf_discovery_status": pdf_status,
        "inclusion_decision": "include",
        "source_url": source_url,
        "observed_at": source_observed,
        "field_provenance": provenance,
        "missing_fields": missing,
        "observed_pdf_url_evidence": {
            "selected_url": pdf_url,
            "selected_source": "official_list_pdf_anchor" if list_pdf else ("citation_pdf_url_meta" if detail_pdf else None),
            "observations": pdf_evidence,
        },
    }
    return row, unresolved


def open_cache(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS detail_records (
             source_native_id TEXT PRIMARY KEY,
             venue_id TEXT NOT NULL,
             year INTEGER NOT NULL,
             state TEXT NOT NULL,
             record_json TEXT NOT NULL,
             error TEXT,
             last_observed_at TEXT NOT NULL,
             attempts INTEGER NOT NULL DEFAULT 1
           )"""
    )
    connection.commit()
    return connection


def record_cache_row(connection: sqlite3.Connection, row: dict[str, Any], state: str, error: str | None) -> None:
    connection.execute(
        """INSERT INTO detail_records(source_native_id, venue_id, year, state, record_json, error, last_observed_at, attempts)
           VALUES(?,?,?,?,?,?,?,1)
           ON CONFLICT(source_native_id) DO UPDATE SET
             state=excluded.state, record_json=excluded.record_json, error=excluded.error,
             last_observed_at=excluded.last_observed_at, attempts=detail_records.attempts+1""",
        (row["source_native_id"], row["venue_id"], row["year"], state,
         json.dumps(row, ensure_ascii=False, sort_keys=True), error, row["observed_at"]),
    )


def collect_rows(connection: sqlite3.Connection) -> list[tuple[dict[str, Any], str, str | None, int]]:
    result = []
    for record_json, state, error, attempts in connection.execute(
        "SELECT record_json,state,error,attempts FROM detail_records ORDER BY year,source_native_id"
    ):
        result.append((json.loads(record_json), state, error, attempts))
    return result


def reconcile_pdf_link(record: dict[str, Any], expected: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prefer a real HTTPS [pdf] anchor observed in the paired official list.

    Keep any detail-page citation_pdf_url as a separate observation so legacy
    HTTP citation values remain auditable without becoming the staged link.
    """
    result = dict(record)
    provenance = dict(result.get("field_provenance") or {})
    prior_pdf = result.get("pdf_url")
    expected_pdf = expected.get("pdf_url")
    evidence = dict(result.get("observed_pdf_url_evidence") or {})
    observations = list(evidence.get("observations") or [])
    identity = result.get("source_native_id") or expected.get("source_native_id")
    list_url = str(expected.get("source_url") or "")
    list_observed = str(expected.get("source_observed_at") or "")
    if expected_pdf and not any(item.get("url") == expected_pdf and item.get("method") == "official_list_pdf_anchor" for item in observations):
        observations.append({
            "source_url": list_url,
            "observed_at": list_observed,
            "method": "official_list_pdf_anchor",
            "url": expected_pdf,
        })
    old_pdf_source = provenance.get("pdf_discovery_status") or {}
    old_pdf_method = old_pdf_source.get("method")
    if prior_pdf and old_pdf_method == "citation_pdf_url_meta" and not any(
        item.get("url") == prior_pdf and item.get("method") == "citation_pdf_url_meta" for item in observations
    ):
        observations.append({
            "source_url": old_pdf_source.get("source_url") or result.get("landing_url"),
            "observed_at": old_pdf_source.get("observed_at") or result.get("observed_at"),
            "method": "citation_pdf_url_meta",
            "url": prior_pdf,
        })
    selected_pdf = expected_pdf or prior_pdf
    if expected_pdf:
        selected_source = "official_list_pdf_anchor"
        provenance["pdf_discovery_status"] = {
            "source_url": list_url,
            "observed_at": list_observed,
            "method": selected_source,
        }
    elif prior_pdf:
        selected_source = old_pdf_method or "citation_pdf_url_meta"
        if not old_pdf_source:
            provenance["pdf_discovery_status"] = {
                "source_url": result.get("landing_url") or list_url,
                "observed_at": result.get("observed_at") or list_observed,
                "method": selected_source,
            }
    else:
        selected_source = None
        provenance["pdf_discovery_status"] = {
            "source_url": list_url,
            "observed_at": list_observed,
            "method": "official_list_pdf_link_or_checked_absence",
        }
        missing = dict(result.get("missing_fields") or {})
        missing["pdf_url"] = {"reason_code": "not_visible", "source_url": list_url, "observed_at": list_observed}
        result["missing_fields"] = missing
    result["pdf_url"] = selected_pdf
    result["pdf_discovery_status"] = "visible_url" if selected_pdf else "not_visible"
    result["field_provenance"] = provenance
    if selected_pdf:
        missing = dict(result.get("missing_fields") or {})
        missing.pop("pdf_url", None)
        result["missing_fields"] = missing
    report = {
        "source_native_id": identity,
        "landing_url": result.get("landing_url"),
        "selected_url": selected_pdf,
        "selected_source": selected_source,
        "observations": observations,
    }
    result["observed_pdf_url_evidence"] = {
        "selected_url": selected_pdf,
        "selected_source": selected_source,
        "observations": observations,
    }
    return result, report


def apply_abstract_supplement(record: dict[str, Any], supplement: dict[str, Any] | None) -> dict[str, Any]:
    """Use an exact-matched official cached abstract only when detail lacks it."""
    result = dict(record)
    if result.get("abstract") or not supplement or not supplement.get("abstract"):
        return result
    result["abstract"] = supplement["abstract"]
    missing = dict(result.get("missing_fields") or {})
    missing.pop("abstract", None)
    result["missing_fields"] = missing
    provenance = dict(result.get("field_provenance") or {})
    provenance["abstract"] = {
        "source_url": supplement["source_url"],
        "observed_at": supplement["observed_at"],
        "method": "official_virtual_json_" + str(supplement["match_method"]),
    }
    result["field_provenance"] = provenance
    return result


AUTHOR_SUFFIX_RE = re.compile(r"^(.*?)(?:,?\s+)(II|III|IV|Jr\.?|Sr\.?|SR)\.?$", re.I)


def _author_base_and_suffix(name: str) -> tuple[str, str | None]:
    cleaned = normalize_space(name)
    if cleaned.endswith(","):
        cleaned = cleaned[:-1].rstrip()
    match = AUTHOR_SUFFIX_RE.fullmatch(cleaned)
    if not match:
        return cleaned, None
    return normalize_space(match.group(1)), match.group(2).rstrip(".")


def approved_official_author_form_differences(detail_authors: list[str], list_authors: list[str]) -> list[dict[str, Any]]:
    """Identify only list-only final tokens or a detail-only trailing comma."""
    if not detail_authors or len(detail_authors) != len(list_authors):
        return []
    differences: list[dict[str, Any]] = []
    for index, (detail_name, list_name) in enumerate(zip(detail_authors, list_authors)):
        detail_clean = normalize_space(detail_name)
        list_clean = normalize_space(list_name)
        if detail_clean == list_clean:
            continue
        if detail_clean.endswith(",") and detail_clean[:-1].rstrip() == list_clean:
            differences.append({"index": index, "kind": "detail_only_trailing_comma"})
            continue
        detail_base, detail_suffix = _author_base_and_suffix(detail_name)
        list_base, list_suffix = _author_base_and_suffix(list_name)
        if detail_suffix is None and list_suffix and detail_base == list_base:
            differences.append({"index": index, "kind": "official_list_final_token_only", "list_final_token": list_suffix})
            continue
        return []
    return differences


def reconcile_author_names(record: dict[str, Any], expected: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = dict(record)
    detail_authors = list(result.get("authors") or [])
    list_authors = list(expected.get("authors") or [])
    provenance = dict(result.get("field_provenance") or {})
    if provenance.get("authors", {}).get("method") != "citation_author_meta":
        return result, None
    differences = approved_official_author_form_differences(detail_authors, list_authors)
    if not differences:
        return result, None
    list_url = str(expected.get("source_url") or "")
    list_observed_at = str(expected.get("source_observed_at") or "")
    detail_author_source = provenance["authors"]
    result["authors"] = list_authors
    provenance["authors"] = {
        "source_url": list_url,
        "observed_at": list_observed_at,
        "method": "official_cvf_list_author_forms",
    }
    result["field_provenance"] = provenance
    evidence = {
        "source_native_id": result.get("source_native_id") or expected.get("source_native_id"),
        "detail_authors": detail_authors,
        "selected_list_authors": list_authors,
        "approved_author_form_differences": differences,
        "detail_source_url": detail_author_source.get("source_url"),
        "detail_observed_at": detail_author_source.get("observed_at"),
        "list_source_url": list_url,
        "list_observed_at": list_observed_at,
        "reason_code": "only_official_list_final_token_or_detail_trailing_comma_differs",
    }
    result["observed_author_name_evidence"] = evidence
    return result, evidence


def reconcile_refreshed_list_authors(record: dict[str, Any], expected: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Refresh cached listing-fallback authors after a no-network listing reparse."""
    result = dict(record)
    provenance = dict(result.get("field_provenance") or {})
    if provenance.get("authors", {}).get("method") != "official_cvf_list_author_forms":
        return result, None
    prior_authors = list(result.get("authors") or [])
    list_authors = list(expected.get("authors") or [])
    if prior_authors == list_authors:
        return result, None
    result["authors"] = list_authors
    provenance["authors"] = {
        "source_url": str(expected.get("source_url") or ""),
        "observed_at": str(expected.get("source_observed_at") or ""),
        "method": "official_cvf_list_author_forms",
    }
    result["field_provenance"] = provenance
    evidence = {
        "source_native_id": result.get("source_native_id") or expected.get("source_native_id"),
        "prior_cached_list_authors": prior_authors,
        "selected_refreshed_list_authors": list_authors,
        "list_source_url": expected.get("source_url"),
        "list_observed_at": expected.get("source_observed_at"),
        "reason_code": "cached_official_list_author_sequence_reparsed_without_name_deduplication",
    }
    result["observed_author_name_evidence"] = evidence
    return result, evidence


def build_official_field_conflicts(
    observed_record: dict[str, Any],
    selected_record: dict[str, Any],
    expected: dict[str, Any],
    venue_id: str,
) -> list[dict[str, Any]]:
    conflicts = []
    observed_provenance = observed_record.get("field_provenance") or {}
    selected_provenance = selected_record.get("field_provenance") or {}
    for field, detail_method in (("title", "citation_title_meta"), ("authors", "citation_author_meta")):
        list_value = expected.get(field)
        detail_value = observed_record.get(field)
        detail_provenance = observed_provenance.get(field) or {}
        if detail_provenance.get("method") != detail_method or list_value in (None, "", []):
            continue
        if detail_value == list_value:
            continue
        selected_field_provenance = selected_provenance.get(field) or {}
        conflicts.append({
            "schema_version": "cvf-official-list-detail-conflict-v1",
            "venue_id": venue_id,
            "source_native_id": observed_record.get("source_native_id") or expected.get("source_native_id"),
            "field": field,
            "official_list_value": list_value,
            "official_list_source_url": expected.get("source_url"),
            "official_list_observed_at": expected.get("source_observed_at"),
            "detail_value": detail_value,
            "detail_source_url": detail_provenance.get("source_url"),
            "detail_observed_at": detail_provenance.get("observed_at"),
            "selected_value": selected_record.get(field),
            "selected_source_url": selected_field_provenance.get("source_url"),
            "selected_observed_at": selected_field_provenance.get("observed_at"),
            "resolution_method": (
                "official_list_used_for_narrow_author_terminal_form_difference"
                if field == "authors" and selected_record.get(field) == list_value
                else "official_detail_value_retained_for_unapproved_difference"
            ),
        })
    return conflicts


def is_official_violation_notice(title: str | None) -> bool:
    return bool(title and title.startswith(NOTICE_TITLE_PREFIX))


def is_confirmed_official_violation_notice(record: dict[str, Any], state: str) -> bool:
    if state == "excluded":
        return True
    title_provenance = (record.get("field_provenance") or {}).get("title") or {}
    return (
        title_provenance.get("method") == "citation_title_meta"
        and is_official_violation_notice(record.get("title"))
    )


def build_exclusion_record(venue_id: str, record: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    source_url = str(record.get("source_url") or record.get("landing_url"))
    observed_at = str(record.get("observed_at") or expected.get("source_observed_at"))
    listing_url = str(expected.get("source_url") or source_url)
    listing_observed_at = str(expected.get("source_observed_at") or observed_at)
    return {
        "schema_version": EXCLUSION_SCHEMA_VERSION,
        "venue_id": venue_id,
        "source_native_id": record["source_native_id"],
        "title": record["title"],
        "year": int(record["year"]),
        "inclusion_decision": "exclude",
        "exclusion_reason_code": "non_research_content",
        "landing_url": record["landing_url"],
        "source_url": source_url,
        "observed_at": observed_at,
        "exclusion_evidence": {
            "classification_rule": "official title starts with the exact IEEE publication-violation notice prefix",
            "matched_title_prefix": NOTICE_TITLE_PREFIX,
            "official_title": record["title"],
            "official_abstract": record.get("abstract"),
            "source_url": source_url,
            "observed_at": observed_at,
        },
        "field_provenance": {
            "source_native_id": {"source_url": listing_url, "observed_at": listing_observed_at, "method": "official_cvf_list_link", "status": "excluded"},
            "title": {"source_url": source_url, "observed_at": observed_at, "method": "citation_title_meta", "status": "excluded"},
            "year": {"source_url": listing_url, "observed_at": listing_observed_at, "method": "official_cvf_edition_heading", "status": "excluded"},
            "inclusion_decision": {"source_url": source_url, "observed_at": observed_at, "method": "official_main_conference_notice_classification", "status": "excluded"},
            "exclusion_reason_code": {"source_url": source_url, "observed_at": observed_at, "method": "exact_official_notice_title_prefix", "status": "excluded"},
        },
    }


def write_outputs(run_root: Path, venue_id: str, connection: sqlite3.Connection, expected_rows: list[dict[str, Any]], run_state: str, last_identity: str | None) -> dict[str, Any]:
    checkpoint_path = run_root / "checkpoint.json"
    prior_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.is_file() else {}
    all_cached = collect_rows(connection)
    expected_by_id = {row["source_native_id"]: row for row in expected_rows}
    cached = []
    out_of_scope_cache = []
    for cache_entry in all_cached:
        record, state, error, attempts = cache_entry
        if record["source_native_id"] in expected_by_id:
            cached.append(cache_entry)
        else:
            out_of_scope_cache.append({
                "source_native_id": record.get("source_native_id"),
                "year": record.get("year"),
                "state": state,
                "error": error,
                "attempts": attempts,
            })
    supplement_path = run_root / "official_abstract_supplements.jsonl"
    abstract_supplements = {
        row["source_native_id"]: row
        for row in read_jsonl(supplement_path)
        if row.get("source_native_id") and row.get("abstract")
    }
    refreshed_cache = []
    for record, state, error, attempts in cached:
        enriched = apply_abstract_supplement(record, abstract_supplements.get(record["source_native_id"]))
        if enriched.get("abstract") != record.get("abstract"):
            # An offline supplement can be added after details were fetched. Store
            # the enriched fields without changing the actual page-attempt count
            # or detail observation timestamp.
            connection.execute(
                "UPDATE detail_records SET record_json=? WHERE source_native_id=?",
                (json.dumps(enriched, ensure_ascii=False, sort_keys=True), record["source_native_id"]),
            )
        refreshed_cache.append((enriched, state, error, attempts))
    if refreshed_cache:
        connection.commit()
    cached = refreshed_cache
    staging_rows = []
    pdf_link_evidence = []
    author_name_evidence = []
    field_discrepancy_evidence = []
    exclusion_rows = []
    excluded_ids = set()
    for record, state, _, _ in cached:
        observed_record = dict(record)
        expected = expected_by_id.get(record["source_native_id"], {})
        record, author_evidence = reconcile_author_names(record, expected)
        if author_evidence:
            author_name_evidence.append(author_evidence)
        record, refreshed_list_evidence = reconcile_refreshed_list_authors(record, expected)
        if refreshed_list_evidence:
            author_name_evidence.append(refreshed_list_evidence)
        field_discrepancy_evidence.extend(build_official_field_conflicts(
            observed_record, record, expected, venue_id,
        ))
        record, report = reconcile_pdf_link(record, expected)
        pdf_link_evidence.append(report)
        if is_confirmed_official_violation_notice(record, state):
            excluded_ids.add(record["source_native_id"])
            exclusion_rows.append(build_exclusion_record(venue_id, record, expected))
            continue
        staging_rows.append({key: value for key, value in record.items() if key not in {"observed_pdf_url_evidence", "observed_author_name_evidence"}})
    write_jsonl(run_root / "metadata_staging.jsonl", staging_rows)
    write_jsonl(run_root / "pdf_link_evidence.jsonl", pdf_link_evidence)
    write_jsonl(run_root / "author_name_evidence.jsonl", author_name_evidence)
    write_jsonl(run_root / "field_discrepancy_evidence.jsonl", field_discrepancy_evidence)
    write_jsonl(run_root / "metadata_exclusions.jsonl", exclusion_rows)
    write_jsonl(run_root / "out_of_scope_cache.jsonl", out_of_scope_cache)
    unresolved_rows = []
    for record, state, error, attempts in cached:
        if record["source_native_id"] in excluded_ids:
            continue
        missing_abstract = not record.get("abstract")
        if state != "fetched" or missing_abstract or not record.get("authors") or not record.get("title"):
            unresolved_rows.append({
                "venue_id": venue_id,
                "year": record["year"],
                "source_native_id": record["source_native_id"],
                "landing_url": record["landing_url"],
                "state": state,
                "missing_fields": sorted(record.get("missing_fields", {}).keys()),
                "error": error,
                "attempts": attempts,
            })
    write_jsonl(run_root / "unresolved.jsonl", unresolved_rows)
    expected_by_year: dict[int, int] = {}
    for row in expected_rows:
        expected_by_year[int(row["year"])] = expected_by_year.get(int(row["year"]), 0) + 1
    stats_by_year: dict[int, dict[str, int]] = {
        year: {"expected": count, "fetched": 0, "included": 0, "excluded": 0, "fallback": 0, "abstract": 0, "doi": 0, "pdf": 0, "publication_date": 0}
        for year, count in expected_by_year.items()
    }
    for record, state, _, _ in cached:
        stats = stats_by_year[int(record["year"])]
        is_excluded = record["source_native_id"] in excluded_ids
        if is_excluded:
            stats["fetched"] += 1
            stats["excluded"] += 1
            continue
        stats["included"] += 1
        stats["fetched" if state == "fetched" else "fallback"] += 1
        for field, key in (("abstract", "abstract"), ("doi", "doi"), ("pdf_url", "pdf"), ("publication_date", "publication_date")):
            if record.get(field):
                stats[key] += 1
    totals = {key: sum(item[key] for item in stats_by_year.values()) for key in next(iter(stats_by_year.values()), {})}
    progress = {
        **prior_checkpoint,
        "schema_version": CHECKPOINT_VERSION,
        "venue_id": venue_id,
        "run_status": run_state,
        "expected_count": len(expected_rows),
        "staged_count": len(staging_rows),
        "cached_count": len(cached),
        "out_of_scope_cache_count": len(out_of_scope_cache),
        "included_count": len(staging_rows),
        "excluded_count": len(exclusion_rows),
        "fetched_detail_count": sum(1 for _, state, _, _ in cached if state in {"fetched", "excluded"}),
        "fallback_count": sum(1 for _, state, _, _ in cached if state == "fallback"),
        "unresolved_count": len(unresolved_rows),
        "last_source_native_id": last_identity,
        "updated_at": utc_now(),
        "yearly": {str(year): item for year, item in sorted(stats_by_year.items())},
        "totals": totals,
    }
    write_json(run_root / "checkpoint.json", progress)
    write_json(run_root / "stats.json", progress)
    return progress


def years_for(venue_id: str, start_year: int, end_year: int) -> list[int]:
    if venue_id == "cvpr":
        return list(range(start_year, end_year + 1))
    if venue_id != "iccv":
        raise ValueError(f"unsupported CVF venue: {venue_id}")
    first_year = start_year if start_year % 2 == 1 else start_year + 1
    return list(range(first_year, end_year + 1, 2))


def classify_requested_editions(
    venue_id: str,
    start_year: int,
    end_year: int,
    all_official_editions: dict[int, str],
) -> tuple[dict[int, str], list[int], int]:
    """Separate published editions from trailing years not yet listed by CVF.

    A missing year before the newest visible official edition is an enumeration
    gap and fails closed. Legal conference years after that visible frontier are
    recorded as not yet listed, rather than being claimed as covered.
    """
    legal_years = years_for(venue_id, start_year, end_year)
    if not all_official_editions:
        raise RuntimeError("CVF menu exposes no main-conference edition to establish its published frontier")
    latest_visible_year = max(all_official_editions)
    requested_editions = {
        year: url
        for year, url in all_official_editions.items()
        if start_year <= year <= end_year
    }
    published_scope = [year for year in legal_years if year <= latest_visible_year]
    missing_published = sorted(set(published_scope) - set(requested_editions))
    if missing_published:
        raise RuntimeError(
            f"CVF menu is missing requested {venue_id.upper()} editions before its latest visible "
            f"edition ({latest_visible_year}): {missing_published}"
        )
    not_yet_listed = [year for year in legal_years if year > latest_visible_year]
    return dict(sorted(requested_editions.items())), not_yet_listed, latest_visible_year


def enumerate_years(args: argparse.Namespace, last_started: list[float] | RequestRateLimiter) -> list[dict[str, Any]]:
    run_root = args.run_root
    expected_dir = run_root / "expected"
    evidence_dir = run_root / "evidence" / "official_listings"
    run_root.mkdir(parents=True, exist_ok=True)
    checkpoint_path = run_root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.is_file() else {}
    enum_state = checkpoint.get("enumeration", {})
    menu_cache = evidence_dir / "official_menu.html.gz"
    menu_state = checkpoint.get("official_menu") or {}
    if menu_cache.is_file() and menu_state.get("status") == "observed" and not args.refresh_enumeration:
        with gzip.open(menu_cache, "rt", encoding="utf-8", errors="replace") as handle:
            menu_html = handle.read()
        menu_observed_at = menu_state["observed_at"]
    else:
        menu_body, menu_error = fetch_public_html(CVF_MENU_URL, args.delay_seconds, last_started, args.timeout_seconds)
        menu_observed_at = utc_now()
        if menu_error:
            write_jsonl(run_root / "enumeration_unresolved.jsonl", [{"source_url": CVF_MENU_URL, "error": menu_error, "observed_at": menu_observed_at}])
            raise RuntimeError(f"CVF official menu could not be read: {menu_error}")
        menu_html = menu_body.decode("utf-8", "replace")
        write_gzip_bytes(menu_cache, menu_body)
        menu_state = {"status": "observed", "source_url": CVF_MENU_URL, "observed_at": menu_observed_at}

    all_official_editions = official_menu_editions(menu_html, args.venue, 2015, 9999)
    editions, not_yet_listed_years, latest_official_year = classify_requested_editions(
        args.venue, args.start_year, args.end_year, all_official_editions,
    )
    years = list(editions)
    checkpoint = {**checkpoint, "official_menu": menu_state}
    write_json(run_root / "conference_editions.json", {
        "venue_id": args.venue,
        "menu_url": CVF_MENU_URL,
        "menu_observed_at": menu_observed_at,
        "source": "visible CVF Open Access menu links labeled Main Conference",
        "requested_year_range": [args.start_year, args.end_year],
        "latest_official_main_conference_year_visible": latest_official_year,
        "not_yet_listed_years_in_requested_range": not_yet_listed_years,
        "editions": [{"year": year, "official_url": editions[year]} for year in years],
    })

    def parse_listing_page(page_url: str, page_body: bytes) -> list[dict[str, Any]]:
        parser = CVFListingParser(page_url)
        parser.feed(page_body.decode("utf-8", "replace"))
        parser.close()
        return parser.rows

    failures: list[dict[str, Any]] = []
    for year in years:
        manifest = expected_dir / f"{year}.jsonl.gz"
        year_state = enum_state.get(str(year), {})
        if manifest.is_file() and year_state.get("status") == "complete" and not args.refresh_enumeration:
            continue
        edition_url = editions[year]
        root_body, error = fetch_public_html(edition_url, args.delay_seconds, last_started, args.timeout_seconds)
        root_observed_at = utc_now()
        if error:
            failures.append({"year": year, "source_url": edition_url, "error": error, "observed_at": root_observed_at})
            print(f"ENUMERATION FAILED {args.venue} {year}: {error}", flush=True)
            break
        write_gzip_bytes(evidence_dir / f"{year}_index.html.gz", root_body)
        root_html = root_body.decode("utf-8", "replace")
        listing_pages: list[tuple[str, str, bytes, list[dict[str, Any]]]] = []
        root_rows = parse_listing_page(edition_url, root_body)
        targets = official_listing_targets(edition_url, root_body, root_rows)
        if len(targets) == 1 and targets[0][1] == edition_url and root_rows:
            listing_pages.append((edition_url, root_observed_at, root_body, root_rows))
        else:
            for page_no, (label, page_url) in enumerate(targets, start=1):
                if page_url == edition_url and root_rows:
                    page_body, page_observed = root_body, root_observed_at
                else:
                    page_body, page_error = fetch_public_html(page_url, args.delay_seconds, last_started, args.timeout_seconds)
                    page_observed = utc_now()
                    if page_error:
                        failures.append({"year": year, "source_url": page_url, "error": page_error, "observed_at": page_observed})
                        break
                page_rows = parse_listing_page(page_url, page_body)
                if not page_rows:
                    failures.append({"year": year, "source_url": page_url, "error": "official_list_page_has_no_paper_rows", "page_label": label, "observed_at": page_observed})
                    break
                write_gzip_bytes(evidence_dir / f"{year}_page-{page_no}.html.gz", page_body)
                listing_pages.append((page_url, page_observed, page_body, page_rows))
            if failures and failures[-1].get("year") == year:
                print(f"ENUMERATION FAILED {args.venue} {year}: {failures[-1]['error']}", flush=True)
                break
        if not listing_pages:
            failures.append({"year": year, "source_url": edition_url, "error": "no_official_paper_or_day_list_found", "observed_at": root_observed_at})
            break

        ids: set[str] = set()
        year_rows: list[dict[str, Any]] = []
        page_urls: list[str] = []
        for page_url, page_observed, _, page_rows in listing_pages:
            page_urls.append(page_url)
            for item in page_rows:
                identity = source_identity(args.venue, year, item["landing_url"])
                if identity in ids:
                    # CVF day pages can overlap at navigation boundaries; sourceID
                    # is the identity, so retain the first official observation.
                    continue
                ids.add(identity)
                year_rows.append({
                    "venue_id": args.venue,
                    "year": year,
                    "source_native_id": identity,
                    "title": item.get("title"),
                    "authors": item.get("authors") or [],
                    "landing_url": item["landing_url"],
                    "pdf_url": item.get("pdf_url"),
                    "source_url": page_url,
                    "source_observed_at": page_observed,
                    "source_scope": "official_cvF_main_proceedings_listing",
                })
            if failures and failures[-1].get("year") == year:
                break
        if failures and failures[-1].get("year") == year:
            break
        write_jsonl(manifest, year_rows)
        id_hash = hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()
        enum_state[str(year)] = {
            "status": "complete",
            "edition_url": edition_url,
            "source_urls": page_urls,
            "observed_at": max(row[1] for row in listing_pages),
            "source_item_count": len(year_rows),
            "source_item_set_sha256": id_hash,
            "serialized_as": "sorted UTF-8 source_native_id values joined by LF, no trailing LF",
            "listing_cache": [str((evidence_dir / f"{year}_index.html.gz").relative_to(run_root))] + [str((evidence_dir / f"{year}_page-{i}.html.gz").relative_to(run_root)) for i in range(1, len(listing_pages) + 1) if (evidence_dir / f"{year}_page-{i}.html.gz").is_file()],
        }
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.is_file() else checkpoint
        checkpoint.update({"schema_version": CHECKPOINT_VERSION, "venue_id": args.venue, "run_status": "enumerating", "official_menu": menu_state, "enumeration": enum_state, "updated_at": utc_now()})
        write_json(checkpoint_path, checkpoint)
        print(f"ENUMERATED {args.venue} {year}: {len(year_rows)} papers from {len(listing_pages)} official list page(s)", flush=True)

    if failures:
        write_jsonl(run_root / "enumeration_unresolved.jsonl", failures)
        total_so_far = sum(state.get("source_item_count", 0) for state in enum_state.values())
        raise RuntimeError(f"official enumeration stopped after {total_so_far} identities; see enumeration_unresolved.jsonl")
    write_jsonl(run_root / "enumeration_unresolved.jsonl", [])
    if any(enum_state.get(str(year), {}).get("status") != "complete" for year in years):
        raise RuntimeError(f"enumeration incomplete for listed editions: {years}")
    all_expected = []
    for year in years:
        all_expected.extend(read_jsonl(expected_dir / f"{year}.jsonl.gz"))
    ids = sorted(row["source_native_id"] for row in all_expected)
    set_hash = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    source_urls = [CVF_MENU_URL]
    for year in years:
        source_urls.append(enum_state[str(year)]["edition_url"])
        source_urls.extend(enum_state[str(year)]["source_urls"])
    evidence = {
        "venue_id": args.venue,
        "status": "UPDATED",
        "drift_status": "NO_DRIFT",
        "enumeration_complete": True,
        "observed_at": max([menu_observed_at] + [enum_state[str(year)]["observed_at"] for year in years]),
        "source_urls": list(dict.fromkeys(source_urls)),
        "source_item_set_sha256": set_hash,
        "source_item_set_serialization": "sorted UTF-8 source_native_id values joined by LF, no trailing LF",
        "current_source_item_count": len(ids),
        "new_ids": ids,
        "missing_ids": [],
        "yearly_source_item_counts": {str(year): enum_state[str(year)]["source_item_count"] for year in years},
        "scope": "All entries on the official CVF Open Access Main Conference edition pages. The CVF menu distinguishes Main Conference from Workshops, Findings, and Demos. Where an All Papers link is absent, every visible Day link on the edition page is enumerated and unioned.",
        "requested_year_range": [args.start_year, args.end_year],
        "latest_official_main_conference_year_visible": latest_official_year,
        "not_yet_listed_years_in_requested_range": not_yet_listed_years,
        "enumeration_frontier_policy": "All legal requested conference years through the newest visible official Main Conference edition are required; gaps fail closed. Later legal years in the requested range are recorded as not yet listed and are outside the asserted enumerated scope.",
        "doi_policy": "Read from CVF detail metadata when present; no DOI is inferred when the official record does not expose one.",
    }
    write_json(run_root / "waterline_evidence.json", evidence)
    write_json(run_root / "enumeration_summary.json", {
        "venue_id": args.venue,
        "years": years,
        "yearly_counts": evidence["yearly_source_item_counts"],
        "total_source_items": len(ids),
        "source_item_set_sha256": set_hash,
        "observed_at": evidence["observed_at"],
    })
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.is_file() else checkpoint
    checkpoint.update({"official_menu": menu_state, "enumeration": enum_state, "expected_count": len(ids), "run_status": "enumerated", "updated_at": utc_now()})
    write_json(checkpoint_path, checkpoint)
    return all_expected


def refresh_cached_listing_authors(run_root: Path, venue_id: str, start_year: int, end_year: int) -> dict[str, Any]:
    """Reparse saved official listing HTML without network and preserve author order/repeats."""
    expected_dir = run_root / "expected"
    evidence_dir = run_root / "evidence" / "official_listings"
    checkpoint_path = run_root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    enum_state = checkpoint.get("enumeration", {})
    repair_root = run_root / "listing_author_refresh"
    snapshot_dir = repair_root / "expected_before"
    all_changes: list[dict[str, Any]] = []
    yearly: dict[str, dict[str, Any]] = {}

    for manifest in sorted(expected_dir.glob("*.jsonl.gz")):
        try:
            year = int(manifest.name.removesuffix(".jsonl.gz"))
        except ValueError:
            continue
        if not start_year <= year <= end_year:
            continue
        state = enum_state.get(str(year), {})
        source_urls = list(state.get("source_urls") or [])
        if state.get("status") != "complete" or not source_urls:
            raise RuntimeError(f"official cached listing map is incomplete for {year}")
        original_bytes = manifest.read_bytes()
        rows = read_jsonl(manifest)
        expected_by_id = {row["source_native_id"]: row for row in rows}
        refreshed_authors: dict[str, tuple[list[str], str, str]] = {}
        parsed_ids: set[str] = set()

        for page_number, page_url in enumerate(source_urls, start=1):
            page_cache = evidence_dir / f"{year}_page-{page_number}.html.gz"
            if not page_cache.is_file():
                page_cache = evidence_dir / f"{year}_index.html.gz"
            if not page_cache.is_file():
                raise RuntimeError(f"cached official list HTML is missing for {year}: {page_url}")
            with gzip.open(page_cache, "rb") as handle:
                page_body = handle.read()
            parser = CVFListingParser(page_url)
            parser.feed(page_body.decode("utf-8", "replace"))
            parser.close()
            page_ids: set[str] = set()
            for item in parser.rows:
                identity = source_identity(venue_id, year, item["landing_url"])
                page_ids.add(identity)
                parsed_ids.add(identity)
                expected = expected_by_id.get(identity)
                if expected is None or expected.get("source_url") != page_url:
                    continue
                refreshed_authors[identity] = (
                    list(item.get("authors") or []),
                    page_url,
                    hashlib.sha256(page_body).hexdigest(),
                )
        if parsed_ids != set(expected_by_id):
            missing = sorted(set(expected_by_id) - parsed_ids)
            extra = sorted(parsed_ids - set(expected_by_id))
            raise RuntimeError(f"cached author reparse changed {year} listing identities: missing={missing[:5]} extra={extra[:5]}")

        changed_rows = []
        for identity, (authors, page_url, html_sha256) in refreshed_authors.items():
            expected = expected_by_id[identity]
            old_authors = list(expected.get("authors") or [])
            if authors == old_authors:
                continue
            expected["authors"] = authors
            changed_rows.append({
                "venue_id": venue_id,
                "year": year,
                "source_native_id": identity,
                "source_url": page_url,
                "source_observed_at": expected.get("source_observed_at"),
                "cached_listing_html_sha256": html_sha256,
                "previous_parsed_authors": old_authors,
                "refreshed_ordered_authors": authors,
                "reason_code": "official_listing_author_order_and_repeated_names_preserved_from_cached_html",
            })
        if changed_rows:
            snapshot = snapshot_dir / manifest.name
            if not snapshot.exists():
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                snapshot.write_bytes(original_bytes)
            write_jsonl(manifest, rows)
        refreshed_ids = sorted(row["source_native_id"] for row in rows)
        id_set_sha256 = hashlib.sha256("\n".join(refreshed_ids).encode("utf-8")).hexdigest()
        if id_set_sha256 != state.get("source_item_set_sha256"):
            raise RuntimeError(f"cached author refresh unexpectedly changed the {year} source identity set")
        all_changes.extend(changed_rows)
        yearly[str(year)] = {
            "source_item_count": len(rows),
            "source_item_set_sha256": id_set_sha256,
            "updated_author_lists": len(changed_rows),
            "previous_expected_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "refreshed_expected_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        }

    write_jsonl(repair_root / "author_list_differences.jsonl", all_changes)
    receipt = {
        "venue_id": venue_id,
        "network_fetches": 0,
        "cached_official_listing_html_reparsed": True,
        "author_order_and_duplicate_values_preserved": True,
        "id_set_unchanged": True,
        "total_updated_author_lists": len(all_changes),
        "yearly": yearly,
        "updated_at": utc_now(),
    }
    write_json(repair_root / "receipt.json", receipt)
    return receipt


def collect_details(args: argparse.Namespace, last_started: list[float] | RequestRateLimiter) -> dict[str, Any]:
    expected_dir = args.run_root / "expected"
    expected_rows = []
    manifests = sorted(expected_dir.glob("*.jsonl.gz"))
    for manifest in manifests:
        try:
            manifest_year = int(manifest.name.removesuffix(".jsonl.gz"))
        except ValueError:
            continue
        if args.start_year <= manifest_year <= args.end_year:
            expected_rows.extend(read_jsonl(manifest))
    if not expected_rows:
        raise RuntimeError("no expected identities found; run enumerate first")

    supplement_path = args.run_root / "official_abstract_supplements.jsonl"
    abstract_supplements = {
        row["source_native_id"]: row
        for row in read_jsonl(supplement_path)
        if row.get("source_native_id") and row.get("abstract")
    }

    connection = open_cache(args.run_root / "cache" / "detail_records.sqlite")
    processed_this_run = 0
    failures = []
    last_identity = None
    cached_states = {
        row[0]: row[1]
        for row in connection.execute("SELECT source_native_id,state FROM detail_records")
    }
    todo = [
        (index, item)
        for index, item in enumerate(expected_rows, start=1)
        if cached_states.get(item["source_native_id"]) not in {"fetched", "excluded"}
        and not (cached_states.get(item["source_native_id"]) == "fallback" and not args.retry_unresolved)
    ]
    workers = min(3, max(1, args.concurrency))
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cvf-detail")
    access_barrier = False
    try:
        pending = {}
        next_item = 0
        while pending or next_item < len(todo):
            while not access_barrier and len(pending) < workers and next_item < len(todo):
                if args.max_details and processed_this_run + len(pending) >= args.max_details:
                    break
                index, item = todo[next_item]
                future = executor.submit(
                    fetch_public_html,
                    str(item["landing_url"]),
                    args.delay_seconds,
                    last_started,
                    args.timeout_seconds,
                )
                pending[future] = (index, item)
                next_item += 1
            if not pending:
                break
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item_future: pending[item_future][0]):
                index, item = pending.pop(future)
                identity = item["source_native_id"]
                detail_url = str(item["landing_url"])
                body, error = future.result()
                observed_at = utc_now()
                page = None
                if body is not None:
                    html = body.decode("utf-8", "replace")
                    candidate = CVFDetailParser()
                    candidate.feed(html)
                    candidate.close()
                    article_markers = candidate.first("citation_title") and (
                        candidate.meta.get("citation_author") or candidate.abstract
                    )
                    if response_is_access_barrier(None, body):
                        error = "official_access_challenge_detected"
                    elif not article_markers:
                        error = "detail_page_missing_article_metadata"
                    else:
                        candidate._html_for_doi = html
                        page = candidate
                row, missing_fields = make_staging_row(args.venue, item, page, observed_at, error)
                row = apply_abstract_supplement(row, abstract_supplements.get(identity))
                record_state = "excluded" if page is not None and is_official_violation_notice(page.first("citation_title")) else ("fetched" if page is not None else "fallback")
                record_cache_row(connection, row, record_state, error)
                connection.commit()
                processed_this_run += 1
                last_identity = identity
                if error:
                    failures.append({"source_native_id": identity, "year": item["year"], "source_url": detail_url, "error": error, "observed_at": observed_at})
                if processed_this_run % 50 == 0:
                    progress = write_outputs(args.run_root, args.venue, connection, expected_rows, "collecting", last_identity)
                    print(f"DETAILS {args.venue}: {progress['fetched_detail_count']}/{len(expected_rows)} pages fetched; {progress['fallback_count']} fallback", flush=True)
                if error and response_is_access_barrier(error, body):
                    access_barrier = True
                    if isinstance(last_started, RequestRateLimiter):
                        last_started.stop_after_access_barrier()
                if missing_fields and page is not None:
                    # Officially checked missing fields are retained with structured reasons.
                    pass
                if index % 500 == 0 and processed_this_run % 50 != 0:
                    progress = write_outputs(args.run_root, args.venue, connection, expected_rows, "collecting", last_identity)
                    print(f"DETAILS {args.venue}: {progress['fetched_detail_count']}/{len(expected_rows)} pages fetched; {progress['fallback_count']} fallback", flush=True)
            if access_barrier:
                for future in pending:
                    future.cancel()
                break
            if args.max_details and processed_this_run >= args.max_details:
                break
    finally:
        # At most the current batch is already in flight when a barrier is
        # discovered; cancel queued work and wait for active responses to end.
        executor.shutdown(wait=True, cancel_futures=True)

    if access_barrier:
        write_jsonl(args.run_root / "fetch_barriers.jsonl", failures)
        progress = write_outputs(args.run_root, args.venue, connection, expected_rows, "blocked_on_source_response", last_identity)
        connection.close()
        print(f"STOPPED at official access barrier after {progress['staged_count']} staged identities; no bypass attempted", flush=True)
        return progress

    connection.commit()
    cached_after = {row[0] for row in connection.execute("SELECT source_native_id FROM detail_records")}
    expected_ids = {row["source_native_id"] for row in expected_rows}
    final_status = "details_collected" if expected_ids.issubset(cached_after) else "partial"
    progress = write_outputs(args.run_root, args.venue, connection, expected_rows, final_status, last_identity)
    write_jsonl(args.run_root / "fetch_barriers.jsonl", failures)
    connection.close()
    return progress


def print_status(run_root: Path, venue_id: str) -> None:
    checkpoint = run_root / "checkpoint.json"
    if not checkpoint.is_file():
        print(json.dumps({"venue_id": venue_id, "status": "not_started"}, indent=2))
        return
    print(checkpoint.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enumerate", "collect", "refresh-authors", "run", "status"))
    parser.add_argument("--venue", required=True, choices=("cvpr", "iccv"))
    parser.add_argument("--home", required=True, type=Path, help="literature-db home")
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=datetime.now(timezone.utc).year)
    parser.add_argument("--delay-seconds", type=float, default=1.0 / 3.0, help="minimum spacing between CVF request starts (never above 3 per second)")
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=3, help="maximum detail requests in flight (CVF limit: 3)")
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--max-details", type=int, default=0, help="stop after N newly fetched detail pages; 0 means all")
    parser.add_argument("--retry-unresolved", action="store_true", help="retry previous fallback records")
    parser.add_argument("--refresh-enumeration", action="store_true", help="re-fetch official annual lists")
    args = parser.parse_args(argv)
    args.home = args.home.expanduser().resolve()
    if not args.run_root:
        args.run_root = args.home / "runs" / f"expand-{datetime.now().strftime('%Y%m%d')}" / args.venue
    else:
        args.run_root = args.run_root.expanduser().resolve()
    if args.start_year < 2015 or args.end_year < args.start_year:
        parser.error("year range must start at 2015 or later and end on or after start")
    if args.delay_seconds < 1.0 / 3.0:
        parser.error("delay-seconds must keep the global CVF request rate at or below 3 per second")
    last_started = RequestRateLimiter(args.delay_seconds)
    try:
        if args.action == "status":
            print_status(args.run_root, args.venue)
            return 0
        if args.action == "refresh-authors":
            receipt = refresh_cached_listing_authors(args.run_root, args.venue, args.start_year, args.end_year)
            print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
            return 0
        if args.action in {"enumerate", "run"}:
            expected_rows = enumerate_years(args, last_started)
            edition_count = sum(
                1 for path in (args.run_root / "expected").glob("*.jsonl.gz")
                if args.start_year <= int(path.name.removesuffix(".jsonl.gz")) <= args.end_year
            )
            print(f"ENUMERATION COMPLETE {args.venue}: {len(expected_rows)} source items in {edition_count} listed editions", flush=True)
        if args.action in {"collect", "run"}:
            progress = collect_details(args, last_started)
            print(json.dumps(progress, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

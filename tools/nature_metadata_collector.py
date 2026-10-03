#!/usr/bin/env python3
"""Checkpointed metadata collector for a small, explicitly configured Nature portfolio.

Each venue must have a local registry record and an observed listing entry.  A
new venue can only become runnable after its own bounded ``sample``.  The
collector uses only same-origin www.nature.com HTML pages and invokes the
already-installed curl client so the host's public CA/proxy configuration is
respected.  It does not read cookies, authorization headers, or browser
storage, and never fetches PDF bytes.

The collector has three useful stages:

  sample       one mixed listing page for the venue's own entry evidence;
  enumerate    every mixed year-filtered listing page in the venue scope;
  details      detail-validate every allowlisted listing card.

Each stage is resumable.  Details are written one JSON object per line and the
checkpoint is atomically replaced after each completed source page/record.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse


ROOT = Path(__file__).resolve().parents[1]


def default_litdb_home() -> Path:
    value = os.environ.get("LITDB_HOME")
    return (
        Path(value).expanduser().resolve()
        if value
        else (ROOT / "data" / "literature-db").resolve()
    )


def default_registry_root(home: Path | None = None) -> Path:
    value = os.environ.get("LITDB_REGISTRY_ROOT")
    if value:
        return Path(value).expanduser().resolve()
    return Path(home or default_litdb_home()).expanduser().resolve() / "registry" / "venues"


def default_output_root(home: Path | None = None) -> Path:
    return (home or default_litdb_home()) / "runs" / "nature-metadata"


DEFAULT_HOME = default_litdb_home()
DEFAULT_OUTPUT_ROOT = default_output_root(DEFAULT_HOME)
DEFAULT_REGISTRY_ROOT = default_registry_root(DEFAULT_HOME)
DEFAULT_BASE = "https://www.nature.com"
SUPPORTED_VENUES = (
    "nature-machine-intelligence",
    "nature-computational-science",
    "nature-methods",
    "nature",
)
ALLOWED_TYPES = {
    "Article",
    "Analysis",
    "Perspective",
    "Review Article",
    "Research Article",
    "Letter",
    "Brief Communication",
    "Methods",
    "Method",
    "Resource",
    "Data",
    "Data Descriptor",
    "Systems",
    "Systems Article",
    "Technical Article",
    "Technical Report",
    "Review",
    "Survey",
}
EXCLUDED_TYPES = {
    "Addendum",
    "Author Correction",
    "Books & Arts",
    "Challenge Accepted",
    "Comment",
    "Correspondence",
    "Editorial",
    "Feature",
    "Matters Arising",
    "News & Views",
    "News Feature",
    "Publisher Correction",
    "Q&A",
    "Video",
}
REQUEST_INTERVAL_SECONDS = 6.2  # <= 10 source pages/minute, including details.
RETRY_DELAYS = (5.0, 15.0)
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
UNSAFE_QUERY_KEYS = {
    "auth",
    "code",
    "csrf",
    "key",
    "session",
    "share",
    "signature",
    "token",
}


class VenueConfigurationError(ValueError):
    """A venue is not safe to run until its local evidence is complete."""


@dataclass(frozen=True)
class HistoricalSeedEvidence:
    seed_path: Path
    preflight_path: Path
    closed_years: frozenset[int]
    expected_counts: dict[int, int]
    expected_identity_sha256: str
    expected_seed_count: int
    receipt_seed_count: int


@dataclass(frozen=True)
class VenueConfig:
    venue_id: str
    venue: str
    base_url: str
    journal_path: str
    start_year: int
    current_year: int
    output_dir: Path
    registry_path: Path
    evidence_path: Path
    browse_entry_url: str
    entry_template: str
    rss_url: str | None
    sample_verified: bool
    allowed_types: frozenset[str]
    excluded_types: frozenset[str]
    historical: HistoricalSeedEvidence | None
    user_agent: str

    @property
    def years(self) -> list[int]:
        return list(range(self.start_year, self.current_year + 1))


def _load_registry(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VenueConfigurationError(f"registry record missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VenueConfigurationError(f"registry record is not JSON-compatible: {path}") from exc
    if not isinstance(data, dict):
        raise VenueConfigurationError(f"registry record must be an object: {path}")
    return data


def _read_entry_evidence(venue_id: str, output_root: Path, evidence_root: Path) -> tuple[dict[str, Any] | None, Path]:
    candidates = [
        output_root / venue_id / "discovery" / "entry_evidence.json",
        evidence_root / venue_id / "discovery" / "entry_evidence.json",
    ]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        data = read_json(path, None)
        if isinstance(data, dict) and data.get("browse_entry_url"):
            return data, path
    return None, candidates[0]


def _validate_entry_url(value: str, base_url: str = DEFAULT_BASE) -> str:
    parsed = urlparse(urljoin(base_url, value))
    if parsed.scheme != "https" or parsed.netloc != urlparse(base_url).netloc:
        raise VenueConfigurationError(f"entry URL must be same-origin HTTPS: {value}")
    if not parsed.path.rstrip("/").endswith("/articles"):
        raise VenueConfigurationError(f"entry URL must be an observed /articles listing: {value}")
    safe_query: list[tuple[str, str]] = []
    for key, item in parse_qs(parsed.query, keep_blank_values=True).items():
        if key.lower() in UNSAFE_QUERY_KEYS:
            continue
        safe_query.extend((key, value) for value in item)
    return urlunparse(parsed._replace(query=urlencode(safe_query), fragment=""))


def _journal_path_from_entry(entry_url: str) -> str:
    path = urlparse(entry_url).path.rstrip("/")
    if not path.endswith("/articles"):
        raise VenueConfigurationError(f"listing entry has no /articles path: {entry_url}")
    journal_path = path[: -len("/articles")].rstrip("/")
    return journal_path or "/"


def _entry_template(entry_url: str, current_year: int) -> tuple[str, str]:
    parsed = urlparse(entry_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "year" in query:
        query["year"] = ["<year>"]
    else:
        query["year"] = ["<year>"]
    encoded_query = urlencode(query, doseq=True).replace("%3Cyear%3E", "<year>")
    template = urlunparse(parsed._replace(query=encoded_query, fragment=""))
    browse = urlunparse(parsed._replace(query="", fragment=""))
    # Fail closed if a caller supplied an entry whose template does not retain
    # a year selector.  This prevents a portfolio run from silently traversing
    # an unbounded or mixed feed.
    if "<year>" not in template:
        raise VenueConfigurationError(f"listing entry has no year selector: {entry_url}")
    return browse, template


def _resolve_relative(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _historical_seed_evidence(
    venue_id: str,
    preflight_path: Path | None,
    output_root: Path,
) -> HistoricalSeedEvidence | None:
    if venue_id != "nature":
        return None
    path = preflight_path or output_root / "nature" / "historical-preflight" / "historical_closed_year_evidence.json"
    evidence = read_json(path, None)
    if not isinstance(evidence, dict):
        raise VenueConfigurationError(f"Nature historical preflight missing: {path}")
    if evidence.get("status") != "PASS" or not evidence.get("enumeration_only"):
        raise VenueConfigurationError(f"Nature historical preflight is not enumeration-only PASS: {path}")
    value = evidence.get("historical_enumeration")
    if not isinstance(value, str):
        raise VenueConfigurationError(f"Nature historical enumeration path missing: {path}")
    closed_years = evidence.get("closed_years")
    if not isinstance(closed_years, list) or not all(isinstance(year, int) for year in closed_years):
        raise VenueConfigurationError(f"Nature historical closed_years missing: {path}")
    expected_counts_raw = evidence.get("per_year_counts")
    if not isinstance(expected_counts_raw, dict):
        raise VenueConfigurationError(f"Nature historical per_year_counts missing: {path}")
    expected_counts: dict[int, int] = {}
    for raw_year, raw_count in expected_counts_raw.items():
        try:
            year = int(raw_year)
        except (TypeError, ValueError) as exc:
            raise VenueConfigurationError(f"Nature historical per_year_counts has invalid year: {raw_year!r}") from exc
        if not isinstance(raw_count, int) or raw_count < 0:
            raise VenueConfigurationError(f"Nature historical per_year_counts has invalid count for {year}")
        expected_counts[year] = raw_count
    if any(year not in expected_counts for year in closed_years):
        raise VenueConfigurationError(f"Nature historical per_year_counts omits a closed year: {path}")
    expected_identity_sha256 = evidence.get("closed_year_identity_set_sha256")
    if not isinstance(expected_identity_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_identity_sha256):
        raise VenueConfigurationError(f"Nature historical closed identity hash is invalid: {path}")
    receipt_seed_count = evidence.get("receipt_seed_count")
    if not isinstance(receipt_seed_count, int) or receipt_seed_count < 1:
        raise VenueConfigurationError(f"Nature historical receipt_seed_count is invalid: {path}")
    if sum(expected_counts.values()) != receipt_seed_count:
        raise VenueConfigurationError(f"Nature historical receipt_seed_count disagrees with per_year_counts: {path}")
    seed = _resolve_relative(ROOT, value)
    if not seed.exists():
        raise VenueConfigurationError(f"Nature historical enumeration missing: {seed}")
    return HistoricalSeedEvidence(
        seed_path=seed,
        preflight_path=path,
        closed_years=frozenset(closed_years),
        expected_counts=expected_counts,
        expected_identity_sha256=expected_identity_sha256,
        # The adapter file is intentionally closed-year-only even though the
        # preflight receipt also records the current-year refresh count.
        expected_seed_count=sum(expected_counts[year] for year in closed_years),
        receipt_seed_count=receipt_seed_count,
    )


def build_venue_config(
    venue_id: str,
    *,
    current_year: int,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    registry_root: Path = DEFAULT_REGISTRY_ROOT,
    evidence_root: Path | None = None,
    entry_url: str | None = None,
    historical_preflight: Path | None = None,
    require_sample: bool = True,
) -> VenueConfig:
    if venue_id not in SUPPORTED_VENUES:
        raise VenueConfigurationError(f"unsupported venue: {venue_id}")
    evidence_root = evidence_root or output_root
    registry_path = registry_root / f"{venue_id}.yml"
    registry = _load_registry(registry_path)
    if registry.get("id") != venue_id:
        raise VenueConfigurationError(f"registry id mismatch in {registry_path}")
    allowed_domains = registry.get("allowed_domains") or ["nature.com"]
    if allowed_domains != ["nature.com"]:
        raise VenueConfigurationError(f"venue has unsupported allowed domains: {venue_id}")
    evidence, evidence_path = _read_entry_evidence(venue_id, output_root, evidence_root)
    supplied_entry = _validate_entry_url(entry_url) if entry_url else None
    observed_entry = evidence.get("browse_entry_url") if evidence else None
    selected_entry = supplied_entry or observed_entry
    if not selected_entry:
        if require_sample:
            raise VenueConfigurationError(
                f"{venue_id} has no observed listing entry; run sample with --entry-url"
            )
        raise VenueConfigurationError(f"{venue_id} has no listing entry")
    selected_entry = _validate_entry_url(selected_entry)
    journal_path = _journal_path_from_entry(selected_entry)
    if evidence and evidence.get("mixed_year_entry_template") and not supplied_entry:
        validated_template = _validate_entry_url(str(evidence["mixed_year_entry_template"]).replace("<year>", str(current_year)))
        _, template = _entry_template(validated_template, current_year)
        browse_entry = _validate_entry_url(str(evidence["browse_entry_url"]))
    else:
        browse_entry, template = _entry_template(selected_entry, current_year)
    start_value = registry.get("crawl_from")
    active_value = registry.get("active_from")
    if not isinstance(start_value, int):
        raise VenueConfigurationError(f"registry crawl_from missing: {registry_path}")
    if isinstance(active_value, int):
        start_value = max(start_value, active_value)
    if start_value > current_year:
        raise VenueConfigurationError(f"venue scope starts after current year: {venue_id}")
    sample_verified = bool(evidence) and bool(evidence.get("sample_verified", True))
    if require_sample and not sample_verified and not entry_url:
        raise VenueConfigurationError(
            f"{venue_id} entry is not sample-verified; run sample with --entry-url"
        )
    output_dir = output_root / venue_id
    historical = _historical_seed_evidence(venue_id, historical_preflight, output_root)
    rss_url = None
    if evidence and evidence.get("rss_link_observed"):
        candidate_rss = official_url(str(evidence["rss_link_observed"]), journal_path=journal_path)
        rss_url = candidate_rss
    base_url = DEFAULT_BASE
    user_agent = f"Mozilla/5.0 (compatible; LiteratureDB-{venue_id}/2026.10; +{base_url}{journal_path})"
    return VenueConfig(
        venue_id=venue_id,
        venue=str(registry.get("canonical_name") or venue_id),
        base_url=base_url,
        journal_path=journal_path,
        start_year=start_value,
        current_year=current_year,
        output_dir=output_dir,
        registry_path=registry_path,
        evidence_path=evidence_path,
        browse_entry_url=browse_entry,
        entry_template=template,
        rss_url=rss_url,
        # A runtime URL is only a candidate until the bounded sample succeeds
        # and writes its own evidence receipt.
        sample_verified=sample_verified,
        allowed_types=frozenset(ALLOWED_TYPES),
        excluded_types=frozenset(EXCLUDED_TYPES),
        historical=historical,
        user_agent=user_agent,
    )


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def official_url(
    value: str,
    *,
    allow_pdf: bool = False,
    base_url: str = DEFAULT_BASE,
    journal_path: str | None = None,
) -> str | None:
    parsed = urlparse(urljoin(base_url, value))
    if parsed.scheme != "https" or parsed.netloc != urlparse(base_url).netloc:
        return None
    # Preserve visible Nature pagination/filter state.  Strip only credential-
    # like query parameters; never persist an IDP code or session token.
    safe_query = []
    for key, item in parse_qs(parsed.query, keep_blank_values=True).items():
        if key.lower() in UNSAFE_QUERY_KEYS:
            continue
        safe_query.extend((key, value) for value in item)
    parsed = parsed._replace(query=urlencode(safe_query), fragment="")
    allowed_journal_path = journal_path or ""
    if not parsed.path.startswith("/articles/") and not (allowed_journal_path and parsed.path.startswith(allowed_journal_path)):
        return None
    if not allow_pdf and parsed.path.endswith(".pdf"):
        return None
    if allow_pdf and not parsed.path.startswith("/articles/"):
        return None
    return urlunparse(parsed)


def stable_article_path(value: str) -> str | None:
    parsed = urlparse(urljoin(DEFAULT_BASE, value))
    if parsed.netloc and parsed.netloc != "www.nature.com":
        return None
    if not parsed.path.startswith("/articles/"):
        return None
    return parsed.path.rstrip("/")


def canonical_article_url(path: str) -> str:
    return f"{DEFAULT_BASE}{path}"


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = html.unescape(value).strip()
    value = re.sub(r"^https?://doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^doi:\s*", "", value, flags=re.I)
    value = value.rstrip(".,;)")
    if re.fullmatch(r"10\.\d{4,9}/\S+", value) and not re.search(r"\s", value):
        return value.lower()
    return None


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = html.unescape(re.sub(r"\s+", " ", value)).strip()
    return value or None


def abstract_label_kind(value: str | None) -> str | None:
    """Classify only the observed Abstract label and the observed spelling variants."""
    label = clean_text(value)
    if not label:
        return None
    folded = label.casefold()
    if folded == "abstract":
        return "abstract"
    if folded in {"abtract", "abstratct", "asbtract", "abstract>", "abstarct"}:
        return "abstract_typo"
    return "other"


def full_calendar_date(value: str | None) -> str | None:
    """Return an observed ISO calendar date, rejecting month-only values."""
    value = clean_text(value)
    if not value:
        return None
    match = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})(?:$|[T ])", value)
    if not match:
        return None
    try:
        observed = dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return observed.isoformat()


def redact_html_for_cache(value: str) -> str:
    """Remove transient IDP/query codes before retaining public HTML evidence."""
    return re.sub(
        r"([?&](?:code|token|session|csrf|auth|signature)=[^&\"'<> ]*)",
        "",
        value,
        flags=re.I,
    )


def cache_html(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    redacted = redact_html_for_cache(value).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    try:
        with gzip.open(tmp, "wb") as handle:
            handle.write(redacted)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def read_cached_html(path: Path) -> str:
    with gzip.open(path, "rb") as handle:
        return handle.read().decode("utf-8", "replace")


CHALLENGE_PATTERNS = (
    (re.compile(r"\bcaptcha\b", re.I), "captcha"),
    (re.compile(r"human\s+verification", re.I), "human_verification"),
    (re.compile(r"verify\s+(?:you\s+are|that\s+you\s+are)\s+human", re.I), "human_verification"),
    (re.compile(r"security\s+(?:check|challenge)", re.I), "security_challenge"),
    (re.compile(r"checking\s+your\s+browser", re.I), "browser_check"),
    (re.compile(r"challenge-platform|cf-chl-", re.I), "challenge_platform"),
)


def challenge_reason(html_text: str) -> str | None:
    """Return a reason only for a clear challenge page without source structure.

    A normal article may mention CAPTCHA or human verification in its prose.  A
    response is blocked only when a marker is present and neither the observed
    article metadata/abstract structure nor a real listing card is present.
    """
    marker = next((label for pattern, label in CHALLENGE_PATTERNS if pattern.search(html_text)), None)
    if marker is None:
        return None
    article_structure = bool(
        re.search(
            r"<meta\b[^>]*(?:name|property)=[\"'](?:citation_title|prism\.publicationName)[\"'][^>]*>|"
            r"id=[\"']Abs1-content[\"']|application/ld\+json[^>]*>.*?ScholarlyArticle",
            html_text,
            re.I | re.S,
        )
    )
    listing_structure = bool(
        re.search(r"<article\b[^>]*class=[\"'][^\"']*\bc-card\b|class=[\"'][^\"']*\bc-card\b", html_text, re.I)
    )
    if article_structure or listing_structure:
        return None
    return marker


class ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self.pagination: list[dict[str, str | None]] = []
        self.selected_facets: list[str] = []
        self._card: dict[str, Any] | None = None
        self._card_depth = 0
        self._capture: str | None = None
        self._capture_depth = 0
        self._buf: list[str] = []
        self._a_href: str | None = None
        self._a_depth = 0
        self._in_pagination = False
        self._pagination_depth = 0

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        raw = dict(attrs).get("class") or ""
        return set(raw.split())

    @staticmethod
    def _enter_depth(tag: str, depth: int) -> int:
        return depth if tag in VOID_TAGS else depth + 1

    @staticmethod
    def _leave_depth(tag: str, depth: int) -> int:
        return depth if tag in VOID_TAGS else max(0, depth - 1)

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        classes = self._classes(attrs_list)
        if tag == "article" and "c-card" in classes:
            self._card = {
                "title": None,
                "summary": None,
                "authors_listing": [],
                "document_type": None,
                "date": None,
                "open_access": False,
                "path": None,
            }
            self._card_depth = 1
            return
        if self._card is not None:
            self._card_depth = self._enter_depth(tag, self._card_depth)
            if tag == "a" and attrs.get("href") and "/articles/" in attrs["href"]:
                self._a_href = attrs["href"]
                self._a_depth = self._card_depth
            if tag == "h3" or tag == "h2":
                self._capture = "title"
                self._capture_depth = self._card_depth
                self._buf = []
            elif attrs.get("data-test") == "article-description":
                self._capture = "summary"
                self._capture_depth = self._card_depth
                self._buf = []
            elif attrs.get("data-test") == "author-list":
                self._capture = "author-list"
                self._capture_depth = self._card_depth
                self._buf = []
            elif attrs.get("data-test") == "article.type":
                self._capture = "type"
                self._capture_depth = self._card_depth
                self._buf = []
            elif tag == "time" and attrs.get("datetime"):
                self._card["date"] = attrs["datetime"]
            elif attrs.get("data-test") == "open-access":
                self._card["open_access"] = True
            return
        if tag == "span" and "c-facet__selected" in classes:
            self._capture = "facet"
            self._capture_depth = 1
            self._buf = []
        if tag == "a" and "c-pagination__link" in classes:
            self._a_href = attrs.get("href")
            self._a_depth = 0
            self._in_pagination = True
            self._pagination_depth = 1
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture or self._in_pagination:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._card is not None:
            if self._capture and self._card_depth == self._capture_depth:
                value = clean_text(" ".join(self._buf))
                if self._capture == "title":
                    self._card["title"] = value
                elif self._capture == "summary":
                    self._card["summary"] = value
                elif self._capture == "type":
                    self._card["document_type"] = value
                elif self._capture == "author-list" and value:
                    self._card["authors_listing"].append(value)
                self._capture = None
                self._buf = []
            if self._a_href and self._card_depth == self._a_depth and self._card.get("path") is None:
                self._card["path"] = stable_article_path(self._a_href)
                self._a_href = None
            if tag == "article" and self._card_depth == 1:
                if self._card.get("path"):
                    self.cards.append(self._card)
                self._card = None
                self._card_depth = 0
                self._capture = None
                self._a_href = None
            else:
                self._card_depth = self._leave_depth(tag, self._card_depth)
            return
        if self._capture == "facet" and self._capture_depth == 1 and tag == "span":
            value = clean_text(" ".join(self._buf))
            if value:
                self.selected_facets.append(value)
            self._capture = None
            self._buf = []
        if self._in_pagination and tag == "a":
            text_value = clean_text(" ".join(self._buf))
            self.pagination.append({"text": text_value, "href": self._a_href})
            self._in_pagination = False
            self._a_href = None
            self._buf = []


class DetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.jsonld: list[Any] = []
        self.category: str | None = None
        self.abstract_chunks: list[str] = []
        self.abstract_candidates: list[dict[str, Any]] = []
        self._depth = 0
        self._abstract_section_depths: list[int] = []
        self._section_labels: list[dict[str, Any]] = []
        self._non_abstract_section_depths: list[int] = []
        self._abstract_heading_ids: set[str] = set()
        self._abstract_content_depth = 0
        self._abstract_current: dict[str, Any] | None = None
        self._abstract_heading_id: str | None = None
        self._abstract_heading_depth = 0
        self._abstract_heading_chunks: list[str] = []
        self._article_body_depth: int | None = None
        self._publisher_abs_sections: list[dict[str, Any]] = []
        self._publisher_heading_section: dict[str, Any] | None = None
        self._publisher_heading_depth = 0
        self._publisher_heading_chunks: list[str] = []
        self._category_depth = 0
        self._category_chunks: list[str] = []
        self._identifier_depth = 0
        self._identifier_first_item_depth = 0
        self._identifier_first_item_chunks: list[str] = []
        self._published_item_depth = 0
        self._published_item_chunks: list[str] = []
        self._published_item_datetime: str | None = None
        self.publisher_publication_date: str | None = None
        self.publisher_publication_date_locator: str | None = None
        self.publisher_authors: list[str] = []
        self._authors_list_depth = 0
        self._author_link_depth = 0
        self._author_link_chunks: list[str] = []
        self._script_depth = 0
        self._script_type: str | None = None
        self._script_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        start_depth = self._depth + (0 if tag in VOID_TAGS else 1)
        if tag == "meta" and attrs.get("name") and attrs.get("content") is not None:
            name = attrs["name"]
            self.meta.setdefault(name, []).append(html.unescape(attrs["content"]))
        classes = set((attrs.get("class") or "").split())
        if tag == "div" and "c-article-body" in classes and self._article_body_depth is None:
            self._article_body_depth = start_depth
        if tag == "ul" and "c-article-identifiers" in classes:
            self._identifier_depth = start_depth
            self._identifier_first_item_depth = 0
            self._identifier_first_item_chunks = []
        if tag == "ul" and attrs.get("data-test") == "authors-list":
            self._authors_list_depth = start_depth
            self._author_link_depth = 0
            self._author_link_chunks = []
        if self._identifier_depth and tag == "li" and not self._identifier_first_item_depth:
            self._identifier_first_item_depth = start_depth
            self._identifier_first_item_chunks = []
        if self._identifier_depth and tag == "li" and not self._published_item_depth:
            self._published_item_depth = start_depth
            self._published_item_chunks = []
            self._published_item_datetime = None
        if self._published_item_depth and tag == "time" and not self._published_item_datetime:
            self._published_item_datetime = attrs.get("datetime")
        if (
            tag == "a"
            and attrs.get("data-test") == "author-name"
            and self._authors_list_depth
            and not self._author_link_depth
        ):
            self._author_link_depth = start_depth
            self._author_link_chunks = []
        if tag == "script":
            self._script_depth = 1
            self._script_type = attrs.get("type")
            self._script_buf = []
        elif self._script_depth and tag not in VOID_TAGS:
            self._script_depth += 1
        if tag == "section":
            data_title = clean_text(attrs.get("data-title"))
            label_kind = abstract_label_kind(data_title)
            if label_kind == "abstract":
                self._abstract_section_depths.append(start_depth)
            elif label_kind == "other":
                self._non_abstract_section_depths.append(start_depth)
            if data_title:
                self._section_labels.append(
                    {"depth": start_depth, "kind": label_kind or "other", "raw": data_title}
                )
        content_id = attrs.get("id") or ""
        publisher_section_match = re.fullmatch(r"(Abs\d+)-section", content_id, re.I)
        if (
            tag == "div"
            and "c-article-section" in classes
            and publisher_section_match
            and self._article_body_depth is not None
        ):
            container_data_title = clean_text(attrs.get("data-title"))
            container_kind = abstract_label_kind(container_data_title)
            enclosing_label = self._section_labels[-1] if self._section_labels else None
            title_state = None
            raw_data_title = None
            if container_data_title:
                title_state = container_kind or "other"
                if title_state == "other":
                    title_state = "non_abstract"
                raw_data_title = container_data_title
            elif enclosing_label:
                title_state = enclosing_label.get("kind")
                if title_state == "other":
                    title_state = "non_abstract"
                raw_data_title = enclosing_label.get("raw")
            self._publisher_abs_sections.append(
                {
                    "section_id": publisher_section_match.group(1),
                    "depth": start_depth,
                    "title_state": title_state,
                    "explicit_non_abstract": title_state == "non_abstract" or bool(self._non_abstract_section_depths),
                    "raw_data_title": raw_data_title,
                    "raw_heading": None,
                    "candidate": None,
                }
            )
        if tag in {"h1", "h2", "h3"} and self._publisher_abs_sections:
            self._publisher_heading_section = self._publisher_abs_sections[-1]
            self._publisher_heading_depth = start_depth
            self._publisher_heading_chunks = []
        if tag in {"h1", "h2", "h3"} and re.fullmatch(r"Abs\d+", attrs.get("id") or "", re.I):
            self._abstract_heading_id = str(attrs["id"])
            self._abstract_heading_depth = start_depth
            self._abstract_heading_chunks = []
        if tag == "div" and re.fullmatch(r"Abs\d+-content", content_id, re.I):
            section_id = content_id[: -len("-content")]
            publisher_section = next(
                (
                    item
                    for item in reversed(self._publisher_abs_sections)
                    if str(item.get("section_id", "")).casefold() == section_id.casefold()
                ),
                None,
            )
            explicit_basis = None
            if section_id in self._abstract_heading_ids:
                explicit_basis = "explicit Abstract heading"
            elif (
                publisher_section
                and publisher_section.get("title_state") == "abstract_typo"
                and not publisher_section.get("explicit_non_abstract")
                and not self._non_abstract_section_depths
            ):
                typo_source = "heading" if publisher_section.get("raw_heading") else "data-title"
                explicit_basis = f"publisher abstract container with observed typo {typo_source}"
            elif self._abstract_section_depths:
                explicit_basis = "explicit Abstract data-title"
            elif publisher_section and publisher_section.get("title_state") == "abstract":
                explicit_basis = "explicit Abstract data-title"
            publisher_basis = (
                publisher_section is not None
                and self._article_body_depth is not None
                and not publisher_section.get("explicit_non_abstract")
                and not self._non_abstract_section_depths
            )
            if explicit_basis or publisher_basis:
                self._abstract_current = {
                    "section_id": section_id,
                    "locator": f"#{content_id}",
                    "chunks": [],
                    "source_basis": explicit_basis or "publisher abstract container without heading",
                    "raw_heading": publisher_section.get("raw_heading") if publisher_section else None,
                    "raw_data_title": publisher_section.get("raw_data_title") if publisher_section else None,
                    "publisher_section": publisher_section,
                }
                if publisher_section is not None and not explicit_basis:
                    publisher_section["candidate"] = self._abstract_current
                self._abstract_content_depth = start_depth
        if attrs.get("data-test") == "article-category" and self.category is None:
            self._category_depth = start_depth
            self._category_chunks = []
        if tag not in VOID_TAGS:
            self._depth += 1

    def handle_data(self, data: str) -> None:
        if self._script_depth:
            self._script_buf.append(data)
        if self._abstract_current is not None:
            self._abstract_current["chunks"].append(data)
        if self._publisher_heading_section is not None:
            self._publisher_heading_chunks.append(data)
        if self._abstract_heading_id is not None:
            self._abstract_heading_chunks.append(data)
        if self._category_depth:
            self._category_chunks.append(data)
        if self._identifier_first_item_depth:
            self._identifier_first_item_chunks.append(data)
        if self._published_item_depth:
            self._published_item_chunks.append(data)
        if self._author_link_depth:
            self._author_link_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._script_depth:
            self._script_depth -= 1
            if self._script_depth == 0:
                if self._script_type == "application/ld+json":
                    try:
                        self.jsonld.append(json.loads("".join(self._script_buf)))
                    except json.JSONDecodeError:
                        pass
                self._script_type = None
                self._script_buf = []
        if self._abstract_current is not None and tag == "div" and self._depth == self._abstract_content_depth:
            candidate = self._abstract_current
            candidate["text"] = clean_text(" ".join(candidate["chunks"]))
            self.abstract_candidates.append(candidate)
            if len(self.abstract_candidates) == 1:
                self.abstract_chunks = list(candidate["chunks"])
            self._abstract_current = None
            self._abstract_content_depth = 0
        if (
            self._publisher_heading_section is not None
            and tag in {"h1", "h2", "h3"}
            and self._depth == self._publisher_heading_depth
        ):
            heading_text = clean_text(" ".join(self._publisher_heading_chunks)) or ""
            heading_kind = abstract_label_kind(heading_text)
            if heading_kind == "abstract" or (
                heading_kind == "abstract_typo"
                and not self._publisher_heading_section.get("explicit_non_abstract")
                and not self._non_abstract_section_depths
            ):
                self._publisher_heading_section["title_state"] = heading_kind
                self._publisher_heading_section["raw_heading"] = heading_text
            elif heading_text:
                self._publisher_heading_section["title_state"] = "non_abstract"
                self._publisher_heading_section["explicit_non_abstract"] = True
                prior_candidate = self._publisher_heading_section.get("candidate")
                if prior_candidate in self.abstract_candidates:
                    self.abstract_candidates.remove(prior_candidate)
            self._publisher_heading_section = None
            self._publisher_heading_depth = 0
            self._publisher_heading_chunks = []
        if self._abstract_heading_id is not None and tag in {"h1", "h2", "h3"} and self._depth == self._abstract_heading_depth:
            heading_text = clean_text(" ".join(self._abstract_heading_chunks)) or ""
            if abstract_label_kind(heading_text) == "abstract":
                self._abstract_heading_ids.add(self._abstract_heading_id)
            self._abstract_heading_id = None
            self._abstract_heading_depth = 0
            self._abstract_heading_chunks = []
        if self._category_depth and self._depth == self._category_depth:
            self.category = clean_text(" ".join(self._category_chunks))
            self._category_depth = 0
            self._category_chunks = []
        if self._identifier_first_item_depth and tag == "li" and self._depth == self._identifier_first_item_depth:
            if self.category is None:
                self.category = clean_text(" ".join(self._identifier_first_item_chunks))
            self._identifier_first_item_depth = 0
            self._identifier_first_item_chunks = []
        if self._published_item_depth and tag == "li" and self._depth == self._published_item_depth:
            label = clean_text(" ".join(self._published_item_chunks))
            if label and label.casefold().startswith("published:"):
                observed = full_calendar_date(self._published_item_datetime)
                if observed and self.publisher_publication_date is None:
                    self.publisher_publication_date = observed
                    self.publisher_publication_date_locator = (
                        "ul.c-article-identifiers li whose text starts Published: > time[datetime]"
                    )
            self._published_item_depth = 0
            self._published_item_chunks = []
            self._published_item_datetime = None
        if self._author_link_depth and tag == "a" and self._depth == self._author_link_depth:
            author = clean_text(" ".join(self._author_link_chunks))
            if author:
                self.publisher_authors.append(author)
            self._author_link_depth = 0
            self._author_link_chunks = []
        if self._identifier_depth and tag == "ul" and self._depth == self._identifier_depth:
            self._identifier_depth = 0
        if self._authors_list_depth and tag == "ul" and self._depth == self._authors_list_depth:
            self._authors_list_depth = 0
        if tag == "section" and self._abstract_section_depths and self._depth == self._abstract_section_depths[-1]:
            self._abstract_section_depths.pop()
        if (
            tag == "section"
            and self._section_labels
            and self._section_labels[-1].get("depth") == self._depth
        ):
            self._section_labels.pop()
        if tag == "section" and self._non_abstract_section_depths and self._depth == self._non_abstract_section_depths[-1]:
            self._non_abstract_section_depths.pop()
        if tag == "div" and self._publisher_abs_sections and self._depth == self._publisher_abs_sections[-1]["depth"]:
            self._publisher_abs_sections.pop()
        if tag == "div" and self._article_body_depth is not None and self._depth == self._article_body_depth:
            self._article_body_depth = None
        if tag not in VOID_TAGS:
            self._depth = max(0, self._depth - 1)


class RateLimitedFetcher:
    def __init__(
        self,
        state_path: Path,
        interval: float = REQUEST_INTERVAL_SECONDS,
        user_agent: str = "Mozilla/5.0 (compatible; LiteratureDB/2026.10; +https://www.nature.com)",
    ) -> None:
        self.state_path = state_path
        self.interval = interval
        self.user_agent = user_agent
        state = read_json(state_path, {})
        self.last_request_epoch = float(state.get("last_request_epoch", 0.0))

    def _checkpoint(self) -> None:
        state = read_json(self.state_path, {})
        state["last_request_epoch"] = self.last_request_epoch
        atomic_json(self.state_path, state)

    def get(self, url: str, purpose: str, cache_path: Path | None = None) -> tuple[str | None, dict[str, Any]]:
        if cache_path and cache_path.exists():
            cached = read_cached_html(cache_path)
            reason = challenge_reason(cached)
            if reason:
                # Do not let an older pre-hard-stop cache turn a challenge into
                # a seemingly valid source page.
                cache_path.unlink(missing_ok=True)
                return None, {
                    "attempts": 0,
                    "errors": [f"challenge_page:{reason}"],
                    "purpose": purpose,
                    "access_blocked": True,
                    "challenge": reason,
                    "cache": "discarded_challenge",
                }
            return cached, {"attempts": 0, "cache": "hit", "purpose": purpose}
        delay = self.interval - (time.time() - self.last_request_epoch)
        if delay > 0:
            time.sleep(delay)
        attempts = 0
        errors: list[str] = []
        while True:
            attempts += 1
            self.last_request_epoch = time.time()
            self._checkpoint()
            with tempfile.TemporaryDirectory(prefix="portfolio-fetch-") as work:
                header_path = Path(work) / "headers.txt"
                body_path = Path(work) / "body.html"
                proc = subprocess.run(
                    [
                        "curl",
                        "--disable",
                        "--http1.1",
                        "-L",
                        "--max-time",
                        "60",
                        "--silent",
                        "--show-error",
                        "-A",
                        self.user_agent,
                        "-D",
                        str(header_path),
                        "-o",
                        str(body_path),
                        "-w",
                        "%{http_code}",
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                try:
                    status = int((proc.stdout or "")[-3:])
                except ValueError:
                    status = 0
                response_headers = header_path.read_text(encoding="utf-8", errors="replace") if header_path.exists() else ""
                retry_after = None
                for line in response_headers.splitlines():
                    if line.lower().startswith("retry-after:"):
                        retry_after = line.split(":", 1)[1].strip()
                if status in {403, 429}:
                    return None, {
                        "attempts": attempts,
                        "errors": errors + [clean_text(proc.stderr) or f"http_{status}"],
                        "purpose": purpose,
                        "status": status,
                        "retry_after": retry_after,
                        "access_blocked": True,
                    }
                if proc.returncode == 0 and 200 <= status < 300 and body_path.exists():
                    body = body_path.read_text(encoding="utf-8", errors="replace")
                    reason = challenge_reason(body)
                    if reason:
                        return None, {
                            "attempts": attempts,
                            "errors": errors + [f"challenge_page:{reason}"],
                            "purpose": purpose,
                            "status": status,
                            "access_blocked": True,
                            "challenge": reason,
                            "cache": "not_cached_challenge",
                        }
                    if cache_path:
                        cache_html(cache_path, body)
                    return body, {"attempts": attempts, "errors": errors, "status": status, "cache": "miss", "purpose": purpose}
                errors.append(clean_text(proc.stderr) or f"curl_exit_{proc.returncode}_http_{status}")
                # Retry only transient transport/5xx responses.  Other status
                # codes are a source failure and are checkpointed immediately.
                if not (proc.returncode != 0 or status >= 500):
                    return None, {"attempts": attempts, "errors": errors, "purpose": purpose, "status": status}
                if attempts > len(RETRY_DELAYS):
                    return None, {"attempts": attempts, "errors": errors, "purpose": purpose, "status": status}
            time.sleep(RETRY_DELAYS[attempts - 1])


def first(meta: dict[str, list[str]], key: str) -> str | None:
    values = meta.get(key) or []
    return values[0] if values else None


def jsonld_article_authors(items: Iterable[Any]) -> list[str]:
    """Read only the author list attached to a ScholarlyArticle mainEntity."""
    for item in items:
        candidates = item.get("mainEntity") if isinstance(item, dict) else None
        candidates = candidates if isinstance(candidates, list) else [candidates]
        for entity in candidates:
            if not isinstance(entity, dict):
                continue
            types = entity.get("@type")
            type_values = types if isinstance(types, list) else [types]
            if not (entity.get("author") and ("ScholarlyArticle" in type_values or entity.get("@type") == "ScholarlyArticle")):
                continue
            author_items = entity["author"] if isinstance(entity["author"], list) else [entity["author"]]
            names: list[str] = []
            for author in author_items:
                if isinstance(author, dict) and author.get("name"):
                    names.append(str(author["name"]).strip())
                elif isinstance(author, str):
                    names.append(author.strip())
            if names:
                return names
    return []


def parse_listing(
    html_text: str,
    source_url: str,
    year: int,
    page_number: int,
    config: VenueConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parser = ListingParser()
    parser.feed(html_text)
    next_links = [item for item in parser.pagination if (item.get("text") or "").lower() == "next page"]
    next_url = None
    if next_links:
        next_url = official_url(
            urljoin(source_url, str(next_links[-1].get("href"))),
            base_url=config.base_url,
            journal_path=config.journal_path,
        )
    page_evidence = {
        "source_url": source_url,
        "observed_at": now_utc(),
        "year_filter": year,
        "page_number": page_number,
        "card_count": len(parser.cards),
        "selected_facets": parser.selected_facets,
        "pagination": parser.pagination,
        "next_url": next_url,
        "next_present": bool(next_url),
        "html_sha256": hashlib.sha256(html_text.encode("utf-8")).hexdigest(),
    }
    cards: list[dict[str, Any]] = []
    for position, raw in enumerate(parser.cards, start=1):
        path = raw.get("path")
        if not path:
            continue
        document_type = clean_text(raw.get("document_type"))
        included = document_type in config.allowed_types
        cards.append(
            {
                "venue_id": config.venue_id,
                "venue": config.venue,
                "venue_year": year,
                "source_page_url": source_url,
                "source_page_kind": "mixed_year_listing",
                "page_number": page_number,
                "listing_position": position,
                "observed_at": page_evidence["observed_at"],
                "source_item_id": f"{config.venue_id}:{path}",
                "stable_article_path": path,
                "landing_url": canonical_article_url(path),
                "title_listing": raw.get("title"),
                "summary_listing": raw.get("summary"),
                "authors_listing": raw.get("authors_listing") or [],
                "listed_document_type": document_type,
                "listing_date": raw.get("date"),
                "open_access_listing": bool(raw.get("open_access")),
                "include_candidate": included,
                "exclusion_reason_code": None if included else "type_not_in_baseline_allowlist",
            }
        )
    return cards, [page_evidence]


def _sample_validation_error(config: VenueConfig, cards: list[dict[str, Any]], evidence: dict[str, Any]) -> str | None:
    if not cards or int(evidence.get("card_count", 0)) <= 0:
        return "sample_listing_empty"
    facets = evidence.get("selected_facets") or []
    if not any(re.search(rf"(?<!\d){config.current_year}(?!\d)", str(facet)) for facet in facets):
        return "sample_current_year_facet_missing"
    source_years = parse_qs(urlparse(str(evidence.get("source_url") or "")).query).get("year") or []
    if source_years != [str(config.current_year)]:
        return "sample_source_year_filter_missing_or_mismatched"
    next_url = evidence.get("next_url")
    if evidence.get("next_present"):
        if not isinstance(next_url, str) or not next_url:
            return "sample_next_link_missing_url"
        next_years = parse_qs(urlparse(next_url).query).get("year") or []
        if next_years != [str(config.current_year)]:
            return "sample_next_year_filter_mismatch"
    elif next_url:
        return "sample_next_presence_mismatch"
    return None


def normalize_document_type(value: str | None) -> str | None:
    """Normalize whitespace while preserving the publisher's type vocabulary.

    Nature's ``OriginalPaper`` and ``ReviewPaper`` values are schema-level
    classes.  They must remain observable as raw/normalized values instead of
    being rewritten to a visible listing label such as ``Article`` or
    ``Review Article``; compatibility is decided below with the visible
    category and listing evidence.
    """
    return clean_text(value)


def _compact_document_type(value: str | None) -> str:
    return (value or "").casefold().replace("_", "").replace("-", "").replace(" ", "")


def _is_broad_document_type(value: str | None, visible_type: str | None = None) -> bool:
    """Whether a metadata value is a schema class rather than a subtype.

    ``OriginalPaper``/``ReviewPaper`` are broad Nature schema values.  A
    citation ``Article`` value is also generic when a more specific visible
    category is selected.  Treating those as compatible preserves the
    header/listing agreement while still exposing concrete source conflicts.
    """
    compact = _compact_document_type(value)
    if compact in {"originalpaper", "reviewpaper", "researchpaper"}:
        return True
    if compact == "article":
        return visible_type is None or _compact_document_type(visible_type) != "article"
    return False


def compatible_document_types(
    detail_type: str | None,
    listed_type: str | None,
    allowed_types: frozenset[str] | set[str],
) -> tuple[bool, dict[str, Any] | None]:
    """Compare the two official Nature type labels without erasing either raw value."""
    if detail_type not in allowed_types:
        return False, None
    if detail_type == listed_type:
        return True, None
    if {detail_type, listed_type} == {"Review", "Review Article"}:
        return True, {
            "code": "review_review_article_equivalence",
            "detail_type": detail_type,
            "listed_type": listed_type,
            "decision": "compatible",
            "basis": "Official Nature detail and listing use the equivalent Review and Review Article labels.",
        }
    return False, None


def candidate_identity_progress(
    candidate_paths: set[str],
    completed_paths: set[str],
) -> tuple[set[str], set[str]]:
    """Return raw candidate coverage and its identity difference.

    ``completed_paths`` may include approved overlay rows (such as the
    Registered Report) outside the immutable raw candidate set.
    """
    covered = completed_paths & candidate_paths
    return covered, candidate_paths - covered


def parse_detail(
    html_text: str,
    source_url: str,
    listing: dict[str, Any],
    fetch_info: dict[str, Any],
    config: VenueConfig,
) -> dict[str, Any]:
    parser = DetailParser()
    parser.feed(html_text)
    meta = parser.meta
    citation_authors = [clean_text(value) for value in meta.get("citation_author", [])]
    citation_authors = [value for value in citation_authors if value]
    ld_authors = jsonld_article_authors(parser.jsonld)
    # Nature's citation_author sequence is the publisher's ordered author
    # source. JSON-LD is a complete-name fallback when citation_author is absent.
    publisher_authors = [clean_text(value) for value in parser.publisher_authors]
    publisher_authors = [value for value in publisher_authors if value]
    authors = citation_authors or ld_authors or publisher_authors
    if citation_authors:
        authors_source = "citation_author meta"
        authors_locator = "meta[name=citation_author]"
    elif ld_authors:
        authors_source = "JSON-LD mainEntity.author"
        authors_locator = "JSON-LD mainEntity.author"
    elif publisher_authors:
        authors_source = "publisher authors-list author-name"
        authors_locator = "ul[data-test=authors-list] a[data-test=author-name]"
    else:
        authors_source = "none observed"
        authors_locator = "meta[name=citation_author] or JSON-LD mainEntity.author or ul[data-test=authors-list] a[data-test=author-name]"
    doi_reported = first(meta, "citation_doi") or first(meta, "DOI")
    doi = normalize_doi(doi_reported)
    pdf_reported = first(meta, "citation_pdf_url")
    pdf_url = official_url(pdf_reported or "", allow_pdf=True, base_url=config.base_url) if pdf_reported else None
    raw_type_values = {
        key: first(meta, key)
        for key in ("citation_article_type", "dc.type", "prism.section")
        if first(meta, key) is not None
    }
    normalized_type_values = {
        key: normalize_document_type(value)
        for key, value in raw_type_values.items()
        if normalize_document_type(value) is not None
    }
    category_type = normalize_document_type(parser.category)
    visible_type = category_type
    type_selection = {
        "value": visible_type,
        "source": "visible article category" if visible_type else None,
        "locator": "ul[data-test=article-identifier] li[data-test=article-category]:first-of-type" if visible_type else None,
    }
    if visible_type is None:
        for key in ("citation_article_type", "dc.type", "prism.section"):
            candidate = normalized_type_values.get(key)
            if candidate:
                visible_type = candidate
                type_selection = {
                    "value": candidate,
                    "source": f"meta[name={key}]",
                    "locator": f"meta[name={key}]",
                }
                break
    article_type = visible_type
    type_conflicts: list[dict[str, Any]] = []
    review_reasons: list[str] = []
    if category_type and normalized_type_values:
        for key, candidate in normalized_type_values.items():
            if candidate == visible_type:
                continue
            conflict = {
                "source": key,
                "raw": raw_type_values[key],
                "normalized": candidate,
                "selected": visible_type,
            }
            type_conflicts.append(conflict)
            if _is_broad_document_type(candidate, visible_type):
                if _compact_document_type(candidate) == "article":
                    conflict["resolution"] = "generic metadata Article compatible with visible category and listing"
                else:
                    conflict["resolution"] = "broad schema value compatible with visible research category and listing"
            else:
                conflict["resolution"] = "substantive conflict requires review"
                review_reasons.append("document_type_source_conflict")
    elif len(set(normalized_type_values.values())) > 1:
        concrete_values = {
            value for value in normalized_type_values.values()
            if not _is_broad_document_type(value)
        }
        type_conflicts = [
            {
                "source": key,
                "raw": raw_type_values[key],
                "normalized": value,
                "selected": visible_type,
                "resolution": (
                    "broad schema values compatible with the selected metadata"
                    if len(concrete_values) <= 1
                    else "substantive conflict requires review"
                ),
            }
            for key, value in normalized_type_values.items()
            if value != visible_type
        ]
        if len(concrete_values) > 1:
            review_reasons.append("document_type_source_conflict")
    publication_date = None
    publication_date_source = None
    publication_date_locator = "meta[name=citation_online_date]/meta[name=prism.publicationDate]/meta[name=dc.date]"
    publication_date_source_basis = None
    for key in ("citation_online_date", "prism.publicationDate", "dc.date"):
        reported = first(meta, key)
        if reported:
            publication_date = reported.replace("/", "-")
            publication_date_source = f"meta[name={key}]"
            publication_date_locator = f"meta[name={key}]"
            publication_date_source_basis = "exact publisher date metadata"
            break
    if publication_date is None and parser.publisher_publication_date:
        publication_date = parser.publisher_publication_date
        publication_date_source = "visible Published time"
        publication_date_locator = parser.publisher_publication_date_locator or (
            "ul.c-article-identifiers li whose text starts Published: > time[datetime]"
        )
        publication_date_source_basis = "publisher visible Published label with time[datetime]"
    # Preserve month-only publisher dates in citation_publication_date; adding
    # a day would fabricate data.  Only an exact meta date or full Published
    # time is accepted for this repair.
    abstract_candidates = parser.abstract_candidates
    abstract_review_required = len(abstract_candidates) > 1
    if abstract_review_required:
        abstract = None
        abstract_source = None
        abstract_source_basis = None
        review_reasons.append("multiple_visible_abstract_sections")
    elif abstract_candidates:
        abstract = clean_text(abstract_candidates[0].get("text"))
        abstract_source = abstract_candidates[0].get("locator") if abstract else None
        abstract_source_basis = abstract_candidates[0].get("source_basis") if abstract else None
    else:
        abstract = None
        abstract_source = None
        abstract_source_basis = None
    venue_identity = (first(meta, "prism.publicationName") or "").strip() == config.venue
    issue_year = None
    publication_issue = first(meta, "citation_publication_date")
    if publication_issue and re.match(r"^\d{4}", publication_issue):
        issue_year = int(publication_issue[:4])
    issue = {
        "volume": first(meta, "citation_volume"),
        "issue": first(meta, "citation_issue"),
        "pages": None,
        "year": issue_year,
    }
    first_page = first(meta, "citation_firstpage")
    last_page = first(meta, "citation_lastpage")
    if first_page or last_page:
        issue["pages"] = f"{first_page or ''}–{last_page or ''}".strip("–")
    if not any(issue.values()):
        issue = None
    missing_fields: dict[str, str] = {}
    if not authors:
        missing_fields["authors"] = "no_citation_jsonld_or_publisher_author"
    if abstract_review_required:
        missing_fields["abstract"] = "multiple_visible_abstract_sections"
    elif not abstract:
        missing_fields["abstract"] = "visible_abstract_section_absent"
    if not doi:
        missing_fields["doi"] = "missing_or_invalid_publisher_doi"
    if not pdf_url:
        missing_fields["pdf_url"] = "citation_pdf_url_absent_or_not_same_origin"
    if not publication_date:
        missing_fields["publication_date"] = "no_exact_date_meta_or_visible_published_time"
    if not venue_identity:
        missing_fields["journal_identity"] = "venue_identity_not_confirmed"
    detail_type_compatible = article_type in config.allowed_types
    if not detail_type_compatible:
        missing_fields["document_type_compatibility"] = "detail_type_not_in_baseline_allowlist"
    listed_type = listing.get("listed_document_type")
    type_compatible, type_compatibility_resolution = compatible_document_types(
        article_type,
        listed_type,
        config.allowed_types,
    )
    review_required = bool(review_reasons)
    abstract_locator = abstract_source or (
        ", ".join(str(item.get("locator")) for item in abstract_candidates) if abstract_review_required else "#AbsN-content"
    )
    abstract_status = "review_required" if abstract_review_required else ("observed" if abstract else "missing")
    observed = now_utc()
    normalized_identity = doi or listing["stable_article_path"]
    return {
        "venue_id": config.venue_id,
        "venue": config.venue,
        "venue_year": listing["venue_year"],
        "source_item_id": listing["source_item_id"],
        "stable_article_path": listing["stable_article_path"],
        "normalized_identity": normalized_identity,
        "landing_url": source_url,
        "source_url": source_url,
        "source_page_url": listing["source_page_url"],
        "source_page_kind": listing["source_page_kind"],
        "listing_page_number": listing["page_number"],
        "listing_position": listing["listing_position"],
        "observed_at": observed,
        "listing_observed_at": listing["observed_at"],
        "title": clean_text(first(meta, "citation_title")) or listing.get("title_listing"),
        "authors": authors,
        "authors_source": authors_source,
        "abstract": abstract,
        "abstract_source": abstract_source,
        "abstract_source_basis": abstract_source_basis,
        "abstract_candidates": [
            {
                "section_id": item.get("section_id"),
                "locator": item.get("locator"),
                "source_basis": item.get("source_basis"),
                "raw_heading": item.get("raw_heading"),
                "raw_data_title": item.get("raw_data_title"),
                "text_present": bool(item.get("text")),
            }
            for item in abstract_candidates
        ],
        "publication_date": publication_date,
        "publication_date_source": publication_date_source,
        "publication_date_source_basis": publication_date_source_basis,
        "publication_date_sources": {
            key: values[0] for key, values in meta.items() if key in {"citation_online_date", "citation_publication_date", "dc.date", "prism.publicationDate"} and values
        }
        | ({"visible_published_time": parser.publisher_publication_date} if parser.publisher_publication_date else {}),
        "document_type": article_type,
        "document_type_meta_values": raw_type_values,
        "document_type_normalized_meta_values": normalized_type_values,
        "document_type_selection": type_selection,
        "document_type_conflicts": type_conflicts,
        "listed_document_type": listing.get("listed_document_type"),
        "type_compatible": type_compatible,
        **({"type_compatibility_resolution": type_compatibility_resolution} if type_compatibility_resolution else {}),
        "review_required": review_required,
        "review_reasons": review_reasons,
        "doi": doi,
        "doi_reported": doi_reported,
        "pdf_url": pdf_url,
        "pdf_reported": pdf_reported,
        "issue_assignment": issue,
        "issue_assignment_source": "citation volume/issue/pages metadata" if issue else "none_visible",
        "open_access_listing": listing.get("open_access_listing", False),
        "venue_identity": venue_identity,
        "nature_identity": venue_identity,
        "metadata_confirmation": {
            "nature_identity": venue_identity,
            "venue_identity": venue_identity,
            "stable_title_and_path": bool(listing.get("stable_article_path")),
            "doi_or_path": bool(doi or listing.get("stable_article_path")),
            "leading_type": bool(article_type),
            "visible_date": bool(publication_date),
            "full_author_list": bool(authors),
            "full_visible_abstract": bool(abstract),
        },
        "missing_fields": missing_fields,
        "include_decision": "manual_review" if review_required else ("include" if not missing_fields else "include_with_missing_fields"),
        "inclusion_rule_id": f"{config.venue_id}-strict-allowlist-v1",
        "fetch": fetch_info,
        "field_provenance": {
            "title": {"source_url": source_url, "locator": "meta[name=citation_title]", "status": "observed"},
            "authors": {"source_url": source_url, "locator": authors_locator, "status": "observed" if authors else "missing"},
            "abstract": {"source_url": source_url, "locator": abstract_locator, "status": abstract_status, "basis": abstract_source_basis},
            "publication_date": {
                "source_url": source_url,
                "locator": publication_date_locator,
                "status": "observed" if publication_date else "missing",
                "source": publication_date_source,
                "basis": publication_date_source_basis,
            },
            "document_type": {"source_url": source_url, "locator": type_selection.get("locator") or "citation_article_type/dc.type/prism.section", "status": "observed" if article_type else "missing"},
            "doi": {"source_url": source_url, "locator": "meta[name=citation_doi]", "status": "observed_validated" if doi else "missing_or_invalid"},
            "pdf_url": {"source_url": source_url, "locator": "meta[name=citation_pdf_url]", "status": "observed_same_origin" if pdf_url else "missing"},
        },
    }


def update_state(config: VenueConfig, stage: str, **kwargs: Any) -> None:
    path = config.output_dir / "run_state.json"
    state = read_json(path, {})
    state.update({"venue_id": config.venue_id, "venue": config.venue, "stage": stage, "updated_at": now_utc()})
    state.update(kwargs)
    atomic_json(path, state)


def _year_url(config: VenueConfig, year: int) -> str:
    value = config.entry_template.replace("<year>", str(year))
    result = official_url(value, base_url=config.base_url, journal_path=config.journal_path)
    if result is None:
        raise VenueConfigurationError(f"entry template produced an unsafe URL: {value}")
    return result


def _historical_identity(item: dict[str, Any]) -> str | None:
    raw = item.get("stable_article_path") or item.get("source_native_id") or item.get("source_item_id")
    if raw is None:
        return None
    raw = str(raw).strip()
    prefix, separator, suffix = raw.partition(":")
    if separator and suffix.startswith("/") and "/" not in prefix and "\\" not in prefix:
        raw = suffix
    return stable_article_path(raw)


def _historical_seed_cards(
    config: VenueConfig,
    seen_paths: set[str],
) -> tuple[list[dict[str, Any]], list[int], dict[str, Any]]:
    """Validate a Nature identity receipt, then adapt only closed-year rows."""
    if not config.historical:
        return [], [], {}
    evidence = config.historical
    try:
        lines = evidence.seed_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise VenueConfigurationError(f"cannot read historical enumeration: {evidence.seed_path}") from exc
    raw_rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VenueConfigurationError(f"historical enumeration has invalid JSON at line {line_number}") from exc
        if not isinstance(item, dict):
            raise VenueConfigurationError(f"historical enumeration row {line_number} is not an object")
        raw_rows.append((line_number, item))
    counts: dict[int, int] = {}
    all_ids: set[str] = set()
    closed_ids: list[str] = []
    cards: list[dict[str, Any]] = []
    seed_years: set[int] = set()
    errors: list[str] = []
    for line_number, item in raw_rows:
        raw_year = item.get("year")
        if isinstance(raw_year, bool):
            year = None
        else:
            try:
                year = int(raw_year)
            except (TypeError, ValueError):
                year = None
        identity = _historical_identity(item)
        if year is None:
            errors.append(f"historical row {line_number} has invalid year")
            continue
        if year not in evidence.expected_counts:
            errors.append(f"historical row {line_number} has unexpected year {year}")
        counts[year] = counts.get(year, 0) + 1
        if not identity:
            errors.append(f"historical row {line_number} has no stable article identity")
            continue
        if identity in all_ids:
            errors.append(f"historical enumeration duplicates source identity {identity}")
        all_ids.add(identity)
        if year not in evidence.closed_years:
            continue
        closed_ids.append(identity)
        if identity in seen_paths:
            continue
        source_url = official_url(str(item.get("source_url") or item.get("landing_url") or canonical_article_url(identity)), base_url=config.base_url)
        if source_url is None:
            errors.append(f"historical row {line_number} has no allowlisted official source URL")
            continue
        document_type = clean_text(item.get("document_type"))
        included = document_type in config.allowed_types
        observed_at = item.get("historical_observed_at") or now_utc()
        provenance = item.get("historical_provenance") if isinstance(item.get("historical_provenance"), dict) else None
        card: dict[str, Any] = {
            "venue_id": config.venue_id,
            "venue": config.venue,
            "venue_year": year,
            "source_page_url": source_url,
            "source_url": source_url,
            "source_page_kind": "historical_enumeration_seed",
            "page_number": 0,
            "listing_position": line_number,
            "observed_at": observed_at,
            "source_item_id": f"{config.venue_id}:{identity}",
            "stable_article_path": identity,
            "landing_url": official_url(str(item.get("landing_url") or canonical_article_url(identity)), base_url=config.base_url) or canonical_article_url(identity),
            "title_listing": clean_text(item.get("title")),
            "summary_listing": None,
            "authors_listing": [],
            "listed_document_type": document_type,
            "listing_date": item.get("publication_date"),
            "open_access_listing": False,
            "include_candidate": included,
            "exclusion_reason_code": None if included else "type_not_in_baseline_allowlist",
            "historical_identity_seed": True,
            "historical_seed_evidence_line": line_number,
            "historical_evidence_file": item.get("evidence_file"),
            "historical_evidence_sha256": item.get("evidence_sha256"),
            "historical_metadata_complete": False,
        }
        if "historical_manual_review_required" in item:
            card["historical_manual_review_required"] = item["historical_manual_review_required"]
        if provenance is not None:
            card["historical_provenance"] = provenance
            if provenance.get("source_gap"):
                card["source_gap"] = True
        if item.get("source_gap"):
            card["source_gap"] = True
        cards.append(card)
        seed_years.add(year)
    for year, expected in evidence.expected_counts.items():
        actual = counts.get(year, 0)
        if year in evidence.closed_years and actual != expected:
            errors.append(f"historical count for {year} is {actual}, preflight says {expected}")
    closed_count = sum(counts.get(year, 0) for year in evidence.closed_years)
    if closed_count != evidence.expected_seed_count:
        errors.append(f"historical closed seed count {closed_count} does not match preflight closed count {evidence.expected_seed_count}")
    actual_hash = hashlib.sha256("\n".join(sorted(set(closed_ids))).encode("utf-8")).hexdigest()
    if actual_hash != evidence.expected_identity_sha256:
        errors.append("historical closed-year identity SHA-256 does not match preflight")
    if errors:
        raise VenueConfigurationError("; ".join(errors[:4]))
    receipt = {
        "status": "PASS",
        "preflight": str(evidence.preflight_path),
        "historical_enumeration": str(evidence.seed_path),
        "closed_years": sorted(evidence.closed_years),
        "per_year_counts": {str(year): counts.get(year, 0) for year in sorted(evidence.closed_years)},
        "preflight_per_year_counts": {str(year): evidence.expected_counts[year] for year in sorted(evidence.expected_counts)},
        "closed_year_identity_set_sha256": actual_hash,
        "seed_count": len(raw_rows),
        "source_receipt_seed_count": evidence.receipt_seed_count,
        "metadata_complete": False,
        "current_year_refresh_required": True,
        "source_gap_rows": sum(1 for _, item in raw_rows if item.get("historical_manual_review_required") or (isinstance(item.get("historical_provenance"), dict) and item["historical_provenance"].get("source_gap"))),
    }
    return cards, sorted(seed_years), receipt


def fetch_listing_stage(fetcher: RateLimitedFetcher, config: VenueConfig, sample: bool = False) -> dict[str, Any]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    enum_dir = config.output_dir / ("discovery" if sample else "enumeration")
    enum_dir.mkdir(parents=True, exist_ok=True)
    cards_path = enum_dir / ("sample_listing.jsonl" if sample else "listing_cards.jsonl")
    pages_path = enum_dir / ("sample_page_evidence.jsonl" if sample else "page_evidence.jsonl")
    exclusions_path = enum_dir / ("sample_exclusions.jsonl" if sample else "exclusions.jsonl")
    state_name = "sample_listing" if sample else "enumerate"
    years = [config.current_year] if sample else ([config.current_year] if config.historical else config.years)
    update_state(config, state_name, status="running", failed_url=None, years=config.years if not sample else years, completed_pages=0)
    seen_paths: set[str] = set()
    if not sample and cards_path.exists():
        for line in cards_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("stable_article_path"):
                seen_paths.add(item["stable_article_path"])
    historical_cards, historical_years, historical_receipt = _historical_seed_cards(config, seen_paths) if not sample else ([], [], {})
    for card in historical_cards:
        append_jsonl(cards_path, card)
        seen_paths.add(card["stable_article_path"])
        if not card["include_candidate"]:
            append_jsonl(exclusions_path, {**card, "excluded_reason_code": card["exclusion_reason_code"], "excluded_rule_version": f"{config.venue_id}-strict-allowlist-v1"})
    if historical_receipt:
        historical_receipt["historical_years_seeded"] = historical_years
        atomic_json(config.output_dir / "enumeration" / "historical_seed_receipt.json", historical_receipt)
    page_count = 0
    type_facets: dict[str, int] = {}
    for year in years:
        url = _year_url(config, year)
        page_number = 1
        year_seen: set[str] = set()
        while url:
            cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
            cache_path = config.output_dir / "cache" / "listings" / f"{cache_key}.html.gz"
            html_text, fetch_info = fetcher.get(url, f"listing_{year}_{page_number}", cache_path)
            if html_text is None:
                append_jsonl(pages_path, {"source_url": url, "year_filter": year, "page_number": page_number, "status": "fetch_failed", "fetch": fetch_info, "observed_at": now_utc()})
                update_state(config, state_name, status="blocked", failed_url=url, completed_pages=page_count)
                return {"status": "blocked", "completed_pages": page_count, "failed_url": url, "fetch": fetch_info}
            cards, evidence_list = parse_listing(html_text, url, year, page_number, config)
            evidence = evidence_list[0]
            evidence["fetch"] = fetch_info
            if sample:
                validation_error = _sample_validation_error(config, cards, evidence)
                if validation_error:
                    evidence["status"] = "sample_validation_failed"
                    evidence["validation_error"] = validation_error
                    append_jsonl(pages_path, evidence)
                    update_state(config, state_name, status="blocked", failed_url=url, completed_pages=page_count, validation_error=validation_error)
                    return {"status": "blocked", "completed_pages": page_count, "failed_url": url, "validation_error": validation_error}
            append_jsonl(pages_path, evidence)
            page_count += 1
            for card in cards:
                document_type = card.get("listed_document_type") or ""
                type_facets[document_type] = type_facets.get(document_type, 0) + 1
                path = card["stable_article_path"]
                if path in year_seen:
                    card["duplicate_within_year"] = True
                year_seen.add(path)
                if path not in seen_paths:
                    append_jsonl(cards_path, card)
                    seen_paths.add(path)
                    if not card["include_candidate"]:
                        append_jsonl(exclusions_path, {**card, "excluded_reason_code": card["exclusion_reason_code"], "excluded_rule_version": f"{config.venue_id}-strict-allowlist-v1"})
            update_state(config, state_name, completed_pages=page_count, last_year=year, last_page=page_number, unique_paths=len(seen_paths))
            if sample:
                return {"status": "complete", "completed_pages": page_count, "unique_paths": len(seen_paths), "sample_url": evidence["source_url"], "observed_type_facets": type_facets}
            url = evidence.get("next_url")
            page_number += 1
    update_state(config, "enumerated", status="complete", failed_url=None, completed_pages=page_count, unique_paths=len(seen_paths), current_waterline_year=config.current_year, historical_seed_count=len(historical_cards))
    atomic_json(
        config.output_dir / "enumeration" / "enumeration_manifest.json",
        {
            "status": "complete",
            "completed_pages": page_count,
            "unique_paths": len(seen_paths),
            "years": config.years,
            "live_years": years,
            "historical_seed_path": str(config.historical.seed_path) if config.historical else None,
            "historical_seed_receipt": str(config.output_dir / "enumeration" / "historical_seed_receipt.json") if config.historical else None,
            "historical_seed_count": len(historical_cards),
            "allowlisted_types": sorted(config.allowed_types),
            "observed_at": now_utc(),
        },
    )
    return {"status": "complete", "completed_pages": page_count, "unique_paths": len(seen_paths), "historical_seed_count": len(historical_cards)}


def load_candidates(config: VenueConfig) -> list[dict[str, Any]]:
    path = config.output_dir / "enumeration" / "listing_cards.jsonl"
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not path.exists():
        return items
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        p = item.get("stable_article_path")
        if item.get("include_candidate") and p and p not in seen:
            items.append(item)
            seen.add(p)
    return items


TRANSIENT_DETAIL_ERROR_FRAGMENTS = (
    "curl: (18)",
    "curl: (28)",
    "curl: (35)",
    "curl: (52)",
)
MAX_CONSECUTIVE_DETAIL_RETRYABLE_FAILURES = 3


def is_retryable_detail_fetch(fetch_info: dict[str, Any]) -> bool:
    """Return whether a detail fetch may be deferred without weakening gates."""
    if fetch_info.get("access_blocked") or fetch_info.get("challenge"):
        return False
    try:
        status = int(fetch_info.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    if 400 <= status < 500:
        return False
    if status >= 500:
        return True
    errors = fetch_info.get("errors") or []
    return any(
        fragment in str(error)
        for error in errors
        for fragment in TRANSIENT_DETAIL_ERROR_FRAGMENTS
    )


def _append_detail_retry_event(
    path: Path,
    listing: dict[str, Any],
    source_url: str,
    fetch_info: dict[str, Any],
    retry_round: int,
    event: str,
) -> None:
    append_jsonl(
        path,
        {
            "event": event,
            "stable_article_path": listing["stable_article_path"],
            "source_url": source_url,
            "listing": listing,
            "fetch": fetch_info,
            "retry_round": retry_round,
            "observed_at": now_utc(),
        },
    )


def _load_unresolved_detail_retry_paths(path: Path) -> set[str]:
    unresolved: set[str] = set()
    if not path.exists():
        return unresolved
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        native = event.get("stable_article_path")
        if not native:
            continue
        if event.get("event") == "pending":
            unresolved.add(native)
        elif event.get("event") == "resolved":
            unresolved.discard(native)
    return unresolved


def fetch_details(fetcher: RateLimitedFetcher, config: VenueConfig, limit: int | None = None) -> dict[str, Any]:
    staging = config.output_dir / "staging"
    prov = config.output_dir / "provenance"
    staging.mkdir(parents=True, exist_ok=True)
    prov.mkdir(parents=True, exist_ok=True)
    metadata_path = staging / "metadata.jsonl"
    provenance_path = prov / "field_provenance.jsonl"
    review_path = staging / "manual_review.jsonl"
    errors_path = staging / "detail_errors.jsonl"
    retry_events_path = staging / "detail_pending_retry.jsonl"
    candidates = load_candidates(config)
    completed: set[str] = set()
    if metadata_path.exists():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if item.get("stable_article_path"):
                    completed.add(item["stable_article_path"])
            except json.JSONDecodeError:
                continue
    candidate_paths = {item["stable_article_path"] for item in candidates}
    completed_candidate_paths, remaining_candidate_paths = candidate_identity_progress(candidate_paths, completed)
    pending = [item for item in candidates if item["stable_article_path"] not in completed_candidate_paths]
    if limit is not None:
        pending = pending[:limit]
    unresolved_retry_paths = _load_unresolved_detail_retry_paths(retry_events_path)
    # A completed row from a prior invocation is authoritative for this run.
    # The next successful source touch below still writes a resolved event.
    unresolved_retry_paths.difference_update(completed)
    update_state(
        config,
        "details",
        status="running",
        failed_url=None,
        candidates=len(candidate_paths),
        completed_details=len(completed_candidate_paths),
        metadata_records=len(completed),
        remaining_details=len(remaining_candidate_paths),
        pending_details=len(pending),
    )

    def save_detail(listing: dict[str, Any], html_text: str, source_url: str, fetch_info: dict[str, Any]) -> None:
        path = listing["stable_article_path"]
        record = parse_detail(html_text, source_url, listing, fetch_info, config)
        if record.get("review_required") or not record["type_compatible"] or not record["venue_identity"]:
            append_jsonl(review_path, record)
        else:
            append_jsonl(metadata_path, record)
            append_jsonl(provenance_path, {"stable_article_path": path, "source_url": source_url, "observed_at": record["observed_at"], "field_provenance": record["field_provenance"], "missing_fields": record["missing_fields"]})

    pending_retry: list[tuple[dict[str, Any], dict[str, Any], int]] = []
    consecutive_retryable_failures = 0
    circuit_breaker: str | None = None
    for index, listing in enumerate(pending, start=1):
        path = listing["stable_article_path"]
        source_url = canonical_article_url(path)
        cache_key = hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]
        cache_path = config.output_dir / "cache" / "details" / f"{cache_key}.html.gz"
        html_text, fetch_info = fetcher.get(source_url, f"detail_{path}", cache_path)
        if html_text is None:
            append_jsonl(errors_path, {"stable_article_path": path, "source_url": source_url, "listing": listing, "fetch": fetch_info, "observed_at": now_utc()})
            if is_retryable_detail_fetch(fetch_info):
                _append_detail_retry_event(retry_events_path, listing, source_url, fetch_info, 0, "pending")
                pending_retry.append((listing, fetch_info, 0))
                unresolved_retry_paths.add(path)
                consecutive_retryable_failures += 1
                update_state(
                    config,
                    "details",
                    status="running",
                    failed_url=None,
                    candidates=len(candidate_paths),
                    completed_details=len(completed_candidate_paths),
                    metadata_records=len(completed),
                    remaining_details=len(candidate_paths - completed_candidate_paths),
                    last_index=index,
                    pending_retry_count=len(pending_retry),
                )
                if consecutive_retryable_failures >= MAX_CONSECUTIVE_DETAIL_RETRYABLE_FAILURES:
                    circuit_breaker = "consecutive_retryable_detail_failures"
                    break
                continue
            update_state(
                config,
                "details",
                status="blocked",
                failed_url=source_url,
                candidates=len(candidate_paths),
                completed_details=len(completed_candidate_paths),
                metadata_records=len(completed),
                remaining_details=len(candidate_paths - completed_candidate_paths),
            )
            return {
                "status": "blocked",
                "candidates": len(candidate_paths),
                "completed_details": len(completed_candidate_paths),
                "metadata_records": len(completed),
                "remaining_details": len(candidate_paths - completed_candidate_paths),
                "failed_url": source_url,
                "fetch": fetch_info,
            }
        save_detail(listing, html_text, source_url, fetch_info)
        completed.add(path)
        completed_candidate_paths.add(path)
        consecutive_retryable_failures = 0
        if path in unresolved_retry_paths:
            _append_detail_retry_event(retry_events_path, listing, source_url, fetch_info, 0, "resolved")
            unresolved_retry_paths.discard(path)
        update_state(
            config,
            "details",
            status="running",
            candidates=len(candidate_paths),
            completed_details=len(completed_candidate_paths),
            metadata_records=len(completed),
            remaining_details=len(candidate_paths - completed_candidate_paths),
            last_path=path,
            last_index=index,
        )

    # Give deferred transport failures one bounded second chance after the
    # normal queue.  A later invocation will resume any paths still pending.
    retry_round = 1 if circuit_breaker is None else 0
    for listing, first_fetch_info, _ in list(pending_retry) if circuit_breaker is None else []:
        path = listing["stable_article_path"]
        source_url = canonical_article_url(path)
        cache_key = hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]
        cache_path = config.output_dir / "cache" / "details" / f"{cache_key}.html.gz"
        html_text, fetch_info = fetcher.get(source_url, f"detail_retry_{retry_round}_{path}", cache_path)
        if html_text is None:
            append_jsonl(errors_path, {"stable_article_path": path, "source_url": source_url, "listing": listing, "fetch": fetch_info, "retry_round": retry_round, "observed_at": now_utc()})
            if is_retryable_detail_fetch(fetch_info):
                _append_detail_retry_event(retry_events_path, listing, source_url, fetch_info, retry_round, "pending")
                unresolved_retry_paths.add(path)
                consecutive_retryable_failures += 1
                if consecutive_retryable_failures >= MAX_CONSECUTIVE_DETAIL_RETRYABLE_FAILURES:
                    circuit_breaker = "consecutive_retryable_detail_failures"
                    break
                continue
            update_state(
                config,
                "details",
                status="blocked",
                failed_url=source_url,
                candidates=len(candidate_paths),
                completed_details=len(completed_candidate_paths),
                metadata_records=len(completed),
                remaining_details=len(candidate_paths - completed_candidate_paths),
                pending_retry_count=len(pending_retry),
            )
            return {
                "status": "blocked",
                "candidates": len(candidate_paths),
                "completed_details": len(completed_candidate_paths),
                "metadata_records": len(completed),
                "remaining_details": len(candidate_paths - completed_candidate_paths),
                "failed_url": source_url,
                "fetch": fetch_info,
            }
        save_detail(listing, html_text, source_url, fetch_info)
        completed.add(path)
        completed_candidate_paths.add(path)
        consecutive_retryable_failures = 0
        unresolved_retry_paths.discard(path)
        _append_detail_retry_event(retry_events_path, listing, source_url, fetch_info, retry_round, "resolved")
        update_state(
            config,
            "details",
            status="running",
            candidates=len(candidate_paths),
            completed_details=len(completed_candidate_paths),
            metadata_records=len(completed),
            remaining_details=len(candidate_paths - completed_candidate_paths),
            last_path=path,
            retry_round=retry_round,
        )

    _, remaining_candidate_paths = candidate_identity_progress(candidate_paths, completed)
    final_status = "complete" if not remaining_candidate_paths and not unresolved_retry_paths and circuit_breaker is None else "partial"
    update_state(
        config,
        "details",
        status=final_status,
        failed_url=None,
        candidates=len(candidate_paths),
        completed_details=len(completed_candidate_paths),
        metadata_records=len(completed),
        remaining_details=len(remaining_candidate_paths),
        pending_retry_count=len(unresolved_retry_paths),
        retry_round=retry_round,
        circuit_breaker=circuit_breaker,
    )
    atomic_json(
        config.output_dir / "staging" / "detail_manifest.json",
        {
            "status": final_status,
            "candidates": len(candidate_paths),
            "candidate_identity_count": len(candidate_paths),
            "completed_details": len(completed_candidate_paths),
            "metadata_records": len(completed),
            "remaining_details": len(remaining_candidate_paths),
            "pending_retry_count": len(unresolved_retry_paths),
            "pending_retry_paths": sorted(unresolved_retry_paths),
            "circuit_breaker": circuit_breaker,
            "metadata_path": str(metadata_path),
            "review_path": str(review_path),
            "errors_path": str(errors_path),
            "retry_events_path": str(retry_events_path),
            "observed_at": now_utc(),
        },
    )
    return {
        "status": final_status,
        "candidates": len(candidate_paths),
        "candidate_identity_count": len(candidate_paths),
        "completed_details": len(completed_candidate_paths),
        "metadata_records": len(completed),
        "remaining_details": len(remaining_candidate_paths),
        "pending_retry_count": len(unresolved_retry_paths),
        "pending_retry_paths": sorted(unresolved_retry_paths),
    }


def write_discovery_evidence(config: VenueConfig, sample_result: dict[str, Any]) -> None:
    """Persist only evidence observed for this venue's own sample.

    Existing evidence is deliberately preserved.  In particular, the default
    discovery receipts are not replaced by a sample-page count, and no NCS,
    Methods, or Nature RSS/facet data is synthesized.
    """
    discovery = config.output_dir / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)
    entry_path = discovery / "entry_evidence.json"
    if not entry_path.exists():
        entry: dict[str, Any] = {
            "venue_id": config.venue_id,
            "venue": config.venue,
            "observed_at": now_utc(),
            "official_origin": config.base_url,
            "browse_entry_url": config.browse_entry_url,
            "mixed_year_entry_template": config.entry_template,
            "allowed_domains": [urlparse(config.base_url).netloc],
            "scope_years": config.years,
            "sample_verified": True,
            "method": "runtime supplied official public HTML listing sample; no PDF download",
        }
        if config.rss_url:
            entry["rss_link_observed"] = config.rss_url
        atomic_json(entry_path, entry)
    type_path = discovery / "type_allowlist_evidence.json"
    if not type_path.exists():
        observed_facets = sample_result.get("observed_type_facets") or {}
        atomic_json(
            type_path,
            {
                "venue_id": config.venue_id,
                "venue": config.venue,
                "observed_at": now_utc(),
                "source_url": sample_result.get("sample_url") or config.browse_entry_url,
                "observed_type_facets": observed_facets,
                "allowlisted_observed_types": sorted(set(observed_facets).intersection(config.allowed_types)),
                "excluded_observed_types": sorted(config.excluded_types),
                "scope": "sample_page_only",
                "note": "Sample-page observations only; these are not full venue totals.",
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["sample", "enumerate", "details"])
    parser.add_argument("--venue", choices=SUPPORTED_VENUES, default="nature-machine-intelligence")
    parser.add_argument("--limit", type=int, default=None, help="detail records for a bounded pilot")
    parser.add_argument("--interval", type=float, default=REQUEST_INTERVAL_SECONDS)
    parser.add_argument("--entry-url", default=None, help="same-origin /articles?year=YYYY URL, required for a new venue sample")
    parser.add_argument("--registry-root", type=Path, default=DEFAULT_REGISTRY_ROOT)
    parser.add_argument("--evidence-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--historical-preflight", type=Path, default=None)
    parser.add_argument("--current-year", type=int, default=dt.datetime.now(dt.timezone.utc).year)
    args = parser.parse_args()
    try:
        if args.entry_url and args.mode != "sample":
            raise VenueConfigurationError("--entry-url is accepted only by sample")
        config = build_venue_config(
            args.venue,
            current_year=args.current_year,
            output_root=args.output_root,
            registry_root=args.registry_root,
            evidence_root=args.evidence_root,
            entry_url=args.entry_url,
            historical_preflight=args.historical_preflight,
            require_sample=True,
        )
    except VenueConfigurationError as exc:
        result = {"status": "config_error", "venue_id": args.venue, "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False))
        return 2
    config.output_dir.mkdir(parents=True, exist_ok=True)
    fetcher = RateLimitedFetcher(config.output_dir / "run_state.json", interval=max(args.interval, 6.0), user_agent=config.user_agent)
    if args.mode in {"sample", "enumerate"}:
        result = fetch_listing_stage(fetcher, config, sample=args.mode == "sample")
        if args.mode == "sample" and result.get("status") == "complete":
            write_discovery_evidence(config, result)
    else:
        result = fetch_details(fetcher, config, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") in {"complete", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

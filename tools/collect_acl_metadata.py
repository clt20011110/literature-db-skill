#!/usr/bin/env python3
"""Collect ACL main-conference metadata from the official ACL Anthology.

This tool reads the official ACL Anthology XML collections and the linked
volume pages. XML is the source for official volume scope and bibliographic
fields; visible volume-page links supply landing/PDF URLs and any displayed
abstracts. It never downloads PDF files or manufactures a PDF URL.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


DEFAULT_COMMIT = "0e1bf6e"
YEARS = tuple(range(2015, 2027))
COLLECTIONS = {year: (f"P{str(year)[-2:]}" if year < 2020 else f"{year}.acl") for year in YEARS}
TARGET_VOLUME_IDS = {
    2015: ("P15-1", "P15-2"),
    2016: ("P16-1", "P16-2"),
    2017: ("P17-1", "P17-2"),
    2018: ("P18-1", "P18-2"),
    2019: ("P19-1",),
    2020: ("2020.acl-main",),
    2021: ("2021.acl-long", "2021.acl-short"),
    2022: ("2022.acl-long", "2022.acl-short"),
    2023: ("2023.acl-long", "2023.acl-short"),
    2024: ("2024.acl-long", "2024.acl-short"),
    2025: ("2025.acl-long", "2025.acl-short"),
    2026: ("2026.acl-long", "2026.acl-short"),
}
SCOPE_REVIEW_PATTERN = re.compile(
    r"\b(?:program|area|general|senior|publication)?\s*chairs?\b|editorial|preface|foreword|"
    r"acknowledg(?:ment|ements)|in memoriam|obituar|keynote|invited (?:talk|paper|lecture)|"
    r"plenary|call for papers|conference report|report on peer review|opening remarks|closing remarks|"
    r"proceedings of the",
    re.IGNORECASE,
)
MANUAL_SCOPE_DECISIONS = {
    "2023.acl-long.911": {
        "decision": "exclude",
        "reason_code": "non_research_content",
        "rationale": "The official record is a Program Chairs' peer-review process report, not a research paper; its pages are numbered xl-lxxv and its abstract describes a report to future chairs and meta-research context.",
    },
    "2020.acl-main.287": {
        "decision": "include",
        "reason_code": None,
        "rationale": "The word editorial refers to the research domain (news argumentation); the official title and Arabic page range identify an ordinary research paper.",
    },
    "2024.acl-long.538": {
        "decision": "include",
        "reason_code": None,
        "rationale": "The phrase editorial capabilities describes the evaluated system task; the official title and Arabic page range identify an ordinary research paper.",
    },
}
OFFICIAL_DOCS = {
    "api_faq": "https://aclanthology.org/faq/api/",
    "development": "https://aclanthology.org/info/development/",
    "linking_faq": "https://aclanthology.org/faq/linking/",
    "repository": "https://github.com/acl-org/acl-anthology/",
}
MONTHS = {
    "jan": "01", "january": "01", "feb": "02", "february": "02",
    "mar": "03", "march": "03", "apr": "04", "april": "04",
    "may": "05", "jun": "06", "june": "06", "jul": "07",
    "july": "07", "aug": "08", "august": "08", "sep": "09",
    "sept": "09", "september": "09", "oct": "10", "october": "10",
    "nov": "11", "november": "11", "dec": "12", "december": "12",
}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_of(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def file_observed_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")


def compare_source_ids(previous_ids: set[str], current_ids: set[str]) -> tuple[list[str], list[str]]:
    """Compare like-for-like full source identity sets, including exclusions."""
    return sorted(current_ids - previous_ids), sorted(previous_ids - current_ids)


def source_drift_status(new_ids: list[str], missing_ids: list[str]) -> str:
    """Classify source changes; unexplained removals are not mergeable."""
    if missing_ids:
        return "UNRESOLVED_DRIFT"
    return "WITHIN_THRESHOLD" if new_ids else "NO_DRIFT"


def fetch_context(ca_bundle: str | None) -> ssl.SSLContext:
    candidates = [ca_bundle, os.environ.get("SSL_CERT_FILE"), "/etc/ssl/cert.pem"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def fetch(url: str, context: ssl.SSLContext, timeout: int = 90) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "literature-db-acl-collector/1.0"})
    with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {url}")
        body = response.read()
        if not body:
            raise RuntimeError(f"empty response for {url}")
        return body, utc_now()


def canonical_url_from_comment(node: ET.Element) -> str | None:
    for child in node.iter():
        if child.tag is ET.Comment and child.text:
            match = re.search(r"https://aclanthology\.org/([^\s<>]+)/?", child.text)
            if match:
                identifier = match.group(1).rstrip("/")
                if not identifier.endswith(".pdf"):
                    return f"https://aclanthology.org/{identifier}/"
    return None


def native_id_from_node(node: ET.Element, *, allow_url_element: bool = True) -> str | None:
    from_comment = canonical_url_from_comment(node)
    if from_comment:
        return urllib.parse.urlsplit(from_comment).path.strip("/")
    if allow_url_element:
        candidate = text_of(node.find("url"))
        if candidate and not candidate.startswith("http"):
            return candidate.strip("/")
    return None


def parse_collection(data: bytes, year: int, xml_url: str, observed_at: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    collection = ET.fromstring(data, parser=parser)
    collection_id = collection.attrib.get("id", "")
    if collection_id != COLLECTIONS[year]:
        raise RuntimeError(f"collection identity mismatch for {year}: {collection_id!r}")
    volumes: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    for volume in collection.findall("volume"):
        source_volume_tag_id = volume.attrib.get("id", "")
        volume_comment_url = canonical_url_from_comment(volume)
        volume_id = urllib.parse.urlsplit(volume_comment_url).path.strip("/") if volume_comment_url else text_of(volume.find("meta/url"))
        if not volume_id:
            raise RuntimeError(f"official volume URL comment missing: {year}/volume[@id='{source_volume_tag_id}']")
        meta = volume.find("meta")
        title = text_of(meta.find("booktitle") if meta is not None else None)
        item_year = text_of(meta.find("year") if meta is not None else None)
        if item_year and int(item_year) != year:
            raise RuntimeError(f"volume {volume_id} has unexpected year {item_year}")
        month = text_of(meta.find("month") if meta is not None else None)
        venues = [text_of(n).casefold() for n in (meta.findall("venue") if meta is not None else [])]
        volume_doi = text_of(meta.find("doi") if meta is not None else None)
        papers: list[dict[str, Any]] = []
        for paper in volume.findall("paper"):
            native_id = native_id_from_node(paper, allow_url_element=False)
            if not native_id:
                raise RuntimeError(f"paper URL comment missing: {year}/{volume_id}/paper[{paper.attrib.get('id')}]")
            authors = []
            for author in paper.findall("author"):
                first, last = text_of(author.find("first")), text_of(author.find("last"))
                full_name = " ".join(part for part in (first, last) if part).strip()
                if full_name:
                    authors.append(full_name)
            paper_record = {
                "source_native_id": native_id,
                "xml_landing_url": canonical_url_from_comment(paper),
                "year": year,
                "venue_id": "acl",
                "volume_id": volume_id,
                "volume_title": title,
                "volume_month": month,
                "volume_doi": volume_doi,
                "title": text_of(paper.find("title")),
                "authors": authors,
                "doi": text_of(paper.find("doi")) or None,
                "pages": text_of(paper.find("pages")) or None,
                "abstract": text_of(paper.find("abstract")) or None,
                "xml_source_url": xml_url,
                "xml_observed_at": observed_at,
                "xml_pointer": f"/collection[@id='{collection_id}']/volume[@id='{source_volume_tag_id}']/paper[@id='{paper.attrib.get('id', '')}']",
            }
            papers.append(paper_record)
            items.append(paper_record)
        front = volume.find("frontmatter")
        frontmatter_id = native_id_from_node(front) if front is not None else None
        volumes[volume_id] = {
            "year": year,
            "volume_id": volume_id,
            "title": title,
            "month": month,
            "venues": venues,
            "doi": volume_doi or None,
            "paper_count": len(papers),
            "papers": papers,
            "frontmatter_id": frontmatter_id,
            "frontmatter_source_url": xml_url if frontmatter_id else None,
            "frontmatter_landing_url": canonical_url_from_comment(front) if front is not None else None,
        }
    return volumes, items, {"collection_id": collection_id, "volume_count": len(volumes), "paper_count": len(items)}


class VolumePageParser(HTMLParser):
    def __init__(self, page_url: str, known_ids: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.known_ids = known_ids
        self.landings: dict[str, str] = {}
        self.pdfs: dict[str, str] = {}
        self.abstracts: dict[str, str] = {}
        self._abstract_key: str | None = None
        self._abstract_text: list[str] = []
        self._abstract_depth = 0

    def _accept_abstract_id(self, raw: str) -> str | None:
        key = raw.removeprefix("abstract-")
        for candidate in (key, key.replace("--", ".")):
            if candidate in self.known_ids:
                return candidate
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if self._abstract_depth:
            if tag not in VOID_TAGS:
                self._abstract_depth += 1
            return
        if tag == "div" and "abstract-collapse" in (attributes.get("class") or "").split():
            abstract_id = attributes.get("id") or ""
            self._abstract_key = self._accept_abstract_id(abstract_id)
            self._abstract_text = []
            self._abstract_depth = 1
        if tag != "a" or not attributes.get("href"):
            return
        absolute = urllib.parse.urljoin(self.page_url, html.unescape(attributes["href"] or ""))
        parsed = urllib.parse.urlsplit(absolute)
        path_id = urllib.parse.unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
        if path_id.endswith(".pdf"):
            native_id = path_id[:-4]
            if native_id in self.known_ids and parsed.hostname == "aclanthology.org":
                previous = self.pdfs.get(native_id)
                if previous and previous != absolute:
                    raise RuntimeError(f"conflicting PDF links for {native_id}: {previous} / {absolute}")
                self.pdfs[native_id] = absolute
        elif path_id in self.known_ids and parsed.hostname == "aclanthology.org":
            if "pdf" not in attributes.get("class", "").split():
                self.landings.setdefault(path_id, absolute)

    def handle_endtag(self, tag: str) -> None:
        if not self._abstract_depth:
            return
        if tag not in VOID_TAGS:
            self._abstract_depth -= 1
        if self._abstract_depth == 0:
            if self._abstract_key:
                value = " ".join(" ".join(self._abstract_text).split())
                if value:
                    self.abstracts[self._abstract_key] = value
            self._abstract_key = None
            self._abstract_text = []

    def handle_data(self, data: str) -> None:
        if self._abstract_depth:
            self._abstract_text.append(data)


def parse_page(data: bytes, page_url: str, ids: set[str]) -> VolumePageParser:
    parser = VolumePageParser(page_url, ids)
    parser.feed(data.decode("utf-8", "replace"))
    parser.close()
    return parser


def provenance(source_url: str, observed_at: str, method: str, **details: Any) -> dict[str, Any]:
    return {"source_url": source_url, "observed_at": observed_at, "method": method, **details}


def publication_date(month: str, year: int) -> str | None:
    if not month:
        return None
    number = MONTHS.get(month.casefold())
    return f"{year}-{number}" if number else f"{month} {year}"


def write_jsonl(path: Path, rows: list[dict[str, Any]], compressed: bool = False) -> None:
    data = jsonl_bytes(rows)
    if compressed:
        data = gzip.compress(data, mtime=0)
    atomic_write(path, data)


def load_previous_expected(root: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(root.glob("*.jsonl*")):
        if path.suffix == ".gz":
            handle = gzip.open(path, "rt", encoding="utf-8")
        else:
            handle = path.open("rt", encoding="utf-8")
        with handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                native_id = row.get("source_native_id") or row.get("source_item_id")
                if native_id:
                    found[str(native_id)] = int(row.get("year", path.name.split(".", 1)[0]))
    return found


def collect(args: argparse.Namespace) -> dict[str, Any]:
    home = Path(args.home).expanduser().resolve()
    run_root = home / "runs" / args.run_id / "acl"
    raw_xml_root = run_root / "raw" / "xml"
    raw_html_root = run_root / "raw" / "volume_pages"
    expected_root = run_root / "expected"
    context = fetch_context(args.ca_bundle)
    source_observations: list[dict[str, Any]] = []
    collection_data: dict[int, dict[str, Any]] = {}

    # Preserve historical and current official XML as compact raw source evidence.
    for year in YEARS:
        collection = COLLECTIONS[year]
        url = f"https://raw.githubusercontent.com/acl-org/acl-anthology/{args.commit}/data/xml/{collection}.xml"
        path = raw_xml_root / f"{collection}.xml"
        if args.reuse_raw and path.is_file():
            data = path.read_bytes()
            observed_at = file_observed_at(path)
        else:
            data, observed_at = fetch(url, context)
            atomic_write(path, data)
        volumes, items, parsed_summary = parse_collection(data, year, url, observed_at)
        collection_data[year] = {"volumes": volumes, "items": items}
        source_observations.append({"kind": "official_collection_xml", "year": year, "url": url, "observed_at": observed_at, "raw_path": str(path), "sha256": sha256(data), **parsed_summary})
        atomic_json(run_root / "checkpoint.json", {"phase": "collection_xml", "completed_years": [item["year"] for item in source_observations], "updated_at": utc_now()})
        time.sleep(args.delay)

    volume_page_data: dict[str, dict[str, Any]] = {}
    for year in YEARS:
        volumes = collection_data[year]["volumes"]
        selected_ids = TARGET_VOLUME_IDS[year]
        missing = sorted(set(selected_ids) - set(volumes))
        if missing:
            raise RuntimeError(f"official XML is missing expected ACL main volumes for {year}: {missing}; available={sorted(volumes)}")
        for volume_id in selected_ids:
            volume = volumes[volume_id]
            if "acl" not in volume["venues"]:
                raise RuntimeError(f"selected volume {volume_id} is not tagged for ACL in official XML")
            title_folded = volume["title"].casefold()
            if any(token in title_folded for token in ("findings", "student research", "system demonstration", "industry track", "workshop")):
                raise RuntimeError(f"non-main track unexpectedly selected: {volume_id}: {volume['title']}")
            page_url = f"https://aclanthology.org/volumes/{volume_id}/"
            # Compression keeps the exact observed HTML while excluding PDF content.
            raw_path = raw_html_root / f"{year}-{re.sub(r'[^A-Za-z0-9.-]+', '_', volume_id)}.html.gz"
            if args.reuse_raw and raw_path.is_file():
                data = gzip.decompress(raw_path.read_bytes())
                observed_at = file_observed_at(raw_path)
            else:
                data, observed_at = fetch(page_url, context)
                atomic_write(raw_path, gzip.compress(data, mtime=0))
            volume_ids = {row["source_native_id"] for row in volume["papers"]}
            if volume["frontmatter_id"]:
                volume_ids.add(volume["frontmatter_id"])
            page = parse_page(data, page_url, volume_ids)
            xml_ids = set(volume_ids)
            listing_ids = set(page.landings)
            missing_from_listing = sorted(xml_ids - listing_ids)
            extra_in_listing = sorted(listing_ids - xml_ids)
            volume_page_data[volume_id] = {
                "url": page_url,
                "observed_at": observed_at,
                "raw_path": str(raw_path),
                "sha256": sha256(data),
                "ids": sorted(listing_ids),
                "missing_from_listing": missing_from_listing,
                "extra_in_listing": extra_in_listing,
                "pdf_ids": sorted(page.pdfs),
                "abstract_ids": sorted(page.abstracts),
                "landings": page.landings,
                "pdfs": page.pdfs,
                "abstracts": page.abstracts,
            }
            source_observations.append({"kind": "official_volume_page", "year": year, "volume_id": volume_id, "url": page_url, "observed_at": observed_at, "raw_path": str(raw_path), "sha256": sha256(data), "listed_source_item_count": len(listing_ids), "visible_pdf_link_count": len(page.pdfs), "visible_abstract_count": len(page.abstracts), "xml_listing_missing_ids": missing_from_listing, "html_listing_extra_ids": extra_in_listing})
            atomic_json(run_root / "checkpoint.json", {"phase": "volume_pages", "completed_volume_ids": sorted(volume_page_data), "updated_at": utc_now()})
            time.sleep(args.delay)

    expected_by_year: dict[int, list[dict[str, Any]]] = {year: [] for year in YEARS}
    staging_rows: list[dict[str, Any]] = []
    exclusion_rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    scope_exclusions: list[dict[str, Any]] = []
    selected_all_ids: set[str] = set()
    selected_paper_ids: set[str] = set()
    included_ids: set[str] = set()
    nonresearch_excluded_ids: set[str] = set()
    content_scope_review: list[dict[str, Any]] = []
    for year in YEARS:
        volumes = collection_data[year]["volumes"]
        selected_ids = set(TARGET_VOLUME_IDS[year])
        for volume_id, volume in sorted(volumes.items()):
            if volume_id not in selected_ids:
                scope_exclusions.append({"year": year, "volume_id": volume_id, "title": volume["title"], "venues": volume["venues"], "paper_count": volume["paper_count"], "frontmatter_id": volume["frontmatter_id"], "reason": "non_main_track_or_other_proceedings_volume"})
        for volume_id in TARGET_VOLUME_IDS[year]:
            volume = volumes[volume_id]
            page = volume_page_data[volume_id]
            page_url, page_time = page["url"], page["observed_at"]
            if page["missing_from_listing"] or page["extra_in_listing"]:
                unresolved.append({"year": year, "volume_id": volume_id, "kind": "xml_html_identity_mismatch", "missing_from_html": page["missing_from_listing"], "extra_in_html": page["extra_in_listing"], "source_url": page_url})
            track_title = volume["title"].casefold()
            if "short papers" in track_title:
                track = "short"
            elif "long papers" in track_title:
                track = "long"
            else:
                track = "main"
            front_id = volume["frontmatter_id"]
            if front_id:
                selected_all_ids.add(front_id)
                expected_by_year[year].append({"venue_id": "acl", "year": year, "source_native_id": front_id, "volume_id": volume_id, "document_type": "front-matter", "inclusion_decision": "exclude", "exclusion_reason_code": "front_matter", "source_url": page_url})
                front_landing = page["landings"].get(front_id) or volume.get("frontmatter_landing_url")
                front = {
                    "schema_version": "literature-metadata-exclusion-v1",
                    "venue_id": "acl",
                    "source_native_id": front_id,
                    "title": volume["title"],
                    "year": year,
                    "inclusion_decision": "exclude",
                    "exclusion_reason_code": "front_matter",
                    "source_url": page_url,
                    "landing_url": front_landing,
                    "observed_at": page_time,
                    "field_provenance": {
                        "source_native_id": provenance(page_url, page_time, "visible_volume_page_landing_link"),
                        "title": provenance(page_url, page_time, "official_volume_title"),
                        "year": provenance(volume_page_data[volume_id]["url"], page_time, "official_volume_page"),
                        "inclusion_decision": provenance(page_url, page_time, "official_volume_front_matter_block"),
                        "exclusion_reason_code": provenance(page_url, page_time, "official_volume_front_matter_block"),
                    },
                }
                exclusion_rows.append(front)
            for item in volume["papers"]:
                native_id = item["source_native_id"]
                selected_all_ids.add(native_id)
                selected_paper_ids.add(native_id)
                title = item["title"]
                authors = item["authors"]
                landing_url = page["landings"].get(native_id) or item.get("xml_landing_url")
                if not title or not authors or not landing_url:
                    unresolved.append({"source_native_id": native_id, "year": year, "volume_id": volume_id, "kind": "required_identity_field_missing", "missing": [key for key, value in (("title", title), ("authors", authors), ("landing_url", landing_url)) if not value], "source_url": item["xml_source_url"]})
                abstract = item["abstract"] or page["abstracts"].get(native_id)
                page_range = item["pages"] or ""
                roman_page_range = bool(re.fullmatch(r"[ivxlcdm]+(?:[-–][ivxlcdm]+)?", page_range.casefold()))
                review_triggers = []
                if SCOPE_REVIEW_PATTERN.search(title):
                    review_triggers.append("organizational_or_nonresearch_title_terms")
                if roman_page_range:
                    review_triggers.append("roman_numeral_page_range")
                manual_decision = MANUAL_SCOPE_DECISIONS.get(native_id)
                if review_triggers:
                    if not manual_decision:
                        unresolved.append({"source_native_id": native_id, "year": year, "volume_id": volume_id, "kind": "content_scope_review_required", "title": title, "pages": page_range, "triggers": review_triggers, "source_url": item["xml_source_url"]})
                    content_scope_review.append({
                        "source_native_id": native_id,
                        "year": year,
                        "volume_id": volume_id,
                        "title": title,
                        "pages": page_range,
                        "abstract_excerpt": abstract,
                        "triggers": review_triggers,
                        "decision": manual_decision.get("decision") if manual_decision else "unresolved",
                        "reason_code": manual_decision.get("reason_code") if manual_decision else None,
                        "rationale": manual_decision.get("rationale") if manual_decision else None,
                        "source_url": item["xml_source_url"],
                        "volume_page_url": page_url,
                    })
                exclude_content = manual_decision is not None and manual_decision["decision"] == "exclude"
                expected_row = {"venue_id": "acl", "year": year, "source_native_id": native_id, "volume_id": volume_id, "document_type": "non-research-content" if exclude_content else "research-paper", "track": track, "inclusion_decision": "exclude" if exclude_content else "include", "source_url": item["xml_source_url"]}
                if exclude_content:
                    expected_row["exclusion_reason_code"] = manual_decision["reason_code"]
                    nonresearch_excluded_ids.add(native_id)
                expected_by_year[year].append(expected_row)
                if exclude_content:
                    nonresearch_provenance = {
                        "source_native_id": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_paper_url_comment", source_pointer=item["xml_pointer"]),
                        "title": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_paper_title", source_pointer=item["xml_pointer"]),
                        "year": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_volume_year", volume_id=volume_id),
                        "inclusion_decision": provenance(item["xml_source_url"], item["xml_observed_at"], "manual_main_research_scope_review", source_pointer=item["xml_pointer"]),
                        "exclusion_reason_code": provenance(item["xml_source_url"], item["xml_observed_at"], "manual_main_research_scope_review", review_rationale=manual_decision["rationale"]),
                        "authors": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_ordered_author_elements", source_pointer=item["xml_pointer"]),
                        "abstract": provenance(item["xml_source_url"] if item["abstract"] else page_url, item["xml_observed_at"] if item["abstract"] else page_time, "official_abstract_supporting_exclusion"),
                        "landing_url": provenance(page_url, page_time, "visible_volume_page_paper_link"),
                    }
                    exclusion_rows.append({
                        "schema_version": "literature-metadata-exclusion-v1",
                        "venue_id": "acl",
                        "source_native_id": native_id,
                        "title": title,
                        "authors": authors,
                        "abstract": abstract,
                        "year": year,
                        "volume": volume_id,
                        "pages": page_range,
                        "doi": item["doi"],
                        "document_type": "program-chair-report",
                        "inclusion_decision": "exclude",
                        "exclusion_reason_code": manual_decision["reason_code"],
                        "exclusion_reason_detail": manual_decision["rationale"],
                        "source_url": item["xml_source_url"],
                        "landing_url": landing_url,
                        "pdf_url": page["pdfs"].get(native_id),
                        "observed_at": max(item["xml_observed_at"], page_time),
                        "field_provenance": nonresearch_provenance,
                    })
                    continue
                abstract_source = item["xml_source_url"] if item["abstract"] else page_url
                abstract_time = item["xml_observed_at"] if item["abstract"] else page_time
                pdf_url = page["pdfs"].get(native_id)
                pdf_status = "visible_url" if pdf_url else "not_visible"
                pub_date = publication_date(volume["month"], year)
                missing_fields: dict[str, Any] = {}
                if not abstract:
                    missing_fields["abstract"] = {"reason_code": "not_present_on_official_page", "checked_sources": [item["xml_source_url"], page_url]}
                if not item["doi"]:
                    missing_fields["doi"] = {"reason_code": "not_present_on_official_page", "checked_sources": [item["xml_source_url"]]}
                if not pub_date:
                    missing_fields["publication_date"] = {"reason_code": "not_assigned", "checked_sources": [item["xml_source_url"]]}
                if not pdf_url:
                    missing_fields["pdf_url"] = {"reason_code": "not_visible", "checked_sources": [page_url]}
                field_provenance = {
                    "source_native_id": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_paper_url_comment", source_pointer=item["xml_pointer"]),
                    "title": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_paper_title", source_pointer=item["xml_pointer"]),
                    "authors": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_ordered_author_elements", source_pointer=item["xml_pointer"]),
                    "year": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_volume_year", volume_id=volume_id),
                    "document_type": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_volume_title", volume_id=volume_id, track=track),
                    "landing_url": provenance(page_url, page_time, "visible_volume_page_paper_link") if native_id in page["landings"] else provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_paper_url_comment", source_pointer=item["xml_pointer"]),
                    "abstract": provenance(abstract_source, abstract_time, "official_xml_abstract" if item["abstract"] else ("visible_volume_page_abstract" if abstract else "checked_absent_in_xml_and_volume_page")),
                    "doi": provenance(item["xml_source_url"], item["xml_observed_at"], "official_xml_paper_doi" if item["doi"] else "checked_absent_in_official_xml", source_pointer=item["xml_pointer"]),
                    "publication_date": provenance(item["xml_source_url"], item["xml_observed_at"], "official_volume_month_and_year", volume_id=volume_id),
                    "pdf_discovery_status": provenance(page_url, page_time, "visible_pdf_anchor" if pdf_url else "checked_no_pdf_anchor", volume_id=volume_id),
                    "pdf_url": provenance(page_url, page_time, "observed_pdf_anchor" if pdf_url else "checked_no_pdf_anchor", volume_id=volume_id),
                }
                row = {
                    "schema_version": "literature-metadata-staging-v1",
                    "venue_id": "acl",
                    "source_native_id": native_id,
                    "title": title,
                    "authors": authors,
                    "abstract": abstract,
                    "document_type": "research-paper",
                    "track": track,
                    "publication_date": pub_date,
                    "year": year,
                    "volume": volume_id,
                    "pages": item["pages"],
                    "doi": item["doi"],
                    "landing_url": landing_url,
                    "pdf_url": pdf_url,
                    "pdf_discovery_status": pdf_status,
                    "inclusion_decision": "include",
                    "source_url": item["xml_source_url"],
                    "observed_at": max(item["xml_observed_at"], page_time),
                    "field_provenance": field_provenance,
                    "missing_fields": missing_fields,
                }
                staging_rows.append(row)
                included_ids.add(native_id)

    for year, rows in expected_by_year.items():
        rows.sort(key=lambda row: (row["volume_id"], row["source_native_id"]))
        write_jsonl(expected_root / f"{year}.jsonl.gz", rows, compressed=True)
    staging_rows.sort(key=lambda row: (row["year"], row["volume"], row["source_native_id"]))
    exclusion_rows.sort(key=lambda row: (row["year"], row["source_native_id"]))
    write_jsonl(run_root / "metadata_staging.jsonl", staging_rows)
    write_jsonl(run_root / "metadata_exclusions.jsonl", exclusion_rows)
    write_jsonl(run_root / "unresolved.jsonl", unresolved)
    atomic_json(run_root / "scope_exclusions.json", {"policy": "Only official ACL main-conference research volumes are in the expected identity set; sibling proceedings in the ACL collection are listed here at volume level. Findings and other separate publication series are outside this collection-level scope and are not included in expected identities.", "excluded_volumes": scope_exclusions})
    atomic_json(run_root / "content_scope_review.json", {"screened_paper_count": len(selected_paper_ids), "screening_rule": "Flag organizational/non-research title terms and Roman-numeral page ranges for manual review; retain an explicit decision for each flagged item.", "review_candidates": content_scope_review, "unresolved_candidate_count": sum(row["decision"] == "unresolved" for row in content_scope_review)})

    previous = load_previous_expected(home / "manifests" / "expected" / "acl")
    previous_ids = set(previous)
    current_source_ids = selected_all_ids
    newly_visible, missing_from_current = compare_source_ids(previous_ids, current_source_ids)
    previous_diff: list[dict[str, Any]] = []
    for native_id in newly_visible:
        current_row = next(row for row in [*staging_rows, *exclusion_rows] if row["source_native_id"] == native_id)
        previous_diff.append({"source_native_id": native_id, "change": "new_in_current_official_main_volume", "year": current_row["year"]})
    for native_id in missing_from_current:
        previous_diff.append({"source_native_id": native_id, "change": "in_prior_expected_not_in_current_official_main_volume", "year": previous[native_id]})
    for native_id in sorted(nonresearch_excluded_ids & previous_ids):
        item_row = next(row for row in content_scope_review if row["source_native_id"] == native_id)
        previous_diff.append({"source_native_id": native_id, "change": "reclassified_from_prior_include_to_non_research_content_exclusion", "year": item_row["year"], "reason": item_row["rationale"]})
    write_jsonl(run_root / "previous_expected_diff.jsonl", previous_diff)

    # The current source set is the full target-volume listing, including the
    # front-matter identities carried into exclusions for complete accounting.
    source_ids = sorted(selected_all_ids)
    source_hash = hashlib.sha256("\n".join(source_ids).encode("utf-8")).hexdigest()
    front_ids = sorted({row["source_native_id"] for row in exclusion_rows if row.get("exclusion_reason_code") == "front_matter"})
    new_source_ids, unresolved_source_missing = compare_source_ids(previous_ids, set(source_ids))
    observed_at = max([row["observed_at"] for row in source_observations])
    source_urls = [OFFICIAL_DOCS["api_faq"], OFFICIAL_DOCS["development"]] + [row["url"] for row in source_observations]
    waterline = {
        "venue_id": "acl",
        "status": "UPDATED" if new_source_ids or unresolved_source_missing else "NO_CHANGE",
        "drift_status": source_drift_status(new_source_ids, unresolved_source_missing),
        "enumeration_complete": not any(row.get("kind") == "xml_html_identity_mismatch" for row in unresolved),
        "observed_at": observed_at,
        "source_urls": sorted(set(source_urls)),
        "source_item_set_sha256": source_hash,
        "source_item_set_hash_method": "sha256 over newline-joined lexicographically sorted ACL Anthology IDs from exact selected main-conference volume pages, including front-matter IDs",
        "current_source_item_count": len(source_ids),
        "new_ids": new_source_ids,
        "missing_ids": unresolved_source_missing,
        "prior_expected_paper_count": len(previous_ids),
        "current_main_research_paper_count": len(included_ids),
        "current_main_volume_paper_source_count": len(selected_paper_ids),
        "nonresearch_main_volume_exclusion_count": len(nonresearch_excluded_ids),
        "front_matter_exclusion_count": len(front_ids),
        "current_expected_identity_count": len(source_ids),
        "target_volumes_by_year": {str(year): list(TARGET_VOLUME_IDS[year]) for year in YEARS},
        "official_build_commit": args.commit,
        "official_build_observed_in_docs": "2026-10-04 site build; documented by ACL Anthology development page",
    }
    atomic_json(run_root / "waterline_evidence.json", waterline)

    source_manifest = {
        "venue_id": "acl",
        "run_root": str(run_root),
        "official_repository_commit": args.commit,
        "official_documents": OFFICIAL_DOCS,
        "official_source_evidence": [
            {
                "url": OFFICIAL_DOCS["api_faq"],
                "observed_at": utc_now(),
                "verified_claim": "ACL Anthology's official API FAQ identifies the official GitHub repository's data/xml directory as the source of paper metadata and says PDFs are hosted on ACL Anthology servers.",
            },
            {
                "url": OFFICIAL_DOCS["development"],
                "observed_at": utc_now(),
                "verified_claim": "The official development page describes the XML as authoritative for events, volumes, papers and authors; it recommends the Python API and records the site build date and commit used to pin this collection.",
                "website_build_date": "2026-10-04",
                "website_build_commit": args.commit,
            },
            {
                "url": OFFICIAL_DOCS["linking_faq"],
                "observed_at": utc_now(),
                "verified_claim": "The official linking FAQ documents canonical Anthology IDs and the website's PDF link conventions; this collection instead records each PDF href observed on its official volume page.",
            },
            {
                "url": OFFICIAL_DOCS["repository"],
                "observed_at": utc_now(),
                "verified_claim": "The ACL Anthology website points to acl-org/acl-anthology as its official data and software repository.",
            },
        ],
        "source_observations": source_observations,
        "enumerated_years": list(YEARS),
        "selected_volume_ids_by_year": {str(year): list(TARGET_VOLUME_IDS[year]) for year in YEARS},
        "raw_pdf_downloads": 0,
    }
    atomic_json(run_root / "source_manifest.json", source_manifest)
    atomic_json(run_root / "official_source_evidence.json", {"venue_id": "acl", "repository_commit": args.commit, "documents": source_manifest["official_source_evidence"]})

    stats: dict[str, Any] = {
        "venue_id": "acl",
        "range": {"from": 2015, "through": 2026},
        "status": "READY_FOR_STRICT_VALIDATION" if not unresolved else "UNRESOLVED_SOURCE_ITEMS",
        "official_commit": args.commit,
        "total_staging_records": len(staging_rows),
        "total_exclusions": len(exclusion_rows),
        "total_expected_source_items": len(source_ids),
        "total_unresolved": len(unresolved),
        "total_scope_excluded_volumes": len(scope_exclusions),
        "prior_expected_papers": len(previous_ids),
        "current_main_papers": len(included_ids),
        "current_main_volume_paper_source_items": len(selected_paper_ids),
        "prior_papers_missing_from_current": len(missing_from_current),
        "new_main_papers_vs_prior_expected": len(newly_visible),
        "prior_expected_reclassified_nonresearch": len(nonresearch_excluded_ids & previous_ids),
        "pdf_visible_link_count": sum(bool(row["pdf_url"]) for row in staging_rows),
        "abstract_present_count": sum(bool(row["abstract"]) for row in staging_rows),
        "doi_present_count": sum(bool(row["doi"]) for row in staging_rows),
        "landing_url_count": sum(bool(row["landing_url"]) for row in staging_rows),
        "yearly": [],
    }
    for year in YEARS:
        year_rows = [row for row in staging_rows if row["year"] == year]
        year_exclusions = [row for row in exclusion_rows if row["year"] == year]
        stats["yearly"].append({
            "year": year,
            "target_volumes": list(TARGET_VOLUME_IDS[year]),
            "expected_source_items": len(expected_by_year[year]),
            "included_research_papers": len(year_rows),
            "front_matter_exclusions": sum(row.get("exclusion_reason_code") == "front_matter" for row in year_exclusions),
            "nonresearch_exclusions": sum(row.get("exclusion_reason_code") == "non_research_content" and row.get("year") == year for row in exclusion_rows),
            "abstract_present": sum(bool(row["abstract"]) for row in year_rows),
            "doi_present": sum(bool(row["doi"]) for row in year_rows),
            "observed_pdf_link": sum(bool(row["pdf_url"]) for row in year_rows),
        })
    atomic_json(run_root / "collection_stats.json", stats)
    atomic_json(run_root / "checkpoint.json", {"phase": "complete", "completed_years": list(YEARS), "completed_volume_ids": sorted(volume_page_data), "updated_at": utc_now(), "unresolved_count": len(unresolved)})
    return {"run_root": str(run_root), "stats": stats, "waterline": waterline, "unresolved": unresolved}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, help="absolute literature database home")
    parser.add_argument("--run-id", default="expand-20261004", help="run folder under <home>/runs")
    parser.add_argument("--commit", default=DEFAULT_COMMIT, help="ACL Anthology Git commit pinned by the official site build")
    parser.add_argument("--ca-bundle", help="optional PEM CA bundle path for HTTPS certificate verification")
    parser.add_argument("--reuse-raw", action="store_true", help="rebuild outputs from previously captured XML/HTML files without network access")
    parser.add_argument("--delay", type=float, default=0.2, help="polite pause between official requests in seconds")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", args.commit):
        parser.error("--commit must be a Git commit SHA")
    if args.delay < 0:
        parser.error("--delay must be nonnegative")
    try:
        result = collect(args)
    except Exception as exc:  # Preserve a readable checkpoint on an interrupted fetch or source mismatch.
        print(json.dumps({"status": "BLOCKED", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["stats"]["total_unresolved"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

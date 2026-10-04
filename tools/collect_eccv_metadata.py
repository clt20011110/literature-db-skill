#!/usr/bin/env python3
"""Collect ECCV proceedings metadata from public SpringerLink HTML.

The collector keeps compressed source pages and per-item checkpoints under the
selected run directory. It never requests PDF bytes.
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
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urljoin, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOME = ROOT / "data" / "literature-db"
RUN_ID = "expand-20261004"
VENUE_ID = "eccv"
YEARS = (2016, 2018, 2020, 2022, 2024, 2026)
SPRINGER_HOST = "link.springer.com"
SERIES_URL = "https://link.springer.com/conference/eccv"
SPRINGER_SEARCH = "https://link.springer.com/search"
PAPERS_INDEX_URL = "https://www.ecva.net/papers.php"
USER_AGENT = "LiteratureDBMetadataCollector/1.0 (public bibliographic metadata)"
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def norm_space(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def doi_from_url(value: str) -> str | None:
    match = re.search(r"/(?:chapter|book)/(10\.\d{4,9}/[^?#\s]+)", value, re.I)
    if not match:
        return None
    return match.group(1).rstrip("/").lower()


def normalized_title(value: str) -> str:
    value = html.unescape(value)
    # Springer wraps Greek letters in a MathJax span. Unwrap that span before
    # stripping other markup so an inline symbol does not become a false word
    # boundary (for example, DεpS versus D\epsilon pS).
    value = re.sub(
        r'<span\b[^>]*class=["\'][^"\']*\bmathjax-tex\b[^"\']*["\'][^>]*>(.*?)</span>',
        lambda match: match.group(1),
        value,
        flags=re.I | re.S,
    )
    value = re.sub(r"\${1,2}\s*", "", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\\\(\s*(.*?)\s*\\\)", r"\1", value)
    value = re.sub(r"\\\[\s*(.*?)\s*\\\]", r"\1", value)
    value = re.sub(
        r"(?<=\w)\s*\\+(?:epsilon|varepsilon)\s*(?=\w)",
        "epsilon",
        value,
    )
    value = re.sub(r"\\+(?:epsilon|varepsilon)\s*", "epsilon", value)
    value = value.replace("ε", "epsilon").replace("ϵ", "epsilon")
    return " ".join(re.findall(r"[\w]+", norm_space(value).casefold(), flags=re.UNICODE))


def first_author_key(value: str) -> str:
    """Normalize the first author name across ECVA's comma list and Springer."""
    first = re.split(r"[,;]", value.replace("*", ""), maxsplit=1)[0]
    return " ".join(re.findall(r"[\w]+", html.unescape(first).casefold(), flags=re.UNICODE))


def ecva_author_matches(chapter_authors: list[str], index_authors: str) -> bool:
    if not chapter_authors or not index_authors:
        return False
    return first_author_key(chapter_authors[0]) == first_author_key(index_authors)


def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key.lower(): (value or "") for key, value in attrs}


class SpringerSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.card: dict[str, Any] | None = None
        self.card_depth = 0
        self.link_depth = 0
        self.total_capture = False
        self.total_text = ""
        self.cards: list[dict[str, str]] = []
        self.pagination_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attrs(attrs)
        if tag not in VOID_TAGS:
            self.stack.append(tag)
        if tag == "li" and values.get("data-test") == "search-result-item":
            self.card = {"href": "", "text": ""}
            self.card_depth = len(self.stack)
        if self.card is not None and tag == "a" and not self.card["href"]:
            href = values.get("href", "")
            if "/book/" in href:
                self.card["href"] = urljoin("https://link.springer.com", href)
        href = values.get("href", "")
        if tag == "a" and re.search(r"/search\?[^#]*[?&]page=\d+", href):
            self.pagination_urls.append(urljoin("https://link.springer.com", href))
        if values.get("data-test") == "results-data-total":
            self.total_capture = True
        if self.total_capture and tag == "span":
            self.link_depth = len(self.stack)

    def handle_data(self, data: str) -> None:
        if self.card is not None:
            self.card["text"] += data + " "
        if self.total_capture:
            self.total_text += data + " "

    def handle_endtag(self, tag: str) -> None:
        if self.card is not None and tag == "li" and len(self.stack) == self.card_depth:
            if self.card.get("href"):
                self.cards.append(
                    {"url": self.card["href"], "text": norm_space(self.card["text"])}
                )
            self.card = None
        if self.total_capture and tag == "span" and len(self.stack) == self.link_depth:
            self.total_capture = False
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag) :]


def parse_springer_search_page(source: str) -> tuple[list[dict[str, str]], int | None]:
    parser = SpringerSearchParser()
    parser.feed(source)
    match = re.search(r"of\s+(\d+)\s+results", norm_space(parser.total_text), re.I)
    total = int(match.group(1)) if match else None
    return parser.cards, total


def parse_springer_search_pagination(source: str) -> list[str]:
    parser = SpringerSearchParser()
    parser.feed(source)
    return list(dict.fromkeys(parser.pagination_urls))


class TocParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.item: dict[str, str] | None = None
        self.item_depth = 0
        self.title_depth = 0
        self.title_text = ""
        self.items: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attrs(attrs)
        if tag not in VOID_TAGS:
            self.stack.append(tag)
        if tag == "li" and values.get("data-test") == "chapter":
            self.item = {"title": "", "url": "", "doi": ""}
            self.item_depth = len(self.stack)
        if self.item is not None:
            test = values.get("data-test", "")
            if test.startswith("chapter-title-"):
                title = test[len("chapter-title-") :]
                self.item["title"] = norm_space(title)
                self.title_depth = len(self.stack)
            if tag in {"a", "area"} and "/chapter/" in values.get("href", ""):
                absolute = urljoin("https://link.springer.com", values["href"])
                self.item["url"] = absolute
                self.item["doi"] = doi_from_url(absolute) or ""

    def handle_data(self, data: str) -> None:
        if self.item is not None and not self.item["title"]:
            self.title_text += data + " "

    def handle_endtag(self, tag: str) -> None:
        if self.item is not None and tag in {"h3", "h4"} and self.title_depth:
            self.title_depth = 0
            if not self.item["title"]:
                self.item["title"] = norm_space(self.title_text)
            self.title_text = ""
        if self.item is not None and tag == "li" and len(self.stack) == self.item_depth:
            if self.item.get("doi") and self.item.get("url"):
                if not self.item.get("title"):
                    self.item["title"] = norm_space(self.title_text)
                self.items.append(dict(self.item))
            self.item = None
            self.title_depth = 0
            self.title_text = ""
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag) :]


def parse_springer_book_toc(source: str) -> list[dict[str, str]]:
    parser = TocParser()
    parser.feed(source)
    return parser.items


class SpringerBookStructureParser(HTMLParser):
    """Read only volume and pagination links explicitly published on a book page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.volume: dict[str, Any] | None = None
        self.volume_depth = 0
        self.volumes: list[dict[str, str]] = []
        self.in_pagination = False
        self.pagination_depth = 0
        self.pagination_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attrs(attrs)
        if tag not in VOID_TAGS:
            self.stack.append(tag)
        test = values.get("data-test", "")
        if tag == "li" and re.fullmatch(r"conferenceProceedingBook-\d+", test):
            self.volume = {"url": "", "text": ""}
            self.volume_depth = len(self.stack)
        if self.volume is not None and tag == "a" and not self.volume["url"]:
            href = values.get("href", "")
            if "/book/" in href:
                self.volume["url"] = urljoin("https://link.springer.com", href)
        if tag == "nav" and test == "book-pagination":
            self.in_pagination = True
            self.pagination_depth = len(self.stack)
        if self.in_pagination and tag == "a":
            aria_label = values.get("aria-label", "")
            if re.fullmatch(r"Page\s+\d+", aria_label, flags=re.I) or values.get("data-test") in {
                "next-page", "previous-page"
            }:
                href = values.get("href", "")
                if href:
                    self.pagination_urls.append(
                        urljoin("https://link.springer.com", href)
                    )

    def handle_data(self, data: str) -> None:
        if self.volume is not None:
            self.volume["text"] += data + " "

    def handle_endtag(self, tag: str) -> None:
        if self.volume is not None and tag == "li" and len(self.stack) == self.volume_depth:
            if self.volume.get("url"):
                self.volumes.append(
                    {
                        "url": self.volume["url"],
                        "title": norm_space(self.volume["text"]),
                    }
                )
            self.volume = None
        if self.in_pagination and tag == "nav" and len(self.stack) == self.pagination_depth:
            self.in_pagination = False
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag) :]


def parse_springer_book_structure(source: str) -> dict[str, list[dict[str, str]]]:
    parser = SpringerBookStructureParser()
    parser.feed(source)
    volumes: dict[str, dict[str, str]] = {}
    for item in parser.volumes:
        volumes[item["url"]] = item
    return {
        "volumes": list(volumes.values()),
        "pagination_urls": list(dict.fromkeys(parser.pagination_urls)),
    }


class SpringerChapterParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: dict[str, list[str]] = {}
        self.canonical_url = ""
        self.jsonld_parts: list[str] = []
        self.jsonld_buffer: list[str] | None = None
        self.abstract_text: list[str] = []
        self.abstract_depth = 0
        self.abstract_heading_depth = 0
        self.times: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attrs(attrs)
        if tag == "meta" and values.get("name"):
            self.metas.setdefault(values["name"].lower(), []).append(
                html.unescape(values.get("content", ""))
            )
        if tag == "link" and values.get("rel", "").lower() == "canonical":
            self.canonical_url = values.get("href", "")
        if tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self.jsonld_buffer = []
        if tag == "time" and values.get("datetime"):
            self.times.append(values["datetime"])
        if tag == "section" and values.get("data-title", "").casefold() == "abstract":
            self.abstract_depth += 1
        elif self.abstract_depth and tag == "section":
            self.abstract_depth += 1
        if self.abstract_depth and tag in {"h1", "h2", "h3", "h4"}:
            self.abstract_heading_depth += 1

    def handle_data(self, data: str) -> None:
        if self.jsonld_buffer is not None:
            self.jsonld_buffer.append(data)
        if self.abstract_depth and not self.abstract_heading_depth:
            self.abstract_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4"} and self.abstract_heading_depth:
            self.abstract_heading_depth -= 1
        if tag == "script" and self.jsonld_buffer is not None:
            value = "".join(self.jsonld_buffer).strip()
            if value:
                self.jsonld_parts.append(value)
            self.jsonld_buffer = None
        if tag == "section" and self.abstract_depth:
            self.abstract_depth -= 1


def _walk_jsonld(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_jsonld(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_jsonld(item)


def _schema_article(parser: SpringerChapterParser) -> dict[str, Any]:
    for part in parser.jsonld_parts:
        try:
            payload = json.loads(part)
        except json.JSONDecodeError:
            continue
        for item in _walk_jsonld(payload):
            type_value = item.get("@type", [])
            if isinstance(type_value, str):
                types = {type_value.casefold()}
            elif isinstance(type_value, list):
                types = {str(value).casefold() for value in type_value}
            else:
                types = set()
            if types & {"scholarlyarticle", "article", "chapter"}:
                return item
    return {}


def _schema_authors(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for author in value:
        if isinstance(author, str):
            name = norm_space(author)
        elif isinstance(author, dict):
            name = norm_space(str(author.get("name") or ""))
        else:
            name = ""
        if name:
            output.append(name)
    return output


def parse_springer_chapter(source: str, page_url: str, expected_year: int) -> dict[str, Any]:
    parser = SpringerChapterParser()
    parser.feed(source)
    schema = _schema_article(parser)
    metas = parser.metas

    def meta(*keys: str) -> str:
        for key in keys:
            values = metas.get(key.lower(), [])
            for value in values:
                if norm_space(value):
                    return norm_space(value)
        return ""

    title = norm_space(str(schema.get("headline") or schema.get("name") or "")) or meta(
        "citation_title", "dc.title"
    )
    authors = _schema_authors(schema.get("author"))
    if not authors:
        for raw in metas.get("citation_author", []):
            raw = norm_space(raw)
            if "," in raw:
                surname, given = raw.split(",", 1)
                raw = norm_space(given + " " + surname)
            if raw:
                authors.append(raw)
    # Prefer the publisher's visible Abstract section. Springer schema/meta
    # descriptions can concatenate nearby author names or other page chrome.
    abstract = norm_space(" ".join(parser.abstract_text))
    if not abstract:
        abstract = norm_space(str(schema.get("description") or ""))
    if not abstract:
        abstract = meta("dc.description")
    doi = (
        meta("citation_doi", "doi", "dc.identifier")
        or doi_from_url(page_url)
        or ""
    )
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I).strip().lower()
    publication_date = norm_space(str(schema.get("datePublished") or "")) or meta(
        "citation_publication_date", "citation_online_date", "prism.publicationdate"
    )
    if not publication_date and parser.times:
        publication_date = parser.times[0]
    publication_year_match = re.search(r"\b(20\d{2})\b", publication_date)
    publication_year = int(publication_year_match.group(1)) if publication_year_match else expected_year
    landing = parser.canonical_url or meta("citation_abstract_html_url") or page_url
    if landing.startswith("/"):
        landing = urljoin("https://link.springer.com", landing)
    pdf_url = meta("citation_pdf_url")
    accessible = schema.get("isAccessibleForFree")
    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "doi": doi,
        "publication_date": publication_date,
        "publication_year": publication_year,
        "landing_url": landing,
        "pdf_url": pdf_url,
        "is_accessible_for_free": accessible,
        "document_type": "conference-paper",
    }


def parse_conference_series(source: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    sections = list(
        re.finditer(
            r'<li\b[^>]*\bid=["\']conference-list-(\d{4})["\'][^>]*>(.*?)(?=<li\b[^>]*\bid=["\']conference-list-|\Z)',
            source,
            flags=re.I | re.S,
        )
    )
    for section in sections:
        year = int(section.group(1))
        block = section.group(2)
        items = re.findall(
            r'<li\b[^>]*class=["\'][^"\']*app-conference-series-timeline__item[^"\']*["\'][^>]*>(.*?)</li>',
            block,
            flags=re.I | re.S,
        )
        main_item = None
        for item in items:
            title_match = re.search(
                r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                item,
                flags=re.I | re.S,
            )
            title = norm_space(re.sub(r"<[^>]+>", " ", title_match.group(2))) if title_match else ""
            if f"eccv {year}" in title.casefold() and "workshop" not in title.casefold():
                main_item = (title, title_match.group(1) if title_match else "")
                break
        if not main_item:
            continue
        title, href = main_item
        pairs = re.findall(
            r'app-conference-series-timeline__item-count-value["\'][^>]*>(\d+)</span>\s*'
            r'<span[^>]*app-conference-series-timeline__item-count-label["\'][^>]*>([^<]+)</span>',
            item,
            flags=re.I | re.S,
        )
        counts = {label.strip().casefold(): int(value) for value, label in pairs}
        output[year] = {
            "papers": counts.get("papers"),
            "volumes": counts.get("volumes"),
            "book_url": urljoin("https://link.springer.com", href),
            "source_url": SERIES_URL,
            "title": title,
        }
    return output


def parse_ecva_index(source: str, observed_at: str) -> dict[int, list[dict[str, Any]]]:
    """Parse only the one already-saved official index page; never fetch details."""
    markers = list(re.finditer(r"<!--\s*ECCV\s+(20\d{2})\s*-->", source, re.I))
    output: dict[int, list[dict[str, Any]]] = {}
    for index, marker in enumerate(markers):
        year = int(marker.group(1))
        end = markers[index + 1].start() if index + 1 < len(markers) else len(source)
        block = source[marker.end() : end]
        rows: list[dict[str, Any]] = []
        pattern = re.compile(
            r"<dt\b[^>]*class\s*=\s*['\"][^'\"]*ptitle[^'\"]*['\"][^>]*>(.*?)</dt>\s*"
            r"<dd\b[^>]*>(.*?)</dd>\s*<dd\b[^>]*>(.*?)</dd>",
            flags=re.I | re.S,
        )
        for match in pattern.finditer(block):
            title_html, authors_html, links_html = match.groups()
            title_match = re.search(r"<a\b[^>]*href\s*=\s*['\"]?([^'\" >]+)['\"]?[^>]*>(.*?)</a>", title_html, re.I | re.S)
            if not title_match:
                continue
            detail_url = urljoin(PAPERS_INDEX_URL, title_match.group(1))
            title = norm_space(re.sub(r"<[^>]+>", " ", title_match.group(2)))
            authors = norm_space(re.sub(r"<[^>]+>", " ", authors_html)).strip("; ")
            links = re.findall(
                r"<a\b[^>]*href\s*=\s*['\"]?([^'\" >]+)['\"]?[^>]*>(.*?)</a>",
                links_html,
                flags=re.I | re.S,
            )
            doi = ""
            chapter_url = ""
            pdf_url = ""
            for href, label_html in links:
                label = norm_space(re.sub(r"<[^>]+>", " ", label_html)).casefold()
                absolute = urljoin(PAPERS_INDEX_URL, href)
                if "link.springer.com/chapter/" in absolute:
                    doi = doi_from_url(absolute) or doi
                    chapter_url = absolute
                elif label == "pdf" and absolute.casefold().endswith(".pdf"):
                    pdf_url = absolute
            if title:
                rows.append(
                    {
                        "year": year,
                        "title": title,
                        "authors_text": authors,
                        "detail_url": detail_url,
                        "doi": doi,
                        "chapter_url": chapter_url,
                        "pdf_url": pdf_url,
                        "source_url": PAPERS_INDEX_URL,
                        "observed_at": observed_at,
                    }
                )
        output[year] = rows
    return output


class VirtualPaperListParser(HTMLParser):
    def __init__(self, year: int) -> None:
        super().__init__(convert_charrefs=True)
        self.year = year
        self.active: dict[str, str] | None = None
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attrs(attrs)
        if tag == "a":
            match = re.fullmatch(rf"/virtual/{self.year}/poster/(\d+)", values.get("href", ""))
            if match:
                self.active = {"native_id": match.group(1), "title": ""}

    def handle_data(self, data: str) -> None:
        if self.active is not None:
            self.active["title"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.active is not None:
            self.active["title"] = norm_space(self.active["title"])
            if self.active["title"]:
                self.rows.append(dict(self.active))
            self.active = None


def parse_virtual_papers_page(source: str, year: int) -> list[dict[str, str]]:
    parser = VirtualPaperListParser(year)
    parser.feed(source)
    deduped: dict[str, dict[str, str]] = {}
    for row in parser.rows:
        deduped[row["native_id"]] = row
    return [deduped[key] for key in sorted(deduped, key=lambda value: int(value))]


class VirtualPosterPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_jsonld = False
        self.jsonld_parts: list[str] = []
        self.jsonld_text: list[str] = []
        self.abstract_depth = 0
        self.abstract_text: list[str] = []
        self.canonical_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attrs(attrs)
        if tag == "script" and values.get("type", "").casefold() == "application/ld+json":
            self.in_jsonld = True
            self.jsonld_text = []
        elif tag == "link" and "canonical" in values.get("rel", "").casefold():
            self.canonical_url = values.get("href", "")
        elif tag == "div":
            classes = set(values.get("class", "").split())
            if self.abstract_depth:
                self.abstract_depth += 1
            elif "abstract-text-inner" in classes:
                self.abstract_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_jsonld:
            self.jsonld_parts.append("".join(self.jsonld_text))
            self.jsonld_text = []
            self.in_jsonld = False
        elif tag == "div" and self.abstract_depth:
            self.abstract_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_jsonld:
            self.jsonld_text.append(data)
        if self.abstract_depth:
            self.abstract_text.append(data)


def parse_virtual_poster(source: str, expected_year: int) -> dict[str, Any]:
    parser = VirtualPosterPageParser()
    parser.feed(source)
    schema: dict[str, Any] = {}
    for part in parser.jsonld_parts:
        try:
            candidate = json.loads(part)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and candidate.get("name") and candidate.get("author"):
            schema = candidate
            break
    authors = _schema_authors(schema.get("author"))
    abstract = norm_space(" ".join(parser.abstract_text))
    name = norm_space(str(schema.get("name") or ""))
    credit_text = norm_space(str(schema.get("creditText") or ""))
    if not name or not authors or not abstract or str(expected_year) not in credit_text:
        raise ValueError("ECCV Virtual poster page lacked a matching venue year, title, authors, or abstract")
    return {
        "title": name,
        "authors": authors,
        "abstract": abstract,
        "venue_year": expected_year,
        "date_published": norm_space(str(schema.get("datePublished") or "")),
        "canonical_url": parser.canonical_url,
        "credit_text": credit_text,
    }


def exclusion_reason(title: str) -> str | None:
    value = norm_space(title).casefold()
    if re.match(r"^correction to\s*:", value):
        return "correction"
    if value in {"front matter", "preface", "foreword", "copyright", "table of contents", "contents"}:
        return "front_matter"
    if value in {"index", "author index", "subject index"}:
        return "index"
    if value in {"acknowledgment", "acknowledgments", "acknowledgement", "acknowledgements"}:
        return "acknowledgment"
    return None


def canonical_jsonl(path: Path, rows: list[dict[str, Any]], *, compress: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if compress or path.suffix == ".gz":
        with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)


def decode_http_body(body: bytes, content_encoding: str) -> bytes:
    encoding = content_encoding.casefold().strip()
    if encoding in {"", "identity"}:
        return body
    if encoding == "gzip":
        return gzip.decompress(body)
    raise ValueError(f"unsupported content encoding: {encoding}")


def is_springer_client_challenge(source: str) -> bool:
    return bool(
        re.search(r"<title\b[^>]*>\s*client\s+challenge\s*</title\s*>", source, re.I)
        or re.search(r"/_fs-ch-[A-Za-z0-9_-]+/", source)
    )


class PageFetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str, *, blocked: bool = False):
        super().__init__(message)
        self.url = url
        self.status = status
        self.blocked = blocked


class CachedFetcher:
    def __init__(self, raw_root: Path, interval: float = 0.75, refresh: bool = False):
        self.raw_root = raw_root
        self.page_root = raw_root / "pages"
        self.page_root.mkdir(parents=True, exist_ok=True)
        self.index_path = raw_root / "page_cache.json"
        try:
            self.cache = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.cache = {}
        self.interval = max(0.0, interval)
        self.refresh = refresh
        self.last_request = 0.0
        self._request_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._thread_state = threading.local()
        self.ssl_context = self._verified_ssl_context()

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self._thread_state, "last_meta", {})

    @last_meta.setter
    def last_meta(self, value: dict[str, Any]) -> None:
        self._thread_state.last_meta = value

    @staticmethod
    def _verified_ssl_context() -> ssl.SSLContext:
        candidates = [
            os.environ.get("SSL_CERT_FILE", ""),
            ssl.get_default_verify_paths().cafile or "",
            "/etc/ssl/cert.pem",
            "/etc/ssl/certs/ca-certificates.crt",
            "/opt/homebrew/etc/openssl@3/cert.pem",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return ssl.create_default_context(cafile=candidate)
        return ssl.create_default_context()

    def _save_index(self) -> None:
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.index_path)

    def _cache_name(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest() + ".html.gz"

    def fetch(self, url: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        if urlsplit(url).scheme != "https" or host != SPRINGER_HOST:
            raise PageFetchError(url, None, f"refusing non-Springer or non-HTTPS URL: {url}")
        entry = self.cache.get(url)
        if not self.refresh and isinstance(entry, dict):
            cached_path = self.raw_root / entry.get("path", "")
            if cached_path.is_file():
                with gzip.open(cached_path, "rt", encoding="utf-8", errors="replace") as handle:
                    source = handle.read()
                self.last_meta = dict(entry)
                self.last_meta["cached"] = True
                if is_springer_client_challenge(source):
                    raise PageFetchError(
                        url,
                        int(entry.get("status") or 200),
                        "Springer returned a client challenge page; stop new requests and retain the checkpoint",
                        blocked=True,
                    )
                return source
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
                "Accept-Encoding": "gzip",
            },
        )
        with self._request_lock:
            wait = self.interval - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
        try:
            with urlopen(request, timeout=60, context=self.ssl_context) as response:
                final_url = response.geturl()
                final_host = (urlsplit(final_url).hostname or "").lower()
                if urlsplit(final_url).scheme != "https" or final_host != SPRINGER_HOST:
                    raise PageFetchError(
                        url, getattr(response, "status", 200),
                        f"redirected outside the approved Springer host: {final_url}",
                        blocked=True,
                    )
                content_encoding = response.headers.get("Content-Encoding", "").casefold()
                try:
                    body = decode_http_body(response.read(), content_encoding)
                except (OSError, EOFError, ValueError) as exc:
                    raise PageFetchError(
                        url,
                        int(getattr(response, "status", 200)),
                        f"official source response content could not be decoded: {exc}",
                    ) from exc
                status = int(getattr(response, "status", 200))
                observed_at = utc_now()
                path = Path("pages") / self._cache_name(url)
                absolute = self.raw_root / path
                temp = absolute.with_suffix(".tmp")
                with gzip.open(temp, "wb", compresslevel=6) as handle:
                    handle.write(body)
                temp.replace(absolute)
                entry = {
                    "path": str(path),
                    "source_url": url,
                    "final_url": final_url,
                    "status": status,
                    "observed_at": observed_at,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                    "content_type": response.headers.get("Content-Type", ""),
                }
                with self._cache_lock:
                    self.cache[url] = entry
                    self._save_index()
                self.last_meta = dict(entry)
                source = body.decode("utf-8", errors="replace")
                if is_springer_client_challenge(source):
                    raise PageFetchError(
                        url,
                        status,
                        "Springer returned a client challenge page; stop new requests and retain the checkpoint",
                        blocked=True,
                    )
                return source
        except HTTPError as exc:
            status = exc.code
            retry_after = exc.headers.get("Retry-After", "")
            if status in {403, 429, 503}:
                raise PageFetchError(
                    url, status,
                    f"public source returned HTTP {status}; checkpoint saved and collection stopped"
                    + (f" (Retry-After: {retry_after})" if retry_after else ""),
                    blocked=True,
                ) from exc
            raise PageFetchError(url, status, f"public source returned HTTP {status}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise PageFetchError(url, None, f"request failed: {exc}") from exc


def _safe_html_text(value: str) -> str:
    return norm_space(re.sub(r"<[^>]+>", " ", value))


def _search_url(year: int, page: int) -> str:
    query = quote_plus(f"Computer Vision ECCV {year}")
    return f"{SPRINGER_SEARCH}?query={query}&content-type=Book&sortBy=relevance&page={page}"


class ECCVCollector:
    def __init__(
        self,
        home: Path,
        run_root: Path,
        *,
        interval: float,
        refresh: bool = False,
        ecva_index: Path | None = None,
    ):
        self.home = home
        self.run_root = run_root
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.expected_root = run_root / "expected"
        self.expected_root.mkdir(parents=True, exist_ok=True)
        self.raw_root = run_root / "raw" / "springer"
        self.fetcher = CachedFetcher(self.raw_root, interval=interval, refresh=refresh)
        self.state_path = run_root / "checkpoint.json"
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.state = {
                "schema_version": "eccv-collector-checkpoint-v1",
                "venue_id": VENUE_ID,
                "run_id": RUN_ID,
                "started_at": utc_now(),
                "completed_enumeration_years": [],
                "completed_detail_ids": [],
                "page_failures": [],
            }
        self.ecva_index_path = ecva_index
        self.ecva_records: dict[int, list[dict[str, Any]]] = {}
        self.ecva_index_meta: dict[str, Any] = {}
        if ecva_index is not None:
            if not ecva_index.is_file():
                raise ValueError(f"saved ECVA index does not exist: {ecva_index}")
            source_bytes = ecva_index.read_bytes()
            source = source_bytes.decode("utf-8", errors="replace")
            observation_path = ecva_index.parent / "ecva_index_observation.json"
            try:
                self.ecva_index_meta = json.loads(observation_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise ValueError(
                    f"saved ECVA index lacks its observation receipt: {observation_path}"
                )
            observed_at = str(self.ecva_index_meta.get("observed_at") or "")
            source_url = str(self.ecva_index_meta.get("source_url") or "")
            expected_hash = str(self.ecva_index_meta.get("sha256") or "")
            try:
                datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"ECVA index receipt has invalid observed_at: {observed_at!r}") from exc
            if source_url != PAPERS_INDEX_URL:
                raise ValueError(f"ECVA index receipt source_url mismatch: {source_url!r}")
            if expected_hash != hashlib.sha256(source_bytes).hexdigest():
                raise ValueError("ECVA index receipt hash does not match the saved index file")
            self.ecva_records = parse_ecva_index(
                source, observed_at
            )

    def _save_state(self) -> None:
        if isinstance(self.state.get("completed_detail_ids"), list):
            self.state["completed_detail_ids"] = sorted(
                {str(value) for value in self.state["completed_detail_ids"]}
            )
        self.state["updated_at"] = utc_now()
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)

    def _write_expected(self, year: int, chapters: list[dict[str, Any]], source_urls: list[str]) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for row in chapters:
            identity = row["doi"]
            if identity in deduped and deduped[identity]["title"] != row["title"]:
                raise PageFetchError(row["source_url"], None, f"conflicting chapter titles for DOI {identity}")
            deduped[identity] = row
        rows = []
        for identity, chapter in sorted(deduped.items()):
            rows.append(
                {
                    "venue_id": VENUE_ID,
                    "year": year,
                    "source_native_id": identity,
                    "source_document_type": "Springer ECCV proceedings chapter",
                    "title": chapter["title"],
                    "landing_url": chapter["url"],
                    "book_url": chapter["book_url"],
                    "enumeration_source_url": chapter["source_url"],
                    "enumeration_observed_at": chapter["observed_at"],
                }
            )
        canonical_jsonl(self.expected_root / f"{year}.jsonl.gz", rows, compress=True)
        report = {
            "year": year,
            "source_urls": sorted(set(source_urls)),
            "source_item_count": len(rows),
            "research_candidate_count": sum(exclusion_reason(row["title"]) is None for row in rows),
            "excluded_chapter_count": sum(exclusion_reason(row["title"]) is not None for row in rows),
            "source_item_set_sha256": hashlib.sha256(
                "\n".join(sorted(deduped)).encode("utf-8")
            ).hexdigest(),
            "items": rows,
        }
        return report

    def _details_expected_path(self, year: int) -> Path:
        """Return the manifest used by detail collection without promoting partial 2026."""
        path = self.expected_root / f"{year}.jsonl.gz"
        if year == 2026 and not path.is_file():
            partial_path = self.expected_root / "2026.incomplete-series-only.jsonl.gz"
            if partial_path.is_file():
                return partial_path
        return path

    def enumerate(self, years: tuple[int, ...]) -> dict[str, Any]:
        series_html = self.fetcher.fetch(SERIES_URL)
        series = parse_conference_series(series_html)
        robots_evidence = self.run_root / "raw" / "access_policy_observations.json"
        robots_evidence.parent.mkdir(parents=True, exist_ok=True)
        if not robots_evidence.exists():
            robots_evidence.write_text(
                json.dumps(
                    {
                        "observed_at": utc_now(),
                        "observations": [
                            {
                                "robots_url": "https://link.springer.com/robots.txt",
                                "finding": "User-agent: * permits /conference/, /search, /book*, /chapter/; collector uses only these routes and never requests /content/pdf/.",
                            },
                            {
                                "robots_url": "https://www.ecva.net/robots.txt",
                                "finding": "User-agent: * Disallow: /; no additional ECVA requests are made. A single saved papers.php index from initial source discovery is parsed locally.",
                            },
                            {
                                "robots_url": "https://eccv.ecva.net/robots.txt",
                                "finding": "User-agent: * disallows /static (including the Virtual Site JSON files referenced by papers.html), /search, /virtual/*/search, and query routes containing page=. Only explicitly linked non-static Virtual Site pages are collected; no disallowed data file is requested.",
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        all_reports: dict[str, Any] = {}
        for year in years:
            completed = set(self.state.get("completed_enumeration_years", []))
            completed.discard(year)
            self.state["completed_enumeration_years"] = sorted(completed)
            self._save_state()
            if year not in series:
                self.state.setdefault("enumeration_failures", []).append(
                    {"year": year, "reason": "year missing from official Springer conference series page"}
                )
                self._save_state()
                continue
            main_book = {
                "url": series[year]["book_url"],
                "title": str(series[year].get("title") or f"Computer Vision – ECCV {year}"),
            }
            book_page_failures: list[dict[str, Any]] = []
            try:
                main_book_html = self.fetcher.fetch(main_book["url"])
                main_book_meta = dict(self.fetcher.last_meta)
            except PageFetchError as exc:
                self.state.setdefault("enumeration_failures", []).append(
                    {"year": year, "book_url": main_book["url"], "reason": str(exc)}
                )
                self._save_state()
                continue

            structure = parse_springer_book_structure(main_book_html)
            expected_volume_title = normalized_title(f"Computer Vision ECCV {year}")
            books_by_url: dict[str, dict[str, str]] = {main_book["url"]: main_book}
            for candidate in structure["volumes"]:
                normalized_volume_title = normalized_title(candidate["title"])
                if normalized_volume_title == expected_volume_title:
                    books_by_url[candidate["url"]] = candidate

            search_pages: list[str] = []
            search_page_failures: list[dict[str, Any]] = []
            search_total_results: int | None = None
            search_matching_books = 0
            search_blocked = False
            if year == 2026:
                # The saved official result page reports 78 books while the series
                # and the related-volume widget currently show 77. Follow only the
                # pagination hrefs already present in that page; never synthesize
                # additional query pages.
                search_url = _search_url(year, 1)
                try:
                    search_html = self.fetcher.fetch(search_url)
                    search_pages.append(search_url)
                    candidates, search_total_results = parse_springer_search_page(search_html)
                    page_queue = parse_springer_search_pagination(search_html)
                    search_seen = {search_url}
                    if search_total_results is None or not candidates:
                        search_page_failures.append(
                            {
                                "year": year,
                                "search_url": search_url,
                                "reason": "official search page lacked its visible result count or book rows",
                            }
                        )
                    while page_queue:
                        page_url = page_queue.pop(0)
                        parts = urlsplit(page_url)
                        if (
                            parts.scheme != "https"
                            or parts.netloc != SPRINGER_HOST
                            or parts.path != "/search"
                            or page_url in search_seen
                        ):
                            continue
                        search_seen.add(page_url)
                        try:
                            page_html = self.fetcher.fetch(page_url)
                        except PageFetchError as exc:
                            search_page_failures.append(
                                {"year": year, "search_url": page_url, "reason": str(exc)}
                            )
                            if exc.blocked:
                                search_blocked = True
                                break
                            continue
                        search_pages.append(page_url)
                        page_candidates, page_total = parse_springer_search_page(page_html)
                        if not page_candidates or page_total is None:
                            search_page_failures.append(
                                {
                                    "year": year,
                                    "search_url": page_url,
                                    "reason": "official search page lacked its visible result count or book rows",
                                }
                            )
                            continue
                        if page_total is not None:
                            search_total_results = page_total
                        candidates.extend(page_candidates)
                        page_queue.extend(
                            url for url in parse_springer_search_pagination(page_html)
                            if url not in search_seen and url not in page_queue
                        )
                except PageFetchError as exc:
                    search_page_failures.append(
                        {"year": year, "search_url": search_url, "reason": str(exc)}
                    )
                    candidates = []
                    search_blocked = exc.blocked
            if search_blocked:
                report = {
                    "year": year,
                    "source_urls": sorted(set([SERIES_URL, main_book["url"], *search_pages, _search_url(year, 1)])),
                    "source_item_count": 0,
                    "research_candidate_count": 0,
                    "source_item_set_sha256": hashlib.sha256(b"").hexdigest(),
                    "in_progress": True,
                    "enumeration_blocked": True,
                    "conference_series_papers": series[year].get("papers"),
                    "conference_series_volumes": series[year].get("volumes"),
                    "springer_search_total_results": search_total_results,
                    "springer_search_matching_books": search_matching_books,
                    "springer_search_page_failures": search_page_failures,
                }
                all_reports[str(year)] = report
                self.state.setdefault("enumerated", {})[str(year)] = report
                self.state.setdefault("enumeration_blocked_years", []).append(year)
                self.state["enumeration_blocked_years"] = sorted(set(self.state["enumeration_blocked_years"]))
                self._save_state()
                self._write_enumeration_report(all_reports, series)
                continue
                for candidate in candidates:
                    candidate_title = candidate["text"].casefold()
                    if f"eccv {year}" not in candidate_title or "workshop" in candidate_title:
                        continue
                    if candidate["url"] not in books_by_url:
                        books_by_url[candidate["url"]] = candidate
                search_matching_books = len(
                    {
                        candidate["url"]
                        for candidate in candidates
                        if f"eccv {year}" in candidate["text"].casefold()
                        and "workshop" not in candidate["text"].casefold()
                    }
                )
            books = list(books_by_url.values())
            chapters: list[dict[str, Any]] = []
            source_urls = [SERIES_URL, *search_pages]
            completed_volumes: list[str] = []
            work_root = self.run_root / "enumeration_work"
            work_root.mkdir(parents=True, exist_ok=True)
            work_path = work_root / f"{year}.jsonl.gz"
            for book in books:
                if book["url"] == main_book["url"]:
                    book_html = main_book_html
                    book_meta = main_book_meta
                else:
                    try:
                        book_html = self.fetcher.fetch(book["url"])
                        book_meta = dict(self.fetcher.last_meta)
                    except PageFetchError as exc:
                        book_page_failures.append(
                            {"year": year, "book_url": book["url"], "reason": str(exc)}
                        )
                        if exc.blocked:
                            break
                        continue
                source_urls.append(book["url"])
                page_queue: list[tuple[str, str, dict[str, Any]]] = [
                    (book["url"], book_html, book_meta)
                ]
                page_seen: set[str] = set()
                while page_queue:
                    page_url, page_html, page_meta = page_queue.pop(0)
                    if page_url in page_seen:
                        continue
                    if not page_html:
                        try:
                            page_html = self.fetcher.fetch(page_url)
                            page_meta = dict(self.fetcher.last_meta)
                        except PageFetchError as exc:
                            book_page_failures.append(
                                {"year": year, "book_url": book["url"], "page_url": page_url, "reason": str(exc)}
                            )
                            if exc.blocked:
                                page_queue.clear()
                                break
                            continue
                    page_seen.add(page_url)
                    source_urls.append(page_url)
                    for item in parse_springer_book_toc(page_html):
                        if not item.get("doi") or not item.get("title"):
                            continue
                        chapters.append(
                            {
                                **item,
                                "year": year,
                                "book_url": book["url"],
                                "source_url": page_url,
                                "observed_at": page_meta.get("observed_at", utc_now()),
                            }
                        )
                    page_structure = parse_springer_book_structure(page_html)
                    for pagination_url in page_structure["pagination_urls"]:
                        parts = urlsplit(pagination_url)
                        page_match = re.search(r"(?:^|&)page=(\d+)(?:&|$)", parts.query)
                        if (
                            parts.scheme == "https"
                            and parts.netloc == SPRINGER_HOST
                            and parts.path == urlsplit(book["url"]).path
                            and page_match
                            and int(page_match.group(1)) > 1
                            and pagination_url not in page_seen
                            and all(pagination_url != pending[0] for pending in page_queue)
                        ):
                            page_queue.append((pagination_url, "", {}))
                if book["url"] not in {failure.get("book_url") for failure in book_page_failures}:
                    completed_volumes.append(book["url"])
                canonical_jsonl(work_path, chapters, compress=True)
                partial_by_doi = {row["doi"]: row for row in chapters}
                partial_ids = sorted(partial_by_doi)
                partial_hash = hashlib.sha256("\n".join(partial_ids).encode("utf-8")).hexdigest()
                self.state.setdefault("enumeration_work", {})[str(year)] = {
                    "expected_volume_count_from_series": series[year].get("volumes"),
                    "volume_count_seen_on_main_book": len(books),
                    "search_reported_result_count": search_total_results,
                    "search_matching_book_count": search_matching_books,
                    "completed_volume_urls": list(completed_volumes),
                    "current_volume_url": book["url"],
                    "partial_source_item_count": len(partial_ids),
                    "partial_source_item_set_sha256": partial_hash,
                    "updated_at": utc_now(),
                }
                all_reports[str(year)] = {
                    "year": year,
                    "source_urls": sorted(set(source_urls)),
                    "source_item_count": len(partial_ids),
                    "source_item_set_sha256": partial_hash,
                    "in_progress": True,
                    "completed_volume_count": len(completed_volumes),
                    "observed_volume_count": len(books),
                    "conference_series_papers": series[year].get("papers"),
                    "conference_series_volumes": series[year].get("volumes"),
                }
                self._save_state()
                self._write_enumeration_report(all_reports, series)

            # The work file is intentionally separate from expected/<year>.jsonl.gz.
            # Only a complete, successfully enumerated year reaches the final path.
            if year == 2026 and search_page_failures:
                partial_ids = sorted({str(row["doi"]) for row in chapters})
                report = {
                    "year": year,
                    "source_urls": sorted(set(source_urls)),
                    "source_item_count": len(partial_ids),
                    "research_candidate_count": sum(
                        exclusion_reason(str(row["title"])) is None for row in chapters
                    ),
                    "source_item_set_sha256": hashlib.sha256("\n".join(partial_ids).encode("utf-8")).hexdigest(),
                    "in_progress": True,
                    "enumeration_blocked": False,
                    "incomplete_reason": "official 2026 Springer search set could not be reconciled; series volume count alone is insufficient",
                    "conference_series_papers": series[year].get("papers"),
                    "conference_series_volumes": series[year].get("volumes"),
                    "springer_volume_count_observed": len(books),
                    "springer_volume_count_completed": len(completed_volumes),
                    "springer_search_total_results": search_total_results,
                    "springer_search_matching_books": search_matching_books,
                    "springer_search_page_failures": search_page_failures,
                    "book_page_failures": book_page_failures,
                }
                all_reports[str(year)] = report
                self.state.setdefault("enumerated", {})[str(year)] = report
                self.state["completed_enumeration_years"] = sorted(
                    set(self.state.get("completed_enumeration_years", [])) - {year}
                )
                self._save_state()
                self._write_enumeration_report(all_reports, series)
                continue
            report = self._write_expected(year, chapters, source_urls)
            report.update(
                {
                    "conference_series_papers": series[year].get("papers"),
                    "conference_series_volumes": series[year].get("volumes"),
                    "springer_volume_count_observed": len(books),
                    "springer_volume_count_completed": len(completed_volumes),
                    "springer_search_total_results": search_total_results,
                    "springer_search_matching_books": search_matching_books,
                    "springer_search_page_failures": search_page_failures,
                    "book_page_failures": book_page_failures,
                }
            )
            all_reports[str(year)] = report
            self.state.setdefault("enumerated", {})[str(year)] = {
                key: value for key, value in report.items() if key != "items"
            }
            search_count_complete = (
                search_total_results is None
                or search_matching_books >= search_total_results
            )
            if (
                not book_page_failures
                and not search_page_failures
                and search_count_complete
                and len(completed_volumes) == len(books)
                and books
            ):
                completed = set(self.state.get("completed_enumeration_years", []))
                completed.add(year)
                self.state["completed_enumeration_years"] = sorted(completed)
            self._save_state()
            self._write_enumeration_report(all_reports, series)
        return {
            "series": {
                str(year): value
                for year, value in series.items()
                if year in years
            },
            "years": {
                year: {key: value for key, value in report.items() if key != "items"}
                for year, report in all_reports.items()
            },
            "checkpoint": str(self.state_path),
        }

    def _write_enumeration_report(self, year_reports: dict[str, Any], series: dict[int, Any]) -> None:
        path = self.run_root / "enumeration_report.json"
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        merged_year_reports = dict(previous.get("year_reports", {}))
        merged_year_reports.update(year_reports)
        summary = {
            "venue_id": VENUE_ID,
            "run_id": RUN_ID,
            "observed_at": utc_now(),
            "source_url": SERIES_URL,
            "year_reports": merged_year_reports,
            "official_series": {str(year): value for year, value in series.items() if year in YEARS},
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _ecva_match_index(self, year: int) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        rows = self.ecva_records.get(year, [])
        by_doi: dict[str, list[dict[str, Any]]] = {}
        by_title: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("doi"):
                by_doi.setdefault(row["doi"].lower(), []).append(row)
            by_title.setdefault(normalized_title(row["title"]), []).append(row)
        return by_doi, by_title

    def _ecva_doi_conflicts(self, year: int) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.ecva_records.get(year, []):
            if row.get("doi"):
                grouped.setdefault(str(row["doi"]).lower(), []).append(row)
        conflicts: list[dict[str, Any]] = []
        for doi, rows in sorted(grouped.items()):
            titles = sorted({str(row.get("title") or "") for row in rows})
            pdf_urls = sorted({str(row.get("pdf_url") or "") for row in rows if row.get("pdf_url")})
            if len(titles) > 1 or len(pdf_urls) > 1:
                conflicts.append(
                    {
                        "doi": doi,
                        "record_count": len(rows),
                        "distinct_titles": titles,
                        "distinct_pdf_urls": pdf_urls,
                        "records": [
                            {
                                "title": row.get("title"),
                                "chapter_url": row.get("chapter_url"),
                                "pdf_url": row.get("pdf_url"),
                            }
                            for row in rows
                        ],
                    }
                )
        return conflicts

    def _stage_record(
        self,
        year: int,
        expected: dict[str, Any],
        chapter: dict[str, Any],
        page_observed_at: str,
        ecva_match: dict[str, Any] | None,
        source_url: str,
    ) -> dict[str, Any]:
        identity = expected["source_native_id"]
        doi = chapter.get("doi") or identity
        title = chapter["title"]
        authors = chapter["authors"]
        abstract = chapter["abstract"]
        publication_date = chapter["publication_date"]
        landing_url = chapter["landing_url"]
        pdf_url = (ecva_match or {}).get("pdf_url") or chapter.get("pdf_url") or ""
        pdf_source_url = (
            (ecva_match or {}).get("source_url")
            if ecva_match and ecva_match.get("pdf_url")
            else source_url
        )
        pdf_observed_at = (
            (ecva_match or {}).get("observed_at")
            if ecva_match and ecva_match.get("pdf_url")
            else page_observed_at
        )
        provenance: dict[str, dict[str, Any]] = {}
        chapter_fields = (
            "source_native_id", "title", "authors", "year", "document_type",
            "landing_url", "abstract", "doi", "publication_date",
        )
        for field in chapter_fields:
            provenance[field] = {
                "source_url": source_url,
                "observed_at": page_observed_at,
                "method": "official_springer_chapter_html",
            }
        provenance["pdf_discovery_status"] = {
            "source_url": pdf_source_url,
            "observed_at": pdf_observed_at,
            "method": (
                "official_link_observed_in_index_"
                + str((ecva_match or {}).get("_match_method", "unique_title"))
                + "_and_first_author"
                if ecva_match and ecva_match.get("pdf_url")
                else "official_springer_citation_pdf_url"
            ),
        }
        missing: dict[str, Any] = {}
        if not abstract:
            missing["abstract"] = {
                "reason_code": "not_present_on_official_page",
                "source_url": source_url,
            }
        if not publication_date:
            missing["publication_date"] = {
                "reason_code": "not_present_on_official_page",
                "source_url": source_url,
            }
        if not pdf_url:
            missing["pdf_url"] = {
                "reason_code": "not_visible",
                "source_url": source_url,
            }
        if not doi:
            missing["doi"] = {
                "reason_code": "not_present_on_official_page",
                "source_url": source_url,
            }
        return {
            "schema_version": "literature-metadata-staging-v1",
            "venue_id": VENUE_ID,
            "source_native_id": identity,
            "title": title,
            "authors": authors,
            "year": year,
            "document_type": "conference-paper",
            "inclusion_decision": "include",
            "landing_url": landing_url,
            "source_url": source_url,
            "observed_at": page_observed_at,
            "abstract": abstract or None,
            "publication_date": publication_date or None,
            "doi": doi or None,
            "pdf_url": pdf_url or None,
            "pdf_discovery_status": "visible_url" if pdf_url else "not_visible",
            "field_provenance": provenance,
            "missing_fields": missing,
        }

    def _stage_exclusion(self, year: int, expected: dict[str, Any], reason: str, observed_at: str) -> dict[str, Any]:
        source_url = expected["enumeration_source_url"]
        identity = expected["source_native_id"]
        return {
            "schema_version": "literature-metadata-exclusion-v1",
            "venue_id": VENUE_ID,
            "source_native_id": identity,
            "title": expected["title"],
            "year": year,
            "inclusion_decision": "exclude",
            "exclusion_reason_code": reason,
            "source_url": source_url,
            "landing_url": expected["landing_url"],
            "observed_at": observed_at,
            "field_provenance": {
                field: {
                    "source_url": source_url,
                    "observed_at": observed_at,
                    "method": "official_springer_table_of_contents",
                }
                for field in (
                    "source_native_id", "title", "year", "inclusion_decision",
                    "exclusion_reason_code",
                )
            },
        }

    def _supplement_expected_from_ecva_index(self, years: tuple[int, ...]) -> dict[str, Any]:
        """Verify ECVA-index chapter DOI links that are absent from Springer TOCs."""
        summary: dict[str, Any] = {"venue_id": VENUE_ID, "observed_at": utc_now(), "years": {}}
        blocked = False
        enumeration_path = self.run_root / "enumeration_report.json"
        try:
            enumeration = json.loads(enumeration_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            enumeration = {"year_reports": {}}
        for year in years:
            expected_path = self.expected_root / f"{year}.jsonl.gz"
            if not expected_path.is_file():
                continue
            with gzip.open(expected_path, "rt", encoding="utf-8") as handle:
                expected_rows = [json.loads(line) for line in handle if line.strip()]
            expected_by_id = {str(row["source_native_id"]).lower(): row for row in expected_rows}
            proceedings_book_ids = {
                doi_from_url(str(row.get("book_url") or ""))
                for row in expected_rows
                if row.get("book_url")
            }
            by_title: dict[str, list[dict[str, Any]]] = {}
            for row in expected_rows:
                by_title.setdefault(normalized_title(str(row.get("title") or "")), []).append(row)
            year_report = enumeration.setdefault("year_reports", {}).get(str(year), {})
            prior_supplements = [
                item for item in year_report.get("ecva_index_supplements", [])
                if str(item.get("source_native_id", "")).lower() in expected_by_id
            ]
            supplements: list[dict[str, Any]] = list(prior_supplements)
            title_doi_mismatches: list[dict[str, Any]] = []
            unresolved_supplements: list[dict[str, Any]] = []
            for index_row in self.ecva_records.get(year, []):
                doi = str(index_row.get("doi") or "").lower()
                if not doi or not re.fullmatch(r"10\.1007/[^/]+_\d+", doi):
                    continue
                title = str(index_row.get("title") or "")
                if doi in expected_by_id:
                    expected = expected_by_id[doi]
                    if normalized_title(title) != normalized_title(str(expected.get("title") or "")):
                        title_doi_mismatches.append(
                            {
                                "ecva_index_doi": doi,
                                "ecva_index_title": title,
                                "springer_title_for_same_doi": str(expected.get("title") or ""),
                                "ecva_pdf_url": str(index_row.get("pdf_url") or ""),
                            }
                        )
                    continue
                same_title = by_title.get(normalized_title(title), [])
                if len(same_title) == 1:
                    title_doi_mismatches.append(
                        {
                            "ecva_index_doi": doi,
                            "ecva_index_title": title,
                            "springer_title_match_id": str(same_title[0]["source_native_id"]),
                            "ecva_pdf_url": str(index_row.get("pdf_url") or ""),
                        }
                    )
                    continue
                if len(same_title) > 1:
                    unresolved_supplements.append(
                        {
                            "year": year,
                            "source_native_id": doi,
                            "title": title,
                            "reason": "ECVA DOI was absent from the Springer chapter-ID set and title matched multiple Springer chapters",
                            "matching_springer_ids": [str(row["source_native_id"]) for row in same_title],
                        }
                    )
                    continue
                chapter_url = str(index_row.get("chapter_url") or "")
                if not chapter_url or doi_from_url(chapter_url) != doi:
                    unresolved_supplements.append(
                        {
                            "year": year,
                            "source_native_id": doi,
                            "title": title,
                            "reason": "saved ECVA row did not retain a verifiable Springer chapter href",
                            "source_url": PAPERS_INDEX_URL,
                        }
                    )
                    continue
                try:
                    page = self.fetcher.fetch(chapter_url)
                    page_meta = dict(self.fetcher.last_meta)
                    chapter = parse_springer_chapter(page, chapter_url, year)
                    if (
                        chapter.get("doi") != doi
                        or not chapter.get("title")
                        or not chapter.get("authors")
                        or normalized_title(chapter["title"]) != normalized_title(title)
                        or doi.rsplit("_", 1)[0] not in proceedings_book_ids
                    ):
                        raise ValueError(
                            "Springer chapter page did not confirm the ECVA DOI/title/authors and ECCV proceedings book"
                        )
                    new_row = {
                        "venue_id": VENUE_ID,
                        "year": year,
                        "source_native_id": doi,
                        "source_document_type": "Springer ECCV proceedings chapter linked from the official ECVA paper index",
                        "title": chapter["title"],
                        "landing_url": chapter["landing_url"],
                        "enumeration_source_url": str(index_row.get("source_url") or PAPERS_INDEX_URL),
                        "enumeration_observed_at": str(index_row.get("observed_at") or utc_now()),
                        "enumeration_verification_url": chapter_url,
                        "enumeration_verification_observed_at": str(page_meta.get("observed_at") or utc_now()),
                    }
                    expected_rows.append(new_row)
                    expected_by_id[doi] = new_row
                    by_title.setdefault(normalized_title(chapter["title"]), []).append(new_row)
                    canonical_jsonl(expected_path, expected_rows, compress=True)
                    supplements.append(
                        {
                            "source_native_id": doi,
                            "title": chapter["title"],
                            "chapter_url": chapter_url,
                            "ecva_pdf_url": str(index_row.get("pdf_url") or ""),
                            "ecva_observed_at": str(index_row.get("observed_at") or ""),
                            "springer_chapter_observed_at": str(page_meta.get("observed_at") or ""),
                            "reason": "official ECVA index linked a main-paper chapter absent from the paginated Springer TOC; Springer chapter page verified the DOI/title/authors/year",
                        }
                    )
                except PageFetchError as exc:
                    unresolved_supplements.append(
                        {
                            "year": year,
                            "source_native_id": doi,
                            "title": title,
                            "source_url": chapter_url,
                            "http_status": exc.status,
                            "reason": str(exc),
                            "blocked": exc.blocked,
                            "observed_at": utc_now(),
                        }
                    )
                    if exc.blocked:
                        blocked = True
                        break
                except (ValueError, KeyError) as exc:
                    unresolved_supplements.append(
                        {
                            "year": year,
                            "source_native_id": doi,
                            "title": title,
                            "source_url": chapter_url,
                            "reason": str(exc),
                            "observed_at": utc_now(),
                        }
                    )
            if supplements:
                ids = sorted(str(row["source_native_id"]) for row in expected_rows)
                year_report["source_item_count"] = len(expected_rows)
                year_report["research_candidate_count"] = sum(
                    exclusion_reason(str(row.get("title") or "")) is None for row in expected_rows
                )
                year_report["source_item_set_sha256"] = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
                year_report["ecva_index_verified_supplement_count"] = len(supplements)
                year_report["ecva_index_supplements"] = supplements
                urls = set(year_report.get("source_urls", []))
                urls.add(PAPERS_INDEX_URL)
                urls.update(str(row["chapter_url"]) for row in supplements)
                year_report["source_urls"] = sorted(urls)
            year_report["ecva_index_title_doi_mismatches"] = title_doi_mismatches
            year_report["ecva_index_unresolved_supplements"] = unresolved_supplements
            enumeration.setdefault("year_reports", {})[str(year)] = year_report
            summary["years"][str(year)] = {
                "springer_expected_count_before_supplements": len(expected_rows) - len(supplements),
                "verified_supplement_count": len(supplements),
                "title_doi_mismatch_count": len(title_doi_mismatches),
                "unresolved_supplement_count": len(unresolved_supplements),
                "supplements": supplements,
                "title_doi_mismatches": title_doi_mismatches,
                "unresolved_supplements": unresolved_supplements,
            }
            if unresolved_supplements:
                unresolved_path = self.run_root / "enumeration_unresolved.jsonl"
                existing = self._read_jsonl(unresolved_path)
                existing_by_id = {str(row.get("source_native_id")): row for row in existing}
                existing_by_id.update(
                    {str(row["source_native_id"]): row for row in unresolved_supplements}
                )
                canonical_jsonl(unresolved_path, [existing_by_id[key] for key in sorted(existing_by_id)])
                if unresolved_supplements:
                    completed = set(self.state.get("completed_enumeration_years", []))
                    completed.discard(year)
                    self.state["completed_enumeration_years"] = sorted(completed)
            self._save_state()
            if blocked:
                break
        tmp = enumeration_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(enumeration, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(enumeration_path)
        out_path = self.run_root / "ecva_index_supplements.json"
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {**summary, "blocked": blocked}

    @staticmethod
    def _details_manifest_complete(
        expected_by_year: dict[int, list[dict[str, Any]]],
        staging_ids: set[str],
        exclusion_ids: set[str],
        unresolved_ids: set[str],
        blocked: bool,
    ) -> bool:
        expected_ids = {
            str(row.get("source_native_id"))
            for rows in expected_by_year.values()
            for row in rows
            if row.get("source_native_id")
        }
        accounted_ids = staging_ids | exclusion_ids
        return (
            not blocked
            and expected_ids.issubset(accounted_ids)
            and not (expected_ids & unresolved_ids)
        )

    def collect_details(self, years: tuple[int, ...]) -> dict[str, Any]:
        self.state["details_complete"] = False
        self._save_state()
        supplement_result = self._supplement_expected_from_ecva_index(years)
        if supplement_result.get("blocked"):
            self.state["detail_collection_blocked"] = True
            self.state["details_complete"] = False
            self._save_state()
            return {
                "status": "BLOCKED",
                "blocked": True,
                "ecva_index_supplements": supplement_result,
                "checkpoint": str(self.state_path),
            }
        expected_by_year: dict[int, list[dict[str, Any]]] = {}
        all_expected_by_year: dict[int, list[dict[str, Any]]] = {}
        for year in years:
            # The 2026 official search waterline is still unreconciled. Collect
            # details for observed volumes without promoting them to canonical expected.
            path = self._details_expected_path(year)
            if not path.is_file():
                continue
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                all_rows = [json.loads(line) for line in handle if line.strip()]
            all_expected_by_year[year] = all_rows
            # Virtual-only records in a reconciled union are already built
            # from official poster pages; they are not Springer chapter-detail
            # fetch targets and must not inflate the publisher crosswalk count.
            expected_by_year[year] = [
                row for row in all_rows
                if not str(row.get("source_native_id") or "").startswith("eccv-virtual-")
            ]
        partial_staging = self.run_root / "metadata_staging.partial.jsonl"
        partial_exclusions = self.run_root / "metadata_exclusions.partial.jsonl"
        staging_by_id: dict[str, dict[str, Any]] = {}
        exclusions_by_id: dict[str, dict[str, Any]] = {}
        for path, dest in ((partial_staging, staging_by_id), (partial_exclusions, exclusions_by_id)):
            if not path.is_file():
                continue
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        record = json.loads(line)
                        dest[str(record.get("source_native_id"))] = record
        unresolved: dict[str, dict[str, Any]] = {}
        unresolved_path = self.run_root / "unresolved.jsonl"
        if unresolved_path.is_file():
            with unresolved_path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        row = json.loads(line)
                        unresolved[str(row.get("source_native_id"))] = row
        expected_by_id = {
            str(row.get("source_native_id")): row
            for rows in all_expected_by_year.values()
            for row in rows
        }
        stale_exclusions: list[dict[str, Any]] = []
        for identity, exclusion in list(exclusions_by_id.items()):
            expected = expected_by_id.get(identity)
            if expected and exclusion_reason(str(expected.get("title") or "")) is None:
                stale_exclusions.append(
                    {
                        "source_native_id": identity,
                        "title": str(expected.get("title") or exclusion.get("title") or ""),
                        "prior_exclusion_reason": exclusion.get("exclusion_reason_code"),
                        "prior_exclusion_source_url": exclusion.get("source_url"),
                    }
                )
                exclusions_by_id.pop(identity, None)
        if stale_exclusions:
            canonical_jsonl(
                partial_exclusions,
                [exclusions_by_id[key] for key in sorted(exclusions_by_id)],
            )
            self.state["completed_detail_ids"] = [
                identity
                for identity in self.state.get("completed_detail_ids", [])
                if str(identity) not in {str(row["source_native_id"]) for row in stale_exclusions}
            ]
            repair_path = self.run_root / "exclusion_repairs.json"
            try:
                repair_report = json.loads(repair_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                repair_report = {"venue_id": VENUE_ID, "repairs": []}
            prior = {str(row["source_native_id"]): row for row in repair_report.get("repairs", [])}
            prior.update(
                {
                    str(row["source_native_id"]): {
                        **row,
                        "restored_as_research_candidate_at": utc_now(),
                    }
                    for row in stale_exclusions
                }
            )
            repair_report["repairs"] = [prior[key] for key in sorted(prior)]
            repair_path.write_text(json.dumps(repair_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self._write_progress(staging_by_id, exclusions_by_id, unresolved)
            self._save_state()
        ecva_crosswalk: dict[str, Any] = {}
        blocked = False
        def fetch_chapter(task: dict[str, Any]) -> dict[str, Any]:
            expected = task["expected"]
            source_url = task["source_url"]
            year = task["year"]
            identity = str(expected["source_native_id"])
            try:
                source = self.fetcher.fetch(source_url)
                page_meta = dict(self.fetcher.last_meta)
                chapter = parse_springer_chapter(source, source_url, year)
                if not chapter.get("title") or not chapter.get("authors") or not chapter.get("doi"):
                    raise PageFetchError(
                        source_url,
                        200,
                        "official chapter page lacked expected bibliographic metadata; stopped to avoid continuing through an interstitial",
                        blocked=True,
                    )
                if chapter["doi"] != identity.lower():
                    raise ValueError(f"chapter DOI {chapter['doi']} does not match expected ID {identity}")
                return {
                    **task,
                    "chapter": chapter,
                    "page_observed_at": str(page_meta.get("observed_at") or utc_now()),
                }
            except PageFetchError as exc:
                return {**task, "error": str(exc), "http_status": exc.status, "blocked": exc.blocked}
            except (ValueError, KeyError) as exc:
                return {**task, "error": str(exc), "blocked": False}
            except Exception as exc:
                return {**task, "error": f"unexpected chapter parser/network error: {exc}", "blocked": False}

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="eccv-detail") as executor:
            for year in years:
                by_doi, by_title = self._ecva_match_index(year)
                year_expected = expected_by_year.get(year, [])
                match_count = 0
                ambiguous_titles: list[str] = []
                title_doi_conflicts: list[dict[str, str]] = []
                author_mismatches: list[dict[str, str]] = []
                tasks: list[dict[str, Any]] = []
                for expected in year_expected:
                    identity = str(expected["source_native_id"])
                    title = str(expected.get("title") or "")
                    exact_title = normalized_title(title)
                    doi_rows = by_doi.get(identity.lower(), [])
                    title_matches = by_title.get(exact_title, [])
                    exact_doi_title_matches = [row for row in doi_rows if row in title_matches]
                    ecva_match: dict[str, Any] | None = None
                    # DOI/title matching selects a candidate only. Its author
                    # identity is checked against the Springer detail page
                    # before the candidate PDF URL is accepted.
                    if len(exact_doi_title_matches) == 1:
                        ecva_match = {**exact_doi_title_matches[0], "_match_method": "doi_and_title"}
                    elif len(exact_doi_title_matches) > 1:
                        ambiguous_titles.append(title)
                    else:
                        if len(title_matches) == 1:
                            row = title_matches[0]
                            ecva_match = {**row, "_match_method": "unique_title"}
                            if row.get("doi") and str(row["doi"]).lower() != identity.lower():
                                title_doi_conflicts.append(
                                    {
                                        "title": title,
                                        "springer_doi": identity,
                                        "ecva_index_doi": str(row["doi"]),
                                        "pdf_url": str(row.get("pdf_url") or ""),
                                    }
                                )
                        elif len(title_matches) > 1:
                            ambiguous_titles.append(title)
                    if identity in staging_by_id or identity in exclusions_by_id:
                        continue
                    reason = exclusion_reason(title)
                    if reason:
                        record = self._stage_exclusion(
                            year, expected, reason, expected.get("enumeration_observed_at", utc_now())
                        )
                        exclusions_by_id[identity] = record
                        self._append_record(partial_exclusions, record)
                        self.state.setdefault("completed_detail_ids", []).append(identity)
                        self._save_state()
                        continue
                    tasks.append(
                        {
                            "year": year,
                            "expected": expected,
                            "ecva_match": ecva_match,
                            "source_url": str(expected["landing_url"]),
                        }
                    )

                next_task = 0
                pending: dict[Any, dict[str, Any]] = {}

                def submit_available() -> None:
                    nonlocal next_task
                    while not blocked and next_task < len(tasks) and len(pending) < 3:
                        task = tasks[next_task]
                        next_task += 1
                        pending[executor.submit(fetch_chapter, task)] = task

                def process_future(future: Any) -> None:
                    nonlocal blocked, match_count
                    result = future.result()
                    expected = result["expected"]
                    identity = str(expected["source_native_id"])
                    title = str(expected.get("title") or "")
                    source_url = str(result["source_url"])
                    if result.get("chapter"):
                        ecva_match = result.get("ecva_match")
                        if ecva_match:
                            if ecva_author_matches(
                                result["chapter"].get("authors", []),
                                str(ecva_match.get("authors_text") or ""),
                            ):
                                match_count += 1
                            else:
                                author_mismatches.append(
                                    {
                                        "title": title,
                                        "springer_id": identity,
                                        "ecva_index_doi": str(ecva_match.get("doi") or ""),
                                        "springer_first_author": str((result["chapter"].get("authors") or [""])[0]),
                                        "ecva_first_author": str(ecva_match.get("authors_text") or "").split(",", 1)[0],
                                        "pdf_url": str(ecva_match.get("pdf_url") or ""),
                                    }
                                )
                                ecva_match = None
                        record = self._stage_record(
                            year,
                            expected,
                            result["chapter"],
                            result["page_observed_at"],
                            ecva_match,
                            source_url,
                        )
                        staging_by_id[identity] = record
                        self._append_record(partial_staging, record)
                        self.state.setdefault("completed_detail_ids", []).append(identity)
                        unresolved.pop(identity, None)
                    else:
                        unresolved[identity] = {
                            "venue_id": VENUE_ID,
                            "year": year,
                            "source_native_id": identity,
                            "title": title,
                            "source_url": source_url,
                            "http_status": result.get("http_status"),
                            "reason": result.get("error", "unknown chapter error"),
                            "blocked": bool(result.get("blocked")),
                            "observed_at": utc_now(),
                        }
                        if result.get("blocked"):
                            blocked = True
                    self._save_state()
                    if len(staging_by_id) % 20 == 0 or result.get("blocked"):
                        self._write_progress(staging_by_id, exclusions_by_id, unresolved)

                submit_available()
                while pending:
                    completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                    # Include every result that has completed by the time the
                    # wait returns before deciding whether another request can
                    # be admitted into the rolling window.
                    completed.update(future for future in pending if future.done())
                    for future in completed:
                        pending.pop(future, None)
                        process_future(future)
                    if not blocked:
                        submit_available()
                ecva_crosswalk[str(year)] = {
                    "springer_expected_count": len(year_expected),
                    "ecva_index_count": len(self.ecva_records.get(year, [])),
                    "springer_doi_or_title_matches": match_count,
                    "unmatched_springer_items": max(0, len(year_expected) - match_count),
                    "ambiguous_title_matches": ambiguous_titles,
                    "unique_title_matches_with_doi_conflict": title_doi_conflicts,
                    "author_identity_checks_deferred_to_chapter_detail": True,
                    "author_mismatches": author_mismatches,
                    "ecva_index_doi_conflicts": self._ecva_doi_conflicts(year),
                }
                if blocked:
                    break
        self._write_progress(staging_by_id, exclusions_by_id, unresolved)
        self._write_crosswalk(ecva_crosswalk)
        self.state["detail_collection_blocked"] = blocked
        self.state["details_complete"] = self._details_manifest_complete(
            all_expected_by_year,
            set(staging_by_id),
            set(exclusions_by_id),
            set(unresolved),
            blocked,
        )
        self._save_state()
        return {
            "staging_records": len(staging_by_id),
            "exclusions": len(exclusions_by_id),
            "unresolved": len(unresolved),
            "blocked": blocked,
            "ecva_crosswalk": ecva_crosswalk,
        }

    def _append_record(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()

    def _write_progress(
        self,
        staging: dict[str, dict[str, Any]],
        exclusions: dict[str, dict[str, Any]],
        unresolved: dict[str, dict[str, Any]],
    ) -> None:
        canonical_jsonl(
            self.run_root / "metadata_staging.jsonl",
            [staging[key] for key in sorted(staging)],
        )
        canonical_jsonl(
            self.run_root / "metadata_exclusions.jsonl",
            [exclusions[key] for key in sorted(exclusions)],
        )
        canonical_jsonl(
            self.run_root / "unresolved.jsonl",
            [unresolved[key] for key in sorted(unresolved)],
        )
        summary = {
            "venue_id": VENUE_ID,
            "updated_at": utc_now(),
            "staging_records": len(staging),
            "exclusions": len(exclusions),
            "unresolved": len(unresolved),
            "completed_detail_ids": len(set(staging) | set(exclusions)),
            "next_action": "rerun the same collector command to resume unresolved items",
        }
        path = self.run_root / "progress.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _write_crosswalk(self, crosswalk: dict[str, Any]) -> None:
        path = self.run_root / "ecva_index_crosswalk.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(crosswalk, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _write_virtual_site_crosswalk(
        self, expected_rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        expected_by_year: dict[int, list[dict[str, Any]]] = {}
        for row in expected_rows:
            expected_by_year.setdefault(int(row["year"]), []).append(row)
        observations: dict[str, Any] = {}
        year_reports: dict[str, Any] = {}
        for year in (2024, 2026):
            path = self.run_root / "raw" / f"eccv{year}_virtual_papers.html"
            if not path.is_file():
                year_reports[str(year)] = {
                    "source_url": f"https://eccv.ecva.net/virtual/{year}/papers.html",
                    "status": "source_page_not_saved",
                }
                continue
            raw = path.read_bytes()
            observed_at = datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).isoformat(timespec="seconds").replace("+00:00", "Z")
            source_url = f"https://eccv.ecva.net/virtual/{year}/papers.html"
            source_hash = hashlib.sha256(raw).hexdigest()
            virtual_rows = parse_virtual_papers_page(
                raw.decode("utf-8", errors="replace"), year
            )
            observations[str(year)] = {
                "source_url": source_url,
                "observed_at": observed_at,
                "sha256": source_hash,
                "bytes": len(raw),
                "visible_paper_link_count": len(virtual_rows),
                "robots_allowed_page": True,
            }
            if year == 2024:
                closure_root = self.run_root.parent / "eccv-2024-identity-closure"
                summary_path = closure_root / "identity_closure_summary_2024.json"
                receipt_path = closure_root / "identity_closure_receipt_2024.json"
                crosswalk_path = closure_root / "identity_crosswalk_2024.jsonl"
                correction_path = closure_root / "springer_correction_exclusions_2024.jsonl"
                expected_path = self.expected_root / "2024.jsonl.gz"
                index_path = self.run_root / "raw" / "papers.php.html"
                index_receipt_path = self.run_root / "raw" / "ecva_index_observation.json"
                if all(path.is_file() for path in (
                    summary_path, receipt_path, crosswalk_path, correction_path, expected_path,
                    index_path, index_receipt_path,
                )):
                    summary_bytes = summary_path.read_bytes()
                    summary = json.loads(summary_bytes)
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    crosswalk_sha = hashlib.sha256(crosswalk_path.read_bytes()).hexdigest()
                    correction_sha = hashlib.sha256(correction_path.read_bytes()).hexdigest()
                    index_receipt = json.loads(index_receipt_path.read_text(encoding="utf-8"))
                    closure_crosswalk = self._read_jsonl(crosswalk_path)
                    correction_rows = self._read_jsonl(correction_path)
                    with gzip.open(expected_path, "rt", encoding="utf-8") as handle:
                        current_expected = [json.loads(line) for line in handle if line.strip()]
                    current_exclusions = [
                        row for row in self._read_jsonl(self.run_root / "metadata_exclusions.jsonl")
                        if int(row.get("year", -1)) == 2024
                    ]
                    current_stage = [
                        row for row in self._read_jsonl(self.run_root / "metadata_staging.jsonl")
                        if int(row.get("year", -1)) == 2024
                    ]
                    selected_springer_ids = {
                        str(row["selected_springer_id"]).casefold()
                        for row in closure_crosswalk if row.get("selected_springer_id")
                    }
                    expected_springer_ids = {
                        str(row["source_native_id"]).casefold()
                        for row in current_expected
                        if row.get("source_document_type") == "Springer ECCV proceedings chapter"
                    }
                    expected_virtual_rows = [
                        row for row in current_expected
                        if row.get("source_document_type") == "ECCV official Virtual-site main-conference paper"
                    ]
                    expected_virtual_ids = {str(row["source_native_id"]) for row in expected_virtual_rows}
                    correction_ids = {
                        str(row["source_native_id"]).casefold() for row in correction_rows
                    }
                    current_exclusion_ids = {
                        str(row["source_native_id"]).casefold() for row in current_exclusions
                    }
                    staged_springer_ids = {
                        str(row["source_native_id"]).casefold()
                        for row in current_stage
                        if str(row.get("source_native_id") or "").casefold().startswith("10.")
                    }
                    stage_virtual_ids = {
                        str(row["source_native_id"]) for row in current_stage
                        if str(row.get("source_native_id") or "").startswith("eccv-virtual-")
                    }
                    selected_set_matches = (
                        len(closure_crosswalk) == 2387
                        and len(selected_springer_ids) == 2386
                        and len({str(row.get("ecva_native_id")) for row in closure_crosswalk}) == 2387
                        and len({str(row.get("virtual_native_id")) for row in closure_crosswalk}) == 2387
                        and expected_springer_ids - correction_ids == selected_springer_ids
                        and current_exclusion_ids == correction_ids
                        and staged_springer_ids == selected_springer_ids
                        and len(expected_virtual_rows) == 1
                        and len(expected_virtual_ids) == 1
                        and len(correction_ids) == 2
                        and len(current_expected) == 2389
                        and set(str(row["source_native_id"]) for row in current_expected)
                        == expected_springer_ids | expected_virtual_ids
                    )
                    no_springer_crosswalk = [
                        row for row in closure_crosswalk if not row.get("selected_springer_id")
                    ]
                    virtual_exception_matches = False
                    if len(no_springer_crosswalk) == 1 and len(expected_virtual_rows) == 1:
                        bridge = no_springer_crosswalk[0]
                        virtual_record = (bridge.get("source_evidence") or {}).get("virtual_papers_list") or {}
                        expected_virtual = expected_virtual_rows[0]
                        poster_id = str(virtual_record.get("native_id") or "")
                        virtual_exception_matches = (
                            expected_virtual["source_native_id"] == f"eccv-virtual-2024-poster-{poster_id}"
                            and normalized_title(str(expected_virtual.get("title") or ""))
                            == normalized_title(str(virtual_record.get("title") or ""))
                            and stage_virtual_ids == expected_virtual_ids
                        )
                    source_hashes = receipt.get("source_hashes", {})
                    immutable_source_hashes_match = all(
                        source_hashes.get(key) == hashlib.sha256(path.read_bytes()).hexdigest()
                        for key, path in {
                            "ecva_index_html": index_path,
                            "ecva_index_observation_receipt": index_receipt_path,
                            "virtual_papers_html": path,
                            "enumeration_work_2024": self.run_root / "enumeration_work" / "2024.jsonl.gz",
                            "springer_stage": self.run_root / "metadata_staging.jsonl",
                            "springer_exclusions": self.run_root / "metadata_exclusions.jsonl",
                            "springer_page_cache_manifest": self.run_root / "raw" / "springer" / "page_cache.json",
                            "existing_doi_identity_audit": self.run_root / "springer_ecva_identity_audit_2024.json",
                        }.items()
                    )
                    correction_output_matches = (
                        receipt.get("output_hashes", {}).get("springer_correction_exclusions_2024.jsonl")
                        == correction_sha
                    )
                    hashes_match = (
                        receipt.get("status") == "frozen_offline_identity_closure"
                        and summary.get("status") == "identity_and_scope_closed"
                        and receipt.get("output_hashes", {}).get("identity_closure_summary_2024.json")
                        == hashlib.sha256(summary_bytes).hexdigest()
                        and receipt.get("output_hashes", {}).get("identity_crosswalk_2024.jsonl")
                        == crosswalk_sha
                        and immutable_source_hashes_match
                        and correction_output_matches
                        and selected_set_matches
                        and virtual_exception_matches
                        and receipt.get("counts", {}).get("unresolved_identity_conflicts") == 0
                        and receipt.get("counts", {}).get("duplicate_selected_springer_ids") == 0
                        and receipt.get("counts", {}).get("duplicate_ecva_ids") == 0
                        and receipt.get("counts", {}).get("duplicate_virtual_ids") == 0
                    )
                    if hashes_match:
                        reconciliation = summary.get("source_set_reconciliation", {})
                        counts = summary.get("identity_crosswalk_counts", {})
                        virtual_set = reconciliation.get("virtual_listing", {})
                        springer_set = reconciliation.get("springer_proceedings_chapters", {})
                        ecva_set = reconciliation.get("ecva_index", {})
                        legacy = summary.get("previous_title_only_virtual_crosswalk", {})
                        observations[str(year)] = {
                            **observations[str(year)],
                            "source_url": virtual_set.get("source_url", source_url),
                            "observed_at": virtual_set.get("observed_at", observed_at),
                            "visible_paper_link_count": virtual_set.get("record_count", len(virtual_rows)),
                            "identity_closure_summary_path": str(summary_path),
                            "identity_closure_summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
                            "identity_closure_receipt_path": str(receipt_path),
                            "identity_closure_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                        }
                        year_reports[str(year)] = {
                            "status": "frozen_identity_closure_complete",
                            "source_url": virtual_set.get("source_url", source_url),
                            "observed_at": virtual_set.get("observed_at", observed_at),
                            "ecva_index_record_count": ecva_set.get("record_count"),
                            "springer_observed_chapter_id_count": springer_set.get("observed_enumeration_ids"),
                            "springer_chapter_id_count": springer_set.get("observed_enumeration_ids"),
                            "springer_research_chapter_id_count": springer_set.get("valid_research_chapter_ids_after_correction_exclusions"),
                            "virtual_paper_link_count": virtual_set.get("record_count"),
                            "ecva_to_virtual_exact_title_pairs": counts.get("ecva_to_virtual_exact_unique_title_matches"),
                            "ecva_to_virtual_reviewed_title_variants": counts.get("ecva_to_virtual_title_variant_reviewed"),
                            "ecva_to_springer_same_doi_exact_title_pairs": counts.get("ecva_to_springer_same_doi_and_exact_title"),
                            "ecva_to_springer_reviewed_doi_corrections": counts.get("ecva_to_springer_corrected_known_index_doi_errors"),
                            "ecva_to_springer_reviewed_title_variants": counts.get("ecva_to_springer_doi_title_variants_with_author_support"),
                            "matched_identity_pairs": counts.get("springer_research_chapter_ids_mapped"),
                            "unmatched_virtual_count": counts.get("ecva_virtual_only_no_springer_chapter_observed"),
                            "unmatched_springer_count": counts.get("springer_research_ids_missing_from_crosswalk"),
                            "ambiguous_identity_count": counts.get("unresolved_identity_conflicts"),
                            "identity_union_size": counts.get("ecva_rows"),
                            "correction_exclusion_count": summary.get("excluded_non_research_source_rows", {}).get("correction_count"),
                            "legacy_title_only_diagnostic": {
                                "exact_pairs": legacy.get("exact_pairs"),
                                "unmatched_virtual_ids": legacy.get("unmatched_virtual_ids"),
                                "unmatched_springer_and_exception_ids": legacy.get("unmatched_springer_and_exception_ids"),
                                "interpretation": legacy.get("interpretation"),
                            },
                            "identity_crosswalk_summary_path": str(summary_path),
                            "identity_crosswalk_summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
                            "identity_crosswalk_path": str(crosswalk_path),
                            "identity_crosswalk_sha256": crosswalk_sha,
                            "identity_closure_receipt_path": str(receipt_path),
                            "identity_closure_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                            "closure_expected_manifest_sha256": source_hashes.get("expected_2024"),
                            "current_expected_manifest_sha256": hashlib.sha256(expected_path.read_bytes()).hexdigest(),
                            "expected_manifest_byte_hash_changed_after_frozen_receipt": (
                                source_hashes.get("expected_2024")
                                != hashlib.sha256(expected_path.read_bytes()).hexdigest()
                            ),
                            "expected_manifest_identity_set_verified": selected_set_matches and virtual_exception_matches,
                            "expected_manifest_id_set_sha256": hashlib.sha256(
                                "\n".join(sorted(str(row["source_native_id"]) for row in current_expected)).encode("utf-8")
                            ).hexdigest(),
                            "identity_validation": counts,
                            "source_set_reconciliation": reconciliation,
                        }
                        continue
            if year == 2026:
                gap_root = self.run_root.parent / "eccv-2026-virtual-gap"
                summary_path = gap_root / "final_crosswalk_summary.json"
                freeze_path = gap_root / "final_crosswalk_freeze_receipt.json"
                crosswalk_path = gap_root / "final_identity_crosswalk.jsonl"
                virtual_only_path = gap_root / "final_virtual_only_records.jsonl"
                if all(path.is_file() for path in (summary_path, freeze_path, crosswalk_path, virtual_only_path)):
                    summary_bytes = summary_path.read_bytes()
                    summary = json.loads(summary_bytes)
                    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
                    crosswalk_sha = hashlib.sha256(crosswalk_path.read_bytes()).hexdigest()
                    virtual_only_sha = hashlib.sha256(virtual_only_path.read_bytes()).hexdigest()
                    if (
                        freeze.get("status") == "frozen"
                        and summary.get("status") == "identity_crosswalk_frozen"
                        and hashlib.sha256(summary_bytes).hexdigest() == freeze.get("summary_sha256")
                        and crosswalk_sha == freeze.get("final_identity_crosswalk_sha256")
                        and virtual_only_sha == freeze.get("final_virtual_only_records_sha256")
                        and hashlib.sha256(raw).hexdigest() == summary.get("source_sets", {}).get("virtual_source_sha256")
                    ):
                        source_sets = summary.get("source_sets", {})
                        counts = summary.get("crosswalk_counts", {})
                        validation = summary.get("global_validation", {})
                        observations[str(year)] = {
                            **observations[str(year)],
                            "source_url": source_sets.get("virtual_source", source_url),
                            "observed_at": source_sets.get("virtual_source_observed_at", observed_at),
                            "visible_paper_link_count": source_sets.get("virtual_records", len(virtual_rows)),
                        }
                        year_reports[str(year)] = {
                            "status": "frozen_identity_crosswalk_complete",
                            "source_url": source_sets.get("virtual_source", source_url),
                            "observed_at": source_sets.get("virtual_source_observed_at", observed_at),
                            "springer_observed_chapter_id_count": source_sets.get("springer_unique_ids"),
                            "springer_chapter_id_count": source_sets.get("springer_unique_ids"),
                            "virtual_paper_link_count": source_sets.get("virtual_unique_ids"),
                            "unique_title_matches": counts.get("unique_normalized_title_pairs_title_only"),
                            "matched_identity_pairs": counts.get("matched_total"),
                            "strict_ordered_author_pairs_reviewed": counts.get("strict_ordered_author_pairs_reviewed"),
                            "manual_title_author_abstract_variant_pairs": counts.get("manual_title_author_abstract_variant_pairs"),
                            "unmatched_virtual_count": counts.get("virtual_only"),
                            "unmatched_springer_count": counts.get("springer_only"),
                            "ambiguous_virtual_count": 0 if validation.get("virtual_identity_partition_complete") else None,
                            "identity_union_size": counts.get("identity_union_size"),
                            "publisher_only_ids": [validation.get("publisher_only_id")],
                            "identity_crosswalk_summary_path": str(summary_path),
                            "identity_crosswalk_summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
                            "identity_crosswalk_path": str(crosswalk_path),
                            "identity_crosswalk_sha256": crosswalk_sha,
                            "virtual_only_records_path": str(virtual_only_path),
                            "virtual_only_records_sha256": virtual_only_sha,
                            "identity_validation": validation,
                        }
                        continue
            springer_rows = expected_by_year.get(year, [])
            by_title: dict[str, list[dict[str, Any]]] = {}
            for row in springer_rows:
                by_title.setdefault(normalized_title(str(row.get("title") or "")), []).append(row)
            used_springer_ids: set[str] = set()
            matches: list[dict[str, str]] = []
            unmatched_virtual: list[dict[str, str]] = []
            ambiguous_virtual: list[dict[str, Any]] = []
            for virtual_row in virtual_rows:
                candidates = by_title.get(normalized_title(virtual_row["title"]), [])
                if len(candidates) == 1:
                    springer_row = candidates[0]
                    identity = str(springer_row["source_native_id"])
                    used_springer_ids.add(identity)
                    matches.append(
                        {
                            "virtual_native_id": virtual_row["native_id"],
                            "title": virtual_row["title"],
                            "springer_source_native_id": identity,
                            "springer_landing_url": str(springer_row["landing_url"]),
                        }
                    )
                elif len(candidates) > 1:
                    ambiguous_virtual.append(
                        {
                            **virtual_row,
                            "matching_springer_ids": [
                                str(item["source_native_id"]) for item in candidates
                            ],
                        }
                    )
                else:
                    unmatched_virtual.append(virtual_row)
            unmatched_springer = [
                {
                    "source_native_id": str(row["source_native_id"]),
                    "title": str(row["title"]),
                    "landing_url": str(row["landing_url"]),
                }
                for row in springer_rows
                if str(row["source_native_id"]) not in used_springer_ids
            ]
            year_reports[str(year)] = {
                "status": "title_crosswalk_complete",
                "source_url": source_url,
                "observed_at": observed_at,
                "springer_chapter_id_count": len(springer_rows),
                "virtual_paper_link_count": len(virtual_rows),
                "unique_title_matches": len(matches),
                "unmatched_virtual_count": len(unmatched_virtual),
                "ambiguous_virtual_count": len(ambiguous_virtual),
                "unmatched_springer_count": len(unmatched_springer),
                "matches": matches,
                "unmatched_virtual": unmatched_virtual,
                "ambiguous_virtual": ambiguous_virtual,
                "unmatched_springer": unmatched_springer,
            }
        document = {
            "venue_id": VENUE_ID,
            "observed_at": utc_now(),
            "match_method": "unique normalized title match; virtual numeric poster IDs are retained as an independent site identifier",
            "abstract_json_policy": {
                "status": "not_fetched",
                "reason": "ECCV robots.txt disallows /static, including both JSON files referenced by the allowed papers.html pages",
                "observed_references": {
                    "2024": [
                        "https://eccv.ecva.net/static/virtual/data/eccv-2024-orals-posters.json",
                        "https://eccv.ecva.net/static/virtual/data/eccv-2024-abstracts.json",
                    ],
                    "2026": [
                        "https://eccv.ecva.net/static/virtual/data/eccv-2026-orals-posters.json",
                        "https://eccv.ecva.net/static/virtual/data/eccv-2026-abstracts.json",
                    ],
                },
            },
            "observations": observations,
            "years": year_reports,
        }
        path = self.run_root / "virtual_site_crosswalk.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return document

    def _apply_virtual_poster_exceptions(self, years: tuple[int, ...]) -> dict[str, Any]:
        """Add an official Virtual-listed paper omitted by the publisher TOC.

        Only a directly observed, unique title/first-author match to the saved
        ECVA paper index is eligible. A Springer 404 leaves the DOI empty; the
        candidate DOI stays in the run-local identity audit.
        """
        if 2024 not in years:
            return {"status": "not_requested", "additions": []}
        metadata_path = self.run_root / "virtual_poster_metadata.json"
        identity_path = self.run_root / "springer_ecva_identity_audit_2024.json"
        if not metadata_path.is_file() or not identity_path.is_file():
            return {"status": "evidence_not_present", "additions": []}
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        identity_audit = json.loads(identity_path.read_text(encoding="utf-8"))
        status_by_url = {
            str(row.get("url")): row
            for row in identity_audit.get("springer_chapter_checks", [])
        }
        expected_path = self.expected_root / "2024.jsonl.gz"
        with gzip.open(expected_path, "rt", encoding="utf-8") as handle:
            expected_rows = [json.loads(line) for line in handle if line.strip()]
        expected_ids = {str(row["source_native_id"]) for row in expected_rows}
        expected_titles: dict[str, list[dict[str, Any]]] = {}
        for row in expected_rows:
            expected_titles.setdefault(normalized_title(str(row.get("title") or "")), []).append(row)
        additions: list[dict[str, Any]] = []
        for poster in metadata.get("records", []):
            poster_title = str(poster.get("title") or "")
            if not poster_title:
                continue
            poster_id = str(poster.get("poster_id") or "")
            source_id = f"eccv-virtual-2024-poster-{poster_id}" if poster_id else ""
            already_expected = source_id in expected_ids
            if normalized_title(poster_title) in expected_titles and not already_expected:
                continue
            ecva_row = poster.get("ecva_index") or {}
            candidate_doi = str(ecva_row.get("doi_candidate") or "").lower()
            chapter_url = str(ecva_row.get("chapter_url") or "")
            check = status_by_url.get(chapter_url, {})
            if not poster_id or not candidate_doi or check.get("status") != 404:
                continue
            if (
                first_author_key(str((poster.get("authors") or [""])[0]))
                != first_author_key(str(ecva_row.get("authors_text") or ""))
            ):
                continue
            poster_url = str(poster.get("poster_url") or "")
            if not poster_url.startswith(f"https://eccv.ecva.net/virtual/2024/poster/{poster_id}"):
                continue
            listing_url = str(metadata.get("source_url") or "https://eccv.ecva.net/virtual/2024/papers.html")
            observed_at = str(poster.get("poster_observed_at") or "")
            index_source_url = str((ecva_row.get("source_url") or "https://www.ecva.net/papers.php"))
            index_observed_at = str(ecva_row.get("observed_at") or "")
            doi_observed_at = str(check.get("observed_at") or "")
            if not observed_at or not index_observed_at or not doi_observed_at:
                continue
            if not already_expected:
                expected_rows.append(
                    {
                        "venue_id": VENUE_ID,
                        "year": 2024,
                        "source_native_id": source_id,
                        "source_document_type": "ECCV official Virtual-site main-conference paper",
                        "title": poster_title,
                        "landing_url": poster_url,
                        "enumeration_source_url": listing_url,
                        "enumeration_observed_at": observed_at,
                        "enumeration_verification_url": poster_url,
                        "enumeration_verification_observed_at": observed_at,
                    }
                )
                expected_ids.add(source_id)
            at = observed_at
            pdf_url = str(ecva_row.get("pdf_url") or "")
            record = {
                "schema_version": "literature-metadata-staging-v1",
                "venue_id": VENUE_ID,
                "source_native_id": source_id,
                "title": poster_title,
                "authors": list(poster.get("authors") or []),
                "year": 2024,
                "document_type": "conference-paper",
                "inclusion_decision": "include",
                "landing_url": poster_url,
                "source_url": poster_url,
                "observed_at": at,
                "abstract": str(poster.get("abstract") or "") or None,
                "publication_date": None,
                "doi": None,
                "pdf_url": pdf_url or None,
                "pdf_discovery_status": "visible_url" if pdf_url else "not_visible",
                "field_provenance": {
                    field: {"source_url": poster_url, "observed_at": at, "method": method}
                    for field, method in (
                        ("source_native_id", "official_eccv_virtual_poster_identifier"),
                        ("title", "official_eccv_virtual_poster_jsonld"),
                        ("authors", "official_eccv_virtual_poster_jsonld"),
                        ("year", "official_eccv_virtual_poster_credit_text"),
                        ("document_type", "official_eccv_virtual_poster_listing"),
                        ("landing_url", "official_eccv_virtual_poster_canonical_url"),
                        ("abstract", "official_eccv_virtual_poster_abstract_section"),
                    )
                },
                "missing_fields": {
                    "doi": {
                        "reason_code": "source_unavailable",
                        "source_url": chapter_url,
                        "http_status": 404,
                        "candidate_doi_retained_in_run_evidence": candidate_doi,
                    },
                    "publication_date": {
                        "reason_code": "source_unavailable",
                        "source_url": chapter_url,
                        "http_status": 404,
                    },
                    **({} if pdf_url else {"pdf_url": {"reason_code": "not_visible", "source_url": index_source_url}}),
                },
            }
            record["field_provenance"]["doi"] = {
                "source_url": chapter_url,
                "observed_at": doi_observed_at,
                "method": "official_springer_chapter_route_http_404_candidate_withheld",
            }
            record["field_provenance"]["publication_date"] = {
                "source_url": chapter_url,
                "observed_at": doi_observed_at,
                "method": "official_springer_chapter_route_http_404",
            }
            record["field_provenance"]["pdf_discovery_status"] = {
                "source_url": index_source_url,
                "observed_at": index_observed_at,
                "method": "official_ecva_index_exact_title_and_first_author",
            }
            if pdf_url:
                record["field_provenance"]["pdf_url"] = {
                    "source_url": index_source_url,
                    "observed_at": index_observed_at,
                    "method": "official_ecva_index_exact_title_and_first_author",
                }
            additions.append(
                {
                    "source_native_id": source_id,
                    "title": poster_title,
                    "poster_id": poster_id,
                    "poster_url": poster_url,
                    "unverified_doi_candidate": candidate_doi,
                    "chapter_url": chapter_url,
                    "chapter_http_status": 404,
                    "pdf_url": pdf_url,
                    "expected": expected_rows[-1],
                    "staging": record,
                }
            )
        if additions:
            canonical_jsonl(expected_path, sorted(expected_rows, key=lambda row: str(row["source_native_id"])), compress=True)
            candidate_dois = {str(item["unverified_doi_candidate"]).lower() for item in additions}
            enumeration_unresolved_path = self.run_root / "enumeration_unresolved.jsonl"
            enumeration_unresolved = self._read_jsonl(enumeration_unresolved_path)
            retained_unresolved = []
            resolved_exceptions = []
            for row in enumeration_unresolved:
                if str(row.get("source_native_id") or "").lower() in candidate_dois:
                    resolved_exceptions.append(
                        {
                            **row,
                            "resolution": "paper retained under verified Virtual poster identity; candidate Springer DOI withheld after chapter route returned HTTP 404",
                            "resolved_identity": next(
                                item["source_native_id"]
                                for item in additions
                                if str(item["unverified_doi_candidate"]).lower() == str(row.get("source_native_id") or "").lower()
                            ),
                        }
                    )
                else:
                    retained_unresolved.append(row)
            if resolved_exceptions:
                canonical_jsonl(enumeration_unresolved_path, retained_unresolved)
                resolved_path = self.run_root / "enumeration_resolved_exceptions.jsonl"
                prior_resolved = {
                    str(row.get("source_native_id")): row
                    for row in self._read_jsonl(resolved_path)
                }
                prior_resolved.update({str(row["source_native_id"]): row for row in resolved_exceptions})
                canonical_jsonl(resolved_path, [prior_resolved[key] for key in sorted(prior_resolved)])
            staging_by_id = {
                str(row.get("source_native_id")): row
                for path in (
                    self.run_root / "metadata_staging.partial.jsonl",
                    self.run_root / "metadata_staging.jsonl",
                )
                for row in self._read_jsonl(path)
            }
            for item in additions:
                staging_by_id[str(item["source_native_id"])] = item["staging"]
            exclusion_by_id = {
                str(row.get("source_native_id")): row
                for path in (
                    self.run_root / "metadata_exclusions.partial.jsonl",
                    self.run_root / "metadata_exclusions.jsonl",
                )
                for row in self._read_jsonl(path)
            }
            unresolved_by_id = {
                str(row.get("source_native_id")): row
                for row in self._read_jsonl(self.run_root / "unresolved.jsonl")
            }
            self._write_progress(staging_by_id, exclusion_by_id, unresolved_by_id)
            report_path = self.run_root / "enumeration_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            year_report = report.setdefault("year_reports", {}).get("2024", {})
            year_ids = sorted(str(row["source_native_id"]) for row in expected_rows)
            year_report["source_item_count"] = len(expected_rows)
            year_report["research_candidate_count"] = sum(
                exclusion_reason(str(row.get("title") or "")) is None for row in expected_rows
            )
            year_report["source_item_set_sha256"] = hashlib.sha256("\n".join(year_ids).encode("utf-8")).hexdigest()
            year_report["source_urls"] = sorted(
                set(year_report.get("source_urls", []))
                | {str(item["expected"]["enumeration_source_url"]) for item in additions}
                | {str(item["poster_url"]) for item in additions}
            )
            year_report["virtual_site_union_additions"] = [
                {key: item[key] for key in ("source_native_id", "title", "poster_url", "unverified_doi_candidate", "chapter_url", "chapter_http_status", "pdf_url")}
                for item in additions
            ]
            report["year_reports"]["2024"] = year_report
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            exception_path = self.run_root / "virtual_poster_exceptions_2024.json"
            exception_path.write_text(
                json.dumps({"venue_id": VENUE_ID, "year": 2024, "observed_at": utc_now(), "additions": [{key: item[key] for key in ("source_native_id", "title", "poster_id", "poster_url", "unverified_doi_candidate", "chapter_url", "chapter_http_status", "pdf_url")} for item in additions]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            completed = set(self.state.get("completed_enumeration_years", []))
            virtual_index_path = self.run_root / "raw" / "eccv2024_virtual_papers.html"
            if virtual_index_path.is_file():
                virtual_rows = parse_virtual_papers_page(virtual_index_path.read_text(encoding="utf-8", errors="replace"), 2024)
                candidate_rows = [row for row in expected_rows if exclusion_reason(str(row.get("title") or "")) is None]
                by_title: dict[str, list[dict[str, Any]]] = {}
                for row in candidate_rows:
                    by_title.setdefault(normalized_title(str(row.get("title") or "")), []).append(row)
                all_virtual_matched = all(len(by_title.get(normalized_title(row["title"]), [])) == 1 for row in virtual_rows)
                if all_virtual_matched and len(candidate_rows) == len(virtual_rows) == len(self.ecva_records.get(2024, [])):
                    completed.add(2024)
                else:
                    completed.discard(2024)
            self.state["completed_enumeration_years"] = sorted(completed)
            self.state["enumerated"]["2024"] = {
                key: value for key, value in year_report.items() if key != "items"
            }
            self._save_state()
        return {
            "status": "updated" if additions else "no_additions_needed",
            "additions": [
                {key: item[key] for key in ("source_native_id", "title", "poster_id", "unverified_doi_candidate", "chapter_http_status")}
                for item in additions
            ],
        }

    def reparse_cached_details(
        self,
        years: tuple[int, ...],
        report_filename: str = "cache_reparse_report.json",
    ) -> dict[str, Any]:
        """Rebuild staged publisher details from already-saved HTML only.

        This is used before finalization to apply parser fixes without making
        new requests. Original publisher URL, observation time, and response
        hash are carried forward from the page-cache receipt.
        """
        staging_path = self.run_root / "metadata_staging.jsonl"
        current_rows = self._read_jsonl(staging_path) + self._read_jsonl(
            self.run_root / "metadata_staging.partial.jsonl"
        )
        staging_by_id = {str(row.get("source_native_id")): row for row in current_rows}
        expected_by_year: dict[int, dict[str, dict[str, Any]]] = {}
        for year in years:
            expected_path = self._details_expected_path(year)
            if not expected_path.is_file():
                expected_by_year[year] = {}
                continue
            with gzip.open(expected_path, "rt", encoding="utf-8") as handle:
                expected_by_year[year] = {
                    str(row["source_native_id"]): row
                    for row in (json.loads(line) for line in handle if line.strip())
                    if not str(row.get("source_native_id") or "").startswith("eccv-virtual-")
                }

        report: dict[str, Any] = {
            "venue_id": VENUE_ID,
            "status": "complete",
            "no_network_requests": True,
            "checked_count": 0,
            "updated_count": 0,
            "field_change_counts": {
                field: 0
                for field in ("title", "authors", "abstract", "publication_date", "doi", "pdf_url", "landing_url", "pdf_discovery_status")
            },
            "field_changes": [],
            "abstract_changed_count": 0,
            "abstract_changes": [],
            "unavailable_or_invalid_cache": [],
            "observations": [],
        }
        crosswalk: dict[str, Any] = {}
        raw_root = self.raw_root.resolve()
        for year in years:
            by_doi, by_title = self._ecva_match_index(year)
            expected_rows = expected_by_year.get(year, {})
            match_count = 0
            ambiguous_titles: list[str] = []
            title_doi_conflicts: list[dict[str, str]] = []
            author_mismatches: list[dict[str, str]] = []
            for identity, previous in list(staging_by_id.items()):
                if identity not in expected_rows or identity.startswith("eccv-virtual-"):
                    continue
                expected = expected_rows[identity]
                if exclusion_reason(str(expected.get("title") or "")):
                    continue
                title_matches = by_title.get(normalized_title(str(expected.get("title") or "")), [])
                doi_rows = by_doi.get(identity.lower(), [])
                doi_title_matches = [row for row in doi_rows if row in title_matches]
                ecva_match: dict[str, Any] | None = None
                if len(doi_title_matches) == 1:
                    ecva_match = {**doi_title_matches[0], "_match_method": "doi_and_title"}
                elif len(doi_title_matches) > 1:
                    ambiguous_titles.append(str(expected.get("title") or ""))
                elif len(title_matches) == 1:
                    ecva_match = {**title_matches[0], "_match_method": "unique_title"}
                    if ecva_match.get("doi") and str(ecva_match["doi"]).lower() != identity.lower():
                        title_doi_conflicts.append(
                            {
                                "title": str(expected.get("title") or ""),
                                "springer_doi": identity,
                                "ecva_index_doi": str(ecva_match["doi"]),
                                "pdf_url": str(ecva_match.get("pdf_url") or ""),
                            }
                        )
                elif len(title_matches) > 1:
                    ambiguous_titles.append(str(expected.get("title") or ""))
                source_url = str(expected.get("landing_url") or "")
                entry = self.fetcher.cache.get(source_url)
                if not source_url or not isinstance(entry, dict) or not entry.get("path"):
                    report["unavailable_or_invalid_cache"].append(
                        {"source_native_id": identity, "source_url": source_url, "reason": "page-cache receipt is missing"}
                    )
                    continue
                cached_path = (self.raw_root / str(entry["path"])).resolve()
                if not cached_path.is_relative_to(raw_root) or not cached_path.is_file():
                    report["unavailable_or_invalid_cache"].append(
                        {"source_native_id": identity, "source_url": source_url, "reason": "cached page file is absent or outside the run cache"}
                    )
                    continue
                try:
                    with gzip.open(cached_path, "rt", encoding="utf-8", errors="replace") as handle:
                        source = handle.read()
                    chapter = parse_springer_chapter(source, source_url, year)
                except (OSError, EOFError, ValueError) as exc:
                    report["unavailable_or_invalid_cache"].append(
                        {"source_native_id": identity, "source_url": source_url, "reason": f"cached publisher page could not be parsed: {exc}"}
                    )
                    continue
                if (
                    chapter.get("doi") != identity.lower()
                    or not chapter.get("title")
                    or not chapter.get("authors")
                ):
                    report["unavailable_or_invalid_cache"].append(
                        {"source_native_id": identity, "source_url": source_url, "reason": "cached page identity/title/authors did not verify"}
                    )
                    continue
                if ecva_match:
                    if ecva_author_matches(chapter.get("authors", []), str(ecva_match.get("authors_text") or "")):
                        match_count += 1
                    else:
                        author_mismatches.append(
                            {
                                "title": str(expected.get("title") or ""),
                                "springer_id": identity,
                                "ecva_index_doi": str(ecva_match.get("doi") or ""),
                                "springer_first_author": str((chapter.get("authors") or [""])[0]),
                                "ecva_first_author": str(ecva_match.get("authors_text") or "").split(",", 1)[0],
                                "pdf_url": str(ecva_match.get("pdf_url") or ""),
                            }
                        )
                        ecva_match = None
                observed_at = str(entry.get("observed_at") or "")
                if not observed_at or str(entry.get("source_url") or source_url) != source_url:
                    report["unavailable_or_invalid_cache"].append(
                        {"source_native_id": identity, "source_url": source_url, "reason": "page-cache receipt lacks the original URL or observation time"}
                    )
                    continue
                updated = self._stage_record(year, expected, chapter, observed_at, ecva_match, source_url)
                report["checked_count"] += 1
                changed_fields = [
                    field
                    for field in report["field_change_counts"]
                    if previous.get(field) != updated.get(field)
                ]
                for field in changed_fields:
                    report["field_change_counts"][field] += 1
                if changed_fields:
                    report["field_changes"].append(
                        {"source_native_id": identity, "title": updated.get("title"), "fields_changed": changed_fields}
                    )
                if previous.get("abstract") != updated.get("abstract"):
                    report["abstract_changed_count"] += 1
                    report["abstract_changes"].append(
                        {
                            "source_native_id": identity,
                            "title": updated.get("title"),
                            "source_url": source_url,
                            "observed_at": observed_at,
                            "old_abstract_sha256": hashlib.sha256(str(previous.get("abstract") or "").encode("utf-8")).hexdigest(),
                            "new_abstract_sha256": hashlib.sha256(str(updated.get("abstract") or "").encode("utf-8")).hexdigest(),
                        }
                    )
                staging_by_id[identity] = updated
                report["updated_count"] += 1
                report["observations"].append(
                    {
                        "source_native_id": identity,
                        "source_url": source_url,
                        "observed_at": observed_at,
                        "sha256": str(entry.get("sha256") or ""),
                    }
                )
            research_candidate_count = sum(
                exclusion_reason(str(row.get("title") or "")) is None
                for row in expected_rows.values()
            )
            crosswalk[str(year)] = {
                "springer_expected_count": len(expected_rows),
                "springer_research_candidate_count": research_candidate_count,
                "ecva_index_count": len(self.ecva_records.get(year, [])),
                "springer_doi_or_title_matches": match_count,
                "unmatched_springer_items": max(0, research_candidate_count - match_count),
                "ambiguous_title_matches": ambiguous_titles,
                "unique_title_matches_with_doi_conflict": title_doi_conflicts,
                "author_identity_checks_deferred_to_chapter_detail": False,
                "author_mismatches": author_mismatches,
                "ecva_index_doi_conflicts": self._ecva_doi_conflicts(year),
            }
        if report["unavailable_or_invalid_cache"]:
            report["status"] = "partial"
        canonical_jsonl(
            self.run_root / "metadata_staging.partial.jsonl",
            [staging_by_id[key] for key in sorted(staging_by_id)],
        )
        exclusions_by_id = {
            str(row.get("source_native_id")): row
            for path in (self.run_root / "metadata_exclusions.partial.jsonl", self.run_root / "metadata_exclusions.jsonl")
            for row in self._read_jsonl(path)
        }
        unresolved_by_id = {
            str(row.get("source_native_id")): row
            for row in self._read_jsonl(self.run_root / "unresolved.jsonl")
        }
        self._write_progress(staging_by_id, exclusions_by_id, unresolved_by_id)
        self._write_crosswalk(crosswalk)
        report["ecva_crosswalk"] = crosswalk
        report_path = self.run_root / report_filename
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report

    def finalize(self, years: tuple[int, ...]) -> dict[str, Any]:
        cache_reparse = self.reparse_cached_details(years)
        virtual_exceptions = self._apply_virtual_poster_exceptions(years)
        expected_rows: list[dict[str, Any]] = []
        expected_ids: list[str] = []
        for year in years:
            path = self.expected_root / f"{year}.jsonl.gz"
            if not path.is_file():
                continue
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        row = json.loads(line)
                        expected_rows.append(row)
                        expected_ids.append(row["source_native_id"])
        if len(expected_ids) != len(set(expected_ids)):
            duplicates = sorted({value for value in expected_ids if expected_ids.count(value) > 1})
            raise ValueError(f"duplicate expected source identities: {duplicates[:20]}")
        staging_path = self.run_root / "metadata_staging.jsonl"
        exclusion_path = self.run_root / "metadata_exclusions.jsonl"
        requested_years = set(years)
        staging = [
            row for row in self._read_jsonl(staging_path)
            if int(row.get("year", -1)) in requested_years
        ]
        exclusions = [
            row for row in self._read_jsonl(exclusion_path)
            if int(row.get("year", -1)) in requested_years
        ]
        expected_set = set(expected_ids)
        observed_set = {
            str(row.get("source_native_id"))
            for row in staging + exclusions
            if row.get("source_native_id")
        }
        missing = sorted(expected_set - observed_set)
        extra = sorted(observed_set - expected_set)
        enum = json.loads((self.run_root / "enumeration_report.json").read_text(encoding="utf-8"))
        virtual_crosswalk = self._write_virtual_site_crosswalk(expected_rows)
        enum_year_reports = enum.setdefault("year_reports", {})
        closed_years: set[int] = set()
        for year in years:
            year_rows = [row for row in expected_rows if int(row["year"]) == year]
            year_ids = sorted(str(row["source_native_id"]) for row in year_rows)
            excluded_count = sum(
                exclusion_reason(str(row.get("title") or "")) is not None
                for row in year_rows
            )
            year_report = enum_year_reports.setdefault(str(year), {"year": year})
            year_report["source_item_count"] = len(year_rows)
            year_report["research_candidate_count"] = len(year_rows) - excluded_count
            year_report["source_item_set_sha256"] = hashlib.sha256(
                "\n".join(year_ids).encode("utf-8")
            ).hexdigest()
            closure = virtual_crosswalk.get("years", {}).get(str(year), {})
            if closure.get("status") in {
                "frozen_identity_closure_complete",
                "frozen_identity_crosswalk_complete",
            }:
                closed_years.add(year)
                year_report["scope_closure_status"] = "complete_by_frozen_official_identity_reconciliation"
                year_report["scope_closure_evidence"] = closure
        if closed_years:
            completed = set(self.state.get("completed_enumeration_years", [])) | closed_years
            self.state["completed_enumeration_years"] = sorted(completed)
            enumerated = self.state.setdefault("enumerated", {})
            for year in closed_years:
                enumerated[str(year)] = {
                    **enumerated.get(str(year), {}),
                    "year": year,
                    "status": "complete_by_frozen_official_identity_reconciliation",
                    "scope_closure_evidence": virtual_crosswalk["years"][str(year)],
                }
            self._save_state()
        enum["final_scope_closure_observed_at"] = utc_now()
        enum["year_reports"] = enum_year_reports
        enum_path = self.run_root / "enumeration_report.json"
        enum_path.write_text(
            json.dumps(enum, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        source_urls = {SERIES_URL}
        source_urls.update(
            url for row in expected_rows
            for url in (row.get("enumeration_source_url"), row.get("book_url"))
            if url
        )
        if self.ecva_records:
            source_urls.add(PAPERS_INDEX_URL)
        source_urls.update(
            str(item["source_url"])
            for item in virtual_crosswalk.get("observations", {}).values()
            if item.get("source_url")
        )
        count_differences: dict[str, Any] = {}
        for year, report in enum["year_reports"].items():
            year_crosswalk = virtual_crosswalk.get("years", {}).get(year, {})
            ecva_count = len(self.ecva_records.get(int(year), []))
            observed_count = report.get("source_item_count")
            candidate_count = report.get("research_candidate_count")
            search_count = report.get("springer_search_total_results")
            search_book_count = report.get("springer_search_matching_books")
            virtual_count = year_crosswalk.get("virtual_paper_link_count")
            if year_crosswalk.get("status") in {
                "frozen_identity_closure_complete",
                "frozen_identity_crosswalk_complete",
            }:
                count_differences[year] = {
                    "status": "reconciled_by_frozen_official_identity_crosswalk",
                    "springer_series_reported_papers": report.get("conference_series_papers"),
                    "springer_observed_chapter_ids": year_crosswalk.get(
                        "springer_observed_chapter_id_count",
                        year_crosswalk.get("springer_chapter_id_count", observed_count),
                    ),
                    "springer_research_candidates_after_explicit_exclusions": year_crosswalk.get(
                        "springer_research_chapter_id_count", candidate_count
                    ),
                    "expected_union_source_items_including_exclusions": observed_count,
                    "expected_research_candidates_including_virtual_only": candidate_count,
                    "springer_search_reported_book_count": search_count,
                    "springer_search_matched_main_book_count": search_book_count,
                    "saved_ecva_papers_index_count": ecva_count or None,
                    "virtual_site_papers_page_count": virtual_count,
                    "identity_union_size": year_crosswalk.get("identity_union_size"),
                    "virtual_only_count": year_crosswalk.get("unmatched_virtual_count"),
                    "publisher_only_count": year_crosswalk.get("unmatched_springer_count"),
                    "correction_exclusion_count": year_crosswalk.get("correction_exclusion_count"),
                    "identity_crosswalk_path": year_crosswalk.get("identity_crosswalk_path"),
                    "identity_crosswalk_sha256": year_crosswalk.get("identity_crosswalk_sha256"),
                    "identity_crosswalk_summary_path": year_crosswalk.get("identity_crosswalk_summary_path"),
                    "identity_crosswalk_summary_sha256": year_crosswalk.get("identity_crosswalk_summary_sha256"),
                }
            elif (
                report.get("conference_series_papers") != candidate_count
                or report.get("conference_series_papers") != observed_count
                or (ecva_count and ecva_count != candidate_count)
                or (search_count is not None and search_count != search_book_count)
                or (virtual_count is not None and virtual_count != candidate_count)
            ):
                count_differences[year] = {
                    "springer_series_reported_papers": report.get("conference_series_papers"),
                    "springer_observed_chapter_ids": observed_count,
                    "springer_research_candidates_after_explicit_exclusions": candidate_count,
                    "springer_search_reported_book_count": search_count,
                    "springer_search_matched_main_book_count": search_book_count,
                    "saved_ecva_papers_index_count": ecva_count or None,
                    "virtual_site_papers_page_count": virtual_count,
                    "virtual_site_unique_title_matches": year_crosswalk.get("unique_title_matches"),
                    "virtual_site_unmatched_titles": year_crosswalk.get("unmatched_virtual_count"),
                    "virtual_site_ambiguous_titles": year_crosswalk.get("ambiguous_virtual_count"),
                    "virtual_site_unmatched_springer_ids": year_crosswalk.get("unmatched_springer_count"),
                }
        observed_at = utc_now()
        evidence = {
            "venue_id": VENUE_ID,
            "scope_years": list(years),
            "status": "PASS" if not missing and not extra else "UPDATED",
            "drift_status": "WITHIN_THRESHOLD" if count_differences else "NO_DRIFT",
            "enumeration_complete": bool(
                all(year in self.state.get("completed_enumeration_years", []) for year in years)
            ),
            "observed_at": observed_at,
            "source_urls": sorted(source_urls),
            "source_item_set_sha256": hashlib.sha256(
                "\n".join(sorted(expected_set)).encode("utf-8")
            ).hexdigest(),
            "source_item_set_serialization": "UTF-8, unique source_native_id values sorted lexicographically and joined by LF with no trailing LF",
            "current_source_item_count": len(expected_set),
            "new_ids": sorted(expected_set),
            "missing_ids": missing,
            "cross_source_count_differences": count_differences,
        }
        evidence_path = self.run_root / "waterline_evidence.json"
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        expected_summary = {
            "venue_id": VENUE_ID,
            "scope_years": list(years),
            "observed_at": observed_at,
            "years": {},
            "total_expected_source_items": len(expected_set),
            "total_expected_research_candidates": sum(
                exclusion_reason(row["title"]) is None for row in expected_rows
            ),
            "total_staged": len(staging),
            "total_excluded": len(exclusions),
            "total_unresolved": len(missing),
            "missing_ids": missing,
            "extra_ids": extra,
            "duplicate_ids": [],
            "source_count_differences": count_differences,
            "virtual_site_crosswalk": str(self.run_root / "virtual_site_crosswalk.json"),
            "cache_reparse_report": str(self.run_root / "cache_reparse_report.json"),
            "cache_reparse_updated_count": cache_reparse["updated_count"],
            "cache_reparse_abstract_changed_count": cache_reparse["abstract_changed_count"],
        }
        for year in years:
            rows = [row for row in expected_rows if int(row["year"]) == year]
            staged_year = [row for row in staging if int(row["year"]) == year]
            excluded_year = [row for row in exclusions if int(row["year"]) == year]
            expected_summary["years"][str(year)] = {
                "expected_source_items": len(rows),
                "research_candidates": sum(exclusion_reason(row["title"]) is None for row in rows),
                "staged": len(staged_year),
                "excluded": len(excluded_year),
                "unresolved": sum(row["source_native_id"] in missing for row in rows),
                "title_coverage": sum(bool(row.get("title")) for row in staged_year) / len(staged_year) if staged_year else 0.0,
                "author_coverage": sum(bool(row.get("authors")) for row in staged_year) / len(staged_year) if staged_year else 0.0,
                "abstract_coverage": sum(bool(row.get("abstract")) for row in staged_year) / len(staged_year) if staged_year else 0.0,
                "doi_coverage": sum(bool(row.get("doi")) for row in staged_year) / len(staged_year) if staged_year else 0.0,
                "landing_url_coverage": sum(bool(row.get("landing_url")) for row in staged_year) / len(staged_year) if staged_year else 0.0,
                "pdf_url_coverage": sum(bool(row.get("pdf_url")) for row in staged_year) / len(staged_year) if staged_year else 0.0,
            }
        path = self.run_root / "run_statistics.json"
        path.write_text(json.dumps(expected_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        reconciliation = {
            "venue_id": VENUE_ID,
            "observed_at": observed_at,
            "years": count_differences,
            "virtual_site_page_observations": virtual_crosswalk.get("observations", {}),
            "abstract_json_policy": virtual_crosswalk.get("abstract_json_policy"),
        }
        (self.run_root / "source_count_reconciliation.json").write_text(
            json.dumps(reconciliation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"waterline": evidence, "statistics": expected_summary, "cache_reparse": cache_reparse, "virtual_exceptions": virtual_exceptions}

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        rows = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return rows


def parse_years(value: str) -> tuple[int, ...]:
    if value.casefold() == "all":
        return YEARS
    years = tuple(sorted({int(part.strip()) for part in value.split(",") if part.strip()}))
    invalid = set(years) - set(YEARS)
    if invalid:
        raise argparse.ArgumentTypeError(f"unsupported ECCV years: {sorted(invalid)}")
    return years


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, help="database home (defaults to LITDB_HOME, then the bundled data directory)")
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--years", type=parse_years, default=YEARS)
    parser.add_argument("--phase", choices=("all", "enumerate", "details", "reparse", "finalize"), default="all")
    parser.add_argument("--interval", type=float, default=0.75, help="minimum seconds between new official requests")
    parser.add_argument("--refresh", action="store_true", help="refetch cached pages; use only for deliberate source re-observation")
    parser.add_argument(
        "--ecva-index",
        type=Path,
        help="parse an already-saved ECVA index locally without requesting ECVA; defaults to <run-root>/raw/papers.php.html",
    )
    args = parser.parse_args(argv)
    selected_home = args.home if args.home is not None else Path(os.environ.get("LITDB_HOME") or DEFAULT_HOME)
    home = selected_home.expanduser().resolve()
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root
        else home / "runs" / RUN_ID / VENUE_ID
    )
    ecva_index = (
        args.ecva_index.expanduser().resolve()
        if args.ecva_index is not None
        else run_root / "raw" / "papers.php.html"
    )
    collector = ECCVCollector(
        home,
        run_root,
        interval=args.interval,
        refresh=args.refresh,
        ecva_index=ecva_index,
    )
    try:
        if args.phase in {"all", "enumerate"}:
            result = collector.enumerate(args.years)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if args.phase in {"all", "details"}:
            result = collector.collect_details(args.years)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if args.phase == "reparse":
            partial_manifest = 2026 in args.years and not (run_root / "expected" / "2026.jsonl.gz").is_file()
            report_name = "cache_reparse_report.partial.json" if partial_manifest else "cache_reparse_report.json"
            result = collector.reparse_cached_details(args.years, report_filename=report_name)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if args.phase in {"all", "finalize"}:
            result = collector.finalize(args.years)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    except PageFetchError as exc:
        collector.state.setdefault("page_failures", []).append(
            {
                "url": exc.url,
                "http_status": exc.status,
                "reason": str(exc),
                "blocked": exc.blocked,
                "observed_at": utc_now(),
            }
        )
        collector.state["collection_blocked"] = exc.blocked
        collector._save_state()
        print(
            json.dumps(
                {
                    "status": "BLOCKED" if exc.blocked else "INCOMPLETE",
                    "url": exc.url,
                    "http_status": exc.status,
                    "reason": str(exc),
                    "checkpoint": str(collector.state_path),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2 if exc.blocked else 1
    except Exception as exc:
        collector.state.setdefault("fatal_errors", []).append(
            {"reason": str(exc), "observed_at": utc_now()}
        )
        collector._save_state()
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

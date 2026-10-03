from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit


DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
ACM_NATIVE_RE = re.compile(r"^10\.\d{4,9}/(\d+)$", re.IGNORECASE)


def normalize_doi(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^https?://dl\.acm\.org/doi/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE).rstrip(".,;:")
    return text if DOI_RE.fullmatch(text) else None


def official_acm_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and parsed.hostname == "dl.acm.org" and not parsed.username and not parsed.password


def title_signature(value: object) -> str:
    return " ".join(re.findall(r"[\w]+", str(value or "").casefold(), flags=re.UNICODE))


def source_native_id(doi: str) -> str:
    match = ACM_NATIVE_RE.fullmatch(doi)
    if not match:
        raise ValueError(f"ACM DOI does not expose the verified numeric native identity: {doi}")
    return match.group(1)


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row is not an object at {path}:{line_no}")
            rows.append(row)
    if not rows:
        raise ValueError(f"listing manifest is empty: {path}")
    return rows


def row_priority(row: dict) -> tuple[int, int, int]:
    source_page = str(row.get("source_page_url") or "")
    issue_priority = 0 if source_page.rstrip("/").endswith("/justaccepted") else 1
    author_count = len(row.get("authors") or []) if isinstance(row.get("authors"), list) else 0
    teaser = int(bool(row.get("abstract_teaser")))
    return issue_priority, author_count, teaser


def build_expected(rows: list[dict], venue_id: str, journal_code: str, request_id: str | None) -> tuple[list[dict], dict]:
    chosen: dict[str, dict] = {}
    observation_keys: set[tuple[str, str]] = set()
    type_counts: Counter[str] = Counter()
    overlap_dois: Counter[str] = Counter()
    for index, raw in enumerate(rows, 1):
        if raw.get("venue_id") != venue_id:
            raise ValueError(f"listing row {index} venue mismatch")
        if str(raw.get("journal_code") or "").casefold() != journal_code.casefold():
            raise ValueError(f"listing row {index} journal code mismatch")
        doi = normalize_doi(raw.get("doi") or raw.get("landing_url"))
        if not doi:
            raise ValueError(f"listing row {index} lacks a valid DOI")
        title = str(raw.get("title") or "").strip()
        if not title:
            raise ValueError(f"listing row {index} lacks a title")
        source_page = raw.get("source_page_url")
        landing_url = raw.get("landing_url")
        if not official_acm_url(source_page) or not official_acm_url(landing_url):
            raise ValueError(f"listing row {index} contains a non-official URL")
        try:
            year = int(raw.get("year") or raw.get("publication_year"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"listing row {index} has an invalid year") from exc
        if year < 2015:
            raise ValueError(f"listing row {index} is outside the 2015+ scope")
        document_type = str(raw.get("document_type") or raw.get("article_type") or "").strip()
        if not document_type:
            raise ValueError(f"listing row {index} lacks an article type")
        key = (str(source_page), doi)
        if key in observation_keys:
            raise ValueError(f"duplicate listing observation: {source_page} / {doi}")
        observation_keys.add(key)
        type_counts[document_type] += 1
        overlap_dois[doi] += 1
        candidate = dict(raw)
        candidate["doi"] = doi
        candidate["year"] = year
        previous = chosen.get(doi)
        if previous and title_signature(previous.get("title")) != title_signature(title):
            raise ValueError(f"DOI title conflict across listing surfaces: {doi}")
        if previous is None or row_priority(candidate) > row_priority(previous):
            chosen[doi] = candidate

    expected: list[dict] = []
    for doi, row in sorted(chosen.items(), key=lambda item: (item[1]["year"], source_native_id(item[0]))):
        source_page = str(row["source_page_url"])
        early_access = source_page.rstrip("/").endswith("/justaccepted")
        identity = source_native_id(doi)
        expected.append({
            "schema_version": "literature-expected-source-item-v1",
            "artifact_kind": "expected_listing_identity_not_catalog_metadata",
            "catalog_ready": False,
            "venue_id": venue_id,
            "journal_code": journal_code,
            "source_native_id": identity,
            "doi": doi,
            "title": row["title"],
            "authors": row.get("authors") or [],
            "year": int(row["year"]),
            "publication_date": row.get("publication_date"),
            "document_type": row.get("document_type") or row.get("article_type"),
            "landing_url": f"https://dl.acm.org/doi/{doi}",
            "source_url": f"https://dl.acm.org/doi/{doi}",
            "source_page_url": source_page,
            "selected_unit_id": source_page,
            "enumeration_kind": "early_access" if early_access else "issue",
            "baseline_enumeration_kind": "early_access" if early_access else "issue",
            "source_document_type": "ACM Just Accepted visible listing" if early_access else "ACM issue TOC visible listing",
            "source_grade": "B",
            "request_id": request_id,
            "observed_at": row.get("observed_at"),
            "listing_evidence": {
                "volume": row.get("volume"),
                "issue": row.get("issue"),
                "article_number": row.get("article_number"),
                "pages": row.get("pages"),
                "abstract_status": row.get("abstract_status"),
                "ereader_url": row.get("ereader_url"),
                "abstract_url": row.get("abstract_url"),
                "listing_surface_count_for_doi": overlap_dois[doi],
            },
        })

    year_counts = Counter(row["year"] for row in expected)
    summary = {
        "schema_version": "acm-expected-manifest-build-v1",
        "status": "PASS",
        "venue_id": venue_id,
        "journal_code": journal_code,
        "request_id": request_id,
        "listing_observation_count": len(rows),
        "unique_observation_count": len(observation_keys),
        "canonical_expected_identity_count": len(expected),
        "cross_surface_overlap_observation_count": len(rows) - len(expected),
        "cross_surface_overlap_doi_count": sum(1 for count in overlap_dois.values() if count > 1),
        "year_counts": {str(year): year_counts[year] for year in sorted(year_counts)},
        "listing_type_counts": dict(sorted(type_counts.items())),
        "source_item_set_sha256": hashlib.sha256("\n".join(sorted(row["source_native_id"] for row in expected)).encode()).hexdigest(),
        "doi_set_sha256": hashlib.sha256("\n".join(sorted(row["doi"] for row in expected)).encode()).hexdigest(),
        "catalog_ready": False,
    }
    return expected, summary


def write_outputs(rows: list[dict], summary: dict, output_root: Path, summary_path: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    existing = sorted(output_root.glob("*.jsonl")) + sorted(output_root.glob("*.jsonl.gz"))
    if existing:
        raise ValueError(f"expected output root is not empty: {[str(path) for path in existing]}")
    by_year: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_year[int(row["year"])].append(row)
    for year, year_rows in sorted(by_year.items()):
        target = output_root / f"{year}.jsonl"
        temporary = target.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in sorted(year_rows, key=lambda item: item["source_native_id"]):
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        temporary.replace(target)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_summary = summary_path.with_suffix(summary_path.suffix + ".tmp")
    temporary_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary_summary.replace(summary_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ACM expected identity manifests from visible listing observations")
    parser.add_argument("--listing", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--venue-id", required=True)
    parser.add_argument("--journal-code", required=True)
    parser.add_argument("--request-id")
    args = parser.parse_args()
    listing_rows = read_jsonl(args.listing.expanduser().resolve())
    expected, summary = build_expected(listing_rows, args.venue_id, args.journal_code, args.request_id)
    write_outputs(expected, summary, args.output_root.expanduser().resolve(), args.summary.expanduser().resolve())
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

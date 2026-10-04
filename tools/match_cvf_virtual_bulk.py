#!/usr/bin/env python3
"""Offline exact matcher for a saved CVF virtual-site JSON and expected manifests.

This tool performs no network access. It prefers exact official OpenAccess URL
matches and otherwise requires an exact, unique normalized title plus ordered
authors key on both sides. It never does fuzzy matching or changes staging.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def normalize_piece(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalized.split())


def author_names(record: dict[str, Any]) -> list[str]:
    values = record.get("authors")
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for author in values:
        if isinstance(author, str):
            name = author
        elif isinstance(author, dict):
            name = author.get("fullname") or author.get("full_name") or author.get("name") or ""
            if not name and (author.get("first") or author.get("last")):
                name = " ".join(str(author.get(part) or "").strip() for part in ("first", "last") if author.get(part))
        else:
            name = ""
        result.append(str(name).strip())
    return result


def title_author_key(title: Any, authors: list[str]) -> tuple[str, tuple[str, ...]] | None:
    normalized_title = normalize_piece(title)
    normalized_authors = tuple(normalize_piece(author) for author in authors)
    if not normalized_title or not normalized_authors or any(not name for name in normalized_authors):
        return None
    return normalized_title, normalized_authors


def official_urls(row: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for key in ("landing_url", "pdf_url", "paper_url", "paper_pdf_url", "sourceurl", "source_url"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if value.startswith("https://openaccess.thecvf.com/"):
            urls.add(value.split("#", 1)[0])
    return urls


def load_json_rows(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return rows, {"shape": "list", "declared_count": len(payload), "next": None}
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        rows = [row for row in payload["results"] if isinstance(row, dict)]
        declared_count = payload.get("count")
        next_page = payload.get("next")
        previous_page = payload.get("previous")
        if next_page not in (None, "") or previous_page not in (None, ""):
            raise ValueError(
                f"JSON results payload is paginated (previous={previous_page!r}, next={next_page!r}); "
                "save a complete official payload before matching"
            )
        if declared_count is not None:
            if isinstance(declared_count, bool) or not isinstance(declared_count, int):
                raise ValueError("JSON results payload count must be an integer when declared")
            if declared_count != len(rows):
                raise ValueError(f"JSON results payload is incomplete: declared count {declared_count}, saved rows {len(rows)}")
        return rows, {"shape": "results_object", "declared_count": declared_count, "next": next_page, "previous": previous_page, "complete_payload": True}
    raise ValueError("JSON must be a list or an object with a results list")


def load_expected(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def analyze_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys: list[tuple[str, tuple[str, ...]] | None] = []
    sourceids: list[str | None] = []
    event_types: Counter[str] = Counter()
    for row in rows:
        keys.append(title_author_key(row.get("name") or row.get("title"), author_names(row)))
        value = row.get("sourceid")
        sourceids.append(str(value) if value is not None else None)
        event_types[str(row.get("decision") or row.get("eventtype") or row.get("event_type") or "unknown")] += 1
    key_counts = Counter(key for key in keys if key is not None)
    source_counts = Counter(sourceid for sourceid in sourceids if sourceid is not None)
    return {
        "record_count": len(rows),
        "records_with_abstract": sum(bool(row.get("abstract")) for row in rows),
        "records_without_abstract": sum(not bool(row.get("abstract")) for row in rows),
        "records_with_complete_title_authors": sum(key is not None for key in keys),
        "distinct_sourceid_count": len(source_counts),
        "duplicate_sourceid_count": sum(count > 1 for count in source_counts.values()),
        "duplicate_sourceid_event_count": sum(count - 1 for count in source_counts.values() if count > 1),
        "distinct_normalized_title_author_key_count": len(key_counts),
        "duplicate_title_author_key_count": sum(count > 1 for count in key_counts.values()),
        "duplicate_title_author_event_count": sum(count - 1 for count in key_counts.values() if count > 1),
        "event_type_counts": dict(sorted(event_types.items())),
    }


def match_rows(rows: list[dict[str, Any]], expected_rows: list[dict[str, Any]], source_provenance: dict[str, Any]) -> dict[str, Any]:
    expected_by_url: dict[str, list[dict[str, Any]]] = {}
    expected_by_key: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    expected_by_title: dict[str, list[dict[str, Any]]] = {}
    for row in expected_rows:
        for url in (row.get("landing_url"), row.get("pdf_url")):
            if isinstance(url, str) and url:
                expected_by_url.setdefault(url.split("#", 1)[0], []).append(row)
        key = title_author_key(row.get("title"), row.get("authors", []))
        if key:
            expected_by_key.setdefault(key, []).append(row)
            expected_by_title.setdefault(key[0], []).append(row)

    virtual_by_key: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    no_key: list[dict[str, Any]] = []
    for row in rows:
        key = title_author_key(row.get("name") or row.get("title"), author_names(row))
        if key is None:
            no_key.append(row)
        else:
            virtual_by_key.setdefault(key, []).append(row)

    matched: list[dict[str, Any]] = []
    used_expected: set[str] = set()
    consumed_keys: set[tuple[str, tuple[str, ...]]] = set()
    ambiguous: list[dict[str, Any]] = []
    virtual_groups_by_title: dict[str, list[dict[str, Any]]] = {}
    for key, source_rows in virtual_by_key.items():
        # Same paper may appear as multiple oral/poster/highlight cards. Treat
        # them as one source paper only when its source ID and abstract agree.
        sourceids = {str(row.get("sourceid")) if row.get("sourceid") is not None else None for row in source_rows}
        abstracts = {normalize_piece(row.get("abstract")) for row in source_rows}
        safe_duplicate_group = len(sourceids) == 1 and len(abstracts) == 1
        virtual_groups_by_title.setdefault(key[0], []).append({"key": key, "rows": source_rows, "safe_duplicate_group": safe_duplicate_group})
        url_targets: dict[str, dict[str, Any]] = {}
        url_target_sources: dict[str, list[dict[str, Any]]] = {}
        for row in source_rows:
            for url in official_urls(row):
                for expected in expected_by_url.get(url, []):
                    expected_id = str(expected["source_native_id"])
                    url_targets[expected_id] = expected
                    url_target_sources.setdefault(expected_id, []).append(row)
        if not safe_duplicate_group:
            if url_targets or expected_by_key.get(key):
                ambiguous.append({
                    "title": source_rows[0].get("name"),
                    "sourceid": source_rows[0].get("sourceid"),
                    "reason": "conflicting_duplicate_virtual_records",
                    "expected_ids": sorted(url_targets) or sorted(str(row["source_native_id"]) for row in expected_by_key.get(key, [])),
                })
            continue
        method: str | None = None
        expected: dict[str, Any] | None = None
        if len(url_targets) == 1:
            method = "exact_official_url"
            expected = next(iter(url_targets.values()))
        elif len(url_targets) > 1:
            ambiguous.append({"title": source_rows[0].get("name"), "sourceid": source_rows[0].get("sourceid"), "reason": "official_url_maps_to_multiple_expected_records", "expected_ids": sorted(url_targets)})
            continue
        elif safe_duplicate_group and len(expected_by_key.get(key, [])) == 1:
            method = "normalized_unique_title_ordered_authors"
            expected = expected_by_key[key][0]
        else:
            continue
        expected_id = str(expected["source_native_id"])
        if expected_id in used_expected:
            ambiguous.append({"title": source_rows[0].get("name"), "sourceid": source_rows[0].get("sourceid"), "reason": "multiple_virtual_groups_map_to_one_expected_record", "expected_id": expected_id})
            continue
        used_expected.add(expected_id)
        consumed_keys.add(key)
        representative = url_target_sources[expected_id][0] if method == "exact_official_url" else source_rows[0]
        matched.append({
            "venue_id": expected.get("venue_id"),
            "year": expected.get("year"),
            "expected_source_native_id": expected_id,
            "match_method": method,
            "expected_title": expected.get("title"),
            "virtual_title": representative.get("name") or representative.get("title"),
            "expected_authors": expected.get("authors", []),
            "virtual_authors": author_names(representative),
            "abstract": representative.get("abstract"),
            "sourceid": representative.get("sourceid"),
            "virtualsite_urls": sorted({str(row.get("virtualsite_url")) for row in source_rows if row.get("virtualsite_url")}),
            "source_urls": sorted({url for row in source_rows for url in official_urls(row)}),
            "event_decisions": sorted({str(row.get("decision") or row.get("eventtype") or row.get("event_type") or "") for row in source_rows}),
            "source_event_count": len(source_rows),
            **source_provenance,
        })

    unmatched_expected = [row for row in expected_rows if str(row.get("source_native_id")) not in used_expected]
    title_author_mismatches: list[dict[str, Any]] = []
    unmatched_expected_without_exact_title: list[str] = []
    for expected in unmatched_expected:
        expected_key = title_author_key(expected.get("title"), expected.get("authors", []))
        if expected_key is None:
            continue
        expected_same_title = expected_by_title.get(expected_key[0], [])
        virtual_same_title = virtual_groups_by_title.get(expected_key[0], [])
        if not virtual_same_title:
            unmatched_expected_without_exact_title.append(str(expected.get("source_native_id")))
        elif len(expected_same_title) == 1 and len(virtual_same_title) == 1:
            candidate_group = virtual_same_title[0]
            if candidate_group["safe_duplicate_group"] and candidate_group["key"] != expected_key:
                representative = candidate_group["rows"][0]
                title_author_mismatches.append({
                    "venue_id": expected.get("venue_id"),
                    "year": expected.get("year"),
                    "expected_source_native_id": expected.get("source_native_id"),
                    "expected_title": expected.get("title"),
                    "virtual_title": representative.get("name") or representative.get("title"),
                    "expected_authors": expected.get("authors", []),
                    "virtual_authors": author_names(representative),
                    "sourceid": representative.get("sourceid"),
                    "virtualsite_urls": sorted({str(row.get("virtualsite_url")) for row in candidate_group["rows"] if row.get("virtualsite_url")}),
                    "reason": "exact_normalized_title_but_ordered_author_key_differs; diagnostic_only_not_a_match",
                })
    unmatched_virtual: list[dict[str, Any]] = []
    for key, source_rows in virtual_by_key.items():
        if key in consumed_keys:
            continue
        safe_group = len({row.get("sourceid") for row in source_rows}) == 1 and len({normalize_piece(row.get("abstract")) for row in source_rows}) == 1
        for row in source_rows:
            unmatched_virtual.append({
                "sourceid": row.get("sourceid"),
                "title": row.get("name") or row.get("title"),
                "authors": author_names(row),
                "virtualsite_url": row.get("virtualsite_url"),
                "reason": "no_unique_expected_match" if safe_group else "conflicting_duplicate_title_author_records",
            })
    for row in no_key:
        unmatched_virtual.append({"sourceid": row.get("sourceid"), "title": row.get("name"), "authors": author_names(row), "reason": "title_or_authors_missing"})

    return {
        "matched": matched,
        "unmatched_expected": unmatched_expected,
        "unmatched_virtual": unmatched_virtual,
        "ambiguous": ambiguous,
        "title_author_mismatches": title_author_mismatches,
        "unmatched_expected_without_exact_title": unmatched_expected_without_exact_title,
        "expected_unique_title_author_key_count": sum(count == 1 for count in map(len, expected_by_key.values())),
        "expected_duplicate_title_author_key_count": sum(len(values) > 1 for values in expected_by_key.values()),
        "virtual_unique_title_author_key_count": sum(len(values) == 1 for values in virtual_by_key.values()),
        "virtual_duplicate_title_author_key_count": sum(len(values) > 1 for values in virtual_by_key.values()),
    }


def run_offline(metadata_path: Path, expected_root: Path, output_root: Path, venue: str, year: int, source_url: str | None = None, source_observed_at: str | None = None) -> dict[str, Any]:
    rows, payload_info = load_json_rows(metadata_path)
    payload_stats = analyze_payload(rows)
    manifest_path = output_root / "source_capture_manifest.json"
    capture_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    saved_captures = capture_manifest.get("prior_local_captures", capture_manifest.get("prior_captures", []))
    match_source = next((
        item for item in saved_captures
        if Path(item.get("copied_path", "")).resolve() == metadata_path.resolve()
    ), {})
    provenance = {
        "virtual_json_url": source_url or match_source.get("source_url"),
        "virtual_json_observed_at": source_observed_at or match_source.get("original_observed_at"),
        "virtual_json_sha256": sha256_file(metadata_path),
        "virtual_json_path": str(metadata_path),
        "virtual_json_verification_status": match_source.get("verification_status", "provided_file_not_network_verified_by_offline_matcher"),
    }
    expected_path = expected_root / f"{year}.jsonl.gz"
    result_root = output_root / "offline_matches" / venue / str(year)
    result_root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "venue_id": venue,
        "year": year,
        "metadata_json": provenance,
        "payload": payload_info,
        "payload_stats": payload_stats,
        "matching_policy": "Exact official OpenAccess URL equality first; otherwise exact equality after Unicode NFKC, casefold, and whitespace-run collapse on title and ordered-author names. Punctuation and author order are preserved. Require a unique key on expected and virtual sides; duplicate cards collapse only when sourceid and abstract agree. Never fuzzy-match.",
        "expected_manifest_path": str(expected_path),
    }
    if not expected_path.is_file():
        summary.update({"status": "EXPECTED_MANIFEST_NOT_FOUND", "matched": 0, "unmatched_expected": 0, "unmatched_virtual": 0, "ambiguous": 0})
        (result_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary

    expected_rows = load_expected(expected_path)
    matched = match_rows(rows, expected_rows, provenance)
    atomic_write(result_root / "matches.jsonl", jsonl_bytes(matched["matched"]))
    atomic_write(result_root / "unmatched_expected.jsonl", jsonl_bytes(matched["unmatched_expected"]))
    atomic_write(result_root / "unmatched_virtual.jsonl", jsonl_bytes(matched["unmatched_virtual"]))
    atomic_write(result_root / "ambiguous.jsonl", jsonl_bytes(matched["ambiguous"]))
    atomic_write(result_root / "title_exact_author_mismatches.jsonl", jsonl_bytes(matched["title_author_mismatches"]))
    summary.update({
        "status": "FULL_MATCH" if len(matched["matched"]) == len(expected_rows) and not matched["ambiguous"] else "PARTIAL_MATCH",
        "expected_count": len(expected_rows),
        "matched_count": len(matched["matched"]),
        "unmatched_expected_count": len(matched["unmatched_expected"]),
        "unmatched_virtual_count": len(matched["unmatched_virtual"]),
        "ambiguous_count": len(matched["ambiguous"]),
        "exact_title_author_mismatch_count": len(matched["title_author_mismatches"]),
        "unmatched_expected_without_exact_title_count": len(matched["unmatched_expected_without_exact_title"]),
        "match_method_counts": dict(Counter(row["match_method"] for row in matched["matched"])),
        "matching_key_stats": {key: value for key, value in matched.items() if key.endswith("_count") and isinstance(value, int)},
        "artifacts": {
            "matches": str(result_root / "matches.jsonl"),
            "unmatched_expected": str(result_root / "unmatched_expected.jsonl"),
            "unmatched_virtual": str(result_root / "unmatched_virtual.jsonl"),
            "ambiguous": str(result_root / "ambiguous.jsonl"),
            "exact_title_author_mismatches": str(result_root / "title_exact_author_mismatches.jsonl"),
        },
    })
    (result_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-json", required=True, help="already-saved JSON payload; this tool never fetches it")
    parser.add_argument("--expected-root", required=True, help="directory containing <year>.jsonl.gz")
    parser.add_argument("--output-root", required=True, help="cvf-virtual-bulk evidence root")
    parser.add_argument("--venue", required=True, choices=sorted({"cvpr", "iccv"}))
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--source-url")
    parser.add_argument("--source-observed-at")
    args = parser.parse_args()
    result = run_offline(Path(args.metadata_json), Path(args.expected_root), Path(args.output_root), args.venue, args.year, args.source_url, args.source_observed_at)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"FULL_MATCH", "PARTIAL_MATCH"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline-import frozen ECCV Virtual-only identities into the venue run.

This tool validates the frozen crosswalk and raw receipts before merging the
reviewed Virtual-only records into the 2026 expected manifest and staging. It
never fetches URLs or downloads paper PDFs.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{number}: expected a JSON object")
                rows.append(row)
    return rows


def _read_jsonl_gzip(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")


def _gzip_jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as zipped:
        zipped.write(_jsonl_bytes(rows))
    return output.getvalue()


def _source_identity(virtual_native_id: Any) -> str:
    value = str(virtual_native_id or "").strip()
    if not value:
        raise ValueError("missing Virtual native id")
    return f"eccv-virtual-2026-poster-{value}"


def _verified_poster_body(row: dict[str, Any], workspace_root: Path) -> tuple[dict[str, Any], str]:
    candidate = row.get("normalized_record_candidate")
    if not isinstance(candidate, dict):
        raise ValueError(f"poster {row.get('poster_id')}: normalized candidate is missing")
    receipt = candidate.get("poster_page_source_receipt")
    if not isinstance(receipt, dict):
        raise ValueError(f"poster {row.get('poster_id')}: page receipt is missing")
    raw_path = Path(str(receipt.get("raw_path") or ""))
    if not raw_path.is_absolute():
        raw_path = workspace_root / raw_path
    if not raw_path.is_file():
        raise ValueError(f"poster {row.get('poster_id')}: raw receipt file is unavailable: {raw_path}")
    stored = raw_path.read_bytes()
    body = gzip.decompress(stored) if raw_path.suffix == ".gz" else stored
    body_sha = hashlib.sha256(body).hexdigest()
    if body_sha != receipt.get("raw_sha256") or body_sha != row.get("raw_sha256"):
        raise ValueError(f"poster {row.get('poster_id')}: raw page SHA-256 does not match the receipt")
    if int(receipt.get("status") or 0) != 200 or row.get("status") != 200:
        raise ValueError(f"poster {row.get('poster_id')}: poster page was not observed successfully")
    source_url = str(candidate.get("landing_url") or "")
    if source_url != receipt.get("source_url"):
        raise ValueError(f"poster {row.get('poster_id')}: candidate landing URL disagrees with page receipt")
    return candidate, body_sha


def _make_expected_and_staging(
    row: dict[str, Any],
    crosswalk: dict[str, Any],
    candidate: dict[str, Any],
    crosswalk_path: Path,
    crosswalk_sha256: str,
    listing_url: str,
    listing_observed_at: str,
    listing_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    virtual_id = str(candidate.get("virtual_native_id") or row.get("poster_id") or "")
    if str(row.get("poster_id") or "") != virtual_id:
        raise ValueError(f"poster {row.get('poster_id')}: poster id and Virtual native id disagree")
    identity = _source_identity(virtual_id)
    title = str(candidate.get("title") or "").strip()
    authors = candidate.get("authors")
    abstract = str(candidate.get("abstract") or "").strip()
    landing_url = str(candidate.get("landing_url") or "")
    pdf_url = str(candidate.get("paper_pdf_url") or "")
    if not title or not isinstance(authors, list) or not authors or any(not str(name).strip() for name in authors):
        raise ValueError(f"{identity}: official title and ordered authors are required")
    if not abstract:
        raise ValueError(f"{identity}: official abstract is required")
    expected_landing = f"https://eccv.ecva.net/virtual/2026/poster/{virtual_id}"
    if landing_url != expected_landing:
        raise ValueError(f"{identity}: unexpected poster URL {landing_url!r}")
    pdf_parts = urlparse(pdf_url)
    if (
        pdf_parts.scheme != "https"
        or pdf_parts.netloc != "media.eventhosts.cc"
        or not pdf_parts.path.startswith("/Conferences/ECCV2026/pdfs/")
        or not pdf_parts.path.lower().endswith(".pdf")
    ):
        raise ValueError(f"{identity}: no scoped official ECCV 2026 paper PDF link: {pdf_url!r}")
    if candidate.get("doi") is not None:
        raise ValueError(f"{identity}: reviewed Virtual-only candidate must not acquire an unverified DOI")
    if candidate.get("publication_date") is not None:
        raise ValueError(f"{identity}: poster date must not be imported as a publication date")
    provenance = candidate.get("source_provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"{identity}: field-level source provenance is missing")
    for field in ("title", "authors", "abstract", "landing_url", "paper_pdf_url", "doi"):
        if not isinstance(provenance.get(field), dict):
            raise ValueError(f"{identity}: provenance for {field} is missing")
    receipt = candidate["poster_page_source_receipt"]
    observed_at = str(receipt["observed_at"])
    crosswalk_evidence = {
        "identity_decision": "virtual_only",
        "method": crosswalk.get("method"),
        "selected_springer_id": None,
        "crosswalk_path": str(crosswalk_path),
        "crosswalk_sha256": crosswalk_sha256,
        "source_evidence": crosswalk.get("source_evidence"),
        "review_evidence": row.get("identity_review_evidence"),
    }
    expected = {
        "venue_id": "eccv",
        "year": 2026,
        "source_native_id": identity,
        "source_document_type": "ECCV official Virtual-site main-conference paper",
        "title": title,
        "landing_url": landing_url,
        "enumeration_source_url": listing_url,
        "enumeration_observed_at": listing_observed_at,
        "enumeration_verification_url": landing_url,
        "enumeration_verification_observed_at": observed_at,
        "virtual_native_id": virtual_id,
        "virtual_listing_sha256": listing_sha256,
        "identity_crosswalk_sha256": crosswalk_sha256,
        "identity_crosswalk_decision": "virtual_only",
    }
    field_provenance = {
        key: dict(value) for key, value in provenance.items() if isinstance(value, dict)
    }
    field_provenance.update(
        {
            "source_native_id": {
                "source_url": landing_url,
                "observed_at": observed_at,
                "raw_sha256": receipt["raw_sha256"],
                "method": "official_virtual_poster_identifier_in_page_path",
            },
            "year": {
                "source_url": listing_url,
                "observed_at": listing_observed_at,
                "raw_sha256": listing_sha256,
                "method": "official_virtual_main_conference_papers_listing",
            },
            "document_type": {
                "source_url": listing_url,
                "observed_at": listing_observed_at,
                "raw_sha256": listing_sha256,
                "method": "official_virtual_main_conference_papers_listing",
            },
            "pdf_url": dict(provenance["paper_pdf_url"]),
            "pdf_discovery_status": dict(provenance["paper_pdf_url"]),
            "publication_date": {
                "source_url": landing_url,
                "observed_at": observed_at,
                "raw_sha256": receipt["raw_sha256"],
                "method": "not_assigned_from_unmapped_poster_datePublished",
            },
        }
    )
    missing_fields = {
        "doi": {
            "reason_code": "not_present_on_official_page",
            "reason": "No unique DOI was observed on the official poster page; the reviewed Virtual-only identity has no verified Springer chapter DOI.",
            "source_url": landing_url,
        },
        "publication_date": {
            "reason_code": "not_assigned",
            "reason": "The poster page datePublished is preserved as page evidence but is not a verified publisher publication date.",
            "source_url": landing_url,
            "unmapped_page_datePublished": candidate.get("poster_page_date_published_unmapped"),
        },
    }
    staging = {
        "schema_version": "literature-metadata-staging-v1",
        "venue_id": "eccv",
        "source_native_id": identity,
        "title": title,
        "authors": [str(name) for name in authors],
        "year": 2026,
        "document_type": "conference-paper",
        "inclusion_decision": "include",
        "landing_url": landing_url,
        "source_url": landing_url,
        "observed_at": observed_at,
        "abstract": abstract,
        "publication_date": None,
        "doi": None,
        "doi_status": "not_present_on_official_page",
        "pdf_url": pdf_url,
        "pdf_discovery_status": "visible_url",
        "missing_fields": missing_fields,
        "field_provenance": field_provenance,
        "identity_crosswalk_evidence": crosswalk_evidence,
        "poster_page_date_published_unmapped": candidate.get("poster_page_date_published_unmapped"),
        "poster_page_source_receipt": dict(receipt),
    }
    return expected, staging


def integrate_virtual_union(run_root: Path, gap_root: Path, workspace_root: Path | None = None) -> dict[str, Any]:
    run_root = run_root.resolve()
    gap_root = gap_root.resolve()
    workspace_root = (workspace_root or Path.cwd()).resolve()
    expected_dir = run_root / "expected"
    partial_expected_path = expected_dir / "2026.incomplete-series-only.jsonl.gz"
    provisional_dir = run_root / "provisional_expected"
    provisional_path = provisional_dir / partial_expected_path.name
    if partial_expected_path.exists() and provisional_path.exists():
        if _sha256(partial_expected_path) != _sha256(provisional_path):
            raise ValueError("partial 2026 manifests differ between expected/ and provisional_expected/")
    publisher_manifest_path = provisional_path if provisional_path.is_file() else partial_expected_path
    if not publisher_manifest_path.is_file():
        raise FileNotFoundError("the 2026 incomplete Springer manifest was not found")

    summary_path = gap_root / "final_crosswalk_summary.json"
    freeze_path = gap_root / "final_crosswalk_freeze_receipt.json"
    virtual_path = gap_root / "final_virtual_only_records.jsonl"
    crosswalk_path = gap_root / "final_identity_crosswalk.jsonl"
    springer_only_path = gap_root / "springer_only_unmatched_records.jsonl"
    for path in (summary_path, freeze_path, virtual_path, crosswalk_path, springer_only_path):
        if not path.is_file():
            raise FileNotFoundError(f"frozen identity input is missing: {path}")
    summary = _read_json(summary_path)
    freeze = _read_json(freeze_path)
    input_hashes = {
        "final_crosswalk_summary": _sha256(summary_path),
        "final_virtual_only_records": _sha256(virtual_path),
        "final_identity_crosswalk": _sha256(crosswalk_path),
        "springer_only_unmatched_records": _sha256(springer_only_path),
    }
    declared_hashes = summary.get("sha256", {})
    for key, field in (
        ("final_virtual_only_records", "final_virtual_only_records"),
        ("final_identity_crosswalk", "final_identity_crosswalk"),
        ("springer_only_unmatched_records", "springer_only_unmatched_records"),
    ):
        if input_hashes[key] != declared_hashes.get(field) or input_hashes[key] != freeze.get(f"{key}_sha256"):
            raise ValueError(f"frozen input hash mismatch for {key}")
    if input_hashes["final_crosswalk_summary"] != freeze.get("summary_sha256"):
        raise ValueError("frozen crosswalk summary hash mismatch")
    if freeze.get("status") != "frozen" or summary.get("status") != "identity_crosswalk_frozen":
        raise ValueError("identity crosswalk is not frozen")

    source_sets = summary.get("source_sets", {})
    counts = summary.get("crosswalk_counts", {})
    springer_rows = _read_jsonl_gzip(publisher_manifest_path)
    springer_ids = [str(row.get("source_native_id") or "") for row in springer_rows]
    if not all(springer_ids) or len(springer_ids) != len(set(springer_ids)):
        raise ValueError("publisher manifest has missing or duplicate source_native_id values")
    if len(springer_ids) != int(source_sets.get("springer_unique_ids", -1)):
        raise ValueError("publisher manifest count does not match frozen identity summary")

    crosswalk_rows = _read_jsonl(crosswalk_path)
    crosswalk_by_virtual: dict[str, dict[str, Any]] = {}
    selected_springer_ids: list[str] = []
    for row in crosswalk_rows:
        virtual_id = str(row.get("virtual_native_id") or "")
        if not virtual_id or virtual_id in crosswalk_by_virtual:
            raise ValueError("frozen crosswalk contains missing or duplicate Virtual native IDs")
        crosswalk_by_virtual[virtual_id] = row
        selected = row.get("selected_springer_id")
        if selected:
            selected_springer_ids.append(str(selected))
        elif row.get("identity_decision") != "virtual_only":
            raise ValueError(f"Virtual ID {virtual_id} has no Springer match but is not marked virtual_only")
    if len(crosswalk_rows) != int(source_sets.get("virtual_unique_ids", -1)):
        raise ValueError("crosswalk row count does not match the frozen Virtual set")
    if len(selected_springer_ids) != len(set(selected_springer_ids)):
        raise ValueError("frozen crosswalk selects one Springer ID more than once")
    if not set(selected_springer_ids).issubset(set(springer_ids)):
        raise ValueError("frozen crosswalk selected a Springer ID outside the publisher manifest")

    virtual_only_rows = _read_jsonl(virtual_path)
    virtual_only_ids: list[str] = []
    for row in virtual_only_rows:
        if row.get("identity_decision") != "virtual_only":
            raise ValueError(f"poster {row.get('poster_id')} is not frozen as virtual_only")
        candidate = row.get("normalized_record_candidate") or {}
        virtual_id = str(candidate.get("virtual_native_id") or row.get("poster_id") or "")
        decision = crosswalk_by_virtual.get(virtual_id)
        if not decision or decision.get("identity_decision") != "virtual_only" or decision.get("selected_springer_id") is not None:
            raise ValueError(f"poster {virtual_id} does not agree with the frozen one-to-one crosswalk")
        virtual_only_ids.append(virtual_id)
    if len(virtual_only_ids) != len(set(virtual_only_ids)):
        raise ValueError("final Virtual-only record file contains duplicate poster IDs")
    crosswalk_virtual_only_ids = {
        virtual_id for virtual_id, row in crosswalk_by_virtual.items()
        if row.get("identity_decision") == "virtual_only" and row.get("selected_springer_id") is None
    }
    if set(virtual_only_ids) != crosswalk_virtual_only_ids:
        raise ValueError("final Virtual-only records do not exactly match the frozen crosswalk partition")
    if len(virtual_only_rows) != int(counts.get("virtual_only", -1)):
        raise ValueError("Virtual-only record count does not match the frozen summary")
    if len(selected_springer_ids) != int(counts.get("matched_total", -1)):
        raise ValueError("matched Springer count does not match the frozen summary")
    if len(springer_ids) - len(selected_springer_ids) != int(counts.get("springer_only", -1)):
        raise ValueError("publisher-only source count does not match the frozen summary")

    springer_only_rows = _read_jsonl(springer_only_path)
    publisher_only_ids = [str(row.get("source_native_id") or "") for row in springer_only_rows]
    unmatched_ids = sorted(set(springer_ids) - set(selected_springer_ids))
    if sorted(publisher_only_ids) != unmatched_ids:
        raise ValueError("publisher-only rows do not agree with the crosswalk set difference")
    declared_publisher_only_id = summary.get("global_validation", {}).get("publisher_only_id")
    if publisher_only_ids != [declared_publisher_only_id]:
        raise ValueError("publisher-only identity disagrees with the frozen review")

    listing_url = str(source_sets.get("virtual_source") or "")
    listing_observed_at = str(source_sets.get("virtual_source_observed_at") or "")
    listing_sha256 = str(source_sets.get("virtual_source_sha256") or "")
    listing_path = run_root / "raw" / "eccv2026_virtual_papers.html"
    if not listing_url or not listing_observed_at or not listing_sha256 or not listing_path.is_file():
        raise ValueError("the official Virtual papers listing receipt is incomplete")
    if _sha256(listing_path) != listing_sha256:
        raise ValueError("official Virtual papers listing hash differs from frozen crosswalk evidence")

    search_observation_path = run_root.parent / "eccv-root-browser-search-observations.json"
    search_observations: dict[str, Any] = {}
    if search_observation_path.is_file():
        search_observations = _read_json(search_observation_path)
    search_pages = [
        {
            "url": row.get("url"),
            "observed_at": row.get("observed_at"),
            "visible_result_count": row.get("visible_result_count"),
        }
        for row in search_observations.get("pages", [])
    ]
    search_unique_books = {
        str(book.get("url"))
        for page in search_observations.get("pages", [])
        for book in page.get("books", [])
        if book.get("url")
    }

    virtual_expected: list[dict[str, Any]] = []
    virtual_staging: list[dict[str, Any]] = []
    virtual_ids: list[str] = []
    poster_receipt_hashes: dict[str, str] = {}
    for row in virtual_only_rows:
        candidate, body_sha = _verified_poster_body(row, workspace_root)
        virtual_id = str(candidate.get("virtual_native_id") or row.get("poster_id") or "")
        decision = crosswalk_by_virtual[virtual_id]
        identity = _source_identity(virtual_id)
        expected, staged = _make_expected_and_staging(
            row,
            decision,
            candidate,
            crosswalk_path,
            input_hashes["final_identity_crosswalk"],
            listing_url,
            listing_observed_at,
            listing_sha256,
        )
        expected["identity_crosswalk_summary_sha256"] = input_hashes["final_crosswalk_summary"]
        virtual_expected.append(expected)
        virtual_staging.append(staged)
        virtual_ids.append(identity)
        poster_receipt_hashes[identity] = body_sha
    if len(virtual_ids) != len(set(virtual_ids)):
        raise ValueError("generated Virtual source identities collide")
    if set(virtual_ids) & set(springer_ids):
        raise ValueError("a Virtual-only source identity collides with a Springer source ID")
    expected_union = springer_rows + virtual_expected
    expected_ids = [str(row["source_native_id"]) for row in expected_union]
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("resulting 2026 expected union contains duplicate identities")
    if len(expected_union) != int(counts.get("identity_union_size", -1)):
        raise ValueError("resulting expected union count differs from the frozen crosswalk")

    stage_path = run_root / "metadata_staging.jsonl"
    partial_stage_path = run_root / "metadata_staging.partial.jsonl"
    stage_rows = _read_jsonl(stage_path)
    if not stage_rows:
        stage_rows = _read_jsonl(partial_stage_path)
    stage_by_id: dict[str, dict[str, Any]] = {}
    for row in stage_rows:
        identity = str(row.get("source_native_id") or "")
        if not identity:
            raise ValueError("existing staging has a row without source_native_id")
        if identity in stage_by_id:
            raise ValueError(f"existing staging contains duplicate identity {identity}")
        stage_by_id[identity] = row
    exclusions = _read_jsonl(run_root / "metadata_exclusions.jsonl")
    exclusion_ids = {str(row.get("source_native_id") or "") for row in exclusions}
    unresolved_ids = {
        str(row.get("source_native_id") or "")
        for row in _read_jsonl(run_root / "unresolved.jsonl")
    }
    if set(virtual_ids) & (exclusion_ids | unresolved_ids):
        raise ValueError("a reviewed Virtual-only identity is currently excluded or unresolved")
    for row in virtual_staging:
        identity = str(row["source_native_id"])
        previous = stage_by_id.get(identity)
        if previous and (
            previous.get("title") != row.get("title")
            or previous.get("landing_url") != row.get("landing_url")
        ):
            raise ValueError(f"staging identity {identity} conflicts with the frozen poster record")
        stage_by_id[identity] = row
    all_staging = [stage_by_id[key] for key in sorted(stage_by_id)]
    all_expected = sorted(expected_union, key=lambda row: str(row["source_native_id"]))

    enumeration_path = run_root / "enumeration_report.json"
    enumeration_report = _read_json(enumeration_path)
    year_reports = enumeration_report.setdefault("year_reports", {})
    year_report = year_reports.setdefault("2026", {})
    publisher_item_count = len(springer_rows)
    virtual_item_count = len(crosswalk_rows)
    year_report.update(
        {
            "source_item_count": len(all_expected),
            "research_candidate_count": len(all_expected),
            "springer_observed_chapter_count": publisher_item_count,
            "virtual_site_papers_page_count": virtual_item_count,
            "virtual_site_union_addition_count": len(virtual_expected),
            "identity_union_size": len(all_expected),
            "source_item_set_sha256": hashlib.sha256("\n".join(sorted(expected_ids)).encode("utf-8")).hexdigest(),
            "identity_crosswalk_summary_path": str(gap_root / "final_crosswalk_summary.json"),
            "identity_crosswalk_summary_sha256": input_hashes["final_crosswalk_summary"],
            "identity_crosswalk_path": str(crosswalk_path),
            "identity_crosswalk_sha256": input_hashes["final_identity_crosswalk"],
            "identity_union_reconciliation": {
                "status": "complete",
                "virtual_records": virtual_item_count,
                "springer_records": publisher_item_count,
                "matched_pairs": len(selected_springer_ids),
                "virtual_only": len(virtual_expected),
                "springer_only": len(publisher_only_ids),
                "union_size": len(all_expected),
                "net_virtual_minus_springer": virtual_item_count - publisher_item_count,
                "publisher_only_ids": unmatched_ids,
                "missing_springer_part_note": summary.get("identity_scope_note"),
            },
            "search_waterline_reconciliation": {
                "status": "documented_count_drift",
                "series_listed_volumes": year_report.get("conference_series_volumes"),
                "series_listed_chapter_rows": publisher_item_count,
                "official_search_page_observations": search_pages,
                "official_search_unique_book_urls_observed": len(search_unique_books),
                "official_search_observation_path": str(search_observation_path) if search_observations else None,
                "official_search_observation_sha256": _sha256(search_observation_path) if search_observations else None,
                "virtual_listing_record_count": virtual_item_count,
                "identity_set_resolution": "The 2026 identity set is closed by the frozen official Virtual/Springer one-to-one crosswalk; the missing-part clue is not used to invent or exclude records.",
            },
        }
    )
    year_report["source_urls"] = sorted(
        set(year_report.get("source_urls", []))
        | {listing_url}
        | {str(row.get("enumeration_source_url") or "") for row in springer_rows}
        | {str(row.get("landing_url") or "") for row in virtual_expected}
    )
    year_report["items"] = sorted(
        springer_rows + virtual_expected,
        key=lambda row: str(row.get("source_native_id") or ""),
    )
    enumeration_report["year_reports"]["2026"] = year_report

    state_path = run_root / "checkpoint.json"
    state = _read_json(state_path)
    completed_years = {int(year) for year in state.get("completed_enumeration_years", [])}
    completed_years.add(2026)
    state["completed_enumeration_years"] = sorted(completed_years)
    state.setdefault("enumerated", {})["2026"] = {
        key: value for key, value in year_report.items() if key != "items"
    }
    # The chapter details and Virtual-only records must all be accounted for
    # under their own source identities before this phase is declared complete.
    accounted_after = set(stage_by_id) | exclusion_ids
    state["details_complete"] = set(expected_ids).issubset(accounted_after) and not (set(expected_ids) & unresolved_ids)
    state["detail_collection_blocked"] = False
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    provisional_dir.mkdir(parents=True, exist_ok=True)
    if partial_expected_path.is_file() and not provisional_path.is_file():
        shutil.copyfile(partial_expected_path, provisional_path)
    elif not provisional_path.is_file():
        raise FileNotFoundError("cannot preserve the original incomplete manifest")
    original_partial_sha256 = _sha256(provisional_path)

    # Each file is atomically replaced; a rerun validates frozen inputs and
    # deterministically repairs any interrupted import.
    _atomic_write(expected_dir / "2026.jsonl.gz", _gzip_jsonl_bytes(all_expected))
    _atomic_write(stage_path, _jsonl_bytes(all_staging))
    _atomic_write(partial_stage_path, _jsonl_bytes(all_staging))
    _atomic_write(enumeration_path, _json_bytes(enumeration_report))
    _atomic_write(state_path, _json_bytes(state))
    if partial_expected_path.is_file():
        partial_expected_path.unlink()

    expected_path = expected_dir / "2026.jsonl.gz"
    receipt = {
        "schema_version": "eccv-virtual-union-import-v1",
        "status": "complete",
        "observed_at": state["updated_at"],
        "no_network_requests": True,
        "no_pdf_downloads": True,
        "publisher_manifest_source_before_import": str(publisher_manifest_path),
        "publisher_manifest_preserved_as": str(provisional_path),
        "publisher_manifest_sha256": original_partial_sha256,
        "frozen_inputs": {
            **input_hashes,
            "final_crosswalk_freeze_receipt": _sha256(freeze_path),
            "official_virtual_listing_sha256": listing_sha256,
        },
        "counts": {
            "springer_items": len(springer_rows),
            "virtual_items": len(crosswalk_rows),
            "matched_pairs": len(selected_springer_ids),
            "virtual_only_imported": len(virtual_expected),
            "springer_only": len(publisher_only_ids),
            "identity_union_size": len(all_expected),
            "staging_by_year_2026": sum(int(row.get("year", -1)) == 2026 for row in all_staging),
            "missing_doi_virtual_only": sum(row["doi"] is None for row in virtual_staging),
            "missing_publication_date_virtual_only": sum(row["publication_date"] is None for row in virtual_staging),
            "paper_pdf_url_virtual_only": sum(bool(row.get("pdf_url")) for row in virtual_staging),
        },
        "springer_only_ids": publisher_only_ids,
        "virtual_only_ids": sorted(virtual_ids),
        "expected_union_sha256": _sha256(expected_path),
        "expected_source_id_set_sha256": hashlib.sha256("\n".join(sorted(expected_ids)).encode("utf-8")).hexdigest(),
        "poster_raw_body_sha256_by_source_id": poster_receipt_hashes,
    }
    receipt_path = run_root / "eccv_2026_virtual_union_import_receipt.json"
    _atomic_write(receipt_path, _json_bytes(receipt))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--gap-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = integrate_virtual_union(args.run_root, args.gap_root, args.workspace_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

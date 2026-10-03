#!/usr/bin/env python3
"""Prepare and validate run-local Nature metadata artifacts.

The collector owns enumeration and detail extraction.  This module only copies
its JSONL inputs into a run-local prepared directory, derives expected identity
manifests from a positive completion receipt, writes validator-compatible
waterline evidence, and delegates schema checks to ``validate_staging``.
It never opens or writes the production catalog and never changes registry or
campaign state.  An accepted Nature count baseline may be adapted for closed
years only after its receipts, byte hashes, and identity sets are rechecked;
the current year remains a fresh pagination input.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.litdb.io import atomic_json, utc_now  # noqa: E402
from tools.litdb.metadata_pipeline import ALLOWED_EXCLUSION_REASONS, ALLOWED_MISSING_REASONS, validate_staging  # noqa: E402
from tools.litdb.paths import LitDBPaths  # noqa: E402


VENUE_START_YEARS = {
    "nature-machine-intelligence": 2019,
    "nature-computational-science": 2021,
    "nature-methods": 2015,
    "nature": 2015,
}

_TRUE = {"true", "yes", "complete", "completed", "pass", "passed", "success", "successful"}
_FALSE = {"false", "no", "partial", "in_progress", "in-progress", "failed", "error", "blocked"}
_GOOD_STATUS = {"PASS", "NO_CHANGE", "UPDATED", "COMPLETE", "COMPLETED", "SUCCESS", "SUCCESSFUL"}
_BAD_STATUS = {"PARTIAL", "IN_PROGRESS", "FAILED", "ERROR", "BLOCKED", "RUNNING"}
_GOOD_DRIFT = {"NO_DRIFT", "WITHIN_THRESHOLD"}
_EVIDENCE_CONTEXT_KEYS = ("waterline_evidence", "completion", "enumeration", "result", "receipt", "evidence", "summary")
_EVIDENCE_NAMES = (
    "collection_complete.json",
    "collection_completion.json",
    "completion.json",
    "completion_receipt.json",
    "enumeration_receipt.json",
    "enumeration_report.json",
    "run_receipt.json",
    "thread_receipt.json",
    "output_manifest.json",
    "waterline_evidence.json",
    "enumeration_manifest.json",
    "enumerate_manifest.json",
    "enumeration_controller_review.json",
    "detail_manifest.json",
    "run_state.json",
)
_ENUMERATION_EVIDENCE_NAMES = frozenset({
    "enumeration_receipt.json",
    "enumeration_report.json",
    "enumeration_manifest.json",
    "enumerate_manifest.json",
    "enumeration_controller_review.json",
})
_COLLECTION_EVIDENCE_NAMES = frozenset({
    "run_receipt.json",
    "detail_manifest.json",
    "run_state.json",
})
_IDENTITY_KEYS = ("source_native_id", "stable_article_path", "source_item_id", "arnumber")
_RAW_ENUMERATION_NAMES = ("enumeration/listing_cards.jsonl", "listing_cards.jsonl")
_RAW_PAGE_EVIDENCE_NAMES = ("enumeration/page_evidence.jsonl", "page_evidence.jsonl")
_RAW_EXCLUSION_NAMES = ("enumeration/exclusions.jsonl", "exclusions.jsonl")
_RAW_STAGING_NAMES = (
    "staging/metadata.jsonl",
    "metadata.jsonl",
    "metadata/detail_manifest.jsonl",
    "detail_manifest.jsonl",
    "discovery/detail_manifest.jsonl",
    "discovery/detail.jsonl",
)
_RAW_DETAIL_REVIEW_NAMES = (
    "staging/manual_review.jsonl",
    "manual_review.jsonl",
    "staging/detail_manifest.json",
    "details/detail_manifest.json",
    "detail_manifest.json",
)


def _read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    opener = gzip.open if path.suffix == ".gz" else open
    rows: list[tuple[int, dict[str, Any]]] = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row is not an object: {path}:{line_no}")
            rows.append((line_no, value))
    return rows


def _identity(row: dict[str, Any]) -> str:
    for key in _IDENTITY_KEYS:
        value = row.get(key)
        if value is not None and str(value).strip():
            value = str(value).strip()
            # Nature's raw collector identifies an article as
            # ``venue-id:/articles/...`` in source_item_id, while the
            # validator and the historical seed use ``/articles/...``.  A
            # stable article path wins when present and this fallback strips
            # only the collector's explicit venue prefix.  DOI and URL
            # identities are left intact.
            if key != "arnumber" and not value.startswith(("http://", "https://", "10.")):
                prefix, separator, suffix = value.partition(":")
                if separator and suffix.startswith("/") and "/" not in prefix and "\\" not in prefix:
                    value = suffix
            return value
    return ""


def _year(row: dict[str, Any]) -> int | None:
    value = row.get("year")
    if value is None:
        value = row.get("venue_year")
    if value is None:
        value = row.get("publication_year")
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool):
        return None
    return parsed


def _walk_contexts(value: Any, seen: set[int] | None = None) -> Iterable[dict[str, Any]]:
    if seen is None:
        seen = set()
    if not isinstance(value, dict) or id(value) in seen:
        return
    seen.add(id(value))
    yield value
    for key in _EVIDENCE_CONTEXT_KEYS:
        child = value.get(key)
        if isinstance(child, dict):
            yield from _walk_contexts(child, seen)


def _values(document: dict[str, Any], keys: tuple[str, ...]) -> tuple[list[Any], list[str]]:
    found: list[Any] = []
    locations: list[str] = []
    for context in _walk_contexts(document):
        for key in keys:
            if key in context:
                found.append(context[key])
                locations.append(key)
    return found, locations


def _one(document: dict[str, Any], keys: tuple[str, ...], label: str, errors: list[str]) -> Any:
    found, _ = _values(document, keys)
    if not found:
        return None
    first = found[0]
    if any(value != first for value in found[1:]):
        errors.append(f"completion evidence has conflicting {label} values")
        return None
    return first


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in _TRUE:
            return True
        if normalized in _FALSE:
            return False
    return None


def _as_urls(value: Any) -> list[str] | None:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return [item.strip() for item in value]
    return None


def _nature_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host == "nature.com" or host.endswith(".nature.com"))


def _normal_status(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().upper().replace("-", "_")


def _sha256_ids(ids: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(set(ids))).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_evidence(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"completion evidence is not a JSON object: {path}")
    return value


def _evidence_role(path: Path, document: dict[str, Any]) -> str:
    """Classify receipts before applying role-specific completeness checks."""
    name = path.name.casefold()
    if name in _ENUMERATION_EVIDENCE_NAMES:
        return "enumeration"
    if name in _COLLECTION_EVIDENCE_NAMES:
        return "collection"
    # The test and older collector interface use collection_complete.json for
    # a complete enumeration receipt.  Other unnamed documents are classified
    # from their fields, so a detail receipt cannot be mistaken for one.
    if name == "collection_complete.json":
        return "enumeration"
    enum_values, _ = _values(
        document,
        (
            "enumeration_complete",
            "traversal_complete",
            "pagination_complete",
            "identity_count",
            "unique_paths",
        ),
    )
    detail_values, _ = _values(
        document,
        (
            "detail_stage_complete",
            "completed_details",
            "pending_details",
            "remaining_details",
            "metadata_path",
        ),
    )
    if enum_values and not detail_values:
        return "enumeration"
    if detail_values and not enum_values:
        return "collection"
    return "unknown"


def _collection_evidence_errors(path: Path, document: dict[str, Any]) -> list[str]:
    """Check only collection/detail facts present in a non-enumeration receipt."""
    errors: list[str] = []
    name = path.name.casefold()
    statuses, _ = _values(document, ("status", "completion_status", "detail_status", "run_status"))
    normalized_statuses = [_normal_status(value) for value in statuses if _normal_status(value)]
    if name != "run_state.json" and any(status in _BAD_STATUS for status in normalized_statuses):
        errors.append(f"collection/detail evidence is not complete: status={next(status for status in normalized_statuses if status in _BAD_STATUS)}")
    complete_flags, _ = _values(document, ("detail_stage_complete", "details_complete", "collection_complete"))
    bools = [_bool_value(value) for value in complete_flags]
    if any(value is False for value in bools):
        errors.append("collection/detail evidence explicitly marks details incomplete")
    for context in _walk_contexts(document):
        candidates = context.get("candidates")
        completed = context.get("completed_details")
        if isinstance(candidates, int) and isinstance(completed, int) and completed < candidates:
            errors.append(f"collection/detail evidence completed_details={completed} is below candidates={candidates}")
        for key in ("pending_details", "remaining_details"):
            value = context.get(key)
            if isinstance(value, int) and value > 0:
                errors.append(f"collection/detail evidence {key}={value}")
    return errors


def _discover_evidence(run_root: Path, explicit: list[Path] | None) -> list[Path]:
    if explicit:
        return [path.expanduser().resolve() for path in explicit]
    paths: list[Path] = []
    for name in _EVIDENCE_NAMES:
        for candidate in (run_root / name, run_root / "enumeration" / name, run_root / "staging" / name):
            if candidate.is_file() and candidate not in paths:
                paths.append(candidate)
    return paths


def _completion_summary(paths: list[Path], enum_count: int) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not paths:
        return {}, ["no collection completion evidence was supplied or discovered"]
    documents: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        if not path.is_file():
            errors.append(f"completion evidence does not exist: {path}")
            continue
        try:
            documents.append((path, _load_evidence(path)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    if not documents:
        return {}, errors or ["no readable completion evidence"]
    for path, document in documents:
        findings = _review_findings(document)
        errors.extend(f"{path}: unresolved manual review: {finding}" for finding in findings)
        role = _evidence_role(path, document)
        if role == "collection":
            errors.extend(f"{path}: {error}" for error in _collection_evidence_errors(path, document))

    candidates: list[dict[str, Any]] = []
    for path, document in documents:
        local_errors: list[str] = []
        role = _evidence_role(path, document)
        if role != "enumeration":
            continue
        minimal_manifest = path.name in {"enumeration_manifest.json", "enumeration_controller_review.json"}
        flags, _ = _values(
            document,
            ("enumeration_complete", "collection_complete", "traversal_complete", "pagination_complete", "completed"),
        )
        bools = [_bool_value(value) for value in flags]
        bools = [value for value in bools if value is not None]
        status_raw = _one(document, ("status", "completion_status", "enumeration_status"), "status", local_errors)
        status = _normal_status(status_raw)
        if status in _BAD_STATUS:
            local_errors.append(f"completion evidence is not complete: status={status}")
        if any(value is False for value in bools):
            local_errors.append("completion evidence explicitly marks enumeration incomplete")
        if not any(value is True for value in bools):
            if minimal_manifest and status in _GOOD_STATUS:
                bools.append(True)
            else:
                local_errors.append("completion evidence lacks an explicit enumeration_complete=true signal")
        if status is not None and status not in _GOOD_STATUS and status not in _BAD_STATUS:
            local_errors.append(f"completion evidence has unsupported status: {status}")

        drift_raw = _one(document, ("drift_status", "enumeration_drift_status"), "drift_status", local_errors)
        drift_status = _normal_status(drift_raw)
        if drift_status not in _GOOD_DRIFT:
            if minimal_manifest and status in _GOOD_STATUS:
                # The compact enumeration receipts do not claim recipe drift.
                # Leave this unresolved here; prepare_run derives the final
                # validator field only after page/card/facet identity checks.
                drift_status = None
            else:
                local_errors.append("completion evidence lacks accepted drift_status NO_DRIFT/WITHIN_THRESHOLD")
        observed_at = _one(
            document,
            ("observed_at", "waterline_observed_at", "completed_at", "enumerated_at", "reviewed_at"),
            "observed_at",
            local_errors,
        )
        if not isinstance(observed_at, str) or not observed_at.strip():
            if minimal_manifest:
                observed_at = None
            else:
                local_errors.append("completion evidence lacks observed_at")
        source_urls_raw = _one(
            document,
            ("source_urls", "enumeration_source_urls", "source_url"),
            "source_urls",
            local_errors,
        )
        source_urls = _as_urls(source_urls_raw)
        if not source_urls:
            if not minimal_manifest:
                local_errors.append("completion evidence lacks source_urls")
        elif any(not _nature_url(url) for url in source_urls):
            local_errors.append("completion evidence contains a non-Nature source URL")
        count_raw = _one(
            document,
            ("current_source_item_count", "enumerated_count", "record_count", "total_records", "count", "unique_paths", "identity_count"),
            "current_source_item_count",
            local_errors,
        )
        if isinstance(count_raw, bool):
            count_raw = None
        if isinstance(count_raw, str) and count_raw.isdigit():
            count_raw = int(count_raw)
        if not isinstance(count_raw, int) or count_raw < 0:
            if not minimal_manifest:
                local_errors.append("completion evidence lacks integer current_source_item_count")
        elif count_raw != enum_count:
            local_errors.append(
                f"completion evidence count {count_raw} does not equal unique enumeration identities {enum_count}"
            )
        new_ids_raw = _one(document, ("new_ids",), "new_ids", local_errors)
        missing_ids_raw = _one(document, ("missing_ids",), "missing_ids", local_errors)
        new_ids = [str(item).strip() for item in new_ids_raw] if isinstance(new_ids_raw, list) else []
        missing_ids = [str(item).strip() for item in missing_ids_raw] if isinstance(missing_ids_raw, list) else []
        if new_ids_raw is not None and (not isinstance(new_ids_raw, list) or any(not item for item in new_ids)):
            local_errors.append("completion evidence new_ids must be a list of non-empty identities")
        if missing_ids_raw is not None and (not isinstance(missing_ids_raw, list) or any(not item for item in missing_ids)):
            local_errors.append("completion evidence missing_ids must be a list of non-empty identities")
        if missing_ids:
            local_errors.append("completion evidence reports missing_ids; enumeration is not a complete accepted set")
        set_hash = _one(
            document,
            ("source_item_set_sha256", "enumeration_sha256", "set_sha256"),
            "source_item_set_sha256",
            local_errors,
        )
        if set_hash is not None and (not isinstance(set_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", set_hash)):
            local_errors.append("completion evidence source_item_set_sha256 is not lowercase 64-hex")
        candidate = {
            "path": str(path),
            "status": "PASS" if status in {None, "COMPLETE", "COMPLETED", "SUCCESS", "SUCCESSFUL"} else status,
            "drift_status": drift_status,
            "enumeration_complete": not local_errors,
            "observed_at": observed_at,
            "source_urls": source_urls or [],
            "current_source_item_count": count_raw,
            "new_ids": new_ids,
            "missing_ids": missing_ids,
            "source_item_set_sha256": set_hash,
            "errors": local_errors,
        }
        candidates.append(candidate)

    complete = [candidate for candidate in candidates if candidate["enumeration_complete"]]
    explicit_incomplete = [candidate for candidate in candidates if candidate["errors"]]
    if explicit_incomplete:
        # If callers supplied one evidence file, its error is decisive. For
        # auto-discovery, a contradictory/partial receipt must also fail
        # closed rather than letting an older PASS receipt win.  A blocked
        # run_state is only a resumable progress checkpoint, however: once a
        # later enumeration manifest/controller review proves a complete
        # recovery, its old checkpoint errors must not poison that recovery.
        has_positive_manifest = any(
            candidate["enumeration_complete"]
            and Path(candidate["path"]).name in {"enumeration_manifest.json", "enumeration_controller_review.json"}
            for candidate in candidates
        )
        errors.extend(
            f"{candidate['path']}: {error}"
            for candidate in explicit_incomplete
            for error in candidate["errors"]
            if not (has_positive_manifest and Path(candidate["path"]).name == "run_state.json")
        )
    if len(complete) > 1:
        # The collector may retain both a compact enumeration manifest and a
        # controller review.  They prove the same identity count but carry
        # different observation timestamps or omit derived waterline fields;
        # compare their common structural facts and merge derived fields later
        # from page evidence.  Full receipts still compare all fields.
        minimal_only = all(Path(candidate["path"]).name in {"enumeration_manifest.json", "enumeration_controller_review.json"} for candidate in complete)
        keys = ("status", "drift_status", "current_source_item_count", "missing_ids") if minimal_only else (
            "status", "drift_status", "observed_at", "source_urls", "current_source_item_count", "new_ids", "missing_ids", "source_item_set_sha256"
        )
        signatures = {
            json.dumps({key: candidate.get(key) for key in keys}, sort_keys=True)
            for candidate in complete
        }
        if len(signatures) > 1:
            errors.append("multiple completion evidence files disagree")
    if errors or not complete:
        return {
            "enumeration_complete": False,
            "status": "INCOMPLETE",
            "drift_status": (complete[0]["drift_status"] if complete else "UNRESOLVED"),
            "observed_at": (complete[0]["observed_at"] if complete else None),
            "source_urls": (complete[0]["source_urls"] if complete else []),
            "current_source_item_count": (complete[0]["current_source_item_count"] if complete else enum_count),
            "new_ids": (complete[0]["new_ids"] if complete else []),
            "missing_ids": (complete[0]["missing_ids"] if complete else []),
            "source_item_set_sha256": (complete[0]["source_item_set_sha256"] if complete else None),
            "evidence_files": [str(path) for path in paths],
        }, errors or ["completion evidence did not positively prove a complete enumeration"]
    chosen = complete[0]
    return {
        "enumeration_complete": True,
        "status": chosen["status"],
        "drift_status": chosen["drift_status"],
        "observed_at": chosen["observed_at"],
        "source_urls": chosen["source_urls"],
        "current_source_item_count": chosen["current_source_item_count"],
        "new_ids": chosen["new_ids"],
        "missing_ids": chosen["missing_ids"],
        "source_item_set_sha256": chosen["source_item_set_sha256"],
        "evidence_files": [str(path) for path in paths],
    }, []


def _copy_input(source: Path | None, destination: Path, *, allow_missing: bool = False) -> bool:
    if source is None:
        if allow_missing:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("", encoding="utf-8")
            return False
        raise ValueError(f"required input is missing: {destination}")
    source = source.expanduser().resolve()
    if not source.is_file():
        if allow_missing:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("", encoding="utf-8")
            return False
        raise ValueError(f"input file does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source != destination.resolve():
        shutil.copy2(source, destination)
    return True


def _resolve_candidates(
    run_root: Path,
    explicit: Path | None,
    names: tuple[str, ...],
    *,
    required: bool,
) -> Path | None:
    if explicit:
        path = explicit.expanduser().resolve()
        if path.is_file():
            return path
        if required:
            raise ValueError(f"required collector output does not exist: {path}")
        return None
    for name in names:
        path = run_root / name
        if path.is_file():
            return path
    if required:
        raise ValueError(f"required collector output does not exist under {run_root}: {names[0]}")
    return None


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    temporary.replace(path)


def _date_year(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{4})[-/]\d{1,2}[-/]\d{1,2}", value.strip())
    return int(match.group(1)) if match else None


def _facet_count(values: Any, year: int) -> int | None:
    if not isinstance(values, list):
        return None
    for item in values:
        if isinstance(item, str):
            match = re.search(rf"\b{year}\s*\(\s*(\d+)\s*\)", item)
            if match:
                return int(match.group(1))
        elif isinstance(item, dict):
            item_year = item.get("year") or item.get("value") or item.get("label")
            count = item.get("count") or item.get("total")
            if str(item_year) == str(year) and isinstance(count, int):
                return count
    return None


def _page_number_from_url(value: Any, default: int | None = None) -> int | None:
    if not isinstance(value, str) or not value:
        return default
    try:
        query = urlsplit(value).query
    except ValueError:
        return default
    for pair in query.split("&"):
        if pair.startswith("page="):
            try:
                return int(pair.split("=", 1)[1])
            except ValueError:
                return None
    return default


def _year_from_url(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        query = urlsplit(value).query
    except ValueError:
        return None
    for pair in query.split("&"):
        if pair.startswith("year="):
            try:
                return int(pair.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _validate_page_evidence(
    page_path: Path | None,
    listing_rows: list[dict[str, Any]],
    venue_id: str,
    through_year: int,
    required_years: set[int] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if page_path is None:
        return {"complete": False, "years": {}, "path": None}, ["page_evidence.jsonl is required for a complete enumeration"]
    try:
        raw_page_rows = [row for _, row in _read_jsonl(page_path)]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"complete": False, "years": {}, "path": str(page_path)}, [str(exc)]
    # A retry appends another observation for the same URL/page.  Select the
    # last usable observation, while retaining the retry count as run-local
    # evidence.  A historical failed fetch must not poison a later successful
    # recovery; an observation with cards/hash/next metadata is usable even
    # when the transport recorded a recoverable curl warning.
    page_groups: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in raw_page_rows:
        key = (
            row.get("year_filter") or row.get("venue_year") or row.get("year"),
            row.get("page_number"),
        )
        page_groups.setdefault(key, []).append(row)

    def _page_quality(row: dict[str, Any]) -> tuple[int, int]:
        fetch = row.get("fetch")
        fetch_ok = isinstance(fetch, dict) and (
            fetch.get("status") in {200, "200"} or fetch.get("cache") == "hit"
        )
        card_ok = isinstance(row.get("card_count"), int) and row.get("card_count", 0) > 0
        next_ok = isinstance(row.get("next_present"), bool)
        hash_ok = isinstance(row.get("html_sha256"), str) and bool(re.fullmatch(r"[0-9a-f]{64}", row.get("html_sha256", "")))
        usable = fetch_ok and card_ok and next_ok and hash_ok
        clean = usable and not (isinstance(fetch, dict) and fetch.get("errors"))
        return (2 if clean else 1 if usable else 0, 1)

    page_rows: list[dict[str, Any]] = []
    recovery_warnings: list[str] = []
    for key, candidates in page_groups.items():
        if len(candidates) == 1:
            page_rows.append(candidates[0])
            continue
        ranked = [(index, _page_quality(row), row) for index, row in enumerate(candidates)]
        best_quality = max(item[1][0] for item in ranked)
        usable = [item for item in ranked if item[1][0] == best_quality]
        chosen = usable[-1][2]
        page_rows.append(chosen)
        if best_quality > 0:
            recovery_warnings.append(
                f"selected usable retry for year {key[0]} page {key[1]} from {len(candidates)} observations"
            )
        else:
            recovery_warnings.append(
                f"no usable retry for year {key[0]} page {key[1]}; validating the latest observation"
            )
    listing_by_year_page: dict[tuple[int, int], list[dict[str, Any]]] = {}
    listing_by_year_ids: dict[int, set[str]] = {}
    for index, row in enumerate(listing_rows, 1):
        year = _year(row)
        identity = _identity(row)
        page_number = row.get("page_number")
        if year is None:
            errors.append(f"listing row {index} has no year")
            continue
        if not identity:
            errors.append(f"listing row {index} has no source identity")
        if not isinstance(page_number, int) or page_number < 1:
            errors.append(f"listing row {index} has no positive page_number")
            continue
        listing_by_year_page.setdefault((year, page_number), []).append(row)
        ids = listing_by_year_ids.setdefault(year, set())
        if identity in ids:
            errors.append(f"listing rows duplicate source identity in year {year}: {identity}")
        elif identity:
            ids.add(identity)
        date_value = row.get("listing_date") or row.get("publication_date") or row.get("date")
        date_year = _date_year(date_value)
        if date_year is None:
            errors.append(f"listing row {index} has no parseable listing/publication date")
        elif date_year != year:
            errors.append(f"listing row {index} date year {date_year} differs from venue year {year}")
        source_url = row.get("source_page_url") or row.get("source_url")
        if not isinstance(source_url, str) or not _nature_url(source_url):
            errors.append(f"listing row {index} has no allowlisted source page URL")
        elif _year_from_url(source_url) not in {None, year}:
            errors.append(f"listing row {index} source page URL year differs from venue year {year}")

    by_year: dict[int, list[dict[str, Any]]] = {}
    seen_pages: set[tuple[int, int]] = set()
    source_urls: set[str] = set()
    observed_at_values: list[str] = []
    for index, row in enumerate(page_rows, 1):
        year = row.get("year_filter") or row.get("venue_year") or row.get("year")
        page_number = row.get("page_number")
        if not isinstance(year, int) or not isinstance(page_number, int) or page_number < 1:
            errors.append(f"page evidence row {index} has invalid year_filter/page_number")
            continue
        key = (year, page_number)
        if key in seen_pages:
            errors.append(f"duplicate page evidence for year {year}, page {page_number}")
        seen_pages.add(key)
        by_year.setdefault(year, []).append(row)

    selected_years = required_years if required_years is not None else set(range(VENUE_START_YEARS[venue_id], through_year + 1))
    missing_years = sorted(selected_years - set(by_year))
    if missing_years:
        errors.append(f"page evidence is missing years: {missing_years}")
    year_summary: dict[int, dict[str, Any]] = {}
    for year in sorted(by_year):
        pages = sorted(by_year[year], key=lambda item: int(item["page_number"]))
        numbers = [int(item["page_number"]) for item in pages]
        if numbers != list(range(1, len(numbers) + 1)):
            errors.append(f"page evidence has a gap or duplicate page number for year {year}: {numbers}")
        facet_counts: list[int] = []
        page_card_total = 0
        for position, row in enumerate(pages):
            page_number = int(row["page_number"])
            source_url = row.get("source_url")
            if not isinstance(source_url, str) or not _nature_url(source_url):
                errors.append(f"page evidence year {year} page {page_number} has no allowlisted source_url")
            else:
                source_urls.add(source_url)
            if isinstance(row.get("observed_at"), str) and row.get("observed_at"):
                observed_at_values.append(row["observed_at"])
            if _year_from_url(source_url) != year:
                errors.append(f"page evidence year {year} page {page_number} URL does not retain year filter")
            if _page_number_from_url(source_url, 1) != page_number:
                errors.append(f"page evidence year {year} page {page_number} URL page number does not match evidence")
            card_count = row.get("card_count")
            if not isinstance(card_count, int) or card_count <= 0:
                errors.append(f"page evidence year {year} page {page_number} has empty/invalid card_count")
                card_count = 0
            page_card_total += card_count
            next_present = row.get("next_present")
            if not isinstance(next_present, bool):
                errors.append(f"page evidence year {year} page {page_number} lacks next_present")
            next_url = row.get("next_url")
            if next_present is True:
                if not isinstance(next_url, str) or not next_url:
                    errors.append(f"page evidence year {year} page {page_number} says next_present without next_url")
                elif _year_from_url(next_url) != year or _page_number_from_url(next_url, 2 if page_number == 1 else None) != page_number + 1:
                    errors.append(f"page evidence year {year} page {page_number} has a broken next chain")
                if card_count != 20:
                    errors.append(f"page evidence year {year} page {page_number} is non-terminal but card_count is {card_count}, expected 20")
            elif next_present is False:
                if next_url:
                    errors.append(f"page evidence year {year} page {page_number} terminates with a next_url")
                if card_count < 1 or card_count > 20:
                    errors.append(f"page evidence year {year} terminal page has invalid card_count {card_count}")
            fetch = row.get("fetch")
            if not isinstance(fetch, dict) or not (
                fetch.get("status") in {200, "200"} or fetch.get("cache") == "hit"
            ):
                errors.append(f"page evidence year {year} page {page_number} lacks successful fetch status")
            if isinstance(fetch, dict) and fetch.get("errors"):
                recovery_warnings.append(f"year {year} page {page_number} retained recoverable fetch warnings: {fetch.get('errors')}")
            if isinstance(fetch, dict):
                for flag in ("empty_html", "missing_selector", "selector_missing", "cards_selector_missing"):
                    if fetch.get(flag) or row.get(flag):
                        errors.append(f"page evidence year {year} page {page_number} reports {flag}")
            if not isinstance(row.get("html_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row.get("html_sha256", "")):
                errors.append(f"page evidence year {year} page {page_number} lacks HTML hash")
            facet_count = _facet_count(row.get("selected_facets"), year)
            if facet_count is None or facet_count <= 0:
                errors.append(f"page evidence year {year} page {page_number} lacks a positive selected facet count")
            else:
                facet_counts.append(facet_count)
            listed_cards = listing_by_year_page.get((year, page_number), [])
            if len(listed_cards) != card_count:
                errors.append(f"year {year} page {page_number} card_count={card_count} but listing rows={len(listed_cards)}")
        if len(set(facet_counts)) > 1:
            errors.append(f"selected facet count changes across year {year}: {facet_counts}")
        facet_count = facet_counts[0] if facet_counts else 0
        listing_count = len(listing_by_year_ids.get(year, set()))
        page_set = set(numbers)
        for row in pages:
            page_number = int(row["page_number"])
            next_present = row.get("next_present")
            next_number = page_number + 1
            if next_present is True and next_number not in page_set:
                errors.append(f"page evidence year {year} page {page_number} points to missing page {next_number}")
            if next_present is False and page_number != len(pages):
                errors.append(f"page evidence year {year} page {page_number} terminates before the final observed page")
        if facet_count != page_card_total or facet_count != listing_count:
            errors.append(
                f"year {year} count mismatch: facet={facet_count}, pages={page_card_total}, unique_listing_ids={listing_count}"
            )
        year_summary[year] = {
            "pages": len(pages),
            "facet_count": facet_count,
            "page_card_total": page_card_total,
            "unique_listing_ids": listing_count,
        }
    return {
        "complete": not errors,
        "years": year_summary,
        "path": str(page_path),
        "raw_observations": len(raw_page_rows),
        "selected_observations": len(page_rows),
        "recovery_warnings": recovery_warnings,
        "source_urls": sorted(source_urls),
        "observed_at": max(observed_at_values) if observed_at_values else None,
    }, errors


def _normalize_missing(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        mapped: dict[str, Any] = {}
        for field, detail in value.items():
            if isinstance(detail, dict):
                raw_reason = detail.get("reason_code") or detail.get("reason") or detail.get("status")
                raw_detail = detail.get("detail") or detail.get("message") or detail.get("raw_reason")
            else:
                raw_reason = detail
                raw_detail = None
            reason_text = str(raw_reason).strip() if raw_reason is not None else ""
            if reason_text in ALLOWED_MISSING_REASONS:
                reason = reason_text
            else:
                lowered = reason_text.casefold()
                if "abstract" in str(field).casefold() and ("absent" in lowered or "not found" in lowered or "visible" in lowered):
                    reason = "not_present_on_official_page"
                elif any(token in lowered for token in ("restrict", "denied", "forbidden", "access")):
                    reason = "access_restricted"
                elif any(token in lowered for token in ("not visible", "invisible", "hidden")):
                    reason = "not_visible"
                elif any(token in lowered for token in ("publisher", "suppl", "does not supply")):
                    reason = "publisher_does_not_supply"
                elif any(token in lowered for token in ("not assign", "unassigned")):
                    reason = "not_assigned"
                elif any(token in lowered for token in ("unavailable", "failed to fetch", "source")):
                    reason = "source_unavailable"
                elif reason_text:
                    # This fallback records that the field was checked without
                    # claiming why an identity, title, or author is absent.
                    reason = "checked_missing"
                else:
                    mapped[field] = detail
                    continue
            if isinstance(detail, dict):
                normalized_detail = dict(detail)
                normalized_detail["reason_code"] = reason
                if reason_text and reason_text != reason:
                    normalized_detail["raw_reason"] = reason_text
                if raw_detail and "detail" not in normalized_detail:
                    normalized_detail["detail"] = raw_detail
                mapped[field] = normalized_detail
            else:
                mapped[field] = {"reason_code": reason, **({"raw_reason": reason_text} if reason_text and reason_text != reason else {})}
        return mapped
    if isinstance(value, str) and value.strip():
        # Preserve an unstructured collector note without guessing which field
        # it belongs to. The shared validator will fail a null required field
        # until the collector supplies a structured field-specific reason.
        return {"_raw_missing_fields": value}
    return {}


def _normalize_provenance(row: dict[str, Any], normalized: dict[str, Any], observed_at: Any, source_url: Any) -> dict[str, dict[str, Any]]:
    raw = row.get("field_provenance")
    if isinstance(raw, list):
        raw = {str(item.get("field")): item for item in raw if isinstance(item, dict) and item.get("field")}
    raw = raw if isinstance(raw, dict) else {}
    provenance: dict[str, dict[str, Any]] = {}
    fields = (
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
    for field in fields:
        item = dict(raw.get(field)) if isinstance(raw.get(field), dict) else {}
        if "source_url" not in item and isinstance(source_url, str):
            item["source_url"] = source_url
        if "observed_at" not in item and isinstance(observed_at, str):
            item["observed_at"] = observed_at
        if "status" not in item:
            item["status"] = "present" if normalized.get(field) not in (None, "", []) else "checked_missing"
        provenance[field] = item
    for field, value in raw.items():
        if field not in provenance and isinstance(value, dict):
            provenance[field] = dict(value)
    return provenance


def _ensure_exclusion_provenance(normalized: dict[str, Any], source_url: Any, observed_at: Any) -> None:
    provenance = normalized.get("field_provenance")
    if not isinstance(provenance, dict):
        provenance = {}
        normalized["field_provenance"] = provenance
    for field in ("inclusion_decision", "exclusion_reason_code"):
        item = provenance.get(field)
        if not isinstance(item, dict):
            item = {}
            provenance[field] = item
        if "source_url" not in item and isinstance(source_url, str):
            item["source_url"] = source_url
        if "observed_at" not in item and isinstance(observed_at, str):
            item["observed_at"] = observed_at
        item.setdefault("status", "observed")


def _normalize_detail_row(row: dict[str, Any], venue_id: str, *, exclusion: bool = False) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    normalized = dict(row)
    identity = _identity(row)
    year = _year(row)
    observed_at = row.get("observed_at") or row.get("listing_observed_at")
    source_url = row.get("source_url") or row.get("source_page_url") or row.get("landing_url")
    landing_url = row.get("landing_url") or row.get("source_url")
    normalized["venue_id"] = row.get("venue_id") or venue_id
    normalized["source_native_id"] = identity
    raw_source_item_id = row.get("source_item_id")
    if isinstance(raw_source_item_id, str) and raw_source_item_id.strip() and raw_source_item_id.strip() != identity:
        raw_venue_identity = normalized.get("venue_identity")
        venue_identity = dict(raw_venue_identity) if isinstance(raw_venue_identity, dict) else {}
        # The portfolio collector reports venue_identity as a boolean.  Keep
        # that journal-match assertion while adapting the row to the object
        # shape used by the staging contract for raw identity evidence.
        if isinstance(raw_venue_identity, bool):
            venue_identity.setdefault("confirmed", raw_venue_identity)
        normalized["venue_identity"] = venue_identity
        venue_identity.setdefault("raw_source_item_id", raw_source_item_id.strip())
    normalized["year"] = year
    normalized["title"] = row.get("title") or row.get("title_listing")
    normalized["authors"] = row.get("authors") or row.get("authors_listing") or []
    normalized["abstract"] = row.get("abstract")
    normalized["document_type"] = row.get("document_type") or row.get("listed_document_type")
    normalized["publication_date"] = row.get("publication_date") or row.get("listing_date")
    normalized["doi"] = row.get("doi")
    if not normalized["doi"]:
        candidate = row.get("normalized_identity")
        if isinstance(candidate, str) and re.fullmatch(r"10\.\d{4,9}/\S+", candidate.strip().lower()):
            normalized["doi"] = candidate.strip()
    normalized["landing_url"] = landing_url
    normalized["source_url"] = source_url
    normalized["source_page_url"] = row.get("source_page_url")
    normalized["observed_at"] = observed_at
    normalized["pdf_url"] = row.get("pdf_url")
    normalized["pdf_discovery_status"] = row.get("pdf_discovery_status") or ("visible_url" if normalized["pdf_url"] else "metadata_only")
    normalized["missing_fields"] = _normalize_missing(row.get("missing_fields"))
    issue = row.get("issue_assignment")
    if isinstance(issue, dict):
        for field in ("volume", "issue", "pages", "start_page", "end_page"):
            if field not in normalized and issue.get(field) is not None:
                normalized[field] = issue.get(field)
    normalized["field_provenance"] = _normalize_provenance(row, normalized, observed_at, source_url)
    decision = row.get("inclusion_decision") or row.get("include_decision")
    if exclusion or str(decision).casefold() in {"exclude", "excluded"}:
        normalized["schema_version"] = "literature-metadata-exclusion-v1"
        normalized["inclusion_decision"] = "exclude"
        reason = _map_exclusion_reason(row)
        if reason:
            normalized["exclusion_reason_code"] = reason
        _ensure_exclusion_provenance(normalized, source_url, observed_at)
    else:
        normalized["schema_version"] = "literature-metadata-staging-v1"
        normalized["inclusion_decision"] = "include"
    if normalized.get("venue_id") != venue_id:
        errors.append(f"row identity {identity or '<missing>'} has venue_id {normalized.get('venue_id')!r}, expected {venue_id!r}")
    if not identity:
        errors.append("detail row has no source identity")
    if year is None:
        errors.append(f"detail row {identity or '<missing>'} has no explicit year/venue_year")
    if not isinstance(observed_at, str) or not observed_at:
        errors.append(f"detail row {identity or '<missing>'} has no observed_at to inherit into provenance")
    return normalized, errors


def _map_exclusion_reason(row: dict[str, Any]) -> str | None:
    raw = row.get("exclusion_reason_code") or row.get("excluded_reason_code") or row.get("exclusion_reason") or row.get("reason")
    if isinstance(raw, str) and raw in ALLOWED_EXCLUSION_REASONS:
        return raw
    text = " ".join(str(value) for value in (raw, row.get("document_type"), row.get("listed_document_type"), row.get("title_listing")) if value).casefold()
    if "retraction" in text:
        return "retraction"
    if "erratum" in text:
        return "erratum"
    if "correction" in text:
        return "correction"
    if "editorial" in text:
        return "editorial"
    if "news" in text:
        return "news"
    if "book" in text or "books & arts" in text:
        return "book_review"
    if raw:
        return "non_research_content"
    return None


def _review_findings(value: Any, prefix: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).casefold()
            path = f"{prefix}.{key}" if prefix else str(key)
            negated_manual_review = "manual_review" in key_text and key_text.startswith("no_")
            if negated_manual_review:
                if isinstance(child, bool) and not child:
                    findings.append(f"{path}=false")
                elif isinstance(child, str) and child.strip().casefold() in {"false", "no", "pending", "unresolved"}:
                    findings.append(f"{path}={child}")
                elif isinstance(child, dict):
                    for count_key in ("count", "pending", "unresolved"):
                        count_value = child.get(count_key)
                        if isinstance(count_value, int) and count_value > 0:
                            findings.append(f"{path}.{count_key}={count_value}")
            elif "manual_review" in key_text and any(token in key_text for token in ("empty", "clear", "none", "resolved")):
                # Collection receipts commonly record the clean state as
                # ``manual_review_empty: true``.  Treat only that positive
                # assertion as clean; a false/string-pending value remains a
                # blocking finding.
                if isinstance(child, bool) and not child:
                    findings.append(f"{path}=false")
                elif isinstance(child, int) and not isinstance(child, bool) and child > 0:
                    findings.append(f"{path}={child}")
                elif isinstance(child, str) and child.strip().casefold() not in {
                    "", "true", "yes", "empty", "none", "clear", "resolved", "complete", "completed", "pass", "passed"
                }:
                    findings.append(f"{path}={child}")
                elif isinstance(child, dict):
                    for count_key in ("count", "pending", "unresolved"):
                        count_value = child.get(count_key)
                        if isinstance(count_value, int) and count_value > 0:
                            findings.append(f"{path}.{count_key}={count_value}")
            elif "manual_review" in key_text or key_text in {"review_status", "resolution_status", "detail_status"}:
                if isinstance(child, bool) and child:
                    findings.append(f"{path}=true")
                elif isinstance(child, int) and child > 0:
                    findings.append(f"{path}={child}")
                elif isinstance(child, list) and child:
                    unresolved = []
                    for item in child:
                        if isinstance(item, dict):
                            item_status = str(
                                item.get("resolution_status")
                                or item.get("review_status")
                                or item.get("status")
                                or ""
                            ).strip().casefold()
                            if item_status in {"resolved", "complete", "completed", "pass", "passed", "closed", "none"}:
                                continue
                        unresolved.append(item)
                    if unresolved:
                        findings.append(f"{path} contains {len(unresolved)} unresolved entries")
                elif isinstance(child, dict):
                    for count_key in ("count", "pending", "unresolved"):
                        count_value = child.get(count_key)
                        if isinstance(count_value, int) and count_value > 0:
                            findings.append(f"{path}.{count_key}={count_value}")
                elif isinstance(child, str) and child.strip().casefold() not in {"", "none", "resolved", "complete", "completed", "pass"}:
                    findings.append(f"{path}={child}")
            elif key_text == "status" and isinstance(child, str) and child.strip().casefold() in {
                "manual_review",
                "needs_review",
                "pending",
                "unresolved",
            }:
                findings.append(f"{path}={child}")
            findings.extend(_review_findings(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_review_findings(child, f"{prefix}[{index}]"))
    return findings


def _detail_manifest_review(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        try:
            if path.suffix == ".jsonl":
                values = [row for _, row in _read_jsonl(path)]
            else:
                with path.open("r", encoding="utf-8") as handle:
                    value = json.load(handle)
                values = value if isinstance(value, list) else [value]
            for value in values:
                findings.extend(f"{path}: {finding}" for finding in _review_findings(value))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            findings.append(f"{path}: {exc}")
    return findings


def _normalize_metadata_file(
    path: Path | None,
    venue_id: str,
    label: str,
    *,
    force_exclusion: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str], set[str], list[str], list[str], list[str]]:
    """Normalize one collector output without silently accepting undecided rows.

    The return values are accepted rows, explicit exclusions, accepted IDs,
    exclusion IDs, errors, accepted duplicates, and exclusion duplicates.
    Keeping the two ID sets separate lets preparation enforce the accounting
    invariant ``enumeration == accepted ∪ explicit exclusions``.
    """
    accepted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    accepted_ids: set[str] = set()
    excluded_ids: set[str] = set()
    errors: list[str] = []
    accepted_duplicates: list[str] = []
    excluded_duplicates: list[str] = []
    if path is None:
        return accepted, excluded, accepted_ids, excluded_ids, errors, accepted_duplicates, excluded_duplicates
    try:
        parsed = _read_jsonl(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return accepted, excluded, accepted_ids, excluded_ids, [str(exc)], accepted_duplicates, excluded_duplicates
    for line_no, row in parsed:
        normalized, row_errors = _normalize_detail_row(row, venue_id, exclusion=force_exclusion)
        identity = _identity(normalized)
        errors.extend(f"{label} row {line_no}: {error}" for error in row_errors)
        decision = str(row.get("include_decision") or row.get("inclusion_decision") or "").strip().casefold()
        is_excluded = force_exclusion or decision in {"exclude", "excluded"}
        is_included = decision in {
            "include",
            "included",
            "include_with_missing_fields",
            "included_with_missing_fields",
        } and not force_exclusion
        if is_excluded:
            normalized["inclusion_decision"] = "exclude"
            reason = normalized.get("exclusion_reason_code") or _map_exclusion_reason(row)
            if not reason:
                errors.append(f"{label} row {line_no} has no explicit exclusion reason")
            else:
                normalized["exclusion_reason_code"] = reason
            _ensure_exclusion_provenance(normalized, normalized.get("source_url"), normalized.get("observed_at"))
            if identity and identity in excluded_ids:
                excluded_duplicates.append(identity)
                errors.append(f"{label} duplicate source identity: {identity}")
            elif identity:
                excluded_ids.add(identity)
            excluded.append(normalized)
        elif is_included:
            normalized["inclusion_decision"] = "include"
            if identity and identity in accepted_ids:
                accepted_duplicates.append(identity)
                errors.append(f"{label} duplicate source identity: {identity}")
            elif identity:
                accepted_ids.add(identity)
            accepted.append(normalized)
        else:
            # A manual-review/pending row is neither accepted nor excluded;
            # leaving it out of the sets makes the missing-ID accounting fail
            # closed as well as exposing the reason directly.
            errors.append(
                f"{label} row {line_no} has unresolved inclusion_decision {decision or '<missing>'}"
            )
    return accepted, excluded, accepted_ids, excluded_ids, errors, accepted_duplicates, excluded_duplicates


def _discover_detail_review(run_root: Path, explicit: Path | None) -> list[Path]:
    if explicit:
        return [explicit.expanduser().resolve()]
    return [run_root / name for name in _RAW_DETAIL_REVIEW_NAMES if (run_root / name).is_file()]


def _enumeration(path: Path) -> tuple[list[dict[str, Any]], dict[str, int], list[str], list[str]]:
    errors: list[str] = []
    duplicates: list[str] = []
    by_id: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    try:
        parsed = _read_jsonl(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [], {}, [str(exc)], []
    for line_no, row in parsed:
        identity = _identity(row)
        if not identity:
            errors.append(f"enumeration row {line_no} has no source identity")
            rows.append(row)
            continue
        if identity in by_id:
            duplicates.append(identity)
            errors.append(f"enumeration duplicate source identity: {identity}")
        else:
            by_id[identity] = line_no
        rows.append(row)
    return rows, by_id, errors, duplicates


def _prepare_historical_reuse(
    *,
    seed_path: Path,
    receipt_path: Path,
    output_root: Path,
    through_year: int,
    thread_receipt: Path | None = None,
    state_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Validate and adapt the accepted Nature count baseline.

    The baseline proves enumeration identity sets only.  Its rows are copied
    into a run-local enumeration adapter with ``historical_reuse`` markers;
    no metadata staging rows are produced here.  A fresh 2026 enumeration is
    still required by :func:`prepare_run`.
    """
    seed_path = seed_path.expanduser().resolve()
    receipt_path = receipt_path.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    closed_years = list(range(VENUE_START_YEARS["nature"], 2026))
    all_years = closed_years + [2026]
    artifact: dict[str, Any] = {
        "schema_version": "nature-historical-closed-year-evidence-v1",
        "venue_id": "nature",
        "status": "BLOCKED",
        "seed": str(seed_path),
        "receipt": str(receipt_path),
        "closed_years": closed_years,
        "current_year": 2026,
        "current_year_refresh_required": True,
        "metadata_complete": False,
        "detail_refresh_required": True,
        "enumeration_only": True,
        "errors": [],
    }
    errors: list[str] = []
    seed_rows: list[dict[str, Any]] = []
    source_entries: dict[int, dict[str, Any]] = {}
    source_rows_by_year: dict[int, list[dict[str, Any]]] = {}
    source_root: Path | None = None
    receipt: dict[str, Any] = {}
    thread_path: Path | None = None
    output_manifest_path: Path | None = None
    count_report_path: Path | None = None

    try:
        parsed_seed = _read_jsonl(seed_path)
        seed_rows = [row for _, row in parsed_seed]
        receipt = _load_evidence(receipt_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))

    seed_count = receipt.get("seed_count")
    if not isinstance(seed_count, int) or seed_count < 1:
        errors.append("historical receipt seed_count must be a positive integer")
    elif seed_count != len(seed_rows):
        errors.append(f"historical receipt seed_count {seed_count} does not equal seed rows {len(seed_rows)}")
    if receipt.get("current_year_refresh_required") is not True:
        errors.append("historical receipt must require a current-year refresh")
    if through_year != 2026:
        errors.append("historical reuse is supported only through current year 2026")

    raw_sources = receipt.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        errors.append("historical receipt sources must be a non-empty list")
        raw_sources = []
    for item in raw_sources:
        if not isinstance(item, dict):
            errors.append("historical receipt source entry is not an object")
            continue
        raw_file = item.get("file") or item.get("path")
        raw_hash = item.get("sha256")
        if not isinstance(raw_file, str) or not raw_file.strip():
            errors.append("historical receipt source entry has no file path")
            continue
        source_path = Path(raw_file).expanduser()
        if not source_path.is_absolute():
            source_path = receipt_path.parent / source_path
        source_path = source_path.resolve()
        match = re.fullmatch(r"(\d{4})\.jsonl(?:\.gz)?", source_path.name)
        if not match:
            errors.append(f"historical receipt source is not a year manifest: {source_path}")
            continue
        year = int(match.group(1))
        if year in source_entries:
            errors.append(f"historical receipt has duplicate source manifest for {year}")
            continue
        if not isinstance(raw_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_hash):
            errors.append(f"historical receipt source hash for {year} is not lowercase 64-hex")
        source_entries[year] = {
            "year": year,
            "path": source_path,
            "sha256": raw_hash,
        }
        source_root = source_path.parent.parent
    if set(source_entries) != set(all_years):
        errors.append(f"historical receipt source years must be {all_years}, got {sorted(source_entries)}")

    manifest_hashes: list[dict[str, Any]] = []
    for year in all_years:
        entry = source_entries.get(year)
        if not entry:
            continue
        path = entry["path"]
        if not path.is_file():
            errors.append(f"historical expected manifest does not exist for {year}: {path}")
            continue
        try:
            actual_hash = _sha256_file(path)
        except OSError as exc:
            errors.append(str(exc))
            continue
        entry["actual_sha256"] = actual_hash
        if actual_hash != entry.get("sha256"):
            errors.append(f"historical expected manifest SHA-256 mismatch for {year}: {path}")
        try:
            parsed = _read_jsonl(path)
            source_rows_by_year[year] = [row for _, row in parsed]
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            source_rows_by_year[year] = []
        source_ids: list[str] = []
        source_seen: set[str] = set()
        for index, row in enumerate(source_rows_by_year.get(year, []), 1):
            identity = _identity(row)
            row_year = _year(row)
            if not identity:
                errors.append(f"historical expected row {year}:{index} has no source identity")
                continue
            if identity in source_seen:
                errors.append(f"historical expected manifest {year} duplicates source identity {identity}")
            source_seen.add(identity)
            source_ids.append(identity)
            if row_year != year:
                errors.append(f"historical expected row {year}:{index} has year {row_year}")
        entry["count"] = len(source_ids)
        entry["identity_set_sha256"] = _sha256_ids(source_ids)
        manifest_hashes.append({
            "year": year,
            "path": str(path),
            "sha256": entry.get("actual_sha256"),
            "identity_set_sha256": entry["identity_set_sha256"],
            "count": len(source_ids),
        })

    # The receipt's source paths and hashes are the primary evidence.  The
    # output manifest is an independent final-byte check for the same files.
    if source_root is not None:
        thread_path = (thread_receipt.expanduser().resolve() if thread_receipt else source_root / "thread_receipt.json")
        output_manifest_path = source_root / "output_manifest.json"
        count_report_path = source_root / "venue_count_report.json"
    elif thread_receipt:
        thread_path = thread_receipt.expanduser().resolve()
    if thread_path is None or not thread_path.is_file():
        errors.append(f"accepted historical thread receipt is missing: {thread_path}")
    if output_manifest_path is None or not output_manifest_path.is_file():
        errors.append(f"accepted historical output manifest is missing: {output_manifest_path}")
    if count_report_path is None or not count_report_path.is_file():
        errors.append(f"accepted historical count report is missing: {count_report_path}")

    thread: dict[str, Any] = {}
    output_manifest: dict[str, Any] = {}
    count_report: dict[str, Any] = {}
    for label, path, target in (
        ("thread receipt", thread_path, "thread"),
        ("output manifest", output_manifest_path, "output_manifest"),
        ("count report", count_report_path, "count_report"),
    ):
        if path is None or not path.is_file():
            continue
        try:
            value = _load_evidence(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        if target == "thread":
            thread = value
        elif target == "output_manifest":
            output_manifest = value
        else:
            count_report = value
    if thread:
        if thread.get("status") != "PASS" or thread.get("role") != "venue-count" or thread.get("venue_id") != "nature":
            errors.append("historical thread receipt is not the accepted Nature venue-count PASS")
        if isinstance(seed_count, int) and thread.get("records_emitted") != seed_count:
            errors.append("historical thread receipt records_emitted does not equal seed_count")
        if thread.get("unresolved_anomalies") or thread.get("unresolved_drift"):
            errors.append("historical thread receipt contains unresolved anomalies or drift")
    if output_manifest:
        if output_manifest.get("status") != "PASS" or output_manifest.get("venue_id") != "nature" or output_manifest.get("role") != "venue-count":
            errors.append("historical output manifest is not the accepted Nature venue-count PASS")
        artifacts = output_manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            errors.append("historical output manifest has no artifacts map")
        else:
            for entry in manifest_hashes:
                key = f"expected/{Path(entry['path']).name}"
                record = artifacts.get(key)
                if not isinstance(record, dict):
                    record = next(
                        (item for item in artifacts.values() if isinstance(item, dict) and item.get("path") == entry["path"]),
                        None,
                    )
                if not isinstance(record, dict) or record.get("sha256") != entry.get("sha256"):
                    errors.append(f"historical output manifest hash mismatch for {entry['year']}")
    if count_report:
        totals = count_report.get("totals") if isinstance(count_report.get("totals"), dict) else {}
        checks = count_report.get("checks") if isinstance(count_report.get("checks"), dict) else {}
        if count_report.get("status") != "PASS" or count_report.get("venue_id") != "nature":
            errors.append("historical count report is not Nature PASS")
        if isinstance(seed_count, int) and totals.get("total_expected_records") != seed_count:
            errors.append("historical count report total does not equal seed_count")
        for key in ("every_year_present", "count_equals_manifest_all_years", "duplicate_checks_pass_all_years", "conflict_checks_pass_all_years"):
            if checks.get(key) is not True:
                errors.append(f"historical count report check is not PASS: {key}")

    # The accepted state pointer must point to the same PASS receipt.  The
    # state file is read only and can be replaced by a test fixture.
    state_path = (state_path or LitDBPaths.from_value(None).state).expanduser().resolve()
    artifact["campaign_state"] = str(state_path)
    if not state_path.is_file():
        errors.append(f"campaign state is missing: {state_path}")
    else:
        try:
            state = _load_evidence(state_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"campaign state: {exc}")
            state = {}
        venue_state = state.get("venues", {}).get("nature") if isinstance(state.get("venues"), dict) else None
        if not isinstance(venue_state, dict) or venue_state.get("state") != "COUNT_BASELINED":
            errors.append("campaign state does not mark Nature COUNT_BASELINED")
        last_receipt = venue_state.get("last_receipt") if isinstance(venue_state, dict) else None
        if not isinstance(last_receipt, str) or thread_path is None or Path(last_receipt).expanduser().resolve() != thread_path:
            errors.append("campaign state last_receipt does not point to accepted historical thread receipt")

    seed_ids_by_year: dict[int, set[str]] = {year: set() for year in all_years}
    seed_seen: set[str] = set()
    source_gap_rows: list[dict[str, Any]] = []
    for index, row in enumerate(seed_rows, 1):
        identity = _identity(row)
        year = _year(row)
        if row.get("venue_id") != "nature":
            errors.append(f"historical seed row {index} has venue_id {row.get('venue_id')!r}")
        if not identity:
            errors.append(f"historical seed row {index} has no source identity")
            continue
        if identity in seed_seen:
            errors.append(f"historical seed duplicates source identity {identity}")
        seed_seen.add(identity)
        if year not in seed_ids_by_year:
            errors.append(f"historical seed row {index} has year outside 2015–2026: {year}")
            continue
        seed_ids_by_year[year].add(identity)
        provenance = row.get("historical_provenance") if isinstance(row.get("historical_provenance"), dict) else {}
        if provenance.get("source_gap") or row.get("historical_manual_review_required"):
            source_gap_rows.append({
                "source_native_id": identity,
                "year": year,
                "title": row.get("title"),
                "reason": provenance.get("reconciliation_status") or "historical_source_gap",
                "historical_manual_review_required": True,
                "historical_provenance": provenance,
            })
    for year in all_years:
        entry = source_entries.get(year)
        source_rows = source_rows_by_year.get(year, [])
        source_ids = {_identity(row) for row in source_rows if _identity(row)}
        if len(seed_ids_by_year[year]) != len(source_rows):
            errors.append(f"historical seed count for {year} does not equal expected manifest rows")
        if seed_ids_by_year[year] != source_ids:
            errors.append(f"historical seed identity set does not match expected manifest for {year}")
        if entry and entry.get("count") != len(seed_ids_by_year[year]):
            errors.append(f"historical expected manifest count for {year} does not match seed")
    if len(source_gap_rows) != 2 or any(item["year"] != 2025 for item in source_gap_rows):
        errors.append("historical source-gap review set must contain the two 2025 rows")

    artifact.update({
        "thread_receipt": str(thread_path) if thread_path else None,
        "output_manifest": str(output_manifest_path) if output_manifest_path else None,
        "venue_count_report": str(count_report_path) if count_report_path else None,
        "receipt_seed_count": seed_count,
        "source_manifests": manifest_hashes,
        "per_year_counts": {str(year): len(seed_ids_by_year[year]) for year in all_years},
        "source_gap_rows": source_gap_rows,
        "closed_year_identity_set_sha256": _sha256_ids(
            identity for year in closed_years for identity in seed_ids_by_year[year]
        ),
        "errors": errors,
    })
    historical_rows: list[dict[str, Any]] = []
    if not errors:
        for row in seed_rows:
            year = _year(row)
            if year not in closed_years:
                continue
            identity = _identity(row)
            adapted = dict(row)
            adapted["venue_id"] = "nature"
            adapted["source_native_id"] = identity
            adapted["stable_article_path"] = identity if identity.startswith("/") else adapted.get("stable_article_path")
            adapted["year"] = year
            adapted["source_url"] = adapted.get("source_url") or adapted.get("landing_url")
            adapted["enumeration_kind"] = "historical_reuse"
            adapted["historical_reuse"] = True
            adapted["metadata_complete"] = False
            historical_rows.append(adapted)
        _write_jsonl(output_root / "historical_enumeration.jsonl", historical_rows)
        artifact["status"] = "PASS"
    else:
        _write_jsonl(output_root / "historical_enumeration.jsonl", [])
    artifact["historical_enumeration"] = str(output_root / "historical_enumeration.jsonl")
    artifact["generated_at"] = utc_now()
    atomic_json(output_root / "historical_closed_year_evidence.json", artifact)
    return historical_rows, artifact, errors


def _write_expected(expected_root: Path, venue_id: str, rows: list[dict[str, Any]], through_year: int) -> tuple[list[str], list[str]]:
    expected_root.mkdir(parents=True, exist_ok=True)
    for old in expected_root.glob("*.jsonl.gz"):
        old.unlink()
    for old in expected_root.glob("*.jsonl"):
        old.unlink()
    years: dict[int, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for row in rows:
        identity = _identity(row)
        year = _year(row)
        if not identity or year is None:
            continue
        if year < VENUE_START_YEARS[venue_id] or year > through_year:
            errors.append(f"enumeration identity {identity} has year outside selected range: {year}")
            continue
        expected = {
            "source_native_id": identity,
            "year": year,
        }
        document_type = row.get("source_document_type") or row.get("document_type") or row.get("listed_document_type")
        source_url = row.get("source_url") or row.get("source_page_url") or row.get("landing_url")
        enumeration_kind = row.get("enumeration_kind") or row.get("baseline_enumeration_kind")
        if document_type:
            expected["source_document_type"] = document_type
        if source_url:
            expected["source_url"] = source_url
        if enumeration_kind:
            expected["enumeration_kind"] = enumeration_kind
        for key in ("historical_reuse", "historical_manual_review_required", "source_gap", "historical_provenance"):
            if key in row:
                expected[key] = row[key]
        years.setdefault(year, []).append(expected)
    paths: list[str] = []
    for year, year_rows in sorted(years.items()):
        path = expected_root / f"{year}.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            for row in sorted(year_rows, key=lambda item: item["source_native_id"]):
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        paths.append(str(path))
    return paths, errors


def prepare_run(
    *,
    run_root: Path,
    venue_id: str,
    output_root: Path | None = None,
    staging: Path | None = None,
    exclusions: Path | None = None,
    enumeration: Path | None = None,
    page_evidence: Path | None = None,
    detail_manifest: Path | None = None,
    evidence: list[Path] | None = None,
    through_year: int | None = None,
    historical_seed: Path | None = None,
    historical_receipt: Path | None = None,
    historical_thread_receipt: Path | None = None,
    historical_state: Path | None = None,
) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    if venue_id not in VENUE_START_YEARS:
        raise ValueError(f"unsupported Nature venue: {venue_id}")
    if not run_root.is_dir():
        raise ValueError(f"run root does not exist: {run_root}")
    output_root = (output_root or (run_root / "prepared")).expanduser().resolve()
    if output_root == run_root:
        raise ValueError("output-root must be separate from collector run-root")
    output_root.mkdir(parents=True, exist_ok=True)
    through_year = through_year or datetime.now(timezone.utc).year
    result: dict[str, Any] = {
        "schema_version": "nature-staging-preparation-v1",
        "venue_id": venue_id,
        "run_root": str(run_root),
        "prepared_root": str(output_root),
        "through_year": through_year,
        "writes": {
            "production_catalog": False,
            "registry": False,
            "campaign_state": False,
        },
        "errors": [],
        "warnings": [],
    }
    try:
        historical_seed_path = _resolve_candidates(
            run_root,
            historical_seed,
            ("historical_seed.jsonl",),
            required=False,
        )
        historical_receipt_path = _resolve_candidates(
            run_root,
            historical_receipt,
            ("historical_seed_receipt.json",),
            required=False,
        )
        history_requested = bool(
            historical_seed_path
            or historical_receipt_path
            or historical_seed
            or historical_receipt
        )
        historical_rows: list[dict[str, Any]] = []
        historical_summary: dict[str, Any] = {}
        historical_errors: list[str] = []
        if history_requested:
            if venue_id != "nature":
                historical_errors.append("historical enumeration reuse is supported only for venue nature")
            if historical_seed_path is None or historical_receipt_path is None:
                historical_errors.append("historical_seed.jsonl and historical_seed_receipt.json are both required")
            else:
                historical_rows, historical_summary, historical_errors = _prepare_historical_reuse(
                    seed_path=historical_seed_path,
                    receipt_path=historical_receipt_path,
                    output_root=output_root,
                    through_year=through_year,
                    thread_receipt=historical_thread_receipt,
                    state_path=historical_state,
                )
        enumeration_path = _resolve_candidates(
            run_root,
            enumeration,
            ("enumeration.jsonl",) + _RAW_ENUMERATION_NAMES,
            required=True,
        )
        page_evidence_path = _resolve_candidates(
            run_root,
            page_evidence,
            ("page_evidence.jsonl",) + _RAW_PAGE_EVIDENCE_NAMES,
            required=False,
        )
        staging_path = _resolve_candidates(
            run_root,
            staging,
            ("metadata_staging.jsonl",) + _RAW_STAGING_NAMES,
            required=False,
        )
        exclusion_path = _resolve_candidates(
            run_root,
            exclusions,
            ("metadata_exclusions.jsonl",) + _RAW_EXCLUSION_NAMES,
            required=False,
        )
        assert enumeration_path is not None
        current_enum_rows, current_enum_by_id, enum_errors, enum_duplicates = _enumeration(enumeration_path)
        if history_requested:
            for line_no, row in enumerate(current_enum_rows, 1):
                if _year(row) != through_year:
                    enum_errors.append(
                        f"current enumeration row {line_no} has year {_year(row)!r}; historical reuse requires current year {through_year}"
                    )
            enum_rows = historical_rows + current_enum_rows
            enum_by_id: dict[str, int] = {}
            for index, row in enumerate(enum_rows, 1):
                identity = _identity(row)
                if not identity:
                    continue
                if identity in enum_by_id:
                    enum_duplicates.append(identity)
                    enum_errors.append(f"combined enumeration duplicate source identity: {identity}")
                else:
                    enum_by_id[identity] = index
            overlap = set(_identity(row) for row in historical_rows if _identity(row)) & set(current_enum_by_id)
            if overlap:
                enum_errors.append(f"current enumeration overlaps historical reuse identities: {sorted(overlap)[:20]}")
            page_summary, page_errors = _validate_page_evidence(
                page_evidence_path,
                current_enum_rows,
                venue_id,
                through_year,
                required_years={through_year},
            )
        else:
            enum_rows, enum_by_id = current_enum_rows, current_enum_by_id
            page_summary, page_errors = _validate_page_evidence(page_evidence_path, enum_rows, venue_id, through_year)
        evidence_paths = _discover_evidence(run_root, evidence)
        evidence_summary, evidence_errors = _completion_summary(
            evidence_paths,
            len(current_enum_by_id) if history_requested else len(enum_by_id),
        )
        # Compact enumeration manifests intentionally omit fields that can be
        # derived only from the selected page observations.  Fill those fields
        # only after the page validator has proved every year and next chain.
        if evidence_summary.get("enumeration_complete") and page_summary.get("complete"):
            if not evidence_summary.get("source_urls"):
                evidence_summary["source_urls"] = page_summary.get("source_urls", [])
            if not evidence_summary.get("observed_at"):
                evidence_summary["observed_at"] = page_summary.get("observed_at")
            if not evidence_summary.get("new_ids"):
                evidence_summary["new_ids"] = sorted(current_enum_by_id if history_requested else enum_by_id)
            if evidence_summary.get("source_item_set_sha256") is None:
                evidence_summary["source_item_set_sha256"] = _sha256_ids(current_enum_by_id if history_requested else enum_by_id)
            if evidence_summary.get("current_source_item_count") is None:
                evidence_summary["current_source_item_count"] = len(current_enum_by_id if history_requested else enum_by_id)
            if evidence_summary.get("drift_status") not in _GOOD_DRIFT:
                # Compact enumeration/controller receipts do not carry a
                # duplicate drift field.  Derive the validator's required
                # waterline value only after the independent page, facet,
                # next-chain, and identity-count checks pass.  Raw receipts
                # remain unchanged and do not gain a synthetic PASS field.
                evidence_summary["drift_status"] = "NO_DRIFT"
                evidence_summary["drift_status_source"] = "validated_page_and_identity_evidence"
        if history_requested and historical_summary.get("status") == "PASS":
            # The waterline covers the combined expected identity set.  The
            # completion receipt above still counts only the fresh 2026 set.
            evidence_summary["new_ids"] = sorted(enum_by_id)
            evidence_summary["source_item_set_sha256"] = _sha256_ids(enum_by_id)
            evidence_summary["current_source_item_count"] = len(enum_by_id)
        detail_review_paths = _discover_detail_review(run_root, detail_manifest)
        detail_review_errors = _detail_manifest_review(detail_review_paths)
        structural_errors = (
            list(historical_errors)
            + list(enum_errors)
            + list(page_errors)
            + list(evidence_errors)
            + list(detail_review_errors)
        )

        (
            staging_rows,
            staging_excluded_rows,
            staging_ids,
            staging_excluded_ids,
            staging_errors,
            staging_duplicates,
            staging_excluded_duplicates,
        ) = _normalize_metadata_file(staging_path, venue_id, "metadata_staging", force_exclusion=False)
        (
            exclusion_include_rows,
            exclusion_rows,
            exclusion_ids_from_include,
            exclusion_ids_from_exclude,
            exclusion_errors,
            exclusion_duplicates,
            exclusion_embedded_duplicates,
        ) = _normalize_metadata_file(exclusion_path, venue_id, "metadata_exclusions", force_exclusion=True)
        if exclusion_include_rows:
            exclusion_errors.append("metadata_exclusions contains include rows")
        # A detail file can carry an explicit exclude decision.  Keep that
        # decision in the exclusion set rather than silently dropping it.
        if staging_excluded_rows:
            exclusion_rows.extend(staging_excluded_rows)
            for row in staging_excluded_rows:
                identity = _identity(row)
                if identity:
                    if identity in exclusion_ids_from_exclude:
                        exclusion_duplicates.append(identity)
                    exclusion_ids_from_exclude.add(identity)
        # When the collector has not emitted a separate exclusions file, its
        # listing cards are still explicit exclusion evidence.  This fallback
        # is limited to include_candidate=false; an undecided card never
        # becomes an exclusion by inference.
        if exclusion_path is None:
            for line_no, row in enumerate(enum_rows, 1):
                if row.get("include_candidate") is not False:
                    continue
                identity = _identity(row)
                if not identity or identity in exclusion_ids_from_exclude:
                    continue
                normalized, row_errors = _normalize_detail_row(row, venue_id, exclusion=True)
                reason = _map_exclusion_reason(row)
                if not reason:
                    row_errors.append(f"enumeration row {line_no} has no explicit exclusion reason")
                else:
                    normalized["exclusion_reason_code"] = reason
                    _ensure_exclusion_provenance(normalized, normalized.get("source_url"), normalized.get("observed_at"))
                exclusion_errors.extend(f"listing exclusion row {line_no}: {error}" for error in row_errors)
                if identity in exclusion_ids_from_exclude:
                    exclusion_duplicates.append(identity)
                    exclusion_errors.append(f"metadata_exclusions duplicate source identity: {identity}")
                else:
                    exclusion_ids_from_exclude.add(identity)
                    exclusion_rows.append(normalized)
        # A supplied exclusions file is expected to contain exclusions, but
        # accepting include rows there would make accounting ambiguous.
        exclusion_ids = exclusion_ids_from_exclude | exclusion_ids_from_include
        metadata_errors = list(staging_errors) + list(exclusion_errors)
        staging_duplicates = list(staging_duplicates) + list(staging_excluded_duplicates)
        exclusion_duplicates = list(exclusion_duplicates) + list(exclusion_embedded_duplicates)
        overlap = sorted(staging_ids & exclusion_ids)
        if overlap:
            metadata_errors.append(f"source identities appear in both staging and exclusions: {overlap[:20]}")
        expected_ids = set(enum_by_id)
        observed_ids = staging_ids | exclusion_ids
        missing_metadata = sorted(expected_ids - observed_ids)
        extra_metadata = sorted(observed_ids - expected_ids)
        if missing_metadata:
            metadata_errors.append(f"metadata is missing {len(missing_metadata)} enumerated identities")
        if extra_metadata:
            metadata_errors.append(f"metadata contains {len(extra_metadata)} identities absent from enumeration")

        copied_staging = output_root / "metadata_staging.jsonl"
        copied_exclusions = output_root / "metadata_exclusions.jsonl"
        copied_enumeration = output_root / "enumeration.jsonl"
        _write_jsonl(copied_staging, staging_rows)
        _write_jsonl(copied_exclusions, exclusion_rows)
        prepared_enum_rows: list[dict[str, Any]] = []
        for row in enum_rows:
            prepared_row = dict(row)
            prepared_row["source_native_id"] = _identity(row)
            prepared_row["year"] = _year(row)
            prepared_enum_rows.append(prepared_row)
        _write_jsonl(copied_enumeration, prepared_enum_rows)
        if staging_path is None:
            metadata_errors.append("metadata_staging.jsonl is missing")
        if page_evidence_path:
            _copy_input(page_evidence_path, output_root / "page_evidence.jsonl")

        expected_paths: list[str] = []
        expected_errors: list[str] = []
        complete = not structural_errors and bool(enum_by_id)
        if complete:
            expected_paths, expected_errors = _write_expected(output_root / "expected", venue_id, enum_rows, through_year)
            structural_errors.extend(expected_errors)
            complete = not expected_errors
        else:
            # Never let a previous successful preparation leave stale expected
            # identities for a partial retry.  The validator must see an empty
            # expected root until the current evidence proves completion.
            _write_expected(output_root / "expected", venue_id, [], through_year)

        waterline = {
            "venue_id": venue_id,
            "status": evidence_summary.get("status", "INCOMPLETE") if complete else "INCOMPLETE",
            "drift_status": evidence_summary.get("drift_status", "UNRESOLVED") if complete else "UNRESOLVED",
            "drift_status_source": evidence_summary.get("drift_status_source", "completion_evidence") if complete else None,
            "enumeration_complete": bool(complete and page_summary.get("complete") and evidence_summary.get("enumeration_complete")),
            "observed_at": evidence_summary.get("observed_at"),
            "source_urls": evidence_summary.get("source_urls", []),
            "source_item_set_sha256": evidence_summary.get("source_item_set_sha256") or (_sha256_ids(enum_by_id) if complete else None),
            "new_ids": evidence_summary.get("new_ids", []),
            "missing_ids": evidence_summary.get("missing_ids", []),
            "current_source_item_count": evidence_summary.get("current_source_item_count", len(enum_by_id)),
            "evidence_files": evidence_summary.get("evidence_files", [str(path) for path in evidence_paths]),
            "prepared_at": utc_now(),
        }
        atomic_json(output_root / "waterline_evidence.json", waterline)
        if complete and not waterline["source_item_set_sha256"]:
            structural_errors.append("complete enumeration has no source item set hash")
        status = "READY" if complete and not structural_errors and not metadata_errors else "BLOCKED"
        result.update(
            {
                "status": status,
                "warnings": list(page_summary.get("recovery_warnings", [])),
                "inputs": {
                    "enumeration": str(enumeration_path),
                    "page_evidence": str(page_evidence_path) if page_evidence_path else None,
                    "metadata_staging": str(staging_path) if staging_path else None,
                    "metadata_exclusions": str(exclusion_path) if exclusion_path else None,
                    "completion_evidence": [str(path) for path in evidence_paths],
                    "detail_manifest": [str(path) for path in detail_review_paths],
                    "historical_seed": str(historical_seed_path) if historical_seed_path else None,
                    "historical_receipt": str(historical_receipt_path) if historical_receipt_path else None,
                    "historical_thread_receipt": str(historical_thread_receipt) if historical_thread_receipt else historical_summary.get("thread_receipt"),
                    "historical_state": str(historical_state) if historical_state else historical_summary.get("campaign_state"),
                },
                "counts": {
                    "enumeration_rows": len(enum_rows),
                    "enumeration_unique_ids": len(enum_by_id),
                    "current_enumeration_rows": len(current_enum_rows),
                    "current_enumeration_unique_ids": len(current_enum_by_id),
                    "historical_reuse_rows": len(historical_rows),
                    "staging_rows": len(staging_rows),
                    "staging_unique_ids": len(staging_ids),
                    "exclusion_rows": len(exclusion_rows),
                    "exclusion_unique_ids": len(exclusion_ids),
                    "page_evidence_years": len(page_summary.get("years", {})),
                },
                "page_evidence_summary": page_summary,
                "historical_closed_year_evidence": (
                    str(output_root / "historical_closed_year_evidence.json") if history_requested else None
                ),
                "identity_accounting": {
                    "missing_metadata_ids": missing_metadata[:100],
                    "missing_metadata_count": len(missing_metadata),
                    "extra_metadata_ids": extra_metadata[:100],
                    "extra_metadata_count": len(extra_metadata),
                    "staging_exclusion_overlap": overlap[:100],
                },
                "duplicate_identities": {
                    "enumeration": enum_duplicates,
                    "metadata_staging": staging_duplicates,
                    "metadata_exclusions": exclusion_duplicates,
                },
                "expected_manifests": expected_paths,
                "waterline_evidence": str(output_root / "waterline_evidence.json"),
                "metadata_validator_input": {
                    "staging": str(copied_staging),
                    "exclusions": str(copied_exclusions),
                    "expected_root": str(output_root / "expected"),
                },
                "errors": structural_errors + metadata_errors,
                "source_rows_are_immutable": True,
            }
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result.update({"status": "BLOCKED", "errors": [str(exc)]})
    atomic_json(output_root / "prepare_report.json", result)
    return result


def validate_run(
    *,
    home: Path | None,
    run_root: Path,
    venue_id: str,
    output_root: Path | None = None,
    staging: Path | None = None,
    exclusions: Path | None = None,
    enumeration: Path | None = None,
    page_evidence: Path | None = None,
    detail_manifest: Path | None = None,
    evidence: list[Path] | None = None,
    through_year: int | None = None,
    historical_seed: Path | None = None,
    historical_receipt: Path | None = None,
    historical_thread_receipt: Path | None = None,
    historical_state: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    prepared = prepare_run(
        run_root=run_root,
        venue_id=venue_id,
        output_root=output_root,
        staging=staging,
        exclusions=exclusions,
        enumeration=enumeration,
        page_evidence=page_evidence,
        detail_manifest=detail_manifest,
        evidence=evidence,
        through_year=through_year,
        historical_seed=historical_seed,
        historical_receipt=historical_receipt,
        historical_thread_receipt=historical_thread_receipt,
        historical_state=historical_state,
    )
    prepared_root = Path(prepared["prepared_root"])
    paths = LitDBPaths.from_value(str(home) if home else None)
    validation = validate_staging(
        paths,
        venue_id,
        prepared_root,
        staging_file=prepared_root / "metadata_staging.jsonl",
        exclusion_file=prepared_root / "metadata_exclusions.jsonl",
        expected_root=prepared_root / "expected",
        strict=True,
    )
    status = "PASS" if prepared.get("status") == "READY" and validation.get("status") == "PASS" else "FAIL"
    report = {
        "schema_version": "nature-staging-validation-v1",
        "status": status,
        "venue_id": venue_id,
        "prepared_root": str(prepared_root),
        "prepare": prepared,
        "validation": validation,
        "writes": {
            "production_catalog": False,
            "registry": False,
            "campaign_state": False,
        },
    }
    atomic_json(prepared_root / "prepare_validation.json", report)
    return (0 if status == "PASS" else 2), report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "validate"):
        current = sub.add_parser(command)
        current.add_argument("--run-root", type=Path, required=True)
        current.add_argument("--venue", choices=sorted(VENUE_START_YEARS), required=True)
        current.add_argument("--output-root", type=Path)
        current.add_argument("--home", type=Path)
        current.add_argument("--staging", type=Path)
        current.add_argument("--exclusions", type=Path)
        current.add_argument("--enumeration", type=Path)
        current.add_argument("--page-evidence", type=Path)
        current.add_argument("--detail-manifest", type=Path)
        current.add_argument("--evidence", type=Path, action="append")
        current.add_argument("--through-year", type=int)
        current.add_argument("--historical-seed", type=Path)
        current.add_argument("--historical-receipt", type=Path)
        current.add_argument("--historical-thread-receipt", type=Path)
        current.add_argument("--historical-state", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare":
        result = prepare_run(
            run_root=args.run_root,
            venue_id=args.venue,
            output_root=args.output_root,
            staging=args.staging,
            exclusions=args.exclusions,
            enumeration=args.enumeration,
            page_evidence=args.page_evidence,
            detail_manifest=args.detail_manifest,
            evidence=args.evidence,
            through_year=args.through_year,
            historical_seed=args.historical_seed,
            historical_receipt=args.historical_receipt,
            historical_thread_receipt=args.historical_thread_receipt,
            historical_state=args.historical_state,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status") == "READY" else 2
    code, result = validate_run(
        home=args.home,
        run_root=args.run_root,
        venue_id=args.venue,
        output_root=args.output_root,
        staging=args.staging,
        exclusions=args.exclusions,
        enumeration=args.enumeration,
        page_evidence=args.page_evidence,
        detail_manifest=args.detail_manifest,
        evidence=args.evidence,
        through_year=args.through_year,
        historical_seed=args.historical_seed,
        historical_receipt=args.historical_receipt,
        historical_thread_receipt=args.historical_thread_receipt,
        historical_state=args.historical_state,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

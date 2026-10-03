#!/usr/bin/env python3
"""Promote an ordinary four-Nature-venue metadata merge after strict recheck.

This is a narrow compatibility finalizer for the ordinary-initialization path.
It does not alter the global transition table or registry source files.  The
only catalog/state mutation happens after a PASS merge receipt, an unchanged
staging hash, and a fresh strict ``reconcile_venue`` PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.litdb.io import atomic_json, load_json, sha256_file, utc_now  # noqa: E402
from tools.litdb.metadata_pipeline import _upsert, reconcile_venue  # noqa: E402
from tools.litdb.paths import LitDBPaths  # noqa: E402


NATURE_VENUES = frozenset({
    "nature-machine-intelligence",
    "nature-computational-science",
    "nature-methods",
    "nature",
})


def _catalog_rows(paths: LitDBPaths, table: str, venue_id: str) -> list[tuple[int, dict[str, Any]]]:
    connection = sqlite3.connect(paths.catalog)
    try:
        return _catalog_rows_connection(connection, table, venue_id)
    finally:
        connection.close()


def _catalog_rows_connection(
    connection: sqlite3.Connection,
    table: str,
    venue_id: str,
) -> list[tuple[int, dict[str, Any]]]:
    rows = connection.execute(
        f"SELECT id, payload_json FROM {table} "
        "WHERE json_extract(payload_json, '$.venue_id')=? ORDER BY id",
        (venue_id,),
    ).fetchall()
    return [(int(row[0]), json.loads(row[1])) for row in rows]


def _expected_files(expected_root: Path) -> list[Path]:
    files = sorted(expected_root.glob("*.jsonl.gz")) + sorted(expected_root.glob("*.jsonl"))
    if not files:
        raise ValueError(f"expected manifest root has no JSONL manifests: {expected_root}")
    return files


def _backup_campaign(paths: LitDBPaths, run_root: Path) -> tuple[Path, str]:
    if not paths.state.is_file():
        raise ValueError(f"campaign state is missing: {paths.state}")
    target = run_root / "backup" / "campaign_state.before.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    current_hash = sha256_file(paths.state)
    if target.is_file():
        if sha256_file(target) != current_hash:
            # A previous finalizer run deliberately keeps the original
            # pre-promotion backup; never overwrite it with post-promotion
            # state.  The caller records its hash for audit.
            return target, sha256_file(target)
        return target, current_hash
    shutil.copy2(paths.state, target)
    return target, current_hash


def _install_expected(
    paths: LitDBPaths,
    venue_id: str,
    source_root: Path,
    run_root: Path,
) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve()
    files = _expected_files(source_root)
    target_root = (paths.home / "manifests" / "expected" / venue_id).resolve()
    backup_root = run_root / "backup" / "expected" / venue_id
    target_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_manifest_path = backup_root / "backup_manifest.json"
    previous = load_json(backup_manifest_path) if backup_manifest_path.is_file() else {}
    previous_files = previous.get("files", {}) if isinstance(previous, dict) else {}
    if not isinstance(previous_files, dict):
        raise ValueError(f"invalid expected backup manifest: {backup_manifest_path}")

    source_records: list[dict[str, Any]] = []
    for source in files:
        source_hash = sha256_file(source)
        target = target_root / source.name
        backup = backup_root / source.name
        old_hash = sha256_file(target) if target.is_file() else None
        if old_hash is not None:
            prior = previous_files.get(source.name)
            if isinstance(prior, dict) and prior.get("sha256"):
                if not backup.is_file() or sha256_file(backup) != prior["sha256"]:
                    raise ValueError(f"expected backup hash mismatch: {backup}")
            elif not backup.is_file():
                shutil.copy2(target, backup)
                old_backup_hash = old_hash
                previous_files[source.name] = {"sha256": old_backup_hash, "bytes": target.stat().st_size}
            else:
                previous_files[source.name] = {"sha256": sha256_file(backup), "bytes": backup.stat().st_size}
            if old_hash == source_hash:
                action = "unchanged"
            else:
                action = "replaced"
        else:
            action = "installed"
        if action == "replaced" or action == "installed":
            temporary = target.with_name(f".{target.name}.tmp")
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        if sha256_file(target) != source_hash:
            raise ValueError(f"installed expected manifest hash mismatch: {target}")
        source_records.append({
            "name": source.name,
            "source": str(source),
            "target": str(target),
            "sha256": source_hash,
            "bytes": source.stat().st_size,
            "action": action,
            "backup": str(backup) if old_hash is not None else None,
            "backup_sha256": previous_files.get(source.name, {}).get("sha256") if old_hash is not None else None,
        })

    installed_names = {item["name"] for item in source_records}
    preserved = [
        {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in sorted(target_root.iterdir())
        if path.is_file() and path.name not in installed_names and path.name != backup_manifest_path.name
    ]
    atomic_json(backup_manifest_path, {"venue_id": venue_id, "files": previous_files, "updated_at": utc_now()})
    receipt = {
        "schema_version": "nature-expected-install-v1",
        "venue_id": venue_id,
        "source_root": str(source_root),
        "target_root": str(target_root),
        "backup_root": str(backup_root),
        "files": source_records,
        "preserved_target_files": preserved,
        "installed_at": utc_now(),
    }
    atomic_json(run_root / "expected_install_receipt.json", receipt)
    return receipt


def _write_result(run_root: Path, result: dict[str, Any]) -> dict[str, Any]:
    atomic_json(run_root / "nature_finalize_receipt.json", result)
    return result


def _complete_campaign(
    paths: LitDBPaths,
    *,
    venue_id: str,
    prior_campaign_state: str,
    promotion_path: Path,
    merge_receipt: Path,
    reconcile_receipt: Path,
    event_id: str,
    event_at: str,
) -> bool:
    """Finish campaign bookkeeping without fabricating a formal gate event."""
    state = load_json(paths.state)
    entry = state.get("venues", {}).get(venue_id) if isinstance(state.get("venues"), dict) else None
    if not isinstance(entry, dict):
        raise ValueError(f"campaign state has no venue entry: {venue_id}")
    live_state = entry.get("state")
    if live_state not in {"UNSEEN", "ACTIVE"}:
        raise ValueError(f"campaign state is not recoverable after strict reconcile: {live_state}")
    history = state.get("history")
    if not isinstance(history, list):
        history = []
    already_recorded = any(
        isinstance(item, dict) and item.get("finalizer_event_id") == event_id
        for item in history
    )
    changed = False
    if live_state == "UNSEEN":
        entry = dict(entry)
        entry["state"] = "ACTIVE"
        changed = True
        if not already_recorded:
            history.append({
                "venue_id": venue_id,
                "from": "UNSEEN",
                "to": "ACTIVE",
                "mode": "ordinary_initialization",
                "event_kind": "finalizer_state_promotion",
                "finalizer_event_id": event_id,
                "receipt": str(promotion_path.resolve()),
                "merge_receipt": str(merge_receipt),
                "reconcile_receipt": str(reconcile_receipt),
                "at": event_at,
            })
            changed = True
    elif not already_recorded:
        # reconcile_venue may already have moved BOOTSTRAP_STAGED through
        # RECONCILING to ACTIVE.  Record that fact as an audit event rather
        # than pretending the finalizer performed an illegal state jump.
        history.append({
            "venue_id": venue_id,
            "from": prior_campaign_state,
            "to": "ACTIVE",
            "mode": "ordinary_initialization",
            "event_kind": "finalizer_audit_after_strict_reconcile",
            "state_transition": "already_active_after_strict_reconcile",
            "finalizer_event_id": event_id,
            "receipt": str(promotion_path.resolve()),
            "merge_receipt": str(merge_receipt),
            "reconcile_receipt": str(reconcile_receipt),
            "at": event_at,
        })
        changed = True
    if entry.get("last_receipt") != str(promotion_path.resolve()):
        entry = dict(entry)
        entry["last_receipt"] = str(promotion_path.resolve())
        changed = True
    if entry.get("updated_at") != event_at:
        entry = dict(entry)
        entry["updated_at"] = event_at
        changed = True
    if changed:
        state["venues"][venue_id] = entry
        state["history"] = history
        state["updated_at"] = event_at
        atomic_json(paths.state, state)
    return changed


def finalize(
    *,
    paths: LitDBPaths,
    venue_id: str,
    run_root: Path,
    merge_receipt: Path,
    expected_root: Path | None = None,
    reconcile_root: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    run_root = run_root.expanduser().resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    base: dict[str, Any] = {
        "schema_version": "nature-finalize-receipt-v1",
        "venue_id": venue_id,
        "mode": "ordinary_initialization",
        "status": "BLOCKED",
        "committed": False,
        "run_root": str(run_root),
        "writes": {"catalog": False, "campaign_state": False, "registry": False, "watermark": False},
        "errors": [],
    }
    if venue_id not in NATURE_VENUES:
        return 2, _write_result(run_root, {**base, "errors": [f"venue is outside four-Nature scope: {venue_id}"]})

    merge_receipt = merge_receipt.expanduser().resolve()
    try:
        merge = load_json(merge_receipt)
        if merge.get("status") != "PASS" or merge.get("committed") is not True or merge.get("venue_id") != venue_id:
            raise ValueError("merge receipt is not a committed PASS for this venue")
        staging_path = Path(str(merge.get("staging_file") or "")).expanduser().resolve()
        if not staging_path.is_file():
            raise ValueError(f"merge staging file is missing: {staging_path}")
        staging_hash = sha256_file(staging_path)
        if staging_hash != merge.get("staging_sha256"):
            raise ValueError("merge receipt staging_sha256 does not match current staging bytes")
        exclusion_path_value = merge.get("exclusion_file")
        exclusion_hash_value = merge.get("exclusion_sha256")
        exclusion_path: Path | None = None
        exclusion_hash: str | None = None
        if isinstance(exclusion_path_value, str) and exclusion_path_value:
            exclusion_path = Path(exclusion_path_value).expanduser().resolve()
            if isinstance(exclusion_hash_value, str) and exclusion_hash_value:
                if not exclusion_path.is_file():
                    raise ValueError(f"merge receipt exclusion file is missing: {exclusion_path}")
                exclusion_hash = sha256_file(exclusion_path)
                if exclusion_hash != exclusion_hash_value:
                    raise ValueError("merge receipt exclusion_sha256 does not match current exclusion bytes")
        source_root = (expected_root or (merge_receipt.parent / "expected")).expanduser().resolve()
        expected_files = _expected_files(source_root)
        expected_hashes = {path.name: sha256_file(path) for path in expected_files}
        campaign_before, campaign_before_hash = _backup_campaign(paths, run_root)
        campaign = load_json(paths.state)
        campaign_entry = campaign.get("venues", {}).get(venue_id) if isinstance(campaign.get("venues"), dict) else None
        if not isinstance(campaign_entry, dict):
            raise ValueError(f"campaign state has no venue entry: {venue_id}")
        venue_rows = _catalog_rows(paths, "venue", venue_id)
        venue_state_rows = _catalog_rows(paths, "venue_state", venue_id)
        if len(venue_rows) != 1 or len(venue_state_rows) != 1:
            raise ValueError(f"catalog must have exactly one venue and venue_state row (got {len(venue_rows)}/{len(venue_state_rows)})")
        _, venue_payload = venue_rows[0]
        _, venue_state_payload = venue_state_rows[0]
        prior_campaign_state = campaign_entry.get("state")
        prior_venue_status = venue_payload.get("status")
        prior_venue_state = venue_state_payload.get("state")
        allowed_campaign_states = {"UNSEEN", "BOOTSTRAP_STAGED", "ACTIVE"}
        if prior_campaign_state not in allowed_campaign_states:
            raise ValueError(f"ordinary initialization requires campaign UNSEEN, BOOTSTRAP_STAGED, or ACTIVE; found {prior_campaign_state}")
        catalog_finalizer_match = (
            prior_venue_state == "ACTIVE"
            and prior_venue_status == "ACTIVE"
            and venue_state_payload.get("finalizer_mode") == "ordinary_initialization"
            and venue_payload.get("finalizer_mode") == "ordinary_initialization"
            and venue_state_payload.get("merge_receipt") == str(merge_receipt)
            and venue_payload.get("merge_receipt") == str(merge_receipt)
            and venue_state_payload.get("staging_sha256") == staging_hash
            and venue_payload.get("staging_sha256") == staging_hash
        )
        already_finalized = (
            prior_campaign_state == "ACTIVE"
            and catalog_finalizer_match
        )
        if prior_venue_state == "MERGED_PENDING_RECONCILE":
            if prior_venue_status not in {None, "UNSEEN"}:
                raise ValueError(f"ordinary initialization requires catalog venue UNSEEN, found {prior_venue_status}")
            if venue_state_payload.get("staging_sha256") != staging_hash:
                raise ValueError("catalog venue_state staging_sha256 does not match merge receipt")
        elif prior_venue_state == "ACTIVE":
            if not catalog_finalizer_match:
                raise ValueError("catalog ACTIVE venue_state is unrelated to this finalizer merge receipt")
        else:
            raise ValueError(f"catalog venue_state is not recoverable: {prior_venue_state}")
        catalog_needs_promotion = prior_venue_state == "MERGED_PENDING_RECONCILE"

        reconcile_root = (reconcile_root or (run_root / "reconcile")).expanduser().resolve()
        code, reconciliation = reconcile_venue(
            paths,
            venue_id,
            run_root=reconcile_root,
            expected_root=source_root,
            strict=True,
        )
        reconcile_receipt = reconcile_root / "reconcile_receipt.json"
        if code != 0 or reconciliation.get("status") != "PASS" or not reconcile_receipt.is_file():
            raise ValueError(f"strict reconcile did not PASS: {reconciliation.get('errors', [])[:20]}")
        reconcile_data = load_json(reconcile_receipt)
        if reconcile_data.get("status") != "PASS" or reconcile_data.get("venue_id") != venue_id:
            raise ValueError("strict reconcile receipt is not a PASS for this venue")

        _install_expected(paths, venue_id, source_root, run_root)
        promotion_path = run_root / "nature_promotion_receipt.json"
        base.update({
            "merge_receipt": str(merge_receipt),
            "merge_staging_file": str(staging_path),
            "staging_sha256": staging_hash,
            "exclusion_file": str(exclusion_path) if exclusion_path else None,
            "exclusion_sha256": exclusion_hash,
            "expected_root": str(source_root),
            "expected_manifest_hashes": expected_hashes,
            "campaign_before": str(campaign_before),
            "campaign_before_sha256": campaign_before_hash,
            "prior_states": {
                "campaign": prior_campaign_state,
                "catalog_venue_status": prior_venue_status,
                "catalog_venue_state": prior_venue_state,
            },
            "reconcile_receipt": str(reconcile_receipt),
            "reconcile_report": str(reconcile_root / "venue_coverage_report.json"),
            "expected_install_receipt": str(run_root / "expected_install_receipt.json"),
            "promotion_receipt": str(promotion_path),
        })
        if already_finalized:
            base["prior_states"] = {
                "campaign": venue_state_payload.get("prior_campaign_state", "UNSEEN"),
                "catalog_venue_status": venue_payload.get("prior_status", "UNSEEN"),
                "catalog_venue_state": venue_state_payload.get("prior_state", "MERGED_PENDING_RECONCILE"),
            }
            base["idempotent_of"] = str(promotion_path)
            base.update({"status": "PASS", "committed": True, "idempotent_noop": True, "writes": {"catalog": False, "campaign_state": False, "registry": False, "watermark": False}})
            return 0, _write_result(run_root, base)

        live_campaign = load_json(paths.state)
        live_entry = live_campaign.get("venues", {}).get(venue_id) if isinstance(live_campaign.get("venues"), dict) else None
        if not isinstance(live_entry, dict) or live_entry.get("state") not in allowed_campaign_states:
            raise ValueError("campaign state changed before catalog promotion")
        event_at = utc_now()
        event_id = hashlib.sha256(
            f"nature-finalize\n{venue_id}\n{merge_receipt}\n{staging_hash}".encode("utf-8")
        ).hexdigest()
        base["finalizer_event_id"] = event_id
        committing = dict(base, status="COMMITTING", promotion_started_at=event_at)
        atomic_json(promotion_path, committing)
        if catalog_needs_promotion:
            connection = sqlite3.connect(paths.catalog, timeout=30)
            try:
                connection.execute("BEGIN IMMEDIATE")
                current_venue = _catalog_rows_connection(connection, "venue", venue_id)
                current_state = _catalog_rows_connection(connection, "venue_state", venue_id)
                if len(current_venue) != 1 or len(current_state) != 1:
                    raise ValueError("catalog venue rows changed during finalization")
                current_state_payload = current_state[0][1]
                if current_state_payload.get("state") != "MERGED_PENDING_RECONCILE":
                    raise ValueError("catalog venue_state changed before promotion")
                active_venue = dict(current_venue[0][1])
                active_venue.update({
                    "status": "ACTIVE",
                    "finalizer_mode": "ordinary_initialization",
                    "prior_status": prior_venue_status,
                    "prior_campaign_state": prior_campaign_state,
                    "merge_receipt": str(merge_receipt),
                    "staging_sha256": staging_hash,
                    "reconcile_receipt": str(reconcile_receipt),
                    "promotion_receipt": str(promotion_path),
                    "updated_at": event_at,
                })
                active_state = dict(current_state_payload)
                active_state.update({
                    "state": "ACTIVE",
                    "finalizer_mode": "ordinary_initialization",
                    "prior_state": prior_venue_state,
                    "prior_campaign_state": prior_campaign_state,
                    "merge_receipt": str(merge_receipt),
                    "reconcile_receipt": str(reconcile_receipt),
                    "promotion_receipt": str(promotion_path),
                    "expected_install_receipt": str(run_root / "expected_install_receipt.json"),
                    "updated_at": event_at,
                })
                _upsert(connection, "venue", {"venue_id": venue_id}, active_venue, event_at)
                _upsert(connection, "venue_state", {"venue_id": venue_id}, active_state, event_at)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            base["writes"]["catalog"] = True

        changed_campaign = _complete_campaign(
            paths,
            venue_id=venue_id,
            prior_campaign_state=prior_campaign_state,
            promotion_path=promotion_path,
            merge_receipt=merge_receipt,
            reconcile_receipt=reconcile_receipt,
            event_id=event_id,
            event_at=event_at,
        )
        base["writes"]["campaign_state"] = changed_campaign
        base.update({
            "status": "PASS",
            "committed": True,
            "idempotent_noop": False,
            "recovered_catalog_commit": not catalog_needs_promotion,
            "promoted_at": event_at,
        })
        atomic_json(promotion_path, base)
        return 0, _write_result(run_root, base)
    except Exception as exc:
        base["errors"] = [str(exc)]
        # If catalog commit happened but the campaign write failed, keep that
        # fact explicit; callers must not mistake the receipt for success.
        return 2, _write_result(run_root, base)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, help="LitDB data directory (defaults to LITDB_HOME or the skill data directory)")
    parser.add_argument("--venue", choices=sorted(NATURE_VENUES), required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--merge-receipt", type=Path, required=True)
    parser.add_argument("--expected-root", type=Path)
    parser.add_argument("--reconcile-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = LitDBPaths.from_value(args.home)
    code, result = finalize(
        paths=paths,
        venue_id=args.venue,
        run_root=args.run_root,
        merge_receipt=args.merge_receipt,
        expected_root=args.expected_root,
        reconcile_root=args.reconcile_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

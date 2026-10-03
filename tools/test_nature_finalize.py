from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.litdb.db import initialize
from tools.litdb.io import atomic_json, sha256_file
from tools.litdb.metadata_pipeline import merge_venue
from tools.litdb.paths import LitDBPaths
from tools.litdb.state import initial_state
import tools.nature_finalize as nature_finalize
from tools.nature_finalize import finalize


VENUE = "nature-machine-intelligence"
IDENTITY = "/articles/s42256-026-0001-1"
LANDING = "https://www.nature.com/articles/s42256-026-0001-1"
PAGE = "https://www.nature.com/natmachintell/research-articles?year=2026"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _write_expected(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"venue_id": VENUE, "year": 2026, "source_native_id": IDENTITY}, sort_keys=True) + "\n")


class NatureFinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = LitDBPaths(Path(self.temp.name) / "home")
        self.paths.ensure_tree()
        initialize(self.paths.catalog)
        atomic_json(
            self.paths.venues / f"{VENUE}.yml",
            {
                "id": VENUE,
                "canonical_name": "Nature Machine Intelligence",
                "venue_type": "journal",
                "status": "UNSEEN",
                "allowed_domains": ["nature.com"],
                "allowed_path_prefixes": {},
                "crawl_from": 2015,
            },
        )
        atomic_json(self.paths.state, initial_state([VENUE], "root"))
        self.merge_root = self.paths.home / "runs" / "merge"
        self.merge_root.mkdir(parents=True)
        self.expected_root = self.merge_root / "expected"
        _write_expected(self.expected_root / "2026.jsonl.gz")
        observed = "2026-10-01T08:00:00Z"
        provenance = {
            field: {"source_url": LANDING, "observed_at": observed, "method": "official_detail"}
            for field in (
                "source_native_id", "title", "authors", "year", "document_type",
                "landing_url", "abstract", "doi", "publication_date", "pdf_discovery_status",
            )
        }
        self.record = {
            "schema_version": "literature-metadata-staging-v1",
            "venue_id": VENUE,
            "source_native_id": IDENTITY,
            "title": "A test article for ordinary initialization",
            "authors": ["A. Researcher"],
            "abstract": "A complete abstract.",
            "document_type": "Article",
            "publication_date": "2026-01-01",
            "year": 2026,
            "doi": "10.1038/s42256-026-0001-1",
            "landing_url": LANDING,
            "pdf_url": f"{LANDING}.pdf",
            "pdf_discovery_status": "visible_url",
            "inclusion_decision": "include",
            "source_url": LANDING,
            "observed_at": observed,
            "field_provenance": provenance,
            "missing_fields": {},
        }
        self.staging = self.merge_root / "metadata_staging.jsonl"
        _write_jsonl(self.staging, [self.record])
        atomic_json(
            self.merge_root / "waterline_evidence.json",
            {
                "venue_id": VENUE,
                "status": "PASS",
                "drift_status": "NO_DRIFT",
                "enumeration_complete": True,
                "observed_at": observed,
                "source_urls": [PAGE],
                "source_item_set_sha256": hashlib.sha256(IDENTITY.encode()).hexdigest(),
                "current_source_item_count": 1,
                "new_ids": [],
                "missing_ids": [],
            },
        )
        code, self.merge = merge_venue(
            self.paths,
            VENUE,
            self.merge_root,
            staging_file=self.staging,
            expected_root=self.expected_root,
            verify_before_commit=True,
        )
        self.assertEqual(code, 0, self.merge)
        self.assertTrue(self.merge["committed"])
        self.finalize_root = self.paths.home / "runs" / "finalize"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _states(self) -> tuple[str, str, dict]:
        state = json.loads(self.paths.state.read_text(encoding="utf-8"))
        connection = sqlite3.connect(self.paths.catalog)
        try:
            venue = json.loads(connection.execute("SELECT payload_json FROM venue").fetchone()[0])
            venue_state = json.loads(connection.execute("SELECT payload_json FROM venue_state").fetchone()[0])
            watermark_row = connection.execute("SELECT payload_json FROM update_watermark").fetchone()
            watermark = json.loads(watermark_row[0]) if watermark_row else {}
        finally:
            connection.close()
        return state["venues"][VENUE]["state"], venue_state["state"], watermark

    def _set_campaign_state(self, value: str) -> None:
        state = json.loads(self.paths.state.read_text(encoding="utf-8"))
        state["venues"][VENUE]["state"] = value
        atomic_json(self.paths.state, state)

    def test_promotes_after_strict_reconcile_and_preserves_watermark(self) -> None:
        before = self._states()
        code, result = finalize(
            paths=self.paths,
            venue_id=VENUE,
            run_root=self.finalize_root,
            merge_receipt=self.merge_root / "merge_receipt.json",
            expected_root=self.expected_root,
        )
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["mode"], "ordinary_initialization")
        self.assertEqual(self._states(), ("ACTIVE", "ACTIVE", before[2]))
        target = self.paths.home / "manifests" / "expected" / VENUE / "2026.jsonl.gz"
        self.assertEqual(sha256_file(target), sha256_file(self.expected_root / target.name))
        self.assertTrue((self.finalize_root / "backup" / "campaign_state.before.json").is_file())
        self.assertTrue(result["writes"]["watermark"] is False)

    def test_same_run_is_idempotent_and_preserves_extra_manifest(self) -> None:
        first_code, first = finalize(
            paths=self.paths,
            venue_id=VENUE,
            run_root=self.finalize_root,
            merge_receipt=self.merge_root / "merge_receipt.json",
            expected_root=self.expected_root,
        )
        self.assertEqual(first_code, 0, first)
        extra = self.paths.home / "manifests" / "expected" / VENUE / "old.jsonl"
        extra.write_text("preserve\n", encoding="utf-8")
        state_before = json.loads(self.paths.state.read_text(encoding="utf-8"))
        second_code, second = finalize(
            paths=self.paths,
            venue_id=VENUE,
            run_root=self.finalize_root,
            merge_receipt=self.merge_root / "merge_receipt.json",
            expected_root=self.expected_root,
        )
        self.assertEqual(second_code, 0, second)
        self.assertTrue(second["idempotent_noop"])
        self.assertEqual(extra.read_text(encoding="utf-8"), "preserve\n")
        state_after = json.loads(self.paths.state.read_text(encoding="utf-8"))
        self.assertEqual(len(state_before["history"]), len(state_after["history"]))

    def test_bootstrap_staged_is_reconciled_and_audited_without_fake_unseen_jump(self) -> None:
        self._set_campaign_state("BOOTSTRAP_STAGED")
        code, result = finalize(
            paths=self.paths,
            venue_id=VENUE,
            run_root=self.finalize_root,
            merge_receipt=self.merge_root / "merge_receipt.json",
            expected_root=self.expected_root,
        )
        self.assertEqual(code, 0, result)
        self.assertEqual(result["prior_states"]["campaign"], "BOOTSTRAP_STAGED")
        self.assertEqual(self._states()[:2], ("ACTIVE", "ACTIVE"))
        state = json.loads(self.paths.state.read_text(encoding="utf-8"))
        audit = [item for item in state["history"] if item.get("event_kind") == "finalizer_audit_after_strict_reconcile"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["from"], "BOOTSTRAP_STAGED")

    def test_active_campaign_with_pending_catalog_is_promoted(self) -> None:
        self._set_campaign_state("ACTIVE")
        code, result = finalize(
            paths=self.paths,
            venue_id=VENUE,
            run_root=self.finalize_root,
            merge_receipt=self.merge_root / "merge_receipt.json",
            expected_root=self.expected_root,
        )
        self.assertEqual(code, 0, result)
        self.assertEqual(result["prior_states"]["campaign"], "ACTIVE")
        self.assertEqual(self._states()[:2], ("ACTIVE", "ACTIVE"))
        state = json.loads(self.paths.state.read_text(encoding="utf-8"))
        audit = [item for item in state["history"] if item.get("event_kind") == "finalizer_audit_after_strict_reconcile"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["from"], "ACTIVE")

    def test_rerun_recovers_catalog_commit_when_campaign_write_failed(self) -> None:
        original_atomic_json = nature_finalize.atomic_json

        def fail_campaign_write(path: Path, value: object) -> None:
            if Path(path).resolve() == self.paths.state.resolve():
                raise OSError("injected campaign write failure")
            original_atomic_json(path, value)

        with mock.patch.object(nature_finalize, "atomic_json", side_effect=fail_campaign_write):
            first_code, first = finalize(
                paths=self.paths,
                venue_id=VENUE,
                run_root=self.finalize_root,
                merge_receipt=self.merge_root / "merge_receipt.json",
                expected_root=self.expected_root,
            )
        self.assertNotEqual(first_code, 0)
        self.assertEqual(first["status"], "BLOCKED")
        self.assertEqual(self._states()[:2], ("UNSEEN", "ACTIVE"))
        second_code, second = finalize(
            paths=self.paths,
            venue_id=VENUE,
            run_root=self.finalize_root,
            merge_receipt=self.merge_root / "merge_receipt.json",
            expected_root=self.expected_root,
        )
        self.assertEqual(second_code, 0, second)
        self.assertTrue(second["recovered_catalog_commit"])
        self.assertEqual(self._states()[:2], ("ACTIVE", "ACTIVE"))
        state = json.loads(self.paths.state.read_text(encoding="utf-8"))
        events = [item for item in state["history"] if item.get("finalizer_event_id") == second["finalizer_event_id"]]
        self.assertEqual(len(events), 1)

    def test_staging_hash_drift_blocks_without_promotion(self) -> None:
        self.staging.write_text(self.staging.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        code, result = finalize(
            paths=self.paths,
            venue_id=VENUE,
            run_root=self.finalize_root,
            merge_receipt=self.merge_root / "merge_receipt.json",
            expected_root=self.expected_root,
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(self._states()[:2], ("UNSEEN", "MERGED_PENDING_RECONCILE"))
        self.assertFalse((self.paths.home / "manifests" / "expected" / VENUE / "2026.jsonl.gz").is_file())

    def test_unrelated_active_catalog_is_rejected(self) -> None:
        connection = sqlite3.connect(self.paths.catalog)
        try:
            venue_row = connection.execute("SELECT id, payload_json FROM venue").fetchone()
            state_row = connection.execute("SELECT id, payload_json FROM venue_state").fetchone()
            venue = json.loads(venue_row[1])
            venue_state = json.loads(state_row[1])
            venue.update({
                "status": "ACTIVE",
                "finalizer_mode": "ordinary_initialization",
                "merge_receipt": str(self.merge_root / "unrelated-merge_receipt.json"),
                "staging_sha256": "0" * 64,
            })
            venue_state.update({
                "state": "ACTIVE",
                "finalizer_mode": "ordinary_initialization",
                "merge_receipt": str(self.merge_root / "unrelated-merge_receipt.json"),
                "staging_sha256": "0" * 64,
            })
            connection.execute("UPDATE venue SET payload_json=? WHERE id=?", (json.dumps(venue, sort_keys=True), venue_row[0]))
            connection.execute("UPDATE venue_state SET payload_json=? WHERE id=?", (json.dumps(venue_state, sort_keys=True), state_row[0]))
            connection.commit()
        finally:
            connection.close()
        code, result = finalize(
            paths=self.paths,
            venue_id=VENUE,
            run_root=self.finalize_root,
            merge_receipt=self.merge_root / "merge_receipt.json",
            expected_root=self.expected_root,
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("unrelated", result["errors"][0])
        self.assertEqual(self._states()[:2], ("UNSEEN", "ACTIVE"))
        self.assertFalse((self.paths.home / "manifests" / "expected" / VENUE / "2026.jsonl.gz").is_file())


if __name__ == "__main__":
    unittest.main()

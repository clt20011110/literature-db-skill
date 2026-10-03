from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.nature_staging_prepare import _normalize_detail_row, _normalize_missing, _prepare_historical_reuse, prepare_run


VENUE = "nature-machine-intelligence"
YEAR = 2019
PAGE_URL = "https://www.nature.com/natmachintell/articles?year=2019"
IDENTITY = "/articles/s42256-019-0001-1"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class NatureStagingPrepareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "run"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _enumeration_row(self, *, duplicate: bool = False) -> dict[str, object]:
        return {
            "venue_id": VENUE,
            "venue_year": YEAR,
            "source_item_id": f"{VENUE}:{IDENTITY}",
            "stable_article_path": IDENTITY,
            "source_page_url": PAGE_URL,
            "page_number": 1,
            "listing_position": 1 if not duplicate else 2,
            "listing_date": "2019-12-01",
            "title_listing": "A test article",
            "authors_listing": ["A Author"],
            "listed_document_type": "Article",
            "include_candidate": True,
        }

    def _detail_row(self) -> dict[str, object]:
        observed = "2026-10-01T07:00:00Z"
        return {
            "venue_id": VENUE,
            "venue_year": YEAR,
            "source_item_id": f"{VENUE}:{IDENTITY}",
            "stable_article_path": IDENTITY,
            "source_url": "https://www.nature.com/articles/s42256-019-0001-1",
            "landing_url": "https://www.nature.com/articles/s42256-019-0001-1",
            "title": "A test article",
            "authors": ["A Author"],
            "abstract": "We study a concrete metadata preparation boundary.",
            "publication_date": "2019-12-01",
            "document_type": "Article",
            "doi": "10.1038/s42256-019-0001-1",
            "pdf_url": "https://www.nature.com/articles/s42256-019-0001-1.pdf",
            "observed_at": observed,
            "include_decision": "include",
            "field_provenance": {},
        }

    def _page_evidence(self, *, complete: bool = True) -> dict[str, object]:
        return {
            "source_url": PAGE_URL,
            "observed_at": "2026-10-01T07:00:00Z",
            "year_filter": YEAR,
            "page_number": 1,
            "card_count": 1,
            "selected_facets": ["2019 (1)"],
            "next_url": None,
            "next_present": False,
            "html_sha256": "a" * 64,
            "fetch": {"attempts": 1, "errors": [], "status": 200},
            "enumeration_complete": complete,
        }

    def _completion(self, *, complete: bool = True) -> dict[str, object]:
        observed = "2026-10-01T07:00:00Z"
        return {
            "status": "PASS" if complete else "IN_PROGRESS",
            "drift_status": "NO_DRIFT",
            "enumeration_complete": complete,
            "observed_at": observed,
            "source_urls": [PAGE_URL],
            "current_source_item_count": 1,
            "new_ids": [IDENTITY],
            "missing_ids": [],
            "source_item_set_sha256": hashlib.sha256(IDENTITY.encode("utf-8")).hexdigest(),
        }

    def _valid_inputs(self) -> None:
        _write_jsonl(self.root / "enumeration.jsonl", [self._enumeration_row()])
        _write_jsonl(self.root / "page_evidence.jsonl", [self._page_evidence()])
        _write_jsonl(self.root / "metadata_staging.jsonl", [self._detail_row()])
        _write_json(self.root / "collection_complete.json", self._completion())

    def _historical_inputs(self, base: Path, *, mismatched_hash: bool) -> tuple[Path, Path]:
        identity = "/articles/s41586-015-00001-0"
        seed = base / "historical_seed.jsonl"
        _write_jsonl(seed, [{"venue_id": "nature", "source_native_id": identity, "year": 2015}])
        manifest = base / "expected" / "2015.jsonl.gz"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(manifest, "wt", encoding="utf-8") as handle:
            handle.write(json.dumps({"source_native_id": identity, "year": 2015}) + "\n")
        receipt = base / "historical_seed_receipt.json"
        _write_json(
            receipt,
            {
                "seed_count": 1,
                "current_year_refresh_required": True,
                "sources": [
                    {
                        "file": str(manifest),
                        "sha256": "0" * 64 if mismatched_hash else hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    }
                ],
            },
        )
        return seed, receipt

    def test_valid_raw_detail_is_normalized_and_ready(self) -> None:
        self._valid_inputs()
        result = prepare_run(
            run_root=self.root,
            venue_id=VENUE,
            through_year=YEAR,
            output_root=self.root / "prepared",
        )
        self.assertEqual(result["status"], "READY", result["errors"])
        prepared = self.root / "prepared"
        row = json.loads((prepared / "metadata_staging.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["source_native_id"], IDENTITY)
        self.assertEqual(row["year"], YEAR)
        self.assertEqual(row["field_provenance"]["title"]["observed_at"], row["observed_at"])
        self.assertTrue((prepared / "expected" / "2019.jsonl.gz").is_file())
        waterline = json.loads((prepared / "waterline_evidence.json").read_text(encoding="utf-8"))
        self.assertTrue(waterline["enumeration_complete"])

    def test_missing_enumeration_identity_blocks_and_has_no_expected(self) -> None:
        self._valid_inputs()
        row = self._enumeration_row()
        row.pop("stable_article_path")
        row["source_item_id"] = ""
        _write_jsonl(self.root / "enumeration.jsonl", [row])
        result = prepare_run(run_root=self.root, venue_id=VENUE, through_year=YEAR, output_root=self.root / "prepared")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["expected_manifests"])
        waterline = json.loads((self.root / "prepared" / "waterline_evidence.json").read_text(encoding="utf-8"))
        self.assertFalse(waterline["enumeration_complete"])

    def test_duplicate_enumeration_identity_blocks(self) -> None:
        self._valid_inputs()
        _write_jsonl(self.root / "enumeration.jsonl", [self._enumeration_row(), self._enumeration_row(duplicate=True)])
        # Card evidence must also account for the duplicate card; this makes
        # the failure exercise both duplicate identity and count accounting.
        page = self._page_evidence()
        page["card_count"] = 2
        page["selected_facets"] = ["2019 (2)"]
        _write_jsonl(self.root / "page_evidence.jsonl", [page])
        result = prepare_run(run_root=self.root, venue_id=VENUE, through_year=YEAR, output_root=self.root / "prepared")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(IDENTITY, result["duplicate_identities"]["enumeration"])
        self.assertFalse(result["expected_manifests"])

    def test_partial_page_evidence_never_becomes_complete(self) -> None:
        self._valid_inputs()
        page = self._page_evidence(complete=False)
        page["next_present"] = True
        page["next_url"] = "https://www.nature.com/natmachintell/articles?year=2019&page=2"
        _write_jsonl(self.root / "page_evidence.jsonl", [page])
        _write_json(self.root / "collection_complete.json", self._completion(complete=False))
        result = prepare_run(run_root=self.root, venue_id=VENUE, through_year=YEAR, output_root=self.root / "prepared")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["expected_manifests"])
        waterline = json.loads((self.root / "prepared" / "waterline_evidence.json").read_text(encoding="utf-8"))
        self.assertFalse(waterline["enumeration_complete"])

    def test_failed_page_retry_is_replaced_by_later_usable_observation(self) -> None:
        self._valid_inputs()
        failed = {
            "source_url": PAGE_URL,
            "year_filter": YEAR,
            "page_number": 1,
            "observed_at": "2026-10-01T06:59:00Z",
            "fetch": {"status": 503, "errors": ["temporary failure"]},
        }
        recovered = self._page_evidence()
        recovered["fetch"] = {"cache": "hit", "attempts": 0}
        _write_jsonl(self.root / "page_evidence.jsonl", [failed, recovered])
        result = prepare_run(run_root=self.root, venue_id=VENUE, through_year=YEAR, output_root=self.root / "prepared")
        self.assertEqual(result["status"], "READY", result["errors"])
        self.assertTrue(result["page_evidence_summary"]["recovery_warnings"])

    def test_collection_and_detail_receipts_are_role_specific(self) -> None:
        self._valid_inputs()
        (self.root / "collection_complete.json").unlink()
        _write_json(
            self.root / "enumeration" / "enumeration_manifest.json",
            {
                "status": "complete",
                "completed_pages": 1,
                "unique_paths": 1,
                "observed_at": "2026-10-01T07:00:00Z",
            },
        )
        _write_json(
            self.root / "run_receipt.json",
            {
                "run_status": "verified_collection_and_staging_complete_pending_production_ingest",
                "observed_at": "2026-10-01T07:00:00Z",
                "checks": {
                    "detail_stage_complete": True,
                    "no_manual_review": True,
                },
                "integrity_checks": {"manual_review_empty": True},
                "counts": {"metadata_records": 1},
            },
        )
        _write_json(
            self.root / "staging" / "detail_manifest.json",
            {
                "status": "complete",
                "candidates": 1,
                "completed_details": 1,
                "observed_at": "2026-10-01T07:00:00Z",
            },
        )
        result = prepare_run(
            run_root=self.root,
            venue_id=VENUE,
            through_year=YEAR,
            output_root=self.root / "prepared",
        )
        self.assertEqual(result["status"], "READY", result["errors"])
        self.assertNotIn("unresolved manual review", " ".join(result["errors"]))
        raw_receipt = json.loads((self.root / "run_receipt.json").read_text(encoding="utf-8"))
        self.assertNotIn("drift_status", raw_receipt)
        waterline = json.loads((self.root / "prepared" / "waterline_evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(waterline["drift_status_source"], "validated_page_and_identity_evidence")

    def test_pending_manual_review_still_blocks(self) -> None:
        self._valid_inputs()
        _write_jsonl(self.root / "staging" / "manual_review.jsonl", [{"review_status": "pending", "source_native_id": IDENTITY}])
        result = prepare_run(
            run_root=self.root,
            venue_id=VENUE,
            through_year=YEAR,
            output_root=self.root / "prepared",
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("review_status=pending" in error for error in result["errors"]))

    def test_collector_missing_reason_is_mapped_and_retained(self) -> None:
        mapped = _normalize_missing({"abstract": "visible_abstract_section_absent"})
        self.assertEqual(mapped["abstract"]["reason_code"], "not_present_on_official_page")
        self.assertEqual(mapped["abstract"]["raw_reason"], "visible_abstract_section_absent")

    def test_portfolio_venue_identity_boolean_is_preserved_when_adapting_raw_id(self) -> None:
        detail = self._detail_row()
        detail["venue_identity"] = True
        detail.pop("nature_identity", None)
        normalized, errors = _normalize_detail_row(detail, VENUE)
        self.assertEqual(errors, [])
        self.assertEqual(normalized["venue_identity"], {
            "confirmed": True,
            "raw_source_item_id": f"{VENUE}:{IDENTITY}",
        })

    def test_include_with_missing_fields_is_an_accepted_row(self) -> None:
        self._valid_inputs()
        detail = self._detail_row()
        detail["include_decision"] = "include_with_missing_fields"
        detail["abstract"] = None
        detail["missing_fields"] = {"abstract": "visible_abstract_section_absent"}
        _write_jsonl(self.root / "metadata_staging.jsonl", [detail])
        result = prepare_run(run_root=self.root, venue_id=VENUE, through_year=YEAR, output_root=self.root / "prepared")
        self.assertEqual(result["status"], "READY", result["errors"])
        row = json.loads((self.root / "prepared" / "metadata_staging.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["inclusion_decision"], "include")
        self.assertEqual(row["missing_fields"]["abstract"]["reason_code"], "not_present_on_official_page")

    def test_historical_reuse_rejects_manifest_sha_mismatch(self) -> None:
        seed_path, receipt_path = self._historical_inputs(self.root / "historical", mismatched_hash=True)
        rows, artifact, errors = _prepare_historical_reuse(
            seed_path=seed_path,
            receipt_path=receipt_path,
            output_root=self.root / "historical-preflight",
            through_year=2026,
            state_path=self.root / "missing-campaign-state.json",
        )
        self.assertEqual(rows, [])
        self.assertEqual(artifact["status"], "BLOCKED")
        self.assertTrue(any("SHA-256 mismatch" in error for error in errors))

    def test_historical_reuse_requires_fresh_current_year_page_evidence(self) -> None:
        seed_path, receipt_path = self._historical_inputs(self.root / "historical", mismatched_hash=False)
        current_identity = "/articles/s41586-026-00000-0"
        _write_jsonl(
            self.root / "enumeration.jsonl",
            [{
                "venue_id": "nature",
                "year": 2026,
                "source_native_id": current_identity,
                "source_page_url": "https://www.nature.com/nature/research-articles?year=2026",
                "page_number": 1,
                "listing_date": "2026-01-01",
            }],
        )
        _write_json(
            self.root / "collection_complete.json",
            {
                "status": "PASS",
                "drift_status": "NO_DRIFT",
                "enumeration_complete": True,
                "observed_at": "2026-10-01T07:00:00Z",
                "source_urls": ["https://www.nature.com/nature/research-articles?year=2026"],
                "current_source_item_count": 1,
                "new_ids": [current_identity],
                "missing_ids": [],
            },
        )
        result = prepare_run(
            run_root=self.root,
            venue_id="nature",
            through_year=2026,
            output_root=self.root / "prepared",
            historical_seed=seed_path,
            historical_receipt=receipt_path,
            historical_state=self.root / "missing-campaign-state.json",
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("page_evidence.jsonl is required" in error for error in result["errors"]))
        self.assertTrue((self.root / "prepared" / "historical_closed_year_evidence.json").is_file())


if __name__ == "__main__":
    unittest.main()

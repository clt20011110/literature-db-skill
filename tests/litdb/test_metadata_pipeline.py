from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.db import initialize
from litdb.io import atomic_json
from litdb.metadata_pipeline import merge_venue, reconcile_venue, validate_staging
from litdb.paths import LitDBPaths
from litdb.state import initial_state


def write_gzip_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


class MetadataPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.paths = LitDBPaths(Path(self.folder.name))
        self.paths.ensure_tree()
        initialize(self.paths.catalog)
        atomic_json(
            self.paths.venues / "tcad.yml",
            {
                "id": "tcad",
                "canonical_name": "IEEE Transactions on Computer-Aided Design of Integrated Circuits and Systems",
                "venue_type": "journal",
                "allowed_domains": ["ieeexplore.ieee.org"],
                "crawl_from": 2015,
            },
        )
        state = initial_state(["tcad"], "root")
        state["venues"]["tcad"]["state"] = "COUNT_BASELINED"
        atomic_json(self.paths.state, state)
        self.expected = self.paths.home / "manifests" / "expected" / "tcad"
        write_gzip_jsonl(
            self.expected / "2015.jsonl.gz",
            [{"venue_id": "tcad", "year": 2015, "source_native_id": "7116537"}],
        )
        self.run = self.paths.home / "runs" / "test" / "venues" / "tcad" / "request"
        self.run.mkdir(parents=True)
        atomic_json(
            self.run / "waterline_evidence.json",
            {
                "venue_id": "tcad",
                "status": "NO_CHANGE",
                "drift_status": "NO_DRIFT",
                "enumeration_complete": True,
                "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "source_urls": ["https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=43"],
                "source_item_set_sha256": hashlib.sha256(b"7116537").hexdigest(),
                "current_source_item_count": 1,
                "new_ids": [],
                "missing_ids": [],
            },
        )

    def tearDown(self) -> None:
        self.folder.cleanup()

    def record(self) -> dict:
        observed = "2026-08-24T04:00:00Z"
        landing = "https://ieeexplore.ieee.org/document/7116537"
        provenance = {
            field: {
                "source_url": landing,
                "observed_at": observed,
                "method": "official_inline_metadata",
                "reuse_status": "fresh_detail",
            }
            for field in (
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
        }
        return {
            "schema_version": "literature-metadata-staging-v1",
            "venue_id": "tcad",
            "source_native_id": "7116537",
            "title": "Low Energy yet Reliable Data Communication Scheme for Network-on-Chip",
            "authors": ["Nima Jafarzadeh", "Maurizio Palesi"],
            "abstract": "A complete public abstract.",
            "document_type": "journal-article",
            "publication_date": "02 June 2015",
            "year": 2015,
            "volume": "34",
            "issue": "12",
            "pages": "1892-1904",
            "doi": "10.1109/TCAD.2015.2440311",
            "landing_url": landing,
            "pdf_url": "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=7116537",
            "pdf_discovery_status": "visible_url",
            "inclusion_decision": "include",
            "source_url": landing,
            "observed_at": observed,
            "field_provenance": provenance,
            "missing_fields": {},
        }

    def test_validate_merge_and_reconcile(self) -> None:
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [self.record()])

        validation = validate_staging(self.paths, "tcad", self.run, strict=True)
        self.assertEqual(validation["status"], "PASS")
        self.assertTrue(validation["catalog_ready"])

        code, receipt = merge_venue(self.paths, "tcad", self.run, verify_before_commit=True)
        self.assertEqual(code, 0)
        self.assertTrue(receipt["committed"])

        second_code, _ = merge_venue(self.paths, "tcad", self.run, verify_before_commit=True)
        self.assertEqual(second_code, 0)
        connection = sqlite3.connect(self.paths.catalog)
        try:
            self.assertEqual(connection.execute("select count(*) from source_item").fetchone()[0], 1)
            self.assertEqual(connection.execute("select count(*) from canonical_work").fetchone()[0], 1)
            self.assertEqual(connection.execute("select count(*) from update_watermark").fetchone()[0], 1)
        finally:
            connection.close()

        code, report = reconcile_venue(self.paths, "tcad", run_root=self.run, strict=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        state = json.loads(self.paths.state.read_text())
        self.assertEqual(state["venues"]["tcad"]["state"], "ACTIVE")

    def test_missing_abstract_reason_is_rejected(self) -> None:
        record = self.record()
        record["abstract"] = None
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [record])

        result = validate_staging(self.paths, "tcad", self.run, strict=True)

        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("abstract missing without structured reason" in error for error in result["errors"]))

    def allow_scoped_raw_pdfs(self) -> None:
        path = self.paths.venues / "tcad.yml"
        venue = json.loads(path.read_text())
        venue["allowed_path_prefixes"] = {
            "raw.githubusercontent.com": ["/mlresearch/v235/", "/mlresearch/v267/"]
        }
        atomic_json(path, venue)

    def test_exact_raw_paths_are_validated_before_merge(self) -> None:
        self.allow_scoped_raw_pdfs()
        for volume in (235, 267):
            with self.subTest(volume=volume):
                record = self.record()
                record["pdf_url"] = f"https://raw.githubusercontent.com/mlresearch/v{volume}/main/assets/paper.pdf"
                write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [record])
                result = validate_staging(self.paths, "tcad", self.run, strict=True)
                self.assertEqual(result["status"], "PASS", result["errors"])
        blocked = [
            "https://raw.githubusercontent.com/mlresearch/v258/main/paper.pdf",
            "https://raw.githubusercontent.com/mlresearch/v235x/main/paper.pdf",
            "https://sub.raw.githubusercontent.com/mlresearch/v235/main/paper.pdf",
            "https://raw.githubusercontent.com/mlresearch/v235/../../other/paper.pdf",
            "https://raw.githubusercontent.com/mlresearch/v235/%2e%2e/other/paper.pdf",
            "https://someone@raw.githubusercontent.com/mlresearch/v235/main/paper.pdf",
        ]
        for url in blocked:
            with self.subTest(url=url):
                record = self.record()
                record["pdf_url"] = url
                write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [record])
                code, receipt = merge_venue(self.paths, "tcad", self.run)
                self.assertNotEqual(code, 0)
                self.assertFalse(receipt["committed"])
        connection = sqlite3.connect(self.paths.catalog)
        try:
            self.assertEqual(connection.execute("select count(*) from source_item").fetchone()[0], 0)
        finally:
            connection.close()

    def test_path_scope_also_covers_provenance_and_waterline(self) -> None:
        self.allow_scoped_raw_pdfs()
        bad = "https://raw.githubusercontent.com/other/repository/paper.pdf"
        record = self.record()
        record["field_provenance"]["title"]["source_url"] = bad
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [record])
        result = validate_staging(self.paths, "tcad", self.run, strict=True)
        self.assertTrue(any("provenance source invalid for title" in error for error in result["errors"]))
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [self.record()])
        path = self.run / "waterline_evidence.json"
        evidence = json.loads(path.read_text())
        evidence["source_urls"] = [bad]
        atomic_json(path, evidence)
        result = validate_staging(self.paths, "tcad", self.run, strict=True)
        self.assertTrue(any("non-allowlisted source URL" in error for error in result["errors"]))

    def test_invalid_path_scope_fails_closed(self) -> None:
        self.allow_scoped_raw_pdfs()
        path = self.paths.venues / "tcad.yml"
        venue = json.loads(path.read_text())
        venue["allowed_path_prefixes"]["raw.githubusercontent.com"] = ["/"]
        atomic_json(path, venue)
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [self.record()])
        result = validate_staging(self.paths, "tcad", self.run, strict=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("invalid allowed_path_prefixes configuration", result["errors"])

    def test_expected_identity_gap_is_rejected(self) -> None:
        record = self.record()
        record["source_native_id"] = "other"
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [record])

        result = validate_staging(self.paths, "tcad", self.run, strict=True)

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["missing_expected_count"], 1)

    def test_browser_session_fields_are_rejected(self) -> None:
        record = self.record()
        record["cToken"] = "not-a-real-token"
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [record])

        result = validate_staging(self.paths, "tcad", self.run, strict=True)

        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("forbidden secret/session" in error for error in result["errors"]))

    def test_early_access_baseline_year_is_reclassified_from_fresh_detail(self) -> None:
        expected = self.paths.home / "manifests" / "expected-ea" / "tcad"
        write_gzip_jsonl(
            expected / "2026.jsonl.gz",
            [{
                "venue_id": "tcad",
                "year": 2026,
                "source_native_id": "7116537",
                "selected_unit_id": "https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=6917053",
            }],
        )
        record = self.record()
        record["year"] = 2016
        record["is_early_access"] = True
        record["baseline_enumeration_kind"] = "early_access"
        record["reuse_evidence"] = {"expected_enumeration_kind": "early_access"}
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [record])

        result = validate_staging(self.paths, "tcad", self.run, expected_root=expected, strict=True)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["year_reclassification_count"], 1)
        self.assertEqual(result["yearly"][0]["year"], 2016)
        self.assertEqual(result["yearly"][0]["coverage"], 1.0)

    def test_repeated_doi_with_conflicting_titles_is_rejected(self) -> None:
        expected = self.paths.home / "manifests" / "expected-conflict" / "tcad"
        write_gzip_jsonl(
            expected / "2015.jsonl.gz",
            [
                {"venue_id": "tcad", "year": 2015, "source_native_id": "7116537"},
                {"venue_id": "tcad", "year": 2015, "source_native_id": "7116538"},
            ],
        )
        first = self.record()
        second = self.record()
        second["source_native_id"] = "7116538"
        second["title"] = "A genuinely different work"
        second["landing_url"] = "https://ieeexplore.ieee.org/document/7116538"
        second["source_url"] = second["landing_url"]
        second["pdf_url"] = "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=7116538"
        for provenance in second["field_provenance"].values():
            provenance["source_url"] = second["landing_url"]
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [first, second])

        result = validate_staging(self.paths, "tcad", self.run, expected_root=expected, strict=True)

        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("maps to conflicting titles" in error for error in result["errors"]))

    def test_nonresearch_identity_is_accounted_for_in_exclusion_ledger(self) -> None:
        expected = self.paths.home / "manifests" / "expected-exclusion" / "tcad"
        write_gzip_jsonl(
            expected / "2015.jsonl.gz",
            [
                {"venue_id": "tcad", "year": 2015, "source_native_id": "7116537"},
                {"venue_id": "tcad", "year": 2015, "source_native_id": "7166397"},
            ],
        )
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [self.record()])
        observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        source = "https://ieeexplore.ieee.org/document/7166397"
        provenance = {
            field: {
                "source_url": source,
                "observed_at": observed,
                "method": "official_detail_classification",
                "status": "excluded",
            }
            for field in ("source_native_id", "title", "year", "inclusion_decision", "exclusion_reason_code")
        }
        write_gzip_jsonl(
            self.run / "metadata_exclusions.jsonl.gz",
            [{
                "schema_version": "literature-metadata-exclusion-v1",
                "venue_id": "tcad",
                "source_native_id": "7166397",
                "title": "Open Access",
                "year": 2015,
                "inclusion_decision": "exclude",
                "exclusion_reason_code": "promotional_content",
                "landing_url": source,
                "source_url": source,
                "observed_at": observed,
                "field_provenance": provenance,
            }],
        )

        validation = validate_staging(self.paths, "tcad", self.run, expected_root=expected, strict=True)
        self.assertEqual(validation["status"], "PASS")
        self.assertEqual(validation["included_records"], 1)
        self.assertEqual(validation["excluded_records"], 1)
        self.assertEqual(validation["missing_expected_count"], 0)

        code, receipt = merge_venue(
            self.paths,
            "tcad",
            self.run,
            expected_root=expected,
            verify_before_commit=True,
        )
        self.assertEqual(code, 0)
        self.assertEqual(receipt["records_excluded"], 1)
        code, report = reconcile_venue(
            self.paths,
            "tcad",
            run_root=self.run,
            expected_root=expected,
            strict=True,
        )
        self.assertEqual(code, 0)
        self.assertEqual(report["included_source_items"], 1)
        self.assertEqual(report["excluded_source_items"], 1)
        self.assertEqual(report["canonical_works"], 1)

    def test_legacy_early_access_identity_can_be_explicitly_excluded_from_scope(self) -> None:
        expected = self.paths.home / "manifests" / "expected-legacy-ea" / "tcad"
        write_gzip_jsonl(
            expected / "2026.jsonl.gz",
            [{
                "venue_id": "tcad",
                "year": 2026,
                "source_native_id": "7116537",
                "selected_unit_id": "https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=6917053",
                "source_document_type": "Early Access",
            }],
        )
        write_gzip_jsonl(self.run / "metadata_staging.jsonl.gz", [])
        observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        source = "https://ieeexplore.ieee.org/document/7116537"
        provenance = {
            field: {
                "source_url": source,
                "observed_at": observed,
                "method": "fresh_official_detail_scope_classification",
                "status": "excluded",
            }
            for field in ("source_native_id", "title", "year", "inclusion_decision", "exclusion_reason_code")
        }
        write_gzip_jsonl(
            self.run / "metadata_exclusions.jsonl.gz",
            [{
                "schema_version": "literature-metadata-exclusion-v1",
                "venue_id": "tcad",
                "source_native_id": "7116537",
                "title": "Legacy item retained in the Early Access listing",
                "year": 2013,
                "is_early_access": True,
                "baseline_enumeration_kind": "early_access",
                "reuse_evidence": {"expected_enumeration_kind": "early_access"},
                "inclusion_decision": "exclude",
                "exclusion_reason_code": "outside_scope_year",
                "landing_url": source,
                "source_url": source,
                "observed_at": observed,
                "field_provenance": provenance,
            }],
        )

        validation = validate_staging(self.paths, "tcad", self.run, expected_root=expected, strict=True)

        self.assertEqual(validation["status"], "PASS")
        self.assertEqual(validation["included_records"], 0)
        self.assertEqual(validation["excluded_records"], 1)
        self.assertEqual(validation["year_reclassification_count"], 1)
        self.assertEqual(validation["yearly"][0]["year"], 2013)


if __name__ == "__main__":
    unittest.main()

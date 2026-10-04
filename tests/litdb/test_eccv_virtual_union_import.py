from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.integrate_eccv_virtual_union import integrate_virtual_union


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def write_gzip_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


class ECCVVirtualUnionImportTests(unittest.TestCase):
    def test_imports_reviewed_virtual_only_rows_and_preserves_provisional_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            run_root = base / "run"
            gap_root = base / "gap"
            workspace = base / "workspace"
            expected_dir = run_root / "expected"
            publisher_rows = [
                {"venue_id": "eccv", "year": 2026, "source_native_id": "springer-1", "title": "A"},
                {"venue_id": "eccv", "year": 2026, "source_native_id": "springer-2", "title": "B"},
            ]
            partial = expected_dir / "2026.incomplete-series-only.jsonl.gz"
            write_gzip_jsonl(partial, publisher_rows)

            listing = run_root / "raw" / "eccv2026_virtual_papers.html"
            listing.parent.mkdir(parents=True, exist_ok=True)
            listing.write_text("<html>official papers</html>", encoding="utf-8")
            listing_hash = hashlib.sha256(listing.read_bytes()).hexdigest()

            poster_url = "https://eccv.ecva.net/virtual/2026/poster/7"
            body = b"<html>poster page</html>"
            poster_raw = workspace / "poster.html.gz"
            poster_raw.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(poster_raw, "wb") as handle:
                handle.write(body)
            body_hash = hashlib.sha256(body).hexdigest()
            candidate = {
                "venue_id": "eccv",
                "year": 2026,
                "virtual_native_id": "7",
                "source_native_id": None,
                "title": "Virtual Paper",
                "authors": ["Ada Author", "Ben Author"],
                "abstract": "A complete abstract.",
                "landing_url": poster_url,
                "paper_pdf_url": "https://media.eventhosts.cc/Conferences/ECCV2026/pdfs/7.pdf",
                "doi": None,
                "publication_date": None,
                "poster_page_date_published_unmapped": "2026-08-17",
                "poster_page_source_receipt": {
                    "raw_path": "poster.html.gz",
                    "raw_sha256": body_hash,
                    "observed_at": "2026-10-04T10:00:00Z",
                    "source_url": poster_url,
                    "status": 200,
                },
                "source_provenance": {
                    key: {"source_url": poster_url, "observed_at": "2026-10-04T10:00:00Z", "method": "official"}
                    for key in ("title", "authors", "abstract", "landing_url", "paper_pdf_url", "doi")
                },
            }
            virtual_row = {
                "poster_id": "7",
                "status": 200,
                "raw_sha256": body_hash,
                "normalized_record_candidate": candidate,
                "identity_decision": "virtual_only",
                "identity_review_evidence": {"method": "reviewed_no_match"},
            }
            write_jsonl(gap_root / "final_virtual_only_records.jsonl", [virtual_row])
            crosswalk_rows = [
                {"virtual_native_id": "6", "selected_springer_id": "springer-1", "identity_decision": "matched", "method": "unique_title"},
                {"virtual_native_id": "7", "selected_springer_id": None, "identity_decision": "virtual_only", "method": "reviewed_no_match", "source_evidence": {}},
            ]
            write_jsonl(gap_root / "final_identity_crosswalk.jsonl", crosswalk_rows)
            write_jsonl(gap_root / "springer_only_unmatched_records.jsonl", [{"source_native_id": "springer-2"}])
            summary = {
                "venue_id": "eccv",
                "year": 2026,
                "status": "identity_crosswalk_frozen",
                "identity_scope_note": "The union is closed by reviewed identity evidence.",
                "source_sets": {
                    "virtual_records": 2,
                    "virtual_unique_ids": 2,
                    "springer_records": 2,
                    "springer_unique_ids": 2,
                    "virtual_source": "https://eccv.ecva.net/virtual/2026/papers.html",
                    "virtual_source_observed_at": "2026-10-04T06:00:00Z",
                    "virtual_source_sha256": listing_hash,
                },
                "crosswalk_counts": {
                    "matched_total": 1,
                    "virtual_only": 1,
                    "springer_only": 1,
                    "identity_union_size": 3,
                },
                "global_validation": {"publisher_only_id": "springer-2"},
                "sha256": {},
            }
            summary["sha256"] = {
                "final_virtual_only_records": hashlib.sha256((gap_root / "final_virtual_only_records.jsonl").read_bytes()).hexdigest(),
                "final_identity_crosswalk": hashlib.sha256((gap_root / "final_identity_crosswalk.jsonl").read_bytes()).hexdigest(),
                "springer_only_unmatched_records": hashlib.sha256((gap_root / "springer_only_unmatched_records.jsonl").read_bytes()).hexdigest(),
            }
            summary_path = gap_root / "final_crosswalk_summary.json"
            write_json(summary_path, summary)
            summary_hash = hashlib.sha256(summary_path.read_bytes()).hexdigest()
            write_json(
                gap_root / "final_crosswalk_freeze_receipt.json",
                {
                    "status": "frozen",
                    "summary_sha256": summary_hash,
                    "final_virtual_only_records_sha256": summary["sha256"]["final_virtual_only_records"],
                    "final_identity_crosswalk_sha256": summary["sha256"]["final_identity_crosswalk"],
                    "springer_only_unmatched_records_sha256": summary["sha256"]["springer_only_unmatched_records"],
                },
            )
            write_json(run_root / "checkpoint.json", {"completed_enumeration_years": [], "enumerated": {}, "details_complete": True})
            write_json(run_root / "enumeration_report.json", {"year_reports": {"2026": {"conference_series_volumes": 1, "items": publisher_rows}}})
            write_jsonl(run_root / "metadata_staging.jsonl", [
                {"venue_id": "eccv", "year": 2026, "source_native_id": "springer-1"},
                {"venue_id": "eccv", "year": 2026, "source_native_id": "springer-2"},
            ])

            result = integrate_virtual_union(run_root, gap_root, workspace)

            self.assertEqual(result["counts"]["identity_union_size"], 3)
            self.assertEqual(result["counts"]["virtual_only_imported"], 1)
            self.assertFalse(partial.exists())
            self.assertTrue((run_root / "provisional_expected" / partial.name).is_file())
            expected = _read_jsonl_gzip_for_test(expected_dir / "2026.jsonl.gz")
            self.assertEqual(len(expected), 3)
            staged = [json.loads(line) for line in (run_root / "metadata_staging.jsonl").read_text().splitlines() if line]
            virtual = next(row for row in staged if row["source_native_id"] == "eccv-virtual-2026-poster-7")
            self.assertIsNone(virtual["doi"])
            self.assertIsNone(virtual["publication_date"])
            self.assertEqual(virtual["doi_status"], "not_present_on_official_page")
            self.assertEqual(virtual["missing_fields"]["publication_date"]["reason_code"], "not_assigned")
            self.assertIn("document_type", virtual["field_provenance"])
            self.assertIn("pdf_discovery_status", virtual["field_provenance"])
            self.assertEqual(virtual["pdf_url"], candidate["paper_pdf_url"])
            self.assertEqual(virtual["authors"], candidate["authors"])


def _read_jsonl_gzip_for_test(path: Path) -> list[dict[str, object]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


if __name__ == "__main__":
    unittest.main()

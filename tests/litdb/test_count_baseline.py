from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.count_baseline import verify_count_output
from litdb.io import atomic_json


class CountBaselineTests(unittest.TestCase):
    def _write_historical_inactive_zero(self, root: Path, *, structured: bool) -> None:
        (root / "expected").mkdir()
        (root / "count").mkdir()
        with gzip.open(root / "expected" / "2015.jsonl.gz", "wt"):
            pass
        empty_hash = hashlib.sha256(b"").hexdigest()
        summary = {
            "venue_id": "corl",
            "year": 2015,
            "displayed_count": 0,
            "parsed_count": 0,
            "unique_count": 0,
            "eligible_count": 0,
            "position_paper_count": 0,
            "source_grade": "A",
            "set_hash_sha256": empty_hash,
            "ordered_listing_hash_sha256": empty_hash,
        }
        if structured:
            summary.update({
                "waterline_status": "NOT_YET_ACTIVE",
                "waterline_evidence_urls": ["https://www.corl.org/about"],
                "venue_start_year": 2017,
            })
        atomic_json(root / "count" / "2015.json", summary)
        active_row = {
            "venue_id": "corl", "year": 2016, "source_item_id": "x",
            "landing_url": "https://proceedings.mlr.press/v1/x.html", "title": "X",
            "listing_position": 1, "source_page_url": "https://proceedings.mlr.press/v1/",
            "source_grade": "B", "document_type": "research-paper",
            "include_decision": "include_candidate", "inclusion_rule_id": "x",
            "observed_at": "2026-01-01Z",
        }
        with gzip.open(root / "expected" / "2016.jsonl.gz", "wt") as handle:
            handle.write(json.dumps(active_row) + "\n")
        active_hash = hashlib.sha256(active_row["landing_url"].encode()).hexdigest()
        active_summary = {
            "venue_id": "corl", "year": 2016, "displayed_count": 1,
            "parsed_count": 1, "unique_count": 1, "eligible_count": 1,
            "position_paper_count": 0, "source_grade": "B",
            "set_hash_sha256": active_hash, "ordered_listing_hash_sha256": active_hash,
        }
        atomic_json(root / "count" / "2016.json", active_summary)
        atomic_json(root / "venue_count_report.json", {
            "venue_id": "corl",
            "status": "PASS",
            "yearly": [summary, active_summary],
            "totals": {"displayed": 1, "parsed": 1, "unique": 1, "eligible": 1, "blocked": 0},
            "unresolved_anomalies": [],
        })

    def _write_current_year_zero(self, root: Path, *, structured: bool) -> None:
        (root / "expected").mkdir()
        (root / "count").mkdir()
        with gzip.open(root / "expected" / "2026.jsonl.gz", "wt"):
            pass
        empty_hash = hashlib.sha256(b"").hexdigest()
        summary = {
            "venue_id": "aistats",
            "year": 2026,
            "displayed_count": 0,
            "parsed_count": 0,
            "unique_count": 0,
            "eligible_count": 0,
            "position_paper_count": 0,
            "source_grade": "B",
            "set_hash_sha256": empty_hash,
            "ordered_listing_hash_sha256": empty_hash,
        }
        if structured:
            summary.update({
                "waterline_status": "NO_FORMAL_PMLR_VOLUME_OBSERVED",
                "waterline_evidence_urls": ["https://proceedings.mlr.press/"],
            })
        atomic_json(root / "count" / "2026.json", summary)
        atomic_json(root / "venue_count_report.json", {
            "venue_id": "aistats",
            "status": "PASS",
            "yearly": [summary],
            "totals": {"displayed": 0, "parsed": 0, "unique": 0, "eligible": 0, "blocked": 0},
            "unresolved_anomalies": [],
        })

    def test_accepts_current_year_zero_with_negative_waterline_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._write_current_year_zero(root, structured=True)
            result = verify_count_output(root, "aistats", 2026, 2026, {"proceedings.mlr.press"})
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["yearly"][0]["coverage"], 1.0)

    def test_rejects_unstructured_current_year_zero(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._write_current_year_zero(root, structured=False)
            result = verify_count_output(root, "aistats", 2026, 2026, {"proceedings.mlr.press"})
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("negative-waterline evidence" in item for item in result["errors"]))

    def test_accepts_historical_zero_with_inactive_era_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._write_historical_inactive_zero(root, structured=True)
            result = verify_count_output(root, "corl", 2015, 2016, {"corl.org", "proceedings.mlr.press"})
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["yearly"][0]["coverage"], 1.0)

    def test_rejects_unstructured_historical_zero(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._write_historical_inactive_zero(root, structured=False)
            result = verify_count_output(root, "corl", 2015, 2016, {"corl.org", "proceedings.mlr.press"})
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("inactive-era evidence" in item for item in result["errors"]))

    def test_detects_duplicate_manifest_url(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "expected").mkdir()
            (root / "count").mkdir()
            row = {"venue_id":"icml","year":2015,"source_item_id":"x","landing_url":"https://proceedings.mlr.press/v1/x.html","title":"X","listing_position":1,"source_page_url":"https://proceedings.mlr.press/v1/","source_grade":"A","document_type":"research-paper","include_decision":"include_candidate","inclusion_rule_id":"x","observed_at":"2026-01-01Z"}
            with gzip.open(root / "expected" / "2015.jsonl.gz", "wt") as handle:
                handle.write(json.dumps(row) + "\n")
                row["listing_position"] = 2
                row["source_item_id"] = "y"
                handle.write(json.dumps(row) + "\n")
            urls = ["https://proceedings.mlr.press/v1/x.html"] * 2
            summary = {"venue_id":"icml","year":2015,"displayed_count":2,"parsed_count":2,"unique_count":2,"eligible_count":2,"position_paper_count":0,"source_grade":"A","set_hash_sha256":hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest(),"ordered_listing_hash_sha256":hashlib.sha256("\n".join(urls).encode()).hexdigest()}
            atomic_json(root / "count" / "2015.json", summary)
            atomic_json(root / "venue_count_report.json", {"venue_id":"icml","status":"PASS","yearly":[summary],"totals":{"displayed":2,"parsed":2,"unique":2,"eligible":2,"blocked":0},"unresolved_anomalies":[]})
            result = verify_count_output(
                root,
                "icml",
                2015,
                2015,
                {"icml.cc", "proceedings.mlr.press"},
            )
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("duplicate landing_url" in item for item in result["errors"]))

    def test_rejects_non_registry_domain_for_any_venue(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "expected").mkdir()
            (root / "count").mkdir()
            row = {"venue_id":"acl","year":2015,"source_item_id":"P15-1001","landing_url":"https://example.invalid/P15-1001/","title":"X","listing_position":1,"source_page_url":"https://aclanthology.org/volumes/P15-1/","source_grade":"A","document_type":"research-paper","include_decision":"include_candidate","inclusion_rule_id":"acl-main","observed_at":"2026-01-01Z"}
            with gzip.open(root / "expected" / "2015.jsonl.gz", "wt") as handle:
                handle.write(json.dumps(row) + "\n")
            urls = [row["landing_url"]]
            summary = {"venue_id":"acl","year":2015,"displayed_count":1,"parsed_count":1,"unique_count":1,"eligible_count":1,"position_paper_count":0,"source_grade":"A","set_hash_sha256":hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest(),"ordered_listing_hash_sha256":hashlib.sha256("\n".join(urls).encode()).hexdigest()}
            atomic_json(root / "count" / "2015.json", summary)
            atomic_json(root / "venue_count_report.json", {"venue_id":"acl","status":"PASS","yearly":[summary],"totals":{"displayed":1,"parsed":1,"unique":1,"eligible":1,"blocked":0},"unresolved_anomalies":[]})
            result = verify_count_output(root, "acl", 2015, 2015, {"aclanthology.org"})
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("outside allowlist" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()

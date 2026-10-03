from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.io import atomic_json
from litdb.pilot import verify_pilot_output


class PilotVerifierTests(unittest.TestCase):
    def test_accepts_sample_from_different_source_observation_for_same_canonical_url(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = []
            samples = []
            for index in range(1, 31):
                landing_url = f"https://www.nature.com/articles/example-{index}"
                rows.append({
                    "venue_id": "nature", "selected_unit_id": "issue:1", "year": 2025,
                    "listing_position": index, "source_item_id": f"nature:issue:{index}",
                    "landing_url": landing_url,
                    "source_page_url": "https://www.nature.com/nature/volumes/1/issues/1",
                    "title": f"Title {index}", "authors": ["A"],
                    "document_type": "Article", "include_decision": "include",
                    "inclusion_rule_id": "nature-union", "observed_at": "2026-01-01Z",
                })
                samples.append({
                    "venue_id": "nature", "source_item_id": f"nature:listing:{index}",
                    "landing_url": landing_url, "source_url": landing_url,
                    "title": f"Title {index}", "authors": ["A"], "date": "2025-01-01",
                    "document_type": "Article", "doi": f"10.1038/example-{index}",
                    "abstract": "Abstract", "pdf_location": f"{landing_url}.pdf",
                })
            (root / "pilot_manifest.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            (root / "pilot_sample_metadata.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in samples), encoding="utf-8"
            )
            atomic_json(root / "pilot_report.json", {
                "venue_id": "nature", "status": "PASS",
                "totals": {"eligible": 30}, "unresolved_anomalies": [],
            })

            result = verify_pilot_output(root, "nature", {"nature.com"})

            self.assertEqual(result["status"], "PASS")

    def test_rejects_sample_outside_manifest_and_domain(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = []
            for index in range(1, 31):
                rows.append({
                    "venue_id": "acl", "selected_unit_id": "P15-1", "year": 2015,
                    "listing_position": index, "source_item_id": f"P15-{index}",
                    "landing_url": f"https://aclanthology.org/P15-{index}/",
                    "source_page_url": "https://aclanthology.org/volumes/P15-1/",
                    "title": f"Title {index}", "authors": ["A"],
                    "document_type": "research-paper", "include_decision": "include_candidate",
                    "inclusion_rule_id": "acl-main", "observed_at": "2026-01-01Z",
                })
            (root / "pilot_manifest.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            samples = []
            for row in rows:
                samples.append({
                    "venue_id": "acl", "source_item_id": row["source_item_id"],
                    "landing_url": row["landing_url"], "source_url": row["landing_url"],
                    "title": row["title"], "authors": ["A"], "date": "2015-01-01",
                    "document_type": "research-paper", "doi": None, "abstract": None,
                    "pdf_location": None,
                })
            samples[-1]["landing_url"] = "https://example.invalid/bad"
            (root / "pilot_sample_metadata.jsonl").write_text("".join(json.dumps(row) + "\n" for row in samples), encoding="utf-8")
            atomic_json(root / "pilot_report.json", {"venue_id": "acl", "status": "PASS", "totals": {"eligible": 30}, "unresolved_anomalies": []})
            result = verify_pilot_output(root, "acl", {"aclanthology.org"})
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("sample not found" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()

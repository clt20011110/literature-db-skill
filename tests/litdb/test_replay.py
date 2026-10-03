from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.io import atomic_json
from litdb.replay import verify_replay_output


class ReplayVerifierTests(unittest.TestCase):
    def test_exact_small_replay_passes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = {"venue_id":"acl","year":2015,"source_item_id":"P15-1001","landing_url":"https://aclanthology.org/P15-1001/","source_page_url":"https://aclanthology.org/volumes/P15-1/"}
            sample = {"venue_id":"acl","source_item_id":"P15-1001","landing_url":"https://aclanthology.org/P15-1001/","title":"Title","authors":["A"],"date":"2015-01-01","document_type":"research-paper","doi":"10.1/X","abstract":"Text","pdf_location":"https://aclanthology.org/P15-1001.pdf"}
            pilot_manifest = root / "pilot_manifest.jsonl"
            pilot_samples = root / "pilot_sample_metadata.jsonl"
            pilot_manifest.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            pilot_samples.write_text(json.dumps(sample) + "\n", encoding="utf-8")
            (root / "replay_manifest.jsonl").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            replay_sample = dict(
                sample,
                source_item_id="different-source-observation",
                doi="https://doi.org/10.1/x",
                landing_url="https://aclanthology.org/P15-1001",
            )
            (root / "replay_sample_metadata.jsonl").write_text(json.dumps(replay_sample) + "\n", encoding="utf-8")
            atomic_json(root / "replay_report.json", {"venue_id":"acl","status":"PASS"})
            result = verify_replay_output(root, "acl", pilot_manifest, pilot_samples, {"aclanthology.org"})
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["set_agreement"], 1.0)
            self.assertEqual(result["field_agreement"], 1.0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.paths import LitDBPaths
from litdb.registry import install_registry, load_source_venues, validate_runtime, validate_venues
from litdb.search import VENUE_LABELS


class RegistryTests(unittest.TestCase):
    def test_source_registry_exact_counts(self) -> None:
        result = validate_venues(load_source_venues(), strict=True)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["venue_count"], 108)
        self.assertEqual(result["conference_count"], 48)
        self.assertEqual(result["journal_count"], 60)

    def test_bioinformatics_registration_contract_and_search_label(self) -> None:
        venue = next(item for item in load_source_venues() if item["id"] == "bioinformatics")
        self.assertEqual(venue["canonical_name"], "Bioinformatics")
        self.assertEqual(venue["venue_type"], "journal")
        self.assertEqual(venue["active_from"], 1985)
        self.assertEqual(venue["crawl_from"], 2015)
        self.assertEqual(venue["publisher_family"], "oup-academic")
        self.assertEqual(venue["eissn"], ["1367-4811"])
        self.assertEqual(
            venue["allowed_domains"],
            ["academic.oup.com", "doi.org"],
        )
        self.assertEqual(
            venue["allowed_path_prefixes"],
            {
                "academic.oup.com": ["/bioinformatics/"],
                "doi.org": ["/10.1093/bioinformatics/"],
                "www.ebi.ac.uk": ["/europepmc/webservices/rest/"],
                "europepmc.org": ["/articles/"],
                "api.crossref.org": [
                    "/works/doi/10.1093/bioinformatics/btaf647",
                    "/journals/1367-4811/works",
                ],
            },
        )
        self.assertIn("original_paper", venue["main_track_rules"])
        self.assertIn("application_note", venue["main_track_rules"])
        self.assertIn("review", venue["main_track_rules"])
        self.assertIn("editorial", venue["exclude_rules"])
        self.assertIn("correction", venue["exclude_rules"])
        self.assertIn("front_matter", venue["exclude_rules"])
        families = json.loads((ROOT / "config/litdb/publisher_families.yml").read_text())
        self.assertIn("oup-academic", families["families"])
        self.assertEqual(VENUE_LABELS["bioinformatics"], "Bioinformatics")

    def test_runtime_registry_one_file_per_venue(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            install_registry(paths)
            result = validate_runtime(paths, strict=True)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(len(list(paths.venues.glob("*.yml"))), 108)
            summary = json.loads((paths.registry / "venue_registry.yml").read_text())
            self.assertIn("bioinformatics", summary["venue_ids"])
            self.assertIn("venues/bioinformatics.yml", summary["source_files"])


if __name__ == "__main__":
    unittest.main()

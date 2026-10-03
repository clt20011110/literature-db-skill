from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.paths import LitDBPaths
from litdb.registry import install_registry, load_source_venues, validate_runtime, validate_venues


class RegistryTests(unittest.TestCase):
    def test_source_registry_exact_counts(self) -> None:
        result = validate_venues(load_source_venues(), strict=True)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["venue_count"], 107)
        self.assertEqual(result["conference_count"], 48)
        self.assertEqual(result["journal_count"], 59)

    def test_runtime_registry_one_file_per_venue(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            install_registry(paths)
            result = validate_runtime(paths, strict=True)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(len(list(paths.venues.glob("*.yml"))), 107)


if __name__ == "__main__":
    unittest.main()

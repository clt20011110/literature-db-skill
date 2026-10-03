from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.browser_preflight import check
from litdb.constants import API_KEY_NAMES, CONFIG_ROOT
from litdb.paths import LitDBPaths


class PreflightTests(unittest.TestCase):
    def test_zero_key_browser_first_strict_passes_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            shutil.copyfile(CONFIG_ROOT / "browser_capabilities.json", paths.preflight / "browser_capabilities.json")
            saved = {name: os.environ.pop(name, None) for name in API_KEY_NAMES}
            try:
                code, report = check(paths, browser_first=True, strict=True)
            finally:
                for name, value in saved.items():
                    if value is not None:
                        os.environ[name] = value
            self.assertEqual(code, 0)
            self.assertEqual(report["required_api_keys"], 0)
            self.assertEqual(report["status"], "PASS")

    def test_missing_browser_evidence_has_deterministic_exit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            code, report = check(paths, browser_first=True, strict=True)
            self.assertEqual(code, 5)
            self.assertEqual(report["status"], "BROWSER_CAPABILITY_MISSING")


if __name__ == "__main__":
    unittest.main()

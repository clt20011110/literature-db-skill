from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.browser_policy import detect_prompt_injection, domain_allowed


class BrowserPolicyTests(unittest.TestCase):
    def test_allowlist_rejects_lookalikes(self) -> None:
        self.assertTrue(domain_allowed("https://proceedings.mlr.press/v1", ["mlr.press"]))
        self.assertFalse(domain_allowed("https://mlr.press.attacker.example", ["mlr.press"]))

    def test_prompt_injection_fixture_is_blocked(self) -> None:
        fixture = (Path(__file__).parent / "fixtures" / "prompt_injection.html").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(detect_prompt_injection(fixture)), 2)


if __name__ == "__main__":
    unittest.main()

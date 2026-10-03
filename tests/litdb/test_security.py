from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.security_scan import scan_text


class SecurityTests(unittest.TestCase):
    def test_detects_headers_and_signed_urls(self) -> None:
        text = "Authorization: Bearer abcdefghijklmnop\nhttps://x.example/a.pdf?token=secret"
        kinds = {item["kind"] for item in scan_text(text)}
        self.assertIn("authorization_header", kinds)
        self.assertIn("signed_or_session_url", kinds)

    def test_ordinary_metadata_is_clean(self) -> None:
        self.assertEqual(scan_text('{"title":"A paper","doi":"10.1/test"}'), [])


if __name__ == "__main__":
    unittest.main()

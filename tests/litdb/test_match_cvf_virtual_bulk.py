from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.match_cvf_virtual_bulk import load_json_rows, main, match_rows, normalize_piece, title_author_key


class CVFVirtualBulkMatcherTests(unittest.TestCase):
    def test_saved_results_payload_loads_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            path.write_text(json.dumps({"count": 1, "next": None, "results": [{"name": "Paper"}]}), encoding="utf-8")
            rows, info = load_json_rows(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(info["shape"], "results_object")
        self.assertIsNone(info["next"])
        self.assertTrue(info["complete_payload"])

    def test_paginated_or_count_mismatched_results_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            for payload in (
                {"count": 2, "next": "?page=2", "previous": None, "results": [{"name": "Paper"}]},
                {"count": 2, "next": None, "previous": None, "results": [{"name": "Paper"}]},
                {"count": 1, "next": None, "previous": "?page=1", "results": [{"name": "Paper"}]},
            ):
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "paginated|incomplete"):
                    load_json_rows(path)

    def test_title_author_key_normalizes_unicode_punctuation_and_preserves_order(self) -> None:
        self.assertEqual(normalize_piece("  Café—Vision:  3D! "), "café—vision: 3d!")
        self.assertEqual(
            title_author_key("Same: Title", ["Jane A. Doe", "José Smith"]),
            ("same: title", ("jane a. doe", "josé smith")),
        )
        self.assertNotEqual(
            title_author_key("Same: Title", ["Jane A. Doe", "José Smith"]),
            title_author_key("Same Title", ["José Smith", "Jane A. Doe"]),
        )

    def test_match_uses_unique_exact_title_and_ordered_authors_without_fuzzy_guessing(self) -> None:
        expected = [{
            "source_native_id": "CVPR2025:exact",
            "venue_id": "cvpr",
            "year": 2025,
            "title": "A Robust 3D Method!",
            "authors": ["Jane Doe", "John Roe"],
            "landing_url": "https://openaccess.thecvf.com/a.html",
            "pdf_url": "https://openaccess.thecvf.com/a.pdf",
        }]
        virtual = [{
            "sourceid": 12,
            "name": "A robust 3D method!",
            "authors": [{"fullname": "Jane Doe"}, {"fullname": "John Roe"}],
            "abstract": "Abstract text.",
        }]
        result = match_rows(virtual, expected, {})
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["match_method"], "normalized_unique_title_ordered_authors")
        self.assertEqual(result["matched"][0]["expected_source_native_id"], "CVPR2025:exact")

        near_match = [{**virtual[0], "name": "A robust 3D methods!"}]
        result = match_rows(near_match, expected, {})
        self.assertEqual(result["matched"], [])
        self.assertEqual(len(result["unmatched_expected"]), 1)

    def test_exact_official_url_has_precedence(self) -> None:
        expected = [{
            "source_native_id": "CVPR2024:url",
            "venue_id": "cvpr",
            "year": 2024,
            "title": "Expected title",
            "authors": ["A. Author"],
            "landing_url": "https://openaccess.thecvf.com/content/a.html",
            "pdf_url": "https://openaccess.thecvf.com/content/a.pdf",
        }]
        virtual = [{
            "sourceid": 9,
            "name": "Different listing title",
            "authors": [{"fullname": "Someone Else"}],
            "paper_pdf_url": "https://openaccess.thecvf.com/content/a.pdf",
            "abstract": "An abstract",
        }]
        result = match_rows(virtual, expected, {})
        self.assertEqual(result["matched"][0]["match_method"], "exact_official_url")

    def test_duplicate_conflicting_virtual_records_are_not_natural_key_matched(self) -> None:
        expected = [{
            "source_native_id": "CVPR2023:duplicate",
            "venue_id": "cvpr",
            "year": 2023,
            "title": "Repeated title",
            "authors": ["A. Author"],
            "landing_url": None,
            "pdf_url": None,
        }]
        virtual = [
            {"sourceid": 3, "name": "Repeated title", "authors": [{"fullname": "A. Author"}], "abstract": "One"},
            {"sourceid": 4, "name": "Repeated title", "authors": [{"fullname": "A. Author"}], "abstract": "Two"},
        ]
        result = match_rows(virtual, expected, {})
        self.assertEqual(result["matched"], [])
        self.assertEqual(len(result["unmatched_expected"]), 1)

    def test_conflicting_duplicate_records_cannot_match_by_url(self) -> None:
        expected = [{
            "source_native_id": "CVPR2023:duplicate-url",
            "venue_id": "cvpr",
            "year": 2023,
            "title": "Repeated title",
            "authors": ["A. Author"],
            "landing_url": None,
            "pdf_url": "https://openaccess.thecvf.com/paper.pdf",
        }]
        virtual = [
            {"sourceid": 3, "name": "Repeated title", "authors": [{"fullname": "A. Author"}], "abstract": "One"},
            {"sourceid": 4, "name": "Repeated title", "authors": [{"fullname": "A. Author"}], "abstract": "Two", "paper_pdf_url": "https://openaccess.thecvf.com/paper.pdf"},
        ]
        result = match_rows(virtual, expected, {})
        self.assertEqual(result["matched"], [])
        self.assertEqual(result["ambiguous"][0]["reason"], "conflicting_duplicate_virtual_records")

    def test_exact_url_selects_the_matching_card_as_representative(self) -> None:
        expected = [{
            "source_native_id": "CVPR2024:url-representative",
            "venue_id": "cvpr",
            "year": 2024,
            "title": "Same title",
            "authors": ["A. Author"],
            "landing_url": None,
            "pdf_url": "https://openaccess.thecvf.com/target.pdf",
        }]
        virtual = [
            {"sourceid": 7, "name": "Same title", "authors": [{"fullname": "A. Author"}], "abstract": "One agreed abstract"},
            {"sourceid": 7, "name": " SAME   TITLE ", "authors": [{"fullname": "A. Author"}], "abstract": "One agreed abstract", "paper_pdf_url": "https://openaccess.thecvf.com/target.pdf"},
        ]
        result = match_rows(virtual, expected, {})
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["virtual_title"], " SAME   TITLE ")
        self.assertEqual(result["matched"][0]["match_method"], "exact_official_url")

    def test_missing_expected_manifest_makes_command_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps([{"name": "Paper"}]), encoding="utf-8")
            expected_root = root / "expected"
            expected_root.mkdir()
            output = root / "output"
            argv = [
                "match_cvf_virtual_bulk.py", "--metadata-json", str(metadata),
                "--expected-root", str(expected_root), "--output-root", str(output),
                "--venue", "cvpr", "--year", "2024",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(main(), 1)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import fetch_bioinformatics_europepmc as europepmc
from fetch_bioinformatics_europepmc import (
    parse_args,
    curl_request,
    normalize_record,
    redact_response_headers,
    redact_set_cookie_headers,
    redact_sensitive_url,
    redact_urls_in_json,
)


class EuropePmcSupplementRedactionTests(unittest.TestCase):
    def test_run_directory_is_explicit_and_year_to_defaults_to_current_year(self) -> None:
        args = parse_args(["--run-dir", "$litdb_run/supplement"])

        self.assertEqual(str(args.run_dir), "$litdb_run/supplement")
        self.assertEqual(args.year_from, 2014)
        self.assertEqual(args.year_to, datetime.now().year)

    def test_exact_target_doi_prefix_excludes_same_eissn_other_journal(self) -> None:
        record = {
            "source": "MED",
            "id": "41325268",
            "doi": "10.1093/bioadv/vbaf199",
            "journalInfo": {
                "yearOfPublication": 2025,
                "journal": {"title": "Bioinformatics Advances", "essn": "1367-4811"},
            },
        }

        normalized = normalize_record(
            record,
            source_url="https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=safe",
            observed_at="2026-10-05T08:00:00Z",
            page_number=1,
            requested_from_year=2014,
            requested_to_year=2026,
        )

        self.assertTrue(normalized["within_requested_year_window"])
        self.assertFalse(normalized["within_collection_scope"])
        self.assertFalse(normalized["matches_target_doi_prefix"])
        self.assertEqual(normalized["doi_scope_status"], "non_target_doi")

    def test_sensitive_query_values_are_masked_without_losing_url_context(self) -> None:
        raw = "https://watermark.silverchair.com/article.pdf?token=secret-value&pdf=render"

        safe, keys, host = redact_sensitive_url(raw)

        self.assertNotIn("secret-value", safe)
        self.assertIn("watermark.silverchair.com/article.pdf", safe)
        self.assertIn("pdf=render", safe)
        self.assertEqual(keys, ["token"])
        self.assertEqual(host, "watermark.silverchair.com")

    def test_api_json_redaction_tracks_host_and_query_key_counts(self) -> None:
        raw = {"fullTextUrlList": {"fullTextUrl": [
            {"url": "https://watermark.silverchair.com/a.pdf?token=secret"},
            {"url": "https://europepmc.org/articles/PMC1?pdf=render"},
        ]}}

        safe, counts = redact_urls_in_json(raw)

        first = safe["fullTextUrlList"]["fullTextUrl"][0]
        self.assertNotIn("secret", first["url"])
        self.assertEqual(first["url_redacted_query_parameters"], ["token"])
        self.assertEqual(safe["fullTextUrlList"]["fullTextUrl"][1]["url"], raw["fullTextUrlList"]["fullTextUrl"][1]["url"])
        self.assertEqual(counts, {("watermark.silverchair.com", "token"): 1})

    def test_set_cookie_values_are_not_retained(self) -> None:
        raw = b"HTTP/2 200\r\nSet-Cookie: JSESSIONID=private-cookie; Path=/; HttpOnly\r\nContent-Type: application/json\r\n\r\n"

        safe, counts = redact_set_cookie_headers(raw)

        self.assertNotIn(b"private-cookie", safe)
        self.assertIn(b"Set-Cookie: [REDACTED]", safe)
        self.assertEqual(counts, {"JSESSIONID": 1})

    def test_sensitive_header_values_and_url_query_values_are_masked(self) -> None:
        raw = (
            b"HTTP/2 302\r\n"
            b"Authorization: Bearer private-token\r\n"
            b"Location: https://watermark.silverchair.com/a.pdf?signature=private-signature\r\n"
        )

        safe, secret_headers, url_counts = redact_response_headers(raw)

        self.assertNotIn(b"private-token", safe)
        self.assertNotIn(b"private-signature", safe)
        self.assertEqual(secret_headers["authorization"], 1)
        self.assertEqual(url_counts, {("watermark.silverchair.com", "signature"): 1})

    def test_curl_persists_only_redacted_body_and_headers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake_curl = root / "curl-fixture"
            fake_curl.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "args=sys.argv[1:]\n"
                "body=pathlib.Path(args[args.index('--output')+1])\n"
                "headers=pathlib.Path(args[args.index('--dump-header')+1])\n"
                "url=args[-1]\n"
                "body.write_text(json.dumps({'resultList': {'result': [{'fullTextUrlList': {'fullTextUrl': [{'documentStyle': 'pdf', 'url': 'https://watermark.silverchair.com/file.pdf?token=synthetic-secret'}]}}]}}))\n"
                "headers.write_text('HTTP/2 200\\r\\nSet-Cookie: JSESSIONID=synthetic-cookie; Path=/\\r\\n\\r\\n')\n"
                "print('200\\t'+url+'\\tapplication/json')\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            body_path = root / "page.json"
            header_path = root / "page.headers.txt"

            result = curl_request(
                str(fake_curl),
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=safe",
                body_path,
                header_path,
                10,
            )

            safe_body = body_path.read_text(encoding="utf-8")
            safe_headers = header_path.read_text(encoding="utf-8")
            self.assertNotIn("synthetic-secret", safe_body)
            self.assertNotIn("synthetic-cookie", safe_headers)
            self.assertIn("url_redacted_query_parameters", safe_body)
            self.assertEqual(result["sanitization_receipt"]["set_cookie_header_redaction_count"], 1)
            self.assertEqual(
                result["sanitization_receipt"]["sensitive_url_query_value_redactions_by_host_and_key"],
                {"watermark.silverchair.com::token": 1},
            )

    def test_challenge_is_classified_before_redaction_and_blocks_resume(self) -> None:
        challenge_body = b"<html><title>Security verification</title>private-challenge-content</html>"

        with tempfile.TemporaryDirectory() as folder:
            run_dir = Path(folder)
            calls = []

            def fake_curl(command, *, text, capture_output):
                calls.append(command)
                body_path = Path(command[command.index("--output") + 1])
                headers_path = Path(command[command.index("--dump-header") + 1])
                url = command[-1]
                body_path.write_bytes(challenge_body)
                headers_path.write_bytes(b"HTTP/2 200\r\nContent-Type: text/html\r\n\r\n")
                return subprocess.CompletedProcess(
                    command, 0, stdout=f"200\t{url}\ttext/html\n", stderr=""
                )

            args = parse_args(["--run-dir", str(run_dir)])
            with patch.object(europepmc.shutil, "which", return_value="curl"), patch.object(
                europepmc.subprocess, "run", side_effect=fake_curl
            ), contextlib.redirect_stdout(io.StringIO()):
                result = europepmc.fetch(args)

            checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
            saved_body = (run_dir / "pages/page-00001.json").read_text()
            self.assertEqual(result, 2)
            self.assertEqual(checkpoint["status"], "blocked_challenge")
            self.assertTrue(checkpoint["last_error"]["challenge_marker_detected"])
            self.assertEqual(checkpoint["last_error"]["response_body_kind"], "challenge")
            self.assertNotIn("private-challenge-content", saved_body)
            self.assertIn('"challenge_marker_detected":true', saved_body)

            with patch.object(europepmc.shutil, "which", return_value="curl"), patch.object(
                europepmc.subprocess, "run", side_effect=fake_curl
            ):
                with self.assertRaisesRegex(ValueError, "checkpoint is blocked"):
                    europepmc.fetch(args)
            self.assertEqual(len(calls), 1)

    def test_terminal_cursor_or_empty_page_cannot_complete_before_hit_count(self) -> None:
        test_cases = (
            {
                "name": "repeated_cursor",
                "hit_count": 3,
                "second_cursor": "page-2",
                "second_next_cursor": "page-2",
                "second_results": [{"source": "MED", "id": "2", "doi": "10.1093/bioinformatics/b2"}],
                "records_fetched": 2,
            },
            {
                "name": "empty_result_page",
                "hit_count": 2,
                "second_cursor": "page-2",
                "second_next_cursor": "page-3",
                "second_results": [],
                "records_fetched": 1,
            },
        )
        for case in test_cases:
            with self.subTest(case=case["name"]), tempfile.TemporaryDirectory() as folder:
                run_dir = Path(folder)
                calls = []
                payloads = {
                    "*": {
                        "hitCount": case["hit_count"],
                        "nextCursorMark": "page-2",
                        "resultList": {"result": [{
                            "source": "MED",
                            "id": "1",
                            "doi": "10.1093/bioinformatics/b1",
                            "journalInfo": {
                                "yearOfPublication": 2025,
                                "journal": {"title": "Bioinformatics", "essn": "1367-4811"},
                            },
                        }]},
                    },
                    case["second_cursor"]: {
                        "hitCount": case["hit_count"],
                        "nextCursorMark": case["second_next_cursor"],
                        "resultList": {"result": case["second_results"]},
                    },
                }

                def fake_curl(command, *, text, capture_output):
                    calls.append(command)
                    body_path = Path(command[command.index("--output") + 1])
                    headers_path = Path(command[command.index("--dump-header") + 1])
                    url = command[-1]
                    cursor = parse_qs(urlsplit(url).query)["cursorMark"][0]
                    body_path.write_text(json.dumps(payloads[cursor]), encoding="utf-8")
                    headers_path.write_bytes(b"HTTP/2 200\r\nContent-Type: application/json\r\n\r\n")
                    return subprocess.CompletedProcess(
                        command, 0, stdout=f"200\t{url}\tapplication/json\n", stderr=""
                    )

                args = parse_args(["--run-dir", str(run_dir), "--delay-seconds", "1"])
                with patch.object(europepmc.shutil, "which", return_value="curl"), patch.object(
                    europepmc.subprocess, "run", side_effect=fake_curl
                ), patch.object(europepmc.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
                    result = europepmc.fetch(args)

                checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
                self.assertEqual(result, 2)
                self.assertEqual(checkpoint["status"], "blocked_incomplete_hitcount")
                self.assertEqual(checkpoint["records_fetched"], case["records_fetched"])
                self.assertEqual(checkpoint["hit_count"], case["hit_count"])
                self.assertEqual(
                    checkpoint["last_error"]["termination_condition"], case["name"]
                )
                self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

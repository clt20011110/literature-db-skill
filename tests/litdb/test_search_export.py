from __future__ import annotations

import csv
import hashlib
import io
import json
import multiprocessing
import sqlite3
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.paths import LitDBPaths
from litdb.search_export import (
    UnknownPaperIds,
    build_export,
    load_export_records,
)
from litdb.search_server import serve, stop


def serve_test_home(home: str) -> None:
    serve(LitDBPaths(Path(home)), port=0)


class SearchExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.paths = LitDBPaths(self.root)
        self.home = self.root / "search"
        self.home.mkdir()
        self.identities = [hashlib.sha256(f"paper-{index}".encode()).hexdigest() for index in range(3)]
        self.records = [
            {
                "id": self.identities[0],
                "title": "图神经网络 & VLSI_布局 {结果}",
                "authors": ["李明", "Ada Lovelace"],
                "venue": "dac",
                "year": 2024,
                "venues": [{"id": "dac", "year": 2024}, {"id": "iccad", "year": 2023}],
                "abstract": ("完整摘要 αβ。 " * 180),
                "doi": "10.1234/题目_&结果",
                "article_url": "https://example.org/paper?a=1&b=2",
                "landing_url": "https://example.org/landing",
                "pdf_url": "https://example.org/paper.pdf",
            },
            {
                "id": self.identities[1],
                "title": "=1+1",
                "authors": ["=HYPERLINK(\"https://example.org\")"],
                "venue": "icml",
                "year": 2022,
                "venues": [{"id": "icml", "year": 2022}],
                "abstract": "safe",
                "doi": "",
                "article_url": "",
                "landing_url": "",
                "pdf_url": "",
            },
            {
                "id": self.identities[2],
                "title": "RIS line test",
                "authors": ["C. Example"],
                "venue": "neurips",
                "year": 2021,
                "venues": [{"id": "neurips", "year": 2021}],
                "abstract": "first line\nTY  - JOUR\nlast line",
                "doi": "10.1/ris",
                "article_url": "https://example.org/ris",
                "landing_url": None,
                "pdf_url": None,
            },
        ]
        connection = sqlite3.connect(self.home / "papers.sqlite")
        connection.execute("CREATE TABLE papers (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO papers VALUES (?,?)",
            [(record["id"], json.dumps(record, ensure_ascii=False)) for record in self.records],
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_loads_complete_metadata_in_request_order_and_deduplicates(self) -> None:
        records = load_export_records(self.home, [self.identities[2], self.identities[0], self.identities[2]])
        self.assertEqual([record["id"] for record in records], [self.identities[2], self.identities[0]])
        self.assertEqual(records[1]["title"], "图神经网络 & VLSI_布局 {结果}")
        self.assertEqual(records[1]["abstract"], self.records[0]["abstract"])
        self.assertGreater(len(records[1]["abstract"]), 650)
        self.assertEqual(records[1]["article_url"], "https://example.org/paper?a=1&b=2")
        self.assertEqual(records[1]["venues"], self.records[0]["venues"])

    def test_unknown_id_is_reported_as_a_unit(self) -> None:
        unknown = hashlib.sha256(b"not indexed").hexdigest()
        with self.assertRaises(UnknownPaperIds):
            load_export_records(self.home, [self.identities[0], unknown])

    def test_csv_is_utf8_and_neutralizes_spreadsheet_formulas(self) -> None:
        data, mime, filename = build_export(self.records[:2], "csv")
        self.assertEqual(mime, "text/csv")
        self.assertEqual(filename, "litdb-search-results.csv")
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(rows[0]["title"], "图神经网络 & VLSI_布局 {结果}")
        self.assertEqual(rows[0]["authors"], "李明; Ada Lovelace")
        self.assertEqual(rows[0]["abstract"], self.records[0]["abstract"])
        self.assertTrue(rows[1]["title"].startswith("'=1+1"))
        self.assertTrue(rows[1]["authors"].startswith("'=HYPERLINK"))

    def test_json_bibtex_and_ris_keep_unicode_and_escape_format_delimiters(self) -> None:
        json_bytes, json_mime, _ = build_export(self.records[:1], "json")
        self.assertEqual(json_mime, "application/json")
        self.assertEqual(json.loads(json_bytes), [self.records[0]])

        bib_bytes, bib_mime, _ = build_export(self.records[:1], "bibtex")
        bibtex = bib_bytes.decode("utf-8")
        self.assertEqual(bib_mime, "application/x-bibtex")
        self.assertIn(r"图神经网络 \& VLSI\_布局 \{结果\}", bibtex)
        self.assertIn("李明 and Ada Lovelace", bibtex)
        self.assertIn("完整摘要 αβ。", bibtex)

        ris_bytes, ris_mime, _ = build_export([self.records[2]], "ris")
        ris = ris_bytes.decode("utf-8")
        self.assertEqual(ris_mime, "application/x-research-info-systems")
        self.assertIn("TI  - RIS line test", ris)
        self.assertIn("AB  - first line TY  - JOUR last line", ris)
        self.assertEqual(ris.count("TY  - GEN"), 1)
        self.assertIn("ER  -", ris)

    def test_rejects_bad_ids_format_and_empty_export(self) -> None:
        for value in ([], "not-a-list", ["unsafe-id"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                load_export_records(self.home, value)
        with self.assertRaises(ValueError):
            build_export(self.records, "xml")

    def test_http_download_origin_policy_and_ui_controls(self) -> None:
        process = multiprocessing.get_context("spawn").Process(
            target=serve_test_home,
            args=(str(self.root),),
            daemon=True,
        )
        process.start()
        server_file = self.home / "server.json"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not server_file.is_file():
            if process.exitcode is not None:
                self.fail("search server exited before it became ready")
            time.sleep(0.05)
        self.assertTrue(server_file.is_file(), "search server did not write server.json")
        info = json.loads(server_file.read_text())
        base = info["url"]
        try:
            headers = {
                "Content-Type": "application/json",
                "Origin": base,
            }
            request = urllib.request.Request(
                base + "/api/export",
                data=json.dumps({"ids": [self.identities[0]], "format": "json"}).encode(),
                headers=headers,
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "application/json")
                self.assertEqual(response.headers.get_content_charset(), "utf-8")
                self.assertEqual(response.headers.get("Content-Disposition"),
                                 'attachment; filename="litdb-search-results.json"')
                exported = json.loads(response.read().decode("utf-8"))
            self.assertEqual(exported[0]["abstract"], self.records[0]["abstract"])

            query = urlencode([
                ("id", self.identities[1]),
                ("id", self.identities[0]),
                ("id", self.identities[1]),
                ("format", "csv"),
            ])
            request = urllib.request.Request(base + "/api/export?" + query, headers={"Origin": base})
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "text/csv")
                self.assertEqual(response.headers.get_content_charset(), "utf-8")
                self.assertEqual(response.headers.get("Content-Disposition"),
                                 'attachment; filename="litdb-search-results.csv"')
                data = response.read()
            rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
            self.assertEqual([row["id"] for row in rows], [self.identities[1], self.identities[0]])

            denied = urllib.request.Request(
                base + "/api/export",
                data=json.dumps({"ids": [self.identities[0]], "format": "json"}).encode(),
                headers={**headers, "Origin": "https://attacker.example"},
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(denied, timeout=3)
            self.assertEqual(caught.exception.code, 403)

            denied_get = urllib.request.Request(
                base + "/api/export?" + urlencode([("id", self.identities[0]), ("format", "json")]),
                headers={"Origin": "https://attacker.example"},
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(denied_get, timeout=3)
            self.assertEqual(caught.exception.code, 403)

            too_long = urllib.request.Request(
                base + "/api/export?x=" + ("x" * 8193),
                headers={"Origin": base},
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(too_long, timeout=3)
            self.assertEqual(caught.exception.code, 414)

            unknown = hashlib.sha256(b"unknown").hexdigest()
            request = urllib.request.Request(
                base + "/api/export",
                data=json.dumps({"ids": [unknown], "format": "csv"}).encode(),
                headers=headers,
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(caught.exception.code, 404)

            with urllib.request.urlopen(base + "/", timeout=3) as response:
                page = response.read().decode("utf-8")
            with urllib.request.urlopen(base + "/app.js", timeout=3) as response:
                script = response.read().decode("utf-8")
            self.assertIn('id="select-all-results"', page)
            self.assertIn('id="export-current-button"', page)
            self.assertIn("handleSelectAllResults", script)
            self.assertIn("exportResults(selectedResultIds())", script)
            self.assertIn("params.append('id', identity)", script)
            self.assertIn("link.href = `/api/export?${params.toString()}`", script)
            self.assertIn("已发起 ${formatNumber(count)} 篇论文的下载。", script)
            self.assertNotIn("URL.createObjectURL", script)
        finally:
            try:
                stop(self.paths)
            finally:
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

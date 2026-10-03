from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.db import initialize
from litdb.paths import LitDBPaths
from litdb.search import (
    catalog_signature,
    document_id,
    export_catalog,
    safe_url,
    search,
    search_home,
    validate_query,
    ZvecBridge,
)


class FakeBridge:
    """Small bridge double that exposes the exact request sent to retrieval."""

    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.requests: list[dict] = []

    def call(self, request: dict, timeout: float = 180) -> dict:
        self.requests.append(request)
        return {"items": list(self.items)}


class NoCallBridge(FakeBridge):
    def call(self, request: dict, timeout: float = 180) -> dict:
        raise AssertionError("retrieval bridge should not be called for an empty scope")


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.paths = LitDBPaths(self.root)
        initialize(self.paths.catalog)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def insert(self, table: str, payload: dict) -> int:
        connection = sqlite3.connect(self.paths.catalog)
        try:
            cursor = connection.execute(
                f"INSERT INTO {table}(payload_json, created_at) VALUES (?, ?)",
                (json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def replace(self, table: str, row_id: int, payload: dict) -> None:
        connection = sqlite3.connect(self.paths.catalog)
        try:
            connection.execute(
                f"UPDATE {table} SET payload_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), row_id),
            )
            connection.commit()
        finally:
            connection.close()

    def delete_all(self, table: str) -> None:
        connection = sqlite3.connect(self.paths.catalog)
        try:
            connection.execute(f"DELETE FROM {table}")
            connection.commit()
        finally:
            connection.close()

    def canonical(
        self,
        key: str = "icml:shared",
        *,
        title: str = "Temporal Graph Forecasting",
        abstract: str = "A graph model forecasts traffic and explains important roads.",
        year: int = 2020,
        source_id: str = "shared-2020",
        authors: list[object] | None = None,
    ) -> tuple[int, dict]:
        payload = {
            "schema_version": "canonical-work-v1",
            "canonical_key": key,
            "venue_id": "icml",
            "title": title,
            "authors": authors or ["Ada Lovelace", {"name": "Alan Turing"}],
            "abstract": abstract,
            "publication_year": year,
            "document_type": "research-paper",
            "primary_source_item_id": source_id,
            "doi": "10.1234/example.1",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        return self.insert("canonical_work", payload), payload

    def source(
        self,
        key: str = "icml:shared",
        *,
        source_id: str = "shared-2020",
        year: int = 2020,
        inclusion: str = "include",
        landing: str | None = "https://papers.example.org/shared",
        pdf: str | None = "https://papers.example.org/shared.pdf",
        article: str | None = "https://papers.example.org/shared.html",
    ) -> int:
        payload = {
            "canonical_key": key,
            "source_item_id": source_id,
            "source_native_id": source_id,
            "venue_id": "icml",
            "year": year,
            "title": "source title is not the canonical title",
            "landing_url": landing,
            "pdf_url": pdf,
            "article_url": article,
            "inclusion_decision": inclusion,
        }
        return self.insert("source_item", payload)

    def seed_shared(self, *, with_second_year: bool = True) -> tuple[int, str]:
        canonical_id, _ = self.canonical()
        self.source()
        if with_second_year:
            self.source(
                source_id="shared-2019",
                year=2019,
                landing="https://papers.example.org/shared-2019",
                pdf="https://papers.example.org/shared-2019.pdf",
                article="https://papers.example.org/shared-2019.html",
            )
        return canonical_id, "icml:shared"

    def write_ready_state(self) -> None:
        home = search_home(self.paths)
        home.mkdir(parents=True, exist_ok=True)
        (home / "state.json").write_text(
            json.dumps(
                {
                    "ready": True,
                    "phase": "ready",
                    "model": "local/test-model",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "catalog_signature": catalog_signature(self.paths),
                },
                ensure_ascii=False,
            )
        )

    def lookup_paper(self, canonical_key: str = "icml:shared") -> tuple[dict, str]:
        connection = sqlite3.connect(search_home(self.paths) / "papers.sqlite")
        try:
            row = connection.execute(
                "SELECT payload_json, path FROM papers WHERE canonical_key=?", (canonical_key,)
            ).fetchone()
            self.assertIsNotNone(row)
            return json.loads(row[0]), row[1]
        finally:
            connection.close()

    def hit(self, relative_path: str, score: float = 0.9) -> dict:
        return {
            "file": {"relativePath": relative_path},
            "score": score,
            "matchedBy": "vector",
        }

    def test_export_only_indexes_canonicals_with_included_source(self) -> None:
        self.seed_shared(with_second_year=False)
        self.canonical(
            key="icml:excluded",
            title="Excluded work",
            source_id="excluded-2020",
            year=2020,
        )
        self.source(
            key="icml:excluded",
            source_id="excluded-2020",
            year=2020,
            inclusion="exclude",
        )
        self.canonical(
            key="icml:pending",
            title="Pending work",
            source_id="pending-2021",
            year=2021,
        )
        self.source(
            key="icml:pending",
            source_id="pending-2021",
            year=2021,
            inclusion="pending",
        )

        result = export_catalog(self.paths, search_home(self.paths))

        self.assertEqual(result["papers"], 1)
        connection = sqlite3.connect(search_home(self.paths) / "papers.sqlite")
        try:
            keys = [row[0] for row in connection.execute("SELECT canonical_key FROM papers")]
        finally:
            connection.close()
        self.assertEqual(keys, ["icml:shared"])

    def test_cross_year_editions_are_one_result_and_each_year_filter_hits(self) -> None:
        self.seed_shared()
        export_catalog(self.paths, search_home(self.paths))
        self.write_ready_state()
        paper, primary_path = self.lookup_paper()
        paper_id = document_id("icml:shared")

        self.assertEqual(paper["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertEqual(paper["venues"], [{"id": "icml", "year": 2019}, {"id": "icml", "year": 2020}])
        self.assertEqual(paper["pdf_url"], "https://papers.example.org/shared.pdf")
        self.assertEqual(paper["article_url"], "https://papers.example.org/shared.html")

        for year in (2019, 2020):
            bridge = FakeBridge([self.hit(primary_path), self.hit(primary_path, score=0.4)])
            result = search(
                self.paths,
                {
                    "query": "graph forecasting",
                    "limit": 10,
                    "venues": ["icml"],
                    "year_from": year,
                    "year_to": year,
                    "mode": "semantic",
                },
                bridge=bridge,
            )
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(result["results"][0]["id"], paper_id)
            self.assertEqual(result["results"][0]["authors"], ["Ada Lovelace", "Alan Turing"])
            self.assertEqual(result["results"][0]["pdf_url"], "https://papers.example.org/shared.pdf")
            self.assertEqual(result["results"][0]["article_url"], "https://papers.example.org/shared.html")

    def test_incremental_export_preserves_unchanged_file_and_applies_update_and_delete(self) -> None:
        canonical_id, _ = self.seed_shared(with_second_year=False)
        first = export_catalog(self.paths, search_home(self.paths))
        paper, relative_path = self.lookup_paper()
        corpus_path = search_home(self.paths) / "corpus" / relative_path
        initial_mtime = corpus_path.stat().st_mtime_ns
        initial_text = corpus_path.read_text()

        second = export_catalog(self.paths, search_home(self.paths))
        self.assertEqual(second["counts"], {"unchanged": 1})
        self.assertEqual(corpus_path.stat().st_mtime_ns, initial_mtime)
        self.assertEqual(corpus_path.read_text(), initial_text)
        self.assertEqual(first["papers"], 1)

        connection = sqlite3.connect(self.paths.catalog)
        try:
            updated = json.loads(
                connection.execute(
                    "SELECT payload_json FROM canonical_work WHERE id=?", (canonical_id,)
                ).fetchone()[0]
            )
        finally:
            connection.close()
        updated["abstract"] = "The updated abstract describes a new explainable graph model."
        self.replace("canonical_work", canonical_id, updated)
        changed = export_catalog(self.paths, search_home(self.paths))
        self.assertEqual(changed["counts"], {"modified": 1})
        self.assertNotEqual(corpus_path.read_text(), initial_text)
        refreshed, _ = self.lookup_paper()
        self.assertEqual(refreshed["abstract"], updated["abstract"])

        self.delete_all("canonical_work")
        self.delete_all("source_item")
        deleted = export_catalog(self.paths, search_home(self.paths))
        self.assertEqual(deleted["counts"], {"deleted": 1})
        self.assertFalse(corpus_path.exists())
        connection = sqlite3.connect(search_home(self.paths) / "papers.sqlite")
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM papers").fetchone()[0], 0)
        finally:
            connection.close()

    def test_validate_query_rejects_invalid_parameters_and_deduplicates_venues(self) -> None:
        valid = validate_query(
            {
                "query": "  graph forecasting ",
                "limit": 20,
                "venues": ["icml", "icml", "neurips"],
                "year_from": 2019,
                "year_to": 2020,
                "mode": "hybrid",
            }
        )
        self.assertEqual(valid["query"], "graph forecasting")
        self.assertEqual(valid["venues"], ["icml", "neurips"])

        invalid_options = [
            {"query": ""},
            {"query": "x", "limit": 0},
            {"query": "x", "limit": 51},
            {"query": "x", "limit": True},
            {"query": "x", "mode": "unknown"},
            {"query": "x", "venues": ["ICML"]},
            {"query": "x", "venues": ["icml/"]},
            {"query": "x", "venues": "icml"},
            {"query": "x", "venues": 0},
            {"query": "x", "venues": False},
            {"query": "x", "venues": None},
            {"query": "x", "year_from": 1899},
            {"query": "x", "year_to": 2201},
            {"query": "x", "year_from": True},
            {"query": "x", "year_from": 2021, "year_to": 2020},
        ]
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    validate_query(options)

    def test_no_match_scope_returns_empty_without_calling_retrieval(self) -> None:
        self.seed_shared()
        export_catalog(self.paths, search_home(self.paths))
        self.write_ready_state()
        bridge = NoCallBridge([])

        result = search(
            self.paths,
            {
                "query": "graph forecasting",
                "limit": 10,
                "venues": ["icml"],
                "year_from": 2021,
                "year_to": 2021,
                "mode": "keyword",
            },
            bridge=bridge,
        )

        self.assertEqual(result["results"], [])
        self.assertEqual(result["total_candidates"], 0)
        self.assertEqual(result["eligible_papers"], 0)

    def test_duplicate_chunks_are_deduped_and_scope_is_passed_to_bridge(self) -> None:
        self.seed_shared()
        unsafe_id, _ = self.canonical(
            key="icml:unsafe",
            title="Unsafe URL work",
            source_id="unsafe-2020",
            year=2020,
        )
        self.source(
            key="icml:unsafe",
            source_id="unsafe-2020",
            year=2020,
            landing="javascript:alert(1)",
            pdf="data:application/pdf;base64,AAAA",
            article="//evil.example/relative",
        )
        export_catalog(self.paths, search_home(self.paths))
        self.write_ready_state()
        shared, shared_path = self.lookup_paper("icml:shared")
        unsafe, unsafe_path = self.lookup_paper("icml:unsafe")
        bridge = FakeBridge(
            [
                self.hit(shared_path),
                self.hit(shared_path, score=0.5),
                self.hit(unsafe_path, score=0.2),
            ]
        )

        result = search(
            self.paths,
            {
                "query": "graph forecasting",
                "limit": 10,
                "venues": ["icml"],
                "year_from": 2019,
                "year_to": 2019,
                "mode": "semantic",
            },
            bridge=bridge,
        )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["id"], shared["id"])
        self.assertNotIn(unsafe["id"], {item["id"] for item in result["results"]})
        self.assertEqual(len(bridge.requests), 1)
        request = bridge.requests[0]
        self.assertEqual(request["query"], "graph forecasting")
        self.assertEqual(request["model"], "local/test-model")
        self.assertEqual(request["routes"], [{"mode": "vector", "query": "graph forecasting"}])
        self.assertIn("papers/icml/2019", request["include_paths"])
        self.assertIn(shared_path, request["include_paths"])

    def test_zvec_bridge_restarts_mock_process_when_request_model_changes(self) -> None:
        class MockStdin:
            def __init__(self, process) -> None:
                self.process = process
                self.closed = False

            def write(self, data: bytes) -> int:
                request = json.loads(data.decode())
                response = {
                    "id": request["id"],
                    "ok": True,
                    "result": {"model": request.get("model")},
                }
                os.write(
                    self.process.writer_fd,
                    ("LITDB_JSON " + json.dumps(response) + "\n").encode(),
                )
                return len(data)

            def flush(self) -> None:
                return None

            def close(self) -> None:
                if not self.closed:
                    self.closed = True
                    os.close(self.process.writer_fd)

        class MockProcess:
            def __init__(self) -> None:
                self.reader_fd, self.writer_fd = os.pipe()
                self.stdout = os.fdopen(self.reader_fd, "rb", buffering=0)
                self.stdin = MockStdin(self)
                self.returncode = None
                self.terminated = False

            def poll(self):
                return self.returncode

            def terminate(self) -> None:
                self.terminated = True
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        bridge = ZvecBridge(self.root / "search", "local/old-model")
        processes: list[MockProcess] = []

        def start_mock_process() -> None:
            process = MockProcess()
            processes.append(process)
            bridge.process = process

        with patch.object(bridge, "_start", side_effect=start_mock_process):
            try:
                first = bridge.call({"op": "status", "model": "local/old-model"}, timeout=1)
                second = bridge.call({"op": "status", "model": "local/new-model"}, timeout=1)
            finally:
                bridge.close()

        self.assertEqual(first["model"], "local/old-model")
        self.assertEqual(second["model"], "local/new-model")
        self.assertEqual(bridge.model, "local/new-model")
        self.assertEqual(len(processes), 2)
        self.assertTrue(processes[0].terminated)

    def test_safe_url_and_search_text_do_not_expose_dangerous_links(self) -> None:
        self.assertEqual(safe_url("https://example.org/paper"), "https://example.org/paper")
        self.assertEqual(safe_url("http://example.org/paper"), "http://example.org/paper")
        for value in (
            "javascript:alert(1)",
            "data:text/html,not-safe",
            "//example.org/relative",
            "https://user:password@example.org/paper",
            "not a URL",
            123,
        ):
            with self.subTest(value=value):
                self.assertIsNone(safe_url(value))

        self.canonical(key="icml:unsafe", title="Unsafe URL work", source_id="unsafe-2020")
        self.source(
            key="icml:unsafe",
            source_id="unsafe-2020",
            landing="javascript:alert(1)",
            pdf="data:application/pdf;base64,AAAA",
            article="//evil.example/relative",
        )
        export_catalog(self.paths, search_home(self.paths))
        self.write_ready_state()
        unsafe, unsafe_path = self.lookup_paper("icml:unsafe")
        bridge = FakeBridge([self.hit(unsafe_path)])
        result = search(
            self.paths,
            {
                "query": "unsafe",
                "limit": 1,
                "venues": ["icml"],
                "year_from": None,
                "year_to": None,
                "mode": "keyword",
            },
            bridge=bridge,
        )
        self.assertEqual(len(result["results"]), 1)
        paper = result["results"][0]
        self.assertEqual(paper["id"], unsafe["id"])
        self.assertEqual(paper["title"], "Unsafe URL work")
        self.assertEqual(paper["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertIsNone(paper["landing_url"])
        self.assertIsNone(paper["pdf_url"])
        self.assertIsNone(paper["article_url"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb import cli, search  # noqa: E402


class DiscoveryCLITests(unittest.TestCase):
    def parse(self, *arguments):
        return cli.parser().parse_args(["search", "discover", *arguments])

    def run_discover(self, arguments, *, result=None, error=None):
        module = ModuleType("litdb.discovery")
        module.discover = Mock(return_value=result or {
            "query": "topic", "mode": "discovery", "results": [], "warnings": []
        }, side_effect=error)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as home:
            args = self.parse(*arguments, "--home", home)
            with patch.dict(sys.modules, {"litdb.discovery": module}), \
                    patch.object(cli, "check_search_runtime") as runtime, \
                    contextlib.redirect_stdout(output):
                code = cli.handle(args)
        return code, output.getvalue(), module.discover, runtime

    def test_default_and_environment_endpoint(self):
        with patch.dict(os.environ, {"LITDB_KEV_URL": ""}):
            args = self.parse("topic", "--keyword", "phrase")
        self.assertEqual(args.kev_url, "http://127.0.0.1:8019")
        self.assertEqual((args.retriever, args.candidate_limit, args.limit), ("combined", 50, 10))
        self.assertEqual((args.kev_timeout, args.unrelated_threshold, args.format), (180, 0.60, "markdown"))
        with patch.dict(os.environ, {"LITDB_KEV_URL": "http://localhost:8020"}):
            self.assertEqual(self.parse("topic").kev_url, "http://localhost:8020")
            self.assertEqual(self.parse("topic", "--kev-url", "http://127.0.0.1:8030").kev_url,
                             "http://127.0.0.1:8030")

    def test_all_options_map_to_discovery_and_json_is_preserved(self):
        result = {"query": "原主题", "mode": "discovery", "results": [],
                  "warnings": [], "timings": {"total_ms": 2}, "counts": {"candidates": 0}}
        code, output, discover, runtime = self.run_discover([
            "原主题", "--retrieval-query", "technical topic", "--retrieval-query", "topic acronym",
            "--keyword", "technical phrase", "--keyword", "ACRONYM",
            "--candidate-limit", "80", "--limit", "12", "--venue", "dac", "--venue", "iccad",
            "--year-from", "2020", "--year-to", "2026", "--retriever", "combined",
            "--kev-url", "http://localhost:8019", "--kev-timeout", "240",
            "--unrelated-threshold", "0.7", "--format", "json"
        ], result=result)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), result)
        runtime.assert_called_once_with(search)
        discover.assert_called_once()
        paths, options = discover.call_args.args
        self.assertTrue(paths.home.is_absolute())
        self.assertEqual(options, dict(query="原主题", retrieval_queries=["technical topic", "topic acronym"],
                                     keywords=["technical phrase", "ACRONYM"], candidate_limit=80, limit=12,
                                     venues=["dac", "iccad"], year_from=2020, year_to=2026,
                                     retriever="combined", kev_url="http://localhost:8019", kev_timeout=240.0,
                                     unrelated_threshold=0.7))

    def test_keyword_does_not_require_node_runtime(self):
        code, _, discover, runtime = self.run_discover([
            "topic", "--retriever", "keyword", "--keyword", "phrase", "--format", "json"
        ])
        self.assertEqual(code, 0)
        runtime.assert_not_called()
        discover.assert_called_once()

    def test_zvec_can_run_without_explicit_keywords(self):
        code, _, discover, runtime = self.run_discover(["topic", "--retriever", "zvec", "--format", "json"])
        self.assertEqual(code, 0)
        runtime.assert_called_once_with(search)
        self.assertEqual(discover.call_args.args[1]["keywords"], [])

    def test_invalid_inputs_fail_before_runtime_or_discovery(self):
        invalid_cases = [
            ["topic"],
            ["topic", "--retriever", "keyword"],
            ["topic", "--keyword", " "],
            [" ", "--keyword", "phrase"],
            ["topic", "--keyword", "phrase", "--retrieval-query", " "],
            ["topic", "--keyword", "phrase", "--limit", "0"],
            ["topic", "--keyword", "phrase", "--limit", "51"],
            ["topic", "--keyword", "phrase", "--candidate-limit", "0"],
            ["topic", "--keyword", "phrase", "--candidate-limit", "501"],
            ["topic", "--keyword", "phrase", "--candidate-limit", "2", "--limit", "3"],
            ["topic", "--keyword", "phrase", "--kev-timeout", "0"],
            ["topic", "--keyword", "phrase", "--kev-timeout", "3601"],
            ["topic", "--keyword", "phrase", "--kev-timeout", "nan"],
            ["topic", "--keyword", "phrase", "--unrelated-threshold", "0.49"],
            ["topic", "--keyword", "phrase", "--unrelated-threshold", "1.01"],
            ["topic", "--keyword", "phrase", "--unrelated-threshold", "nan"],
            ["topic", "--keyword", "phrase", "--year-from", "2026", "--year-to", "2020"],
        ]
        for arguments in invalid_cases:
            with self.subTest(arguments=arguments):
                code, output, discover, runtime = self.run_discover(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output)["status"], "ERROR")
                runtime.assert_not_called()
                discover.assert_not_called()

    def test_service_and_search_errors_are_reported(self):
        for error in (search.SearchError("failed retrieval"), ValueError("invalid reply"), OSError("connection refused")):
            with self.subTest(error=error):
                code, output, _, _ = self.run_discover([
                    "topic", "--retriever", "keyword", "--keyword", "phrase", "--format", "json"
                ], error=error)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output), {"status": "ERROR", "error": str(error)})

    def test_discovery_markdown_uses_shared_renderer(self):
        with patch.object(search, "markdown_results", return_value="candidate interpretation") as render:
            code, output, discover, _ = self.run_discover(["topic", "--keyword", "phrase"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "candidate interpretation")
        render.assert_called_once_with(discover.return_value)

    def test_existing_query_mapping_stays_unchanged(self):
        args = cli.parser().parse_args(["search", "query", "topic", "--mode", "keyword", "--format", "json"])
        output = io.StringIO()
        with patch.object(search, "status", return_value={"ready": False}), \
                patch.object(search, "search", return_value={"query": "topic", "results": []}) as query, \
                patch.object(cli, "check_search_runtime") as runtime, contextlib.redirect_stdout(output):
            self.assertEqual(cli.handle(args), 0)
        runtime.assert_not_called()
        self.assertEqual(query.call_args.args[1], dict(query="topic", limit=10, venues=[],
                                                     year_from=None, year_to=None, mode="keyword"))


if __name__ == "__main__":
    unittest.main()

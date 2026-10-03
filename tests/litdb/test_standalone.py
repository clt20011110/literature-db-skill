from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb import cli  # noqa: E402
from litdb.constants import DEFAULT_HOME  # noqa: E402
from litdb.db import initialize  # noqa: E402
from litdb.paths import LitDBPaths  # noqa: E402
from litdb.search import (  # noqa: E402
    SearchError,
    catalog_signature,
    document_id,
    export_catalog,
    search,
    search_home,
)


class FakeBridge:
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path

    def call(self, request: dict, timeout: float = 180) -> dict:
        return {
            "items": [
                {
                    "file": {"relativePath": self.relative_path},
                    "score": 0.9,
                    "matchedBy": "vector",
                }
            ]
        }


class StandalonePathsTests(unittest.TestCase):
    def test_default_is_inside_skill_and_has_no_aris_home_fallback(self) -> None:
        with patch.dict(os.environ, {"LITDB_HOME": ""}):
            paths = LitDBPaths.from_value(None)

        self.assertEqual(DEFAULT_HOME, ROOT / "data" / "literature-db")
        self.assertEqual(paths.home, DEFAULT_HOME.resolve())
        self.assertNotIn(".aris", str(paths.home))

    def test_environment_home_and_explicit_home_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env_home = root / "from-env"
            explicit_home = root / "from-argument"
            with patch.dict(os.environ, {"LITDB_HOME": str(env_home)}):
                self.assertEqual(LitDBPaths.from_value(None).home, env_home.resolve())
                self.assertEqual(LitDBPaths.from_value(explicit_home).home, explicit_home.resolve())

    def test_absolute_cli_entrypoint_uses_env_and_explicit_home_from_other_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cwd = root / "unrelated-working-directory"
            cwd.mkdir()
            env_home = root / "env-home"
            explicit_home = root / "explicit-home"
            self._write_status(env_home, ready=True)
            self._write_status(explicit_home, ready=False)

            env = os.environ.copy()
            env["LITDB_HOME"] = str(env_home)
            script = ROOT / "tools" / "litdb.py"

            from_env = subprocess.run(
                [sys.executable, str(script), "search", "status"],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            explicit_wins = subprocess.run(
                [sys.executable, str(script), "search", "status", "--home", str(explicit_home)],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(from_env.returncode, 0, from_env.stderr)
        self.assertTrue(json.loads(from_env.stdout)["ready"])
        self.assertEqual(explicit_wins.returncode, 0, explicit_wins.stderr)
        self.assertFalse(json.loads(explicit_wins.stdout)["ready"])

    def test_runtime_check_requires_node_22_with_actionable_error(self) -> None:
        from litdb import search as search_module

        with patch.object(cli.shutil, "which", return_value="/bin/node"), patch.object(
            cli.subprocess,
            "run",
            return_value=SimpleNamespace(stdout="v20.19.0"),
        ):
            with self.assertRaisesRegex(SearchError, "Node.js 22"):
                cli.check_search_runtime(search_module)

    def test_nature_collector_defaults_follow_litdb_home(self) -> None:
        from tools.nature_metadata_collector import (
            default_litdb_home,
            default_output_root,
            default_registry_root,
        )

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"LITDB_HOME": str(Path(temp) / "portable-home"), "LITDB_REGISTRY_ROOT": ""},
        ):
            home = Path(temp) / "portable-home"
            self.assertEqual(default_litdb_home(), home.resolve())
            self.assertEqual(default_output_root(), home.resolve() / "runs" / "nature-metadata")
            self.assertEqual(default_registry_root(), home.resolve() / "registry" / "venues")

    def test_nature_finalizer_accepts_default_home(self) -> None:
        from tools.nature_finalize import _parser

        args = _parser().parse_args(
            ["--venue", "nature", "--run-root", "/tmp/run", "--merge-receipt", "/tmp/merge.json"]
        )
        self.assertIsNone(args.home)
        with patch.dict(os.environ, {"LITDB_HOME": "/tmp/portable-home"}):
            self.assertEqual(LitDBPaths.from_value(args.home).home, Path("/tmp/portable-home").resolve())


    @staticmethod
    def _write_status(home: Path, *, ready: bool) -> None:
        search_dir = home / "search"
        search_dir.mkdir(parents=True)
        (search_dir / "state.json").write_text(
            json.dumps({"ready": ready, "phase": "ready" if ready else "missing"}),
            encoding="utf-8",
        )


class ReadOnlyCatalogTests(unittest.TestCase):
    def test_search_reads_catalog_without_changing_its_bytes_or_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = LitDBPaths(Path(temp) / "skill-data")
            initialize(paths.catalog)
            self._insert(
                paths.catalog,
                "canonical_work",
                {
                    "canonical_key": "icml:standalone",
                    "venue_id": "icml",
                    "title": "Graph Forecasting with Temporal Features",
                    "authors": ["Ada Lovelace"],
                    "abstract": "A local graph model forecasts traffic from temporal features.",
                    "publication_year": 2022,
                    "primary_source_item_id": "standalone-2022",
                    "doi": "10.1234/standalone",
                },
            )
            self._insert(
                paths.catalog,
                "source_item",
                {
                    "canonical_key": "icml:standalone",
                    "source_item_id": "standalone-2022",
                    "venue_id": "icml",
                    "year": 2022,
                    "landing_url": "https://papers.example.org/standalone",
                    "pdf_url": "https://papers.example.org/standalone.pdf",
                    "article_url": "https://papers.example.org/standalone.html",
                    "inclusion_decision": "include",
                },
            )

            exported = export_catalog(paths, search_home(paths))
            home = search_home(paths)
            (home / "state.json").write_text(
                json.dumps(
                    {
                        "ready": True,
                        "model": "local/test-model",
                        "updated_at": "2026-01-01T00:00:00Z",
                        "indexed_papers": exported["papers"],
                        "catalog_signature": catalog_signature(paths),
                    }
                ),
                encoding="utf-8",
            )

            before_bytes = paths.catalog.read_bytes()
            before_stat = paths.catalog.stat()
            before_artifacts = sorted(path.name for path in paths.home.glob("catalog.sqlite*"))
            relative_path = f"papers/icml/2022/{document_id('icml:standalone')}.txt"
            result = search(
                paths,
                {"query": "graph forecasting", "mode": "semantic"},
                bridge=FakeBridge(relative_path),
            )
            after_bytes = paths.catalog.read_bytes()
            after_stat = paths.catalog.stat()
            after_artifacts = sorted(path.name for path in paths.home.glob("catalog.sqlite*"))

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["canonical_key"], "icml:standalone")
        self.assertEqual(hashlib.sha256(after_bytes).digest(), hashlib.sha256(before_bytes).digest())
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(after_artifacts, before_artifacts)

    @staticmethod
    def _insert(catalog: Path, table: str, payload: dict) -> None:
        connection = sqlite3.connect(catalog)
        try:
            connection.execute(
                f"INSERT INTO {table}(payload_json, created_at) VALUES (?, ?)",
                (json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
            )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()

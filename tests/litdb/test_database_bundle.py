from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bundle_database as builder  # noqa: E402
import install_database as installer  # noqa: E402


def _write_source_home(home: Path) -> None:
    home.mkdir(parents=True)
    conn = sqlite3.connect(home / "catalog.sqlite")
    conn.executescript(
        """
        CREATE TABLE canonical_work (canonical_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
        CREATE TABLE source_item (id INTEGER PRIMARY KEY, payload_json TEXT NOT NULL);
        """
    )
    canonical = {
        "canonical_key": "doi:10.1234/example",
        "title": "COOKIE: Contrastive Cross-Modal Knowledge Sharing Pre-Training for Vision-Language Representation",
        "authors": ["A. Researcher"],
        "abstract": r"The domain is g:\\mathbb{R}^d and the source route is https://example.org/TMP/Users/.",
        "doi": "10.1234/example",
        "publication_year": 2024,
        "venue_id": "demo",
        "document_type": "article",
        "primary_source_item_id": "source:1",
        "session_code": "P-17",
        "scope_session": "Session II",
    }
    source = {
        "source_thread_id": "internal-thread-123",
        "request_id": "internal-request-456",
        "local_file": "/Users/private-user/paper.pdf",
    }
    conn.execute(
        "INSERT INTO canonical_work VALUES (?, ?)",
        (canonical["canonical_key"], json.dumps(canonical, ensure_ascii=False)),
    )
    conn.execute("INSERT INTO source_item VALUES (?, ?)", (1, json.dumps(source)))
    conn.commit()
    conn.close()

    (home / "registry").mkdir()
    (home / "registry" / "venues.yml").write_text(
        json.dumps({"venue": "demo", "thread_id": "registry-thread", "session_code": "P-17"}),
        encoding="utf-8",
    )
    (home / "recipes" / "demo" / "history" / "7ab598f1-1978-46f9-8a0f-5994e40c607a").mkdir(parents=True)
    (home / "recipes" / "demo" / "recipe.yml").write_text(
        "name: demo\nrequest_id: old-request\nsession_code: P-17\n", encoding="utf-8"
    )
    (home / "recipes" / "demo" / "history" / "7ab598f1-1978-46f9-8a0f-5994e40c607a" / "recipe_candidate.yml").write_text(
        "name: demo\n", encoding="utf-8"
    )
    (home / "manifests" / "expected").mkdir(parents=True)
    (home / "manifests" / "expected" / "demo.jsonl").write_text(
        json.dumps({"expected_request_id": "request-expected", "session_code": "P-17"}) + "\n",
        encoding="utf-8",
    )
    (home / "campaign_state.json").write_text(json.dumps({"thread_id": "campaign-thread", "phase": "idle"}), encoding="utf-8")
    (home / "runs").mkdir()
    (home / "runs" / "excluded.json").write_text('{"transcript":"private"}\n', encoding="utf-8")


class DatabaseBundleTests(unittest.TestCase):
    def test_cookie_acronym_title_is_preserved_but_cookie_headers_are_scrubbed(self) -> None:
        title = "COOKIE: Contrastive Cross-Modal Knowledge Sharing Pre-Training for Vision-Language Representation"
        sanitizer = builder.PrivacySanitizer()
        self.assertEqual(sanitizer.scrub_text(title, "canonical_work", "title"), title)
        self.assertEqual(sanitizer.secret_pattern_hits, 0)
        for header in ("Cookie: session=private-value", "Set-Cookie: session=private-value; HttpOnly", "HTTP/1.1 200 OK\r\nSet-Cookie: session=; Max-Age=0"):
            with self.subTest(header=header):
                safe = sanitizer.scrub_text(header, "source_item", "response_headers")
                self.assertTrue(safe.startswith("legacy-evidence://redacted-credential/"))
        explicit = sanitizer.scrub_value({"cookie": "private-value"}, "source_item", "payload_json")
        self.assertTrue(explicit["cookie"].startswith("legacy-evidence://redacted-credential/"))

    def test_sanitizer_preserves_scholarly_routes_latex_and_session_codes(self) -> None:
        sanitizer = builder.PrivacySanitizer()
        academic = r"See g:\\mathbb{R}^d and https://example.org/TMP/Users/."
        self.assertEqual(sanitizer.scrub_text(academic, "canonical_work", "abstract"), academic)
        safe = sanitizer.scrub_value(
            {"session_code": "S-17", "scope_session": "Late session", "thread_id": "thread-abc"},
            "venue",
            "payload_json",
        )
        self.assertEqual(safe["session_code"], "S-17")
        self.assertEqual(safe["scope_session"], "Late session")
        self.assertTrue(safe["thread_id"].startswith("legacy-evidence://internal-thread/"))

    def test_sanitizer_scrubs_explicit_local_path_and_request_id(self) -> None:
        sanitizer = builder.PrivacySanitizer()
        safe = sanitizer.scrub_value(
            {"request_id": "request-abc", "local_file": "/Users/private-user/paper.pdf"},
            "source_item",
            "payload_json",
        )
        serialized = json.dumps(safe)
        self.assertNotIn("request-abc", serialized)
        self.assertNotIn("/Users/private-user", serialized)
        self.assertIn("legacy-evidence://", serialized)

    def test_release_manifest_rejects_inconsistent_part_sizes(self) -> None:
        with self.assertRaises(builder.BundleError):
            installer._validate_release_manifest(
                {
                    "format_version": 1,
                    "archive": {
                        "name": "bundle.tar.gz",
                        "size_bytes": 5,
                        "sha256": "a" * 64,
                        "parts": [{"name": "bundle.tar.gz.part001", "size_bytes": 4, "sha256": "b" * 64}],
                    },
                }
            )

    def test_archive_member_path_rejects_traversal(self) -> None:
        with self.assertRaises(builder.BundleError):
            installer._safe_member_name("../escape.txt")

    def test_release_parts_concatenate_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            first, second, combined = root / "part1", root / "part2", root / "joined.tar.gz"
            first.write_bytes(b"first-")
            second.write_bytes(b"second")
            parts = [
                {"name": first.name, "size_bytes": first.stat().st_size, "sha256": builder.sha256_file(first)},
                {"name": second.name, "size_bytes": second.stat().st_size, "sha256": builder.sha256_file(second)},
            ]
            installer._write_parts_to_archive(parts, combined, root, None)
            self.assertEqual(combined.read_bytes(), b"first-second")
            self.assertEqual(builder.sha256_file(combined), hashlib.sha256(b"first-second").hexdigest())

    def test_https_context_uses_standard_ca_when_openssl_default_is_missing(self) -> None:
        if not any(path.is_file() for path in installer.SYSTEM_CA_BUNDLE_PATHS):
            self.skipTest("no standard system CA bundle is installed on this host")
        with tempfile.TemporaryDirectory() as temp_name:
            empty_capath = Path(temp_name) / "empty-openssl-certs"
            empty_capath.mkdir()
            missing_defaults = SimpleNamespace(cafile="/missing/openssl/cert.pem", capath=str(empty_capath))
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                installer.ssl, "get_default_verify_paths", return_value=missing_defaults
            ):
                context = installer._verified_https_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertTrue(context.get_ca_certs())

    def test_https_context_preserves_explicit_ssl_certificate_environment(self) -> None:
        for variable in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            with self.subTest(variable=variable):
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                with mock.patch.dict(os.environ, {variable: "/user-configured/custom-roots"}, clear=True):
                    with mock.patch.object(installer.ssl, "create_default_context", return_value=context) as create_context:
                        with mock.patch.object(installer.ssl, "get_default_verify_paths") as get_defaults:
                            result = installer._verified_https_context()
                self.assertIs(result, context)
                create_context.assert_called_once_with()
                get_defaults.assert_not_called()

    def test_full_bundle_preserves_core_and_restores_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source_home = root / "source-home"
            output_home = root / "package" / "literature-db"
            dist = root / "dist"
            _write_source_home(source_home)
            source_before = builder.sha256_file(source_home / "catalog.sqlite")

            release = builder.build_bundle(
                source_home,
                output_home,
                dist,
                "test-v1",
                minimum_canonical_works=1,
                max_part_bytes=1024,
            )
            bundle_manifest = json.loads((output_home / "bundle-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(source_before, builder.sha256_file(source_home / "catalog.sqlite"))
            self.assertEqual(bundle_manifest["entities"]["core_fields"]["source_sha256"], bundle_manifest["entities"]["core_fields"]["bundled_sha256"])
            self.assertEqual(bundle_manifest["entities"]["tables"]["canonical_work"], 1)
            self.assertTrue((output_home / "registry" / "venues.yml").is_file())
            self.assertTrue((output_home / "recipes" / "demo" / "recipe.yml").is_file())
            self.assertTrue((output_home / "campaign_state.json").is_file())
            self.assertTrue((output_home / "manifests" / "expected" / "demo.jsonl").is_file())
            self.assertFalse((output_home / "runs").exists())
            registry = json.loads((output_home / "registry" / "venues.yml").read_text(encoding="utf-8"))
            self.assertTrue(registry["thread_id"].startswith("legacy-evidence://"))
            self.assertEqual(registry["session_code"], "P-17")

            restored = root / "target-with-#-and-?-chars"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = installer.main(["--release-manifest", str(dist / "database-release-manifest.json"), "--target", str(restored)])
            self.assertEqual(result, 0, stderr.getvalue())
            self.assertTrue((restored / "catalog.sqlite").is_file())
            self.assertEqual(builder.sha256_file(restored / "catalog.sqlite"), bundle_manifest["database"]["sha256"])

            occupied = root / "occupied-target"
            occupied.mkdir()
            marker = occupied / "keep.txt"
            marker.write_text("do not remove", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                result = installer.main(["--release-manifest", str(dist / "database-release-manifest.json"), "--target", str(occupied)])
            self.assertEqual(result, 2)
            self.assertEqual(marker.read_text(encoding="utf-8"), "do not remove")


if __name__ == "__main__":
    unittest.main()

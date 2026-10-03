#!/usr/bin/env python3
"""Safely install and verify a literature database release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import ssl
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from bundle_database import BundleError, _json_bytes, _pk_order, _quote_identifier, _row_digest_update, sha256_file


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_BUNDLE_MANIFEST_BYTES = 64 * 1024 * 1024
CA_HASH_ENTRY_RE = re.compile(r"^[0-9a-fA-F]{8}\.[0-9]+$")
SYSTEM_CA_BUNDLE_PATHS = (
    Path("/etc/ssl/cert.pem"),  # macOS and BSD
    Path("/etc/ssl/certs/ca-certificates.crt"),  # Debian and Ubuntu
    Path("/etc/pki/tls/certs/ca-bundle.crt"),  # RHEL and Fedora
    Path("/etc/ssl/certs/ca-bundle.crt"),  # openSUSE
    Path("/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"),  # RHEL-family
    Path("/usr/local/share/certs/ca-root-nss.crt"),  # FreeBSD
)


def _has_hashed_ca_entries(directory: Path) -> bool:
    """Check that an OpenSSL CApath contains at least one usable hashed entry."""
    try:
        with os.scandir(directory) as entries:
            return any(
                CA_HASH_ENTRY_RE.fullmatch(entry.name) and entry.is_file()
                for entry in entries
            )
    except OSError:
        return False


def _safe_asset_name(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BundleError("release manifest contains an unsafe asset name")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {".", ".."}:
        raise BundleError("release manifest asset names must be plain file names")
    return value


def _load_release_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"cannot read release manifest: {path}") from exc
    _validate_release_manifest(manifest)
    return manifest


def _validate_release_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        raise BundleError("unsupported or malformed database release manifest")
    archive = manifest.get("archive")
    if not isinstance(archive, dict):
        raise BundleError("release manifest is missing archive metadata")
    _safe_asset_name(archive.get("name"))
    if not isinstance(archive.get("size_bytes"), int) or archive["size_bytes"] < 1:
        raise BundleError("release manifest archive size is invalid")
    if not isinstance(archive.get("sha256"), str) or not SHA256_RE.fullmatch(archive["sha256"]):
        raise BundleError("release manifest archive SHA-256 is invalid")
    parts = archive.get("parts")
    if not isinstance(parts, list) or not parts:
        raise BundleError("release manifest has no archive parts")
    total = 0
    for part in parts:
        if not isinstance(part, dict):
            raise BundleError("release manifest contains malformed part metadata")
        _safe_asset_name(part.get("name"))
        if not isinstance(part.get("size_bytes"), int) or part["size_bytes"] < 1:
            raise BundleError("release part size is invalid")
        if not isinstance(part.get("sha256"), str) or not SHA256_RE.fullmatch(part["sha256"]):
            raise BundleError("release part SHA-256 is invalid")
        total += part["size_bytes"]
    if total != archive["size_bytes"]:
        raise BundleError("release part sizes do not add up to the archive size")


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "literature-db-skill-installer/1"})
    with urllib.request.urlopen(request, timeout=60, context=_verified_https_context()) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def _verified_https_context() -> ssl.SSLContext:
    """Build a verified TLS context, using a standard CA file if OpenSSL's default is missing.

    Explicit SSL_CERT_FILE/SSL_CERT_DIR settings are left entirely to Python/OpenSSL.
    The fallback only selects a CA bundle installed by the operating system; it never
    downloads certificates or disables certificate/hostname verification.
    """
    if "SSL_CERT_FILE" in os.environ or "SSL_CERT_DIR" in os.environ:
        return ssl.create_default_context()

    defaults = ssl.get_default_verify_paths()
    default_file = getattr(defaults, "cafile", None)
    if default_file and Path(default_file).is_file():
        return ssl.create_default_context()
    default_dir = getattr(defaults, "capath", None)
    if default_dir and _has_hashed_ca_entries(Path(default_dir)):
        return ssl.create_default_context()

    for candidate in SYSTEM_CA_BUNDLE_PATHS:
        if candidate.is_file():
            return ssl.create_default_context(cafile=str(candidate))

    raise BundleError(
        "no trusted system CA bundle was found and OpenSSL's configured CA path is missing; "
        "install your operating system's CA certificates or set SSL_CERT_FILE/SSL_CERT_DIR. "
        "TLS certificate verification remains enabled."
    )


def _write_parts_to_archive(
    parts: list[dict[str, Any]],
    destination: Path,
    local_directory: Path | None,
    base_url: str | None,
) -> None:
    with destination.open("wb") as combined:
        for part in parts:
            name = _safe_asset_name(part["name"])
            if local_directory is not None:
                source = local_directory / name
                try:
                    resolved = source.resolve(strict=True)
                except OSError as exc:
                    raise BundleError(f"release asset part not found: {source}") from exc
                if resolved.parent != local_directory.resolve() or source.is_symlink() or not resolved.is_file():
                    raise BundleError(f"release asset part must be a regular file beside its manifest: {name}")
                actual_size = resolved.stat().st_size
                if actual_size != part["size_bytes"] or sha256_file(resolved) != part["sha256"]:
                    raise BundleError(f"release asset part failed size or SHA-256 verification: {name}")
                with resolved.open("rb") as stream:
                    shutil.copyfileobj(stream, combined, length=1024 * 1024)
            else:
                assert base_url is not None
                with tempfile.NamedTemporaryFile(prefix="literature-db-part-", delete=False) as tmp_file:
                    temporary = Path(tmp_file.name)
                try:
                    _download(base_url.rstrip("/") + "/" + urllib.parse.quote(name), temporary)
                    if temporary.stat().st_size != part["size_bytes"] or sha256_file(temporary) != part["sha256"]:
                        raise BundleError(f"downloaded release part failed size or SHA-256 verification: {name}")
                    with temporary.open("rb") as stream:
                        shutil.copyfileobj(stream, combined, length=1024 * 1024)
                finally:
                    temporary.unlink(missing_ok=True)


def _safe_member_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise BundleError("archive contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BundleError("archive contains an absolute or traversing path")
    normalized = path.as_posix()
    if normalized != name:
        raise BundleError("archive contains a non-canonical path")
    return normalized


def _read_bundle_manifest(tar: tarfile.TarFile) -> dict[str, Any]:
    matches = [member for member in tar.getmembers() if member.name == "bundle-manifest.json"]
    if len(matches) != 1 or not matches[0].isreg() or matches[0].size > MAX_BUNDLE_MANIFEST_BYTES:
        raise BundleError("archive must contain one bounded regular bundle-manifest.json")
    stream = tar.extractfile(matches[0])
    if stream is None:
        raise BundleError("cannot read bundle manifest from archive")
    try:
        value = json.loads(stream.read(MAX_BUNDLE_MANIFEST_BYTES + 1).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("archive bundle manifest is invalid JSON") from exc
    if not isinstance(value, dict) or value.get("format_version") != 1:
        raise BundleError("unsupported bundle manifest format")
    return value


def _validate_inventory(manifest: dict[str, Any], members: list[tarfile.TarInfo]) -> dict[str, dict[str, Any]]:
    inventory = manifest.get("package_inventory")
    if not isinstance(inventory, list):
        raise BundleError("bundle manifest has no package inventory")
    expected: dict[str, dict[str, Any]] = {}
    for item in inventory:
        if not isinstance(item, dict):
            raise BundleError("bundle inventory entry is malformed")
        name = _safe_member_name(item.get("path", ""))
        if name == "bundle-manifest.json" or name in expected:
            raise BundleError("bundle inventory contains a duplicate or reserved path")
        if not isinstance(item.get("size_bytes"), int) or item["size_bytes"] < 0:
            raise BundleError(f"bundle inventory size is invalid: {name}")
        if not isinstance(item.get("sha256"), str) or not SHA256_RE.fullmatch(item["sha256"]):
            raise BundleError(f"bundle inventory digest is invalid: {name}")
        expected[name] = item
    if "catalog.sqlite" not in expected:
        raise BundleError("bundle inventory is missing catalog.sqlite")

    actual: dict[str, tarfile.TarInfo] = {}
    for member in members:
        name = _safe_member_name(member.name)
        if name in actual or not member.isreg():
            raise BundleError(f"archive contains a duplicate or non-regular entry: {name}")
        actual[name] = member
    if set(actual) != set(expected) | {"bundle-manifest.json"}:
        missing = sorted((set(expected) | {"bundle-manifest.json"}) - set(actual))
        extra = sorted(set(actual) - set(expected) - {"bundle-manifest.json"})
        raise BundleError(f"archive entries do not match bundle inventory (missing={missing[:5]}, extra={extra[:5]})")
    for name, item in expected.items():
        if actual[name].size != item["size_bytes"]:
            raise BundleError(f"archive member size does not match manifest: {name}")
    return expected


def _extract_verified(tar: tarfile.TarFile, stage: Path, manifest: dict[str, Any]) -> None:
    members = tar.getmembers()
    expected = _validate_inventory(manifest, members)
    by_name = {_safe_member_name(member.name): member for member in members}
    for name, member in by_name.items():
        destination = stage.joinpath(*PurePosixPath(name).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = tar.extractfile(member)
        if source is None:
            raise BundleError(f"cannot read archive member: {name}")
        digest = hashlib.sha256()
        size = 0
        with source, destination.open("wb") as output:
            while block := source.read(1024 * 1024):
                size += len(block)
                if name != "bundle-manifest.json" and size > expected[name]["size_bytes"]:
                    raise BundleError(f"archive member exceeds declared size: {name}")
                digest.update(block)
                output.write(block)
        if name == "bundle-manifest.json":
            continue
        item = expected[name]
        if size != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
            raise BundleError(f"extracted file failed size or SHA-256 verification: {name}")


def _verify_database_contents(db_path: Path, manifest: dict[str, Any]) -> None:
    db_meta = manifest.get("database")
    if not isinstance(db_meta, dict) or db_meta.get("filename") != "catalog.sqlite":
        raise BundleError("bundle database metadata is malformed")
    if db_path.stat().st_size != db_meta.get("size_bytes") or sha256_file(db_path) != db_meta.get("sha256"):
        raise BundleError("catalog.sqlite does not match bundle manifest size or SHA-256")

    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=60)
    try:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise BundleError(f"catalog.sqlite integrity_check failed: {integrity[:5]}")
        table_counts: dict[str, int] = {}
        entity_digest = hashlib.sha256()
        core_digest = hashlib.sha256()
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            columns, order = _pk_order(conn, table)
            selection = ",".join(_quote_identifier(c) for c in columns)
            ordering = ",".join(_quote_identifier(c) for c in order)
            count = 0
            for row in conn.execute(f"SELECT {selection} FROM {_quote_identifier(table)} ORDER BY {ordering}"):
                count += 1
                _row_digest_update(entity_digest, table, columns, tuple(row))
                if table == "canonical_work" and "payload_json" in columns:
                    payload = json.loads(str(row[columns.index("payload_json")]))
                    core_fields = manifest.get("entities", {}).get("core_fields", {}).get("fields", [])
                    core = {key: payload.get(key) for key in core_fields}
                    core_digest.update(_json_bytes([row[columns.index(order[0])], core]) + b"\n")
            table_counts[table] = count
    finally:
        conn.close()
    expected_entities = manifest.get("entities", {})
    if table_counts != expected_entities.get("tables"):
        raise BundleError("database table entity counts do not match the bundle manifest")
    if entity_digest.hexdigest() != expected_entities.get("bundled_entity_digest_sha256"):
        raise BundleError("database entity digest does not match the bundle manifest")
    core = expected_entities.get("core_fields", {})
    if core_digest.hexdigest() != core.get("bundled_sha256"):
        raise BundleError("database core-field digest does not match the bundle manifest")


def _install(archive_path: Path, target: Path, force: bool = False, archive_sha256: str | None = None) -> None:
    archive_path = archive_path.expanduser().resolve(strict=True)
    if not archive_path.is_file():
        raise BundleError(f"archive is not a regular file: {archive_path}")
    if archive_sha256 and sha256_file(archive_path) != archive_sha256:
        raise BundleError("archive failed SHA-256 verification")
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="literature-db-install-", dir=target.parent) as temp_name:
        temp_root = Path(temp_name)
        stage = temp_root / "bundle"
        stage.mkdir()
        try:
            with tarfile.open(archive_path, mode="r:gz") as tar:
                manifest = _read_bundle_manifest(tar)
                _extract_verified(tar, stage, manifest)
        except (tarfile.TarError, OSError) as exc:
            raise BundleError(f"cannot safely read release archive: {exc}") from exc
        _verify_database_contents(stage / "catalog.sqlite", manifest)

        if target.exists():
            if not target.is_dir():
                raise BundleError(f"install target exists and is not a directory: {target}")
            current_manifest = target / "bundle-manifest.json"
            current_db = target / "catalog.sqlite"
            if current_manifest.is_file() and current_db.is_file():
                try:
                    current = json.loads(current_manifest.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    current = {}
                if current.get("database", {}).get("sha256") == manifest.get("database", {}).get("sha256") and sha256_file(current_db) == manifest.get("database", {}).get("sha256"):
                    print(f"already installed and verified: {target}")
                    return
            if any(target.iterdir()) and not force:
                raise BundleError(f"install target is not empty: {target} (use --force to replace it)")
            if any(target.iterdir()):
                shutil.rmtree(target)
            else:
                target.rmdir()
        os.replace(stage, target)
    print(f"installed and verified {manifest['entities']['tables'].get('canonical_work', 0):,} canonical works at {target}")


def _local_release_manifest(path: Path) -> tuple[dict[str, Any], Path]:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise BundleError(f"release manifest is not a regular file: {resolved}")
    return _load_release_manifest(resolved), resolved.parent


def _remote_release_manifest(base_url: str, temp_dir: Path) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/database-release-manifest.json"
    path = temp_dir / "database-release-manifest.json"
    _download(url, path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleError("remote database release manifest is invalid JSON") from exc
    _validate_release_manifest(value)
    return value


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--archive", type=Path, help="local complete .tar.gz asset")
    sources.add_argument("--release-manifest", type=Path, help="local database-release-manifest.json, including split archives")
    sources.add_argument("--base-url", help="public release download base URL containing database-release-manifest.json")
    parser.add_argument("--target", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "literature-db")
    parser.add_argument("--force", action="store_true", help="replace a non-empty target after the new bundle has passed verification")
    args = parser.parse_args(argv)
    repo_dist = Path(__file__).resolve().parents[1] / "dist"

    try:
        if args.archive:
            archive = args.archive.expanduser().resolve(strict=True)
            expected_sha = None
            sibling_manifest = archive.parent / "database-release-manifest.json"
            if sibling_manifest.is_file():
                release = _load_release_manifest(sibling_manifest)
                archive_meta = release["archive"]
                if len(archive_meta["parts"]) == 1 and archive_meta["parts"][0]["name"] == archive.name:
                    expected_sha = archive_meta["sha256"]
            if expected_sha is None:
                checksum = archive.with_name(archive.name + ".sha256")
                if checksum.is_file():
                    first = checksum.read_text(encoding="utf-8").split()
                    if first and SHA256_RE.fullmatch(first[0]):
                        expected_sha = first[0]
            if expected_sha is None:
                raise BundleError("local archive needs its release manifest or adjacent .sha256 file")
            _install(archive, args.target, args.force, expected_sha)
            return 0

        with tempfile.TemporaryDirectory(prefix="literature-db-download-") as temp_name:
            temp_dir = Path(temp_name)
            if args.base_url:
                release = _remote_release_manifest(args.base_url, temp_dir)
                parts_dir = None
                base_url = args.base_url.rstrip("/")
            else:
                manifest_path = args.release_manifest or (repo_dist / "database-release-manifest.json")
                release, parts_dir = _local_release_manifest(manifest_path)
                base_url = None
            archive_meta = release["archive"]
            combined = temp_dir / _safe_asset_name(archive_meta["name"])
            _write_parts_to_archive(archive_meta["parts"], combined, parts_dir, base_url)
            if combined.stat().st_size != archive_meta["size_bytes"] or sha256_file(combined) != archive_meta["sha256"]:
                raise BundleError("concatenated release archive failed size or SHA-256 verification")
            _install(combined, args.target, args.force, archive_meta["sha256"])
    except (BundleError, OSError, sqlite3.Error, tarfile.TarError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"install_database: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

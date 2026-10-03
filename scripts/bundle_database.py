#!/usr/bin/env python3
"""Create a portable, privacy-sanitized literature database release bundle.

The source is read with SQLite's online backup API. All output is written under
the repository's data/ and dist/ directories; the source catalog is never
modified. Only explicitly allowlisted maintenance files are copied alongside
the database. Raw/browser evidence, run directories, reports, and chat receipts
are deliberately excluded.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


FORMAT_VERSION = 1
CORE_FIELDS = (
    "canonical_key",
    "title",
    "authors",
    "abstract",
    "doi",
    "publication_year",
    "venue_id",
    "document_type",
    "primary_source_item_id",
)
MIN_CANONICAL_WORKS = 100_000
DEFAULT_PART_BYTES = 1024 * 1024 * 1024
LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:/])(?P<path>(?:file://)?(?:"
    r"/(?:Users/[^/\s]+|home/[^/\s]+|private/(?:var/folders/[^/\s]+|var/tmp|tmp)|"
    r"tmp|var/folders/[^/\s]+|Volumes/[^/\s]+|mnt/[^/\s]+|workspace|workspaces|root)"
    r"(?:/[^\s\"'<>|,;]*)*|"
    r"[A-Za-z]:\\(?:Users\\[^\\/\s]+|Documents and Settings\\[^\\/\s]+|"
    r"Program Files(?: \(x86\))?\\|Windows\\|Temp\\)[^\s\"'<>|,;]*))",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(?im)(?:^|[\r\n])\s*(?:authorization|proxy-authorization)\s*:\s*(?:basic|bearer)\s+\S+|"
    r"(?:^|[\r\n])\s*(?:cookie|set-cookie)\s*:\s*\S+|"
    r"(?:client_secret|access_token|refresh_token|api_key|api_token)\s*[:=]\s*\S+"
)
UUID_RE = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
THREAD_KEYS = {
    "thread_id",
    "actual_thread_id",
    "parent_thread_id",
    "controller_thread_id",
    "source_thread_id",
    "worker_thread_id",
}
REQUEST_KEYS = {"request_id", "expected_request_id", "count_request_id", "discovery_request_id", "pilot_request_id", "replay_request_id"}
IDENTIFIER_KEYS = {"account_id", "user_id", "owner_id", "actor_id", "tenant_id"}
SESSION_ID_KEYS = {"session_id", "session_uuid", "browser_session_id"}
PRIVATE_CONTENT_KEYS = {"prompt", "system_prompt", "conversation", "chat_history", "chat_messages", "transcript"}
SECRET_KEYS = {
    "cookie",
    "cookies",
    "set_cookie",
    "password",
    "passwd",
    "client_secret",
    "access_token",
    "refresh_token",
    "api_token",
    "api_key",
    "api_key_value",
    "authorization",
    "proxy_authorization",
    "authorization_header",
    "credential_value",
    "session_token",
    "access_token",
}
HISTORY_CONFIG_RE = re.compile(r"^recipe(?:_candidate)?(?:\.[A-Za-z0-9_.-]+)?\.ya?ml$", re.I)


class BundleError(RuntimeError):
    pass


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _contains_bytes(path: Path, needle: bytes) -> bool:
    if not needle:
        return False
    overlap = b""
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            data = overlap + block
            if needle in data:
                return True
            overlap = data[-max(0, len(needle) - 1) :]
    return False


def safe_db_uri(path: Path) -> str:
    # Path.as_uri quotes spaces and non-ASCII characters while preserving URI semantics.
    return path.resolve().as_uri() + "?mode=ro"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _pk_order(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[str]]:
    info = list(conn.execute(f"PRAGMA table_info({_quote_identifier(table)})"))
    columns = [str(row[1]) for row in info]
    primary = sorted((int(row[5]), str(row[1])) for row in info if int(row[5]) > 0)
    order = [name for _, name in primary] or columns[:1]
    if not columns or not order:
        raise BundleError(f"cannot determine columns or stable row order for table {table!r}")
    return columns, order


def _row_digest_update(digest: Any, table: str, columns: list[str], row: tuple[Any, ...]) -> None:
    digest.update(_json_bytes([table, list(zip(columns, row))]))
    digest.update(b"\n")


class PrivacySanitizer:
    """Replace local and internal identifiers with stable in-bundle references."""

    def __init__(self) -> None:
        self.refs: dict[str, dict[str, str]] = collections.defaultdict(dict)
        self.transforms: collections.Counter[tuple[str, str, str]] = collections.Counter()
        self.secret_pattern_hits = 0

    def reference(self, category: str, raw: str) -> str:
        refs = self.refs[category]
        if raw not in refs:
            refs[raw] = f"legacy-evidence://{category}/{len(refs) + 1:06d}"
        return refs[raw]

    def _ref_field(self, category: str, raw: str, table: str, field: str) -> str:
        self.transforms[(category, table, field)] += 1
        return self.reference(category, raw)

    def scrub_text(self, value: str, table: str, field: str) -> str:
        def replace_path(match: re.Match[str]) -> str:
            token = match.group("path")
            suffix = ""
            while token and token[-1] in ".:!?)]}":
                suffix = token[-1] + suffix
                token = token[:-1]
            if not token:
                return match.group(0)
            return self._ref_field("omitted-local-artifact", token, table, field) + suffix

        sanitized = LOCAL_PATH_RE.sub(replace_path, value)
        if sanitized != value:
            value = sanitized
        match = SECRET_VALUE_RE.search(value)
        if match:
            self.secret_pattern_hits += 1
            value = self._ref_field("redacted-credential", value, table, field)
        return value

    @staticmethod
    def _normalize_key(key: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")

    def scrub_value(self, value: Any, table: str, field: str, key: str = "") -> Any:
        normalized = self._normalize_key(key)
        category = None
        if normalized in THREAD_KEYS or normalized.endswith("_thread_id"):
            category = "internal-thread"
        elif normalized in REQUEST_KEYS or normalized.endswith("_request_id"):
            category = "internal-request"
        elif normalized in IDENTIFIER_KEYS or normalized.endswith("_account_id") or normalized.endswith("_user_id"):
            category = "private-identity"
        elif normalized in SESSION_ID_KEYS:
            category = "internal-session"
        elif normalized in PRIVATE_CONTENT_KEYS:
            category = "omitted-private-content"
        elif normalized in SECRET_KEYS:
            category = "redacted-credential"
        if category and value is not None and not isinstance(value, bool):
            if isinstance(value, str):
                raw_identity = value
            else:
                raw_identity = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return self._ref_field(category, raw_identity, table, field)
        if isinstance(value, dict):
            return {
                k: self.scrub_value(v, table, f"{field}.{k}" if field else str(k), str(k))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.scrub_value(v, table, f"{field}[]", key) for v in value]
        if isinstance(value, str):
            return self.scrub_text(value, table, field)
        return value

    def sanitize_json_text(self, raw: str, table: str, field: str = "payload_json") -> tuple[str, bool]:
        value = json.loads(raw)
        safe = self.scrub_value(value, table, field)
        if safe == value:
            return raw, False
        return json.dumps(safe, ensure_ascii=False, separators=(",", ":")), True

    def sanitize_yaml_text(self, raw: str, table: str, field: str) -> tuple[str, bool]:
        # Preserve comments and formatting while scrubbing scalar values under sensitive keys.
        output: list[str] = []
        changed = False
        key_line = re.compile(r"^(\s*(?:-\s*)?)([A-Za-z0-9_.-]+)(\s*:\s*)(.*)$")
        for line in raw.splitlines(keepends=True):
            ending = "\n" if line.endswith("\n") else ""
            body = line[:-1] if ending else line
            match = key_line.match(body)
            if match:
                prefix, key, separator, scalar = match.groups()
                normalized = self._normalize_key(key)
                value = scalar.strip()
                quote = ""
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    quote, value = value[0], value[1:-1]
                category = None
                if normalized in THREAD_KEYS or normalized.endswith("_thread_id"):
                    category = "internal-thread"
                elif normalized in REQUEST_KEYS or normalized.endswith("_request_id"):
                    category = "internal-request"
                elif normalized in IDENTIFIER_KEYS or normalized.endswith(("_account_id", "_user_id")):
                    category = "private-identity"
                elif normalized in SESSION_ID_KEYS:
                    category = "internal-session"
                elif normalized in PRIVATE_CONTENT_KEYS:
                    category = "omitted-private-content"
                elif normalized in SECRET_KEYS:
                    category = "redacted-credential"
                if category and value and value not in ("null", "~", "true", "false"):
                    replacement = self._ref_field(category, value, table, f"{field}.{key}")
                    body = prefix + key + separator + quote + replacement + quote
                    changed = True
            safe_line = self.scrub_text(body, table, field)
            if safe_line != body:
                changed = True
            output.append(safe_line + ending)
        return "".join(output), changed

    def summary(self) -> dict[str, Any]:
        by_field: dict[str, dict[str, int]] = collections.defaultdict(dict)
        by_category: collections.Counter[str] = collections.Counter()
        for (category, table, field), count in sorted(self.transforms.items()):
            by_field[f"{table}.{field}"][category] = count
            by_category[category] += count
        return {
            "by_category": dict(sorted(by_category.items())),
            "by_field": {k: dict(sorted(v.items())) for k, v in sorted(by_field.items())},
            "unique_references": {k: len(v) for k, v in sorted(self.refs.items())},
            "credential_pattern_hits": self.secret_pattern_hits,
            "preserved_academic_session_fields": ["session_code", "scope_session"],
        }


def _database_inventory(conn: sqlite3.Connection, sanitizer: PrivacySanitizer, patch_dir: Path) -> tuple[dict[str, Any], dict[str, int]]:
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    counts: dict[str, int] = {}
    source_digest = hashlib.sha256()
    bundled_digest = hashlib.sha256()
    core_source = hashlib.sha256()
    core_bundled = hashlib.sha256()
    changed_rows: collections.Counter[str] = collections.Counter()

    for table in tables:
        columns, order = _pk_order(conn, table)
        quoted_table = _quote_identifier(table)
        selection = ",".join(_quote_identifier(c) for c in columns)
        ordering = ",".join(_quote_identifier(c) for c in order)
        patch_path = patch_dir / f"{hashlib.sha256(table.encode()).hexdigest()}.jsonl"
        count = 0
        with patch_path.open("w", encoding="utf-8") as patch_stream:
            cursor = conn.execute(f"SELECT {selection} FROM {quoted_table} ORDER BY {ordering}")
            for row_tuple in cursor:
                row = list(row_tuple)
                count += 1
                _row_digest_update(source_digest, table, columns, tuple(row))
                new_payload = None
                if "payload_json" in columns:
                    index = columns.index("payload_json")
                    new_payload, changed = sanitizer.sanitize_json_text(str(row[index]), table)
                    if changed:
                        row[index] = new_payload
                        key_index = columns.index(order[0])
                        patch_stream.write(json.dumps([row[key_index], new_payload], ensure_ascii=False, separators=(",", ":")) + "\n")
                        changed_rows[table] += 1
                _row_digest_update(bundled_digest, table, columns, tuple(row))

                if table == "canonical_work" and "payload_json" in columns:
                    original_payload = json.loads(str(row_tuple[columns.index("payload_json")]))
                    bundled_payload = json.loads(str(row[columns.index("payload_json")]))
                    original_core = {key: original_payload.get(key) for key in CORE_FIELDS}
                    safe_core = {key: bundled_payload.get(key) for key in CORE_FIELDS}
                    core_source.update(_json_bytes([row_tuple[columns.index(order[0])], original_core]) + b"\n")
                    core_bundled.update(_json_bytes([row_tuple[columns.index(order[0])], safe_core]) + b"\n")
        if count:
            # Apply only after the SELECT cursor is closed to keep row traversal stable.
            if changed_rows[table]:
                key_column = order[0]
                with patch_path.open("r", encoding="utf-8") as patch_stream:
                    batch: list[tuple[str, Any]] = []
                    for line in patch_stream:
                        primary_key, payload_json = json.loads(line)
                        batch.append((payload_json, primary_key))
                        if len(batch) >= 2000:
                            conn.executemany(
                                f"UPDATE {quoted_table} SET payload_json=? WHERE {_quote_identifier(key_column)}=?",
                                batch,
                            )
                            batch.clear()
                    if batch:
                        conn.executemany(
                            f"UPDATE {quoted_table} SET payload_json=? WHERE {_quote_identifier(key_column)}=?",
                            batch,
                        )
            patch_path.unlink(missing_ok=True)
        counts[table] = count
        print(f"scanned {table}: {count:,} rows; privacy updates {changed_rows[table]:,}", flush=True)

    if counts.get("canonical_work", 0) < 1:
        raise BundleError("source database has no canonical_work rows")
    return {
        "tables": counts,
        "source_entity_digest_sha256": source_digest.hexdigest(),
        "bundled_entity_digest_sha256": bundled_digest.hexdigest(),
        "core_fields": {
            "table": "canonical_work",
            "fields": list(CORE_FIELDS),
            "source_sha256": core_source.hexdigest(),
            "bundled_sha256": core_bundled.hexdigest(),
        },
        "privacy_changed_rows": dict(sorted(changed_rows.items())),
    }, counts


def _copy_sanitized_asset(src: Path, dst: Path, relpath: str, group: str, sanitizer: PrivacySanitizer) -> dict[str, Any]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    name = src.name.casefold()
    if name.endswith(".jsonl.gz"):
        with gzip.open(src, "rt", encoding="utf-8") as input_stream, dst.open("wb") as raw_out:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_out, mtime=0, compresslevel=6) as output_gzip:
                for line_no, line in enumerate(input_stream, 1):
                    if not line.strip():
                        output_gzip.write(line.encode("utf-8"))
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise BundleError(f"invalid JSONL in {relpath} at line {line_no}") from exc
                    safe = sanitizer.scrub_value(item, group, f"{relpath}[{line_no}]")
                    output_gzip.write((json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    elif name.endswith(".jsonl"):
        with src.open("r", encoding="utf-8") as input_stream, dst.open("w", encoding="utf-8", newline="\n") as output_stream:
            for line_no, line in enumerate(input_stream, 1):
                if not line.strip():
                    output_stream.write(line)
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise BundleError(f"invalid JSONL in {relpath} at line {line_no}") from exc
                safe = sanitizer.scrub_value(item, group, f"{relpath}[{line_no}]")
                output_stream.write(json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n")
    elif name.endswith(".json"):
        try:
            value = json.loads(src.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BundleError(f"invalid JSON file: {relpath}") from exc
        safe = sanitizer.scrub_value(value, group, relpath)
        dst.write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif name.endswith((".yml", ".yaml")):
        raw = src.read_text(encoding="utf-8")
        stripped = raw.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                safe, _ = sanitizer.sanitize_yaml_text(raw, group, relpath)
            else:
                safe_value = sanitizer.scrub_value(value, group, relpath)
                safe = json.dumps(safe_value, ensure_ascii=False, indent=2) + ("\n" if raw.endswith("\n") else "")
        else:
            safe, _ = sanitizer.sanitize_yaml_text(raw, group, relpath)
        dst.write_text(safe, encoding="utf-8")
    else:
        raise BundleError(f"refusing unsupported maintenance asset type: {relpath}")
    os.chmod(dst, 0o644)
    return {
        "path": relpath,
        "group": group,
        "size_bytes": dst.stat().st_size,
        "sha256": sha256_file(dst),
    }


def _history_output_path(relative: Path, assigned: dict[str, str]) -> Path:
    parts = list(relative.parts)
    for index, part in enumerate(parts):
        if part.casefold() == "history" and index + 1 < len(parts) and UUID_RE.fullmatch(parts[index + 1]):
            key = "/".join(parts[: index + 2])
            if key not in assigned:
                assigned[key] = f"history-{len(assigned) + 1:04d}"
            parts[index + 1] = assigned[key]
    return Path(*parts)


def _select_maintenance_files(source_home: Path) -> tuple[list[tuple[Path, str, str]], dict[str, Any]]:
    selected: list[tuple[Path, str, str]] = []
    summary: dict[str, Any] = {"included_counts": {}, "excluded_counts": {}}

    registry = source_home / "registry"
    registry_selected = []
    if registry.is_dir():
        for path in sorted(registry.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(registry)
            if path.suffix.casefold() in {".yml", ".yaml", ".json"} and path.name.casefold() != "agents.yml":
                registry_selected.append((path, f"registry/{rel.as_posix()}", "registry"))
    selected.extend(registry_selected)
    summary["included_counts"]["registry"] = len(registry_selected)

    recipes = source_home / "recipes"
    recipe_selected: list[tuple[Path, str, str]] = []
    recipe_total = 0
    history_names: dict[str, str] = {}
    if recipes.is_dir():
        for path in sorted(recipes.rglob("*")):
            if not path.is_file():
                continue
            recipe_total += 1
            rel = path.relative_to(recipes)
            in_history = "history" in rel.parts
            allowed = (HISTORY_CONFIG_RE.fullmatch(path.name) is not None) if in_history else path.name in {"recipe.yml", "recipe_candidate.yml", "recipe_lock.json"}
            if allowed and path.suffix.casefold() in {".yml", ".yaml", ".json"}:
                safe_rel = _history_output_path(rel, history_names)
                recipe_selected.append((path, f"recipes/{safe_rel.as_posix()}", "recipe_config"))
    selected.extend(recipe_selected)
    summary["included_counts"]["recipe_config"] = len(recipe_selected)
    summary["excluded_counts"]["recipe_file"] = recipe_total - len(recipe_selected)

    expected = source_home / "manifests" / "expected"
    expected_selected: list[tuple[Path, str, str]] = []
    if expected.is_dir():
        for path in sorted(expected.rglob("*")):
            if path.is_file() and path.suffix.casefold() in {".jsonl", ".gz"}:
                rel = path.relative_to(source_home / "manifests")
                expected_selected.append((path, f"manifests/{rel.as_posix()}", "expected_manifest"))
    selected.extend(expected_selected)
    summary["included_counts"]["expected_manifest"] = len(expected_selected)

    campaign = source_home / "campaign_state.json"
    if campaign.is_file():
        selected.append((campaign, "campaign_state.json", "campaign_state"))
        summary["included_counts"]["campaign_state"] = 1
    else:
        summary["included_counts"]["campaign_state"] = 0

    summary["excluded_groups"] = {
        "recipes": "Only current recipe, candidate, lock, and history recipe YAML/JSON are included; browser evidence, raw evidence, pilot/replay manifests, sample metadata, reports, errors, login requirements, and thread receipts are omitted.",
        "source_home": "runs/, reports/, raw/, preflight/, queues/, search/, snapshots/, staging/, waivers/, controller locks, SQLite sidecars, and backup campaign state are omitted.",
        "registry": "agents.yml is omitted; venue, retired-venue, defaults, document-type, publisher-family, venue-registry, and browser-policy configuration files are included.",
    }
    return selected, summary


def _make_archive(stage: Path, dist: Path, version: str, max_part_bytes: int) -> dict[str, Any]:
    archive_name = f"literature-db-{version}.tar.gz"
    archive_path = dist / archive_name
    if archive_path.exists():
        raise BundleError(f"release asset already exists: {archive_path} (choose a new version or remove it explicitly)")
    temp_archive = dist / (archive_name + ".partial")
    with tarfile.open(temp_archive, mode="w:gz", compresslevel=6) as tar:
        for path in sorted(p for p in stage.rglob("*") if p.is_file()):
            relative = path.relative_to(stage).as_posix()
            info = tar.gettarinfo(str(path), arcname=relative)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            with path.open("rb") as stream:
                tar.addfile(info, stream)
    os.replace(temp_archive, archive_path)
    archive_size = archive_path.stat().st_size
    archive_sha = sha256_file(archive_path)
    parts: list[dict[str, Any]] = []
    if archive_size <= max_part_bytes:
        parts.append({"name": archive_name, "size_bytes": archive_size, "sha256": archive_sha})
    else:
        with archive_path.open("rb") as stream:
            index = 1
            while block := stream.read(max_part_bytes):
                part_name = f"{archive_name}.part{index:03d}"
                part_path = dist / part_name
                part_path.write_bytes(block)
                parts.append({"name": part_name, "size_bytes": len(block), "sha256": sha256_file(part_path)})
                index += 1
        archive_path.unlink()
    release_manifest = {
        "format_version": FORMAT_VERSION,
        "release_version": version,
        "archive": {
            "name": archive_name,
            "size_bytes": archive_size,
            "sha256": archive_sha,
            "parts": parts,
        },
    }
    release_path = dist / "database-release-manifest.json"
    if release_path.exists():
        raise BundleError(f"release manifest already exists: {release_path}")
    release_path.write_text(json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums_path = dist / "SHA256SUMS"
    if sums_path.exists():
        raise BundleError(f"checksum file already exists: {sums_path}")
    with sums_path.open("w", encoding="utf-8") as stream:
        for part in parts:
            stream.write(f"{part['sha256']}  {part['name']}\n")
        stream.write(f"{archive_sha}  {archive_name} (concatenated archive)\n")
    if len(parts) == 1:
        (dist / f"{archive_name}.sha256").write_text(f"{archive_sha}  {archive_name}\n", encoding="utf-8")
    return release_manifest


def build_bundle(
    source_home: Path,
    destination: Path,
    dist: Path,
    version: str,
    minimum_canonical_works: int = MIN_CANONICAL_WORKS,
    max_part_bytes: int = DEFAULT_PART_BYTES,
    force: bool = False,
) -> dict[str, Any]:
    source_home = source_home.expanduser().resolve()
    source_db = source_home / "catalog.sqlite"
    destination = destination.expanduser().resolve()
    dist = dist.expanduser().resolve()
    if not source_db.is_file():
        raise BundleError(f"source database not found: {source_db}")
    if source_db == destination / "catalog.sqlite":
        raise BundleError("source and output catalog paths must differ")
    if destination.exists() and any(destination.iterdir()) and not force:
        raise BundleError(f"output directory is not empty: {destination} (use --force to replace this generated bundle)")
    if destination.exists() and force and any(destination.iterdir()):
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    if any(dist.iterdir()):
        raise BundleError(f"dist directory is not empty: {dist}; remove or move old release assets before generating")

    sanitizer = PrivacySanitizer()
    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    with tempfile.TemporaryDirectory(prefix="literature-db-bundle-") as temp_name:
        temp = Path(temp_name)
        stage = temp / "bundle"
        stage.mkdir()
        snapshot = stage / "catalog.sqlite"
        source = sqlite3.connect(safe_db_uri(source_db), uri=True, timeout=60)
        target = sqlite3.connect(snapshot, timeout=60)
        try:
            source.backup(target, pages=4096, sleep=0.02)
        finally:
            target.close()
            source.close()

        conn = sqlite3.connect(snapshot, timeout=60)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN IMMEDIATE")
        try:
            with tempfile.TemporaryDirectory(prefix="patches-") as patches:
                db_summary, entity_counts = _database_inventory(conn, sanitizer, Path(patches))
            if entity_counts.get("canonical_work", 0) < minimum_canonical_works:
                raise BundleError(
                    f"snapshot has only {entity_counts.get('canonical_work', 0):,} canonical works; "
                    f"release threshold is {minimum_canonical_works:,}"
                )
            conn.commit()
            # Rewrite every SQLite page so deleted/updated pre-scrub values are not left in free pages.
            conn.execute("VACUUM")
        except Exception:
            conn.rollback()
            conn.close()
            raise
        conn.close()

        check = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True, timeout=60)
        integrity = [row[0] for row in check.execute("PRAGMA integrity_check")]
        final_counts: dict[str, int] = {}
        final_entity_digest = hashlib.sha256()
        final_core_digest = hashlib.sha256()
        final_tables = [row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in final_tables:
            columns, order = _pk_order(check, table)
            selection = ",".join(_quote_identifier(c) for c in columns)
            ordering = ",".join(_quote_identifier(c) for c in order)
            count = 0
            for row in check.execute(f"SELECT {selection} FROM {_quote_identifier(table)} ORDER BY {ordering}"):
                count += 1
                _row_digest_update(final_entity_digest, table, columns, tuple(row))
                if table == "canonical_work" and "payload_json" in columns:
                    payload = json.loads(str(row[columns.index("payload_json")]))
                    core = {key: payload.get(key) for key in CORE_FIELDS}
                    final_core_digest.update(_json_bytes([row[columns.index(order[0])], core]) + b"\n")
            final_counts[table] = count
        check.close()
        if integrity != ["ok"]:
            raise BundleError(f"bundled SQLite integrity_check failed: {integrity[:5]}")
        if final_counts != db_summary["tables"]:
            raise BundleError("SQLite table row counts changed while sanitizing the snapshot")
        if final_entity_digest.hexdigest() != db_summary["bundled_entity_digest_sha256"]:
            raise BundleError("bundled entity digest changed after VACUUM")
        if final_core_digest.hexdigest() != db_summary["core_fields"]["bundled_sha256"]:
            raise BundleError("bundled canonical core-field digest changed after VACUUM")
        if db_summary["core_fields"]["source_sha256"] != db_summary["core_fields"]["bundled_sha256"]:
            raise BundleError("canonical core fields changed during privacy normalization")
        db_summary["source_tables"] = dict(db_summary["tables"])
        db_summary["bundled_tables"] = dict(final_counts)
        local_home = Path.home().as_posix().encode("utf-8")
        if local_home not in {b"/", b""} and _contains_bytes(snapshot, local_home):
            raise BundleError("vacuumed SQLite still contains the source machine's home path")

        selected, maintenance_summary = _select_maintenance_files(source_home)
        asset_files: list[dict[str, Any]] = []
        for src, relpath, group in selected:
            normalized = PurePosixPath(relpath)
            if normalized.is_absolute() or ".." in normalized.parts or "\\" in relpath:
                raise BundleError(f"unsafe maintenance asset path: {relpath}")
            dst = stage.joinpath(*normalized.parts)
            asset_files.append(_copy_sanitized_asset(src, dst, relpath, group, sanitizer))
        maintenance_summary["files"] = asset_files
        maintenance_summary["selected_file_count"] = len(asset_files)

        canonical_db = {
            "filename": "catalog.sqlite",
            "size_bytes": snapshot.stat().st_size,
            "sha256": sha256_file(snapshot),
        }
        manifest = {
            "format_version": FORMAT_VERSION,
            "release_version": version,
            "generated_at_utc": started,
            "database": canonical_db,
            "source_snapshot": {
                "source_filename": source_db.name,
                "source_size_bytes": source_db.stat().st_size,
                "source_sha256": sha256_file(source_db),
                "method": "SQLite online backup API followed by privacy-field normalization",
                "source_catalog_modified": False,
            },
            "entities": db_summary,
            "maintenance_assets": maintenance_summary,
            "privacy_review": {
                "transformations": sanitizer.summary(),
                "raw_html_pdf_browser_evidence_or_chat_receipts_included": False,
                "credential_pattern_hits_included_values": sanitizer.secret_pattern_hits,
                "academic_session_codes_preserved": True,
                "notes": [
                    "Absolute local paths are replaced with stable legacy-evidence://omitted-local-artifact references. The referenced local files are not part of this release.",
                    "Internal thread, request, session, account, and user identifiers are replaced with stable legacy-evidence:// references; scholarly native IDs and conference session_code/scope_session fields are preserved.",
                    "All database rows and fields are retained. The source and bundled entity/core-field digests document the expected privacy-only value changes.",
                ],
            },
        }
        (stage / "bundle-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Inventory after all files are sanitized. The bundle manifest intentionally does not hash itself.
        packaged_files = []
        for path in sorted(p for p in stage.rglob("*") if p.is_file() and p.name != "bundle-manifest.json"):
            packaged_files.append({
                "path": path.relative_to(stage).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
        manifest["package_inventory"] = packaged_files
        (stage / "bundle-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Recheck database hash after the inventory is finalized.
        if sha256_file(snapshot) != canonical_db["sha256"]:
            raise BundleError("database changed after its manifest digest was recorded")

        # Promote the complete, local snapshot into the repository's data home.
        if destination.exists() and not any(destination.iterdir()):
            destination.rmdir()
        os.replace(stage, destination)
        release_manifest = _make_archive(destination, dist, version, max_part_bytes)

    print(f"database: {destination / 'catalog.sqlite'}", flush=True)
    print(f"bundle manifest: {destination / 'bundle-manifest.json'}", flush=True)
    print(f"release manifest: {dist / 'database-release-manifest.json'}", flush=True)
    print(f"archive sha256: {release_manifest['archive']['sha256']}", flush=True)
    print(f"canonical works: {entity_counts['canonical_work']:,}", flush=True)
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-home", type=Path, required=True, help="source literature-db home containing catalog.sqlite and maintenance configuration")
    parser.add_argument("--destination", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "literature-db")
    parser.add_argument("--dist", type=Path, default=Path(__file__).resolve().parents[1] / "dist")
    parser.add_argument("--version", required=True, help="release version label, e.g. v1.0.0")
    parser.add_argument("--minimum-canonical-works", type=int, default=MIN_CANONICAL_WORKS)
    parser.add_argument("--max-part-bytes", type=int, default=DEFAULT_PART_BYTES)
    parser.add_argument("--force", action="store_true", help="replace a non-empty generated data directory")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.version):
        parser.error("--version must be a simple filename-safe version label")
    try:
        build_bundle(args.source_home, args.destination, args.dist, args.version, args.minimum_canonical_works, args.max_part_bytes, args.force)
    except (BundleError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"bundle_database: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .io import atomic_json, utc_now
from .paths import LitDBPaths

PATTERNS = {
    "authorization_header": re.compile(r"(?im)^\s*authorization\s*:\s*\S+"),
    "cookie_header": re.compile(r"(?im)^\s*(?:set-)?cookie\s*:\s*\S+"),
    "bearer_token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    "api_key_assignment": re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|session[_-]?token|csrf[_-]?token)\s*[=:]\s*['\"]?[A-Za-z0-9._~+/=-]{12,}"),
}
SENSITIVE_QUERY_KEYS = {"token", "access_token", "signature", "sig", "x-amz-signature", "session", "sessionid", "auth"}
TEXT_SUFFIXES = {".json", ".jsonl", ".yml", ".yaml", ".md", ".txt", ".html", ".xml", ".csv", ".ris", ".bib"}


def scan_text(text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for kind, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append({"kind": kind, "offset": match.start()})
    for match in re.finditer(r"https?://[^\s\"'<>]+", text):
        # Abstracts and citation text can contain Markdown link remnants such
        # as ``https://host](https://host)``.  They are not valid URLs, and
        # urlsplit raises on bracketed host fragments (notably ``Invalid IPv6
        # URL``).  Ignore only that malformed candidate; valid URL candidates
        # continue through the sensitive-query check below.
        try:
            query = urlsplit(match.group(0)).query
        except ValueError:
            continue
        query_keys = {key.lower() for key, _ in parse_qsl(query, keep_blank_values=True)}
        sensitive = sorted(query_keys & SENSITIVE_QUERY_KEYS)
        if sensitive:
            findings.append({"kind": "signed_or_session_url", "offset": match.start(), "query_keys": sensitive})
    return findings


def scan_path(paths: LitDBPaths, target: Path) -> tuple[int, dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    files_scanned = 0
    if target.is_file():
        candidates = [target]
    elif target.is_dir():
        candidates = [path for path in target.rglob("*") if path.is_file()]
    else:
        candidates = []
    for path in candidates:
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 10 * 1024 * 1024:
            continue
        files_scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for finding in scan_text(text):
            findings.append({"file": str(path), **finding})
    report = {
        "scanned_at": utc_now(),
        "target": str(target),
        "files_scanned": files_scanned,
        "secret_findings": findings,
        "status": "PASS" if not findings else "SECURITY_FAIL",
    }
    atomic_json(paths.home / "reports" / "browser_security_report.json", report)
    return (0 if not findings else 4), report

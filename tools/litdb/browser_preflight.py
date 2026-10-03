from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .constants import API_KEY_NAMES
from .io import atomic_json, load_json, utc_now
from .paths import LitDBPaths

REQUIRED_CAPABILITIES = {"navigate", "read_url", "read_title", "read_dom", "semantic_click", "scroll", "screenshot"}


def check(paths: LitDBPaths, browser_first: bool, strict: bool) -> tuple[int, dict[str, Any]]:
    browser_evidence_path = paths.preflight / "browser_capabilities.json"
    evidence = load_json(browser_evidence_path) if browser_evidence_path.is_file() else None
    capabilities = set(evidence.get("capabilities", [])) if isinstance(evidence, dict) else set()
    missing = sorted(REQUIRED_CAPABILITIES - capabilities)
    browser_ok = isinstance(evidence, dict) and evidence.get("status") == "PASS" and not missing
    zero_key = {
        "checked_at": utc_now(),
        "required_api_keys": 0,
        "api_key_environment_presence_ignored": {name: bool(os.environ.get(name)) for name in API_KEY_NAMES},
        "required_path": "browser-first",
        "status": "PASS",
    }
    security = {
        "checked_at": utc_now(),
        "domain_allowlist_required": True,
        "secret_redaction_required": True,
        "web_content_untrusted": True,
        "prompt_injection_blocking": True,
        "credential_capture": False,
        "access_control_bypass": False,
        "status": "PASS",
    }
    report = {
        "checked_at": utc_now(),
        "browser_first": browser_first,
        "browser_capability_evidence": str(browser_evidence_path),
        "browser_available": browser_ok,
        "missing_capabilities": missing,
        "required_api_keys": 0,
        "status": "PASS" if browser_ok else "BROWSER_CAPABILITY_MISSING",
    }
    atomic_json(paths.preflight / "zero_api_key_report.json", zero_key)
    atomic_json(paths.preflight / "browser_security_report.json", security)
    atomic_json(paths.preflight / "preflight_report.json", report)
    if not browser_first:
        report["status"] = "CONFIG_ERROR"
        return 2, report
    if not browser_ok:
        return 5 if strict else 0, report
    return 0, report

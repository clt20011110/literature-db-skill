from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .constants import ACCEPTANCE_VERSION, EXPECTED_VENUES
from .db import TABLES, table_names
from .io import atomic_json, load_json, utc_now
from .paths import LitDBPaths
from .registry import validate_runtime


def _queue_nonempty(path: Path) -> bool:
    return path.is_file() and bool(path.read_text(encoding="utf-8").strip())


def verify(paths: LitDBPaths, acceptance: str, strict: bool) -> tuple[int, dict[str, Any]]:
    if acceptance != ACCEPTANCE_VERSION:
        return 2, {"status": "CONFIG_ERROR", "errors": [f"unsupported acceptance: {acceptance}"]}
    checks: dict[str, Any] = {}
    errors: list[str] = []
    security_errors: list[str] = []
    blocked_auth = False
    blocked_source = False
    registry = validate_runtime(paths, strict=True)
    checks["registry"] = registry
    if registry["status"] != "PASS":
        errors.extend(registry["errors"])
    if not paths.catalog.is_file():
        errors.append("catalog.sqlite missing")
    else:
        try:
            missing_tables = sorted(set(TABLES) - table_names(paths.catalog))
            checks["database_missing_tables"] = missing_tables
            if missing_tables:
                errors.append(f"database missing tables: {', '.join(missing_tables)}")
        except sqlite3.DatabaseError as exc:
            errors.append(f"database invalid: {exc}")
    preflight_path = paths.preflight / "preflight_report.json"
    if not preflight_path.is_file() or load_json(preflight_path).get("status") != "PASS":
        errors.append("browser-first preflight has not passed")
    security_path = paths.home / "reports" / "browser_security_report.json"
    if security_path.is_file() and load_json(security_path).get("secret_findings"):
        security_errors.append("secret findings remain")
    state = load_json(paths.state) if paths.state.is_file() else {"venues": {}}
    venue_states = {key: value.get("state") for key, value in state.get("venues", {}).items()}
    state_counts: dict[str, int] = {}
    for value in venue_states.values():
        state_counts[value] = state_counts.get(value, 0) + 1
    checks["venue_state_counts"] = state_counts
    if len(venue_states) != EXPECTED_VENUES:
        errors.append("campaign state does not cover all venues")
    blocked_auth = any(value == "AUTH_REQUIRED" for value in venue_states.values()) or _queue_nonempty(paths.home / "queues" / "auth_required.jsonl")
    blocked_source = any(value in {"SOURCE_BLOCKED", "POLICY_BLOCKED", "RECIPE_DRIFT"} for value in venue_states.values()) or _queue_nonempty(paths.home / "queues" / "source_blocked.jsonl") or _queue_nonempty(paths.home / "queues" / "recipe_drift.jsonl")
    active = sum(value == "ACTIVE" for value in venue_states.values())
    if strict and active != EXPECTED_VENUES:
        errors.append(f"active venues {active} != {EXPECTED_VENUES}")
    report = {
        "acceptance": acceptance,
        "verified_at": utc_now(),
        "strict": strict,
        "checks": checks,
        "errors": errors,
        "security_errors": security_errors,
        "auth_blocked": blocked_auth,
        "source_or_drift_blocked": blocked_source,
        "strict_verifier_exit": None,
        "status": "FAIL",
    }
    if security_errors:
        code = 4
    elif blocked_auth:
        code = 6
    elif blocked_source:
        code = 7
    elif errors:
        code = 1
    else:
        code = 0
        report["status"] = "PASS"
    report["strict_verifier_exit"] = code
    atomic_json(paths.home / "reports" / "final_acceptance_report.json", report)
    return code, report

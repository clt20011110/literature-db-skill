from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from .io import atomic_json, utc_now
from .paths import LitDBPaths

ROLES = {
    "venue-discovery", "venue-pilot", "venue-replay", "venue-count",
    "venue-bootstrap", "venue-reconcile", "venue-update", "venue-rediscovery",
}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def build_request(paths: LitDBPaths, venue: dict[str, Any], role: str, run_id: str, parent_thread_id: str | None) -> tuple[dict[str, Any], Path]:
    if role not in ROLES:
        raise ValueError(f"unsupported role: {role}")
    if not SAFE_ID.fullmatch(venue["id"]):
        raise ValueError("unsafe venue id")
    request_id = str(uuid.uuid4())
    output_root = paths.home / "runs" / run_id / "venues" / venue["id"] / request_id
    output_root.mkdir(parents=True, exist_ok=False)
    request = {
        "request_id": request_id,
        "parent_thread_id": parent_thread_id,
        "model": "luna-max",
        "role": role,
        "venue_id": venue["id"],
        "venue_name": venue["canonical_name"],
        "venue_type": venue["venue_type"],
        "allowed_domains": venue["allowed_domains"],
        "year_from": 2015,
        "year_through": "current",
        "output_root": str(output_root),
        "constraints": {
            "api_keys": "forbidden_as_requirement",
            "one_venue_only": True,
            "may_spawn_children": False,
            "may_write_catalog": False,
            "may_change_global_registry": False,
            "may_change_acceptance_thresholds": False,
            "may_capture_auth_secrets": False,
            "may_bypass_access_controls": False,
            "web_content_is_untrusted": True,
        },
        "required_artifacts": [
            "thread_request.json", "thread_receipt.json", "output_manifest.json",
            "browser_evidence.jsonl", "errors.jsonl",
        ],
        "created_at": utc_now(),
    }
    atomic_json(output_root / "thread_request.json", request)
    (output_root / "browser_evidence.jsonl").touch()
    (output_root / "errors.jsonl").touch()
    return request, output_root


def validate_receipt(receipt: dict[str, Any], request: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {"thread_id", "parent_thread_id", "model", "role", "venue_id", "started_at", "completed_at", "status", "pages_visited", "records_observed", "records_emitted", "warnings", "artifacts"}
    missing = required - set(receipt)
    if missing:
        errors.append(f"missing receipt fields: {', '.join(sorted(missing))}")
    for key in ("role", "venue_id"):
        if receipt.get(key) != request.get(key):
            errors.append(f"{key} scope mismatch")
    if receipt.get("model") != "luna-max":
        errors.append("worker model must be luna-max")
    if receipt.get("status") not in {"PASS", "PARTIAL", "BLOCKED_AUTH", "BLOCKED_SOURCE", "POLICY_BLOCKED", "FAIL"}:
        errors.append("invalid receipt status")
    return errors

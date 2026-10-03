from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .constants import TRANSITIONS
from .io import atomic_json, load_json, utc_now
from .paths import LitDBPaths


def initial_state(venue_ids: list[str], root_thread_id: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "campaign_id": f"bootstrap-{utc_now().replace(':', '').replace('-', '')}",
        "root_thread_id": root_thread_id,
        "controller": "sol-high",
        "worker": "luna-max",
        "browser_first": True,
        "required_api_keys": 0,
        "max_active_venues": 1,
        "phase": "M0_M1",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "venues": {venue_id: {"state": "UNSEEN", "updated_at": utc_now()} for venue_id in venue_ids},
        "history": [],
    }


def create_controller_lock(paths: LitDBPaths, owner: dict[str, Any]) -> None:
    lock_path = paths.home / "controller.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(lock_path, flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"controller lock already exists: {lock_path}") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        import json
        json.dump(owner, handle, indent=2, sort_keys=True)
        handle.write("\n")


def transition(paths: LitDBPaths, venue_id: str, expected: str, target: str, receipt: Path) -> dict[str, Any]:
    state = load_json(paths.state)
    if venue_id not in state["venues"]:
        raise ValueError(f"unknown venue: {venue_id}")
    current = state["venues"][venue_id]["state"]
    if current != expected:
        raise ValueError(f"state mismatch for {venue_id}: expected {expected}, found {current}")
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal transition: {current} -> {target}")
    if not receipt.is_file():
        raise ValueError(f"receipt does not exist: {receipt}")
    receipt_data = load_json(receipt)
    if receipt_data.get("venue_id") != venue_id:
        raise ValueError("receipt venue scope mismatch")
    event = {
        "venue_id": venue_id,
        "from": current,
        "to": target,
        "receipt": str(receipt.resolve()),
        "at": utc_now(),
    }
    state["venues"][venue_id] = {"state": target, "updated_at": event["at"], "last_receipt": event["receipt"]}
    state["history"].append(event)
    state["updated_at"] = event["at"]
    atomic_json(paths.state, state)
    return event

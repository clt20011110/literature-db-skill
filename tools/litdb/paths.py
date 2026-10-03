from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_HOME


@dataclass(frozen=True)
class LitDBPaths:
    home: Path

    @classmethod
    def from_value(cls, value: str | Path | None) -> "LitDBPaths":
        selected = value if value is not None else os.environ.get("LITDB_HOME")
        return cls(Path(selected).expanduser().resolve() if selected else DEFAULT_HOME.resolve())

    @property
    def state(self) -> Path:
        return self.home / "campaign_state.json"

    @property
    def registry(self) -> Path:
        return self.home / "registry"

    @property
    def venues(self) -> Path:
        return self.registry / "venues"

    @property
    def preflight(self) -> Path:
        return self.home / "preflight"

    @property
    def catalog(self) -> Path:
        return self.home / "catalog.sqlite"

    def ensure_tree(self) -> None:
        for rel in (
            "registry/venues", "recipes", "runs", "raw", "staging",
            "manifests/expected", "manifests/observed", "manifests/canonical",
            "reports/count", "queues", "snapshots", "waivers", "preflight",
        ):
            (self.home / rel).mkdir(parents=True, exist_ok=True)
        for queue in ("auth_required", "recipe_drift", "source_blocked", "metadata_repair", "manual_review"):
            target = self.home / "queues" / f"{queue}.jsonl"
            target.touch(exist_ok=True)

#!/usr/bin/env python3
"""Build a formal pilot manifest from a reused, classified IEEE listing.

The listing may contain explicit exclusions.  A pilot manifest is an
included-sample contract, so retain only the controller-selected identities,
renumber positions within each year for the pilot validator, and preserve the
original visible position for auditability.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--listing", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = load_jsonl(args.listing.resolve())
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    selected = [str(value) for value in selection.get("source_item_ids", [])]
    by_id = {str(row.get("source_item_id") or row.get("source_native_id") or ""): row for row in rows}
    missing = [identity for identity in selected if identity not in by_id]
    if missing:
        raise SystemExit(f"pilot selection identities missing from reused listing: {missing}")
    chosen = []
    for identity in selected:
        row = dict(by_id[identity])
        if row.get("include_decision") == "exclude":
            raise SystemExit(f"pilot selection contains an excluded identity: {identity}")
        row["original_listing_position"] = row.get("listing_position")
        row["pilot_manifest_reused_from_listing"] = str(args.listing.resolve())
        chosen.append(row)
    chosen.sort(key=lambda row: (int(row.get("year", 0)), int(row.get("original_listing_position") or 0)))
    positions: dict[int, int] = {}
    for row in chosen:
        year = int(row["year"])
        positions[year] = positions.get(year, 0) + 1
        row["listing_position"] = positions[year]
        row["include_decision"] = "include_candidate"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in chosen), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"status": "PASS", "rows": len(chosen), "years": sorted(positions), "output": str(args.output.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

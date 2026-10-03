#!/usr/bin/env python3
"""Run the checkpointed NeurIPS detail pass repeatedly in one writer.

The underlying runner intentionally handles one bounded pass (up to
``max_per_year`` per year) so a control timeout cannot lose already-written
rows.  This wrapper re-enters that runner only after it returns, preserving a
single writer and stopping on a real hard stop or on a no-progress pass.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--expected-root", type=Path, required=True)
    ap.add_argument("--source-exclusions", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--max-per-year", type=int, default=500)
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args()

    run_dir = args.run.expanduser().resolve()
    sys.path.insert(0, str(run_dir))
    from official_detail_runner import run  # type: ignore
    from argparse import Namespace

    runner_args = Namespace(
        run=run_dir,
        expected_root=args.expected_root.expanduser().resolve(),
        source_exclusions=args.source_exclusions.expanduser().resolve(),
        workers=args.workers,
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
        max_per_year=args.max_per_year,
    )
    previous = -1
    pass_no = 0
    while True:
        pass_no += 1
        print(json.dumps({"event": "loop_pass_start", "pass": pass_no}), flush=True)
        rc = int(run(runner_args))
        summary_path = run_dir / "detail_summary.json"
        checkpoint_path = run_dir / "detail_checkpoint.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.is_file() else {}
        staged = int(summary.get("staged_count", checkpoint.get("completed_detail_count", 0)) or 0)
        pending = int(summary.get("pending_count", checkpoint.get("remaining_detail_count", 0)) or 0)
        hard_stop = bool(checkpoint.get("hard_stop"))
        print(json.dumps({"event": "loop_pass_done", "pass": pass_no, "runner_rc": rc, "staged": staged, "pending": pending, "hard_stop": hard_stop}), flush=True)
        if rc == 0 or hard_stop or staged <= previous:
            return 0 if rc == 0 else 3
        previous = staged
        time.sleep(max(0.0, args.pause))


if __name__ == "__main__":
    raise SystemExit(main())

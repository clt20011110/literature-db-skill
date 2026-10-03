from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.io import atomic_json
from litdb.paths import LitDBPaths
from litdb.state import initial_state, transition


class StateTests(unittest.TestCase):
    def test_artifact_driven_transition(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            atomic_json(paths.state, initial_state(["icml"], "root"))
            receipt = paths.home / "dispatch.json"
            atomic_json(receipt, {"venue_id": "icml", "status": "DISPATCHED"})
            event = transition(paths, "icml", "UNSEEN", "DISCOVERY_RUNNING", receipt)
            self.assertEqual(event["to"], "DISCOVERY_RUNNING")

    def test_illegal_transition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            atomic_json(paths.state, initial_state(["icml"], "root"))
            receipt = paths.home / "receipt.json"
            atomic_json(receipt, {"venue_id": "icml"})
            with self.assertRaises(ValueError):
                transition(paths, "icml", "UNSEEN", "ACTIVE", receipt)

    def test_source_blocked_can_resume_a_gated_phase(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            state = initial_state(["tcad"], "root")
            state["venues"]["tcad"]["state"] = "SOURCE_BLOCKED"
            atomic_json(paths.state, state)
            receipt = paths.home / "source-recovery.json"
            atomic_json(receipt, {"venue_id": "tcad", "status": "SOURCE_RECOVERED"})

            event = transition(paths, "tcad", "SOURCE_BLOCKED", "PILOT_RUNNING", receipt)

            self.assertEqual(event["to"], "PILOT_RUNNING")

    def test_source_blocked_can_resume_replay(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            state = initial_state(["tcad"], "root")
            state["venues"]["tcad"]["state"] = "SOURCE_BLOCKED"
            atomic_json(paths.state, state)
            receipt = paths.home / "source-recovery.json"
            atomic_json(receipt, {"venue_id": "tcad", "status": "SOURCE_RECOVERED"})

            event = transition(paths, "tcad", "SOURCE_BLOCKED", "REPLAY_RUNNING", receipt)

            self.assertEqual(event["to"], "REPLAY_RUNNING")

    def test_source_blocked_cannot_skip_gates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            state = initial_state(["tcad"], "root")
            state["venues"]["tcad"]["state"] = "SOURCE_BLOCKED"
            atomic_json(paths.state, state)
            receipt = paths.home / "source-recovery.json"
            atomic_json(receipt, {"venue_id": "tcad", "status": "SOURCE_RECOVERED"})

            with self.assertRaises(ValueError):
                transition(paths, "tcad", "SOURCE_BLOCKED", "PILOT_PASSED", receipt)


if __name__ == "__main__":
    unittest.main()

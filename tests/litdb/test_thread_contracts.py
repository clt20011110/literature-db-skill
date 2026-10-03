from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.paths import LitDBPaths
from litdb.thread_contracts import build_request


class ThreadContractTests(unittest.TestCase):
    def test_request_is_single_venue_luna_contract(self) -> None:
        venue = {"id":"icml","canonical_name":"ICML","venue_type":"conference","allowed_domains":["proceedings.mlr.press"]}
        with tempfile.TemporaryDirectory() as folder:
            paths = LitDBPaths(Path(folder))
            paths.ensure_tree()
            request, output = build_request(paths, venue, "venue-discovery", "run-1", "root")
            self.assertEqual(request["model"], "luna-max")
            self.assertTrue(request["constraints"]["one_venue_only"])
            self.assertFalse(request["constraints"]["may_spawn_children"])
            self.assertFalse(request["constraints"]["may_write_catalog"])
            self.assertTrue((output / "thread_request.json").is_file())


if __name__ == "__main__":
    unittest.main()

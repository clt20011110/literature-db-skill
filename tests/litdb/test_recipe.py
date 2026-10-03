from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.io import atomic_json
from litdb.recipe_models import validate


class RecipeTests(unittest.TestCase):
    def test_minimal_compliant_recipe(self) -> None:
        recipe = {
            "venue_id":"icml", "recipe_version":1, "venue_type":"conference",
            "allowed_domains":["proceedings.mlr.press"], "entry_urls":["https://proceedings.mlr.press/"],
            "access_mode":"public_html", "login_required":False, "manual_auth_checkpoint":None,
            "calendar_model":"annual_proceedings", "start_year":2015,
            "edition_or_issue_enumeration":{}, "article_enumeration":{},
            "pagination":{"termination_condition":"no next link"},
            "metadata_extractors":{key:[] for key in ("title","authors","abstract","doi","landing_url","pdf_url","publication_date","document_type")},
            "content_inclusion_rules":[], "content_exclusion_rules":[],
            "rate_limit":{"max_pages_per_minute":10,"inter_action_delay_ms":1000},
            "retry_policy":{}, "drift_signals":["missing heading"], "validation_anchors":["Proceedings"],
            "redaction_rules":["no cookies"], "primary_path":"volume index", "fallback_path":"article pages"
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recipe.yml"
            atomic_json(path, recipe)
            self.assertEqual(validate(path, "icml")["status"], "PASS")

    def test_issue_archive_recipe_shape(self) -> None:
        recipe = {
            "venue_id":"nature", "recipe_version":1, "venue_type":"journal",
            "allowed_domains":["nature.com"], "entry_urls":["https://www.nature.com/nature/volumes"],
            "access_mode":"public_html", "login_required":False, "manual_auth_checkpoint":None,
            "calendar_model":"issue", "start_year":2015,
            "edition_or_issue_enumeration":{"archive_url":"https://www.nature.com/nature/volumes","archive_entry_rule":"collect volumes","volume_page_rule":"collect issues","termination":["all issue links"]},
            "article_enumeration":{"primary_page_kind":"issue-table-of-contents"},
            "pagination":{"primary":{"termination_signal":"no new unique links"},"fallback_listing":{"termination":"no next link"}},
            "metadata_extractors":{key:[] for key in ("title","authors","abstract","doi","landing_url","pdf_url","publication_date","document_type")},
            "content_inclusion_rules":[], "content_exclusion_rules":[],
            "rate_limit":{"max_pages_per_minute":10}, "retry_policy":{},
            "drift_signals":["missing heading"], "validation_anchors":["Nature"],
            "redaction_rules":["no cookies"], "fallbacks":["filtered article listing"],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recipe.yml"
            atomic_json(path, recipe)
            self.assertEqual(validate(path, "nature")["status"], "PASS")


if __name__ == "__main__":
    unittest.main()

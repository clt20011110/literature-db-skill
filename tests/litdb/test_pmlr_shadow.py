from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_corl_shadow_replay import parse_bibtex, parse_volume, resolve_artifact_paths


class PmlrShadowParserTests(unittest.TestCase):
    def test_shadow_paths_follow_litdb_home_and_allow_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env_home = Path(temp) / "env-home"
            explicit_home = Path(temp) / "explicit-home"
            with patch.dict(os.environ, {"LITDB_HOME": str(env_home)}):
                _, env_run, env_manifest, env_samples, env_output = resolve_artifact_paths()
                explicit, run, manifest, samples, output = resolve_artifact_paths(home=explicit_home)

        self.assertEqual(
            env_run,
            env_home.resolve()
            / "runs"
            / "bootstrap-20260820T081741.316646Z/venues/corl/e95b5632-cea7-4f7f-9ee8-80373f5b7d23",
        )
        self.assertEqual(env_manifest, env_home.resolve() / "recipes/corl/pilot_manifest.jsonl")
        self.assertEqual(env_samples, env_home.resolve() / "recipes/corl/pilot_sample_metadata.jsonl")
        self.assertEqual(env_output, env_run / "controller_shadow")
        self.assertEqual(explicit, explicit_home.resolve())
        self.assertEqual(run.parents[4], explicit.resolve())
        self.assertEqual(manifest, explicit / "recipes/corl/pilot_manifest.jsonl")
        self.assertEqual(samples, explicit / "recipes/corl/pilot_sample_metadata.jsonl")
        self.assertEqual(output, run / "controller_shadow")

    def test_volume_parser_preserves_section_and_identity(self) -> None:
        page = """
        <meta name="description" content="Published as Volume 164 by the Proceedings of
        Machine Learning Research on 04 November 2022." />
        <h3>Blue Sky Papers</h3>
        <div class="paper">
          <p class="title">A &amp; B</p>
          <p class="details"><span class="authors">Ada Lovelace,&nbsp;Alan Turing</span>;
          <span class="info">PMLR 164:10-20</span></p>
          <p class="links">[<a href="https://proceedings.mlr.press/v164/example22a.html">abs</a>]
          [<a href="http://proceedings.mlr.press/v164/example22a/example22a.pdf">Download PDF</a>]</p>
        </div>
        """
        rows, evidence = parse_volume(2021, 164, page, "2026-01-01T00:00:00Z")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_item_id"], "pmlr-v164-example22a")
        self.assertEqual(rows[0]["title"], "A & B")
        self.assertEqual(rows[0]["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertEqual(rows[0]["document_type"], "blue-sky-paper")
        self.assertEqual(rows[0]["pmlr_release_date"], "2022-11-04")
        self.assertEqual(evidence["article_count"], 1)

    def test_bibtex_parser_preserves_nested_math(self) -> None:
        bib = r"""
        @InProceedings{pmlr-v305-black25a,
          title = {$\pi_0.5$: a model},
          abstract = {Uses $\pi_{0.5}$ and {nested braces}.},
          url = {https://proceedings.mlr.press/v305/black25a.html}
        }
        """
        entry = parse_bibtex(bib)["pmlr-v305-black25a"]
        self.assertEqual(entry["title"], r"$\pi_0.5$: a model")
        self.assertEqual(entry["abstract"], r"Uses $\pi_{0.5}$ and {nested braces}.")


if __name__ == "__main__":
    unittest.main()

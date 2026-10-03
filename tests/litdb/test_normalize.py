from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from litdb.normalize import (
    math_rendering_equivalent_for_replay,
    normalize_abstract_for_replay,
    normalize_publication_date_for_replay,
    normalize_scalar,
)


class NormalizeTests(unittest.TestCase):
    def test_mathjax_visual_and_accessibility_duplicate(self) -> None:
        pilot = "given Q∈[0,1 ] n×n Q ∈ [ 0 , 1 ] n × n and m m samples"
        replay = "given Q∈[0,1]n×n and m samples"
        self.assertEqual(normalize_abstract_for_replay(pilot), normalize_abstract_for_replay(replay))

    def test_subscript_duplicate(self) -> None:
        self.assertEqual(
            normalize_abstract_for_replay("the l 1 l 1 - and group-LASSO problems"),
            normalize_abstract_for_replay("the l1- and group-LASSO problems"),
        )

    def test_mathjax_compound_log_duplicate(self) -> None:
        pilot = "derive O d nlogK O d n log K upper bounds"
        replay = "derive O d nlogK upper bounds"
        self.assertEqual(normalize_abstract_for_replay(pilot), normalize_abstract_for_replay(replay))

    def test_mathjax_repeated_numeric_superscript(self) -> None:
        pilot = "a sequence of length 10 4 10 4 steps"
        replay = "a sequence of length 104 steps"
        self.assertEqual(normalize_abstract_for_replay(pilot), normalize_abstract_for_replay(replay))

    def test_publication_date_separator_only(self) -> None:
        self.assertEqual(normalize_publication_date_for_replay("2026/06/29"), "2026-06-29")
        self.assertEqual(normalize_publication_date_for_replay("June 2026"), "June 2026")

    def test_guarded_math_surface_equivalence(self) -> None:
        prose = (
            "We study an online learning problem and give a deterministic algorithm with a regret "
            "guarantee under smooth convex losses. The method requires no prior knowledge of the "
            "comparator norm and applies to dynamic regret in stochastic and adversarial models. "
            "Our proof uses a potential argument and a closed form update in every round. "
        )
        pilot = prose + "The bound is 𝑂̃ (𝐻5𝑆𝐴𝑇√)O~(H5SAT)\\tilde{O}(H^5 S\\sqrt{AT})."
        replay = prose + "The bound is $\\tilde{O}(H^5 S\\sqrt{AT})$."
        self.assertTrue(math_rendering_equivalent_for_replay(pilot, replay))

    def test_guarded_math_surface_rejects_prose_drift(self) -> None:
        expected = (
            "We prove a convergence theorem for a stable algorithm under smooth convex losses. "
            "The estimator is unbiased and the guarantee holds uniformly over all comparators. "
            "Our experiments confirm the theorem across several benchmark data sets and settings. "
            "The final regret is 𝑂̃ (𝑇√)O~(T)\\tilde{O}(\\sqrt{T})."
        )
        observed = (
            "We report an unrelated lower bound for a nonconvex heuristic with no stability result. "
            "The estimator is biased and the claim applies only to one fixed comparator. "
            "No experiments or benchmark data are provided in this version of the work. "
            "The final regret is $\\tilde{O}(\\sqrt{T})$."
        )
        self.assertFalse(math_rendering_equivalent_for_replay(expected, observed))

    def test_scalar_normalization_does_not_impute_null(self) -> None:
        self.assertIsNone(normalize_scalar(None))
        self.assertEqual(normalize_scalar(" A\u00a0 B "), "A B")


if __name__ == "__main__":
    unittest.main()

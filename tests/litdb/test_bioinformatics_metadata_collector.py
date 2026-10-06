from __future__ import annotations

import http.client
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools.collect_bioinformatics_metadata import (
    ADVANCE_URL,
    ARCHIVE_URL,
    CAPTURE_SCHEMA,
    SCOPE_DECISION_FINGERPRINT_METHOD,
    SCOPE_DECISION_SCHEMA,
    CaptureHandler,
    _archive_directory_state,
    _archive_issue_map,
    _reviewed_archive_additions,
    _europe_pmc_scope_fallback_type,
    _load_captures,
    _listing_chain_state,
    article_native_id,
    clean_europe_pmc_pdf_url,
    clean_europe_pmc_source_url,
    clean_crossref_request_url,
    clean_listing_page_url,
    _crossref_date,
    _crossref_link_candidates,
    _load_europe_pmc_records,
    _load_crossref_records,
    _match_europe_pmc,
    classify_scope,
    collect,
    normalize_doi,
    sanitize_capture,
)
from tools.litdb.metadata_pipeline import _validate_exclusion


NOW = "2026-10-05T08:00:00Z"
ISSUE_URL = "https://academic.oup.com/bioinformatics/issue/42/1"
PICNIC_URL = "https://academic.oup.com/bioinformatics/article/42/1/btaf647/8362260"
PICNIC_DOI = "10.1093/bioinformatics/btaf647"


def make_capture(page_type: str, source_url: str, data: dict, complete: bool = True) -> dict:
    return {
        "schema_version": CAPTURE_SCHEMA,
        "page_type": page_type,
        "source_url": source_url,
        "observed_at": NOW,
        "complete": complete,
        "data": data,
    }


def article_data(title: str, doi: str, document_type: str | None, authors: list[str] | None = None) -> dict:
    return {
        "citation_title": title,
        "citation_authors": authors or ["Example, Ada", "Researcher, Bo"],
        "citation_doi": doi,
        "citation_pmid": None,
        "citation_journal_title": "Bioinformatics",
        "citation_volume": "42",
        "citation_issue": "1",
        "citation_publication_date": "2026/01/02",
        "citation_pdf_url": None,
        "visible_publication_date": "01 December 2025",
        "abstract": "A tested abstract.",
        "document_type": document_type,
    }


class BioinformaticsCaptureTests(unittest.TestCase):
    def test_script_cli_works_outside_repository(self) -> None:
        script = Path(__file__).resolve().parents[2] / "tools" / "collect_bioinformatics_metadata.py"
        with tempfile.TemporaryDirectory() as working_directory:
            result = subprocess.run(
                [sys.executable, str(script), "collect", "--help"],
                cwd=working_directory, capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--scope-decisions", result.stdout)

    def test_source_urls_drop_only_observed_volume_browse_parameter(self) -> None:
        capture = make_capture("issue", ISSUE_URL + "?browseBy=volume", {
            "year": 2026, "volume": "42", "issue": "1", "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        })
        normalized = sanitize_capture(capture)
        self.assertEqual(normalized["source_url"], ISSUE_URL)
        capture["source_url"] += "?token=private"
        with self.assertRaisesRegex(ValueError, "clean official"):
            sanitize_capture(capture)

    def test_source_identity_uses_numeric_oup_id_and_doi_does_not_replace_it(self) -> None:
        self.assertEqual(article_native_id(PICNIC_URL), "bioinformatics:8362260")
        self.assertEqual(article_native_id("https://academic.oup.com/bioinformatics/advance-article/doi/10.1093/bioinformatics/btaf647"), "bioinformatics:doi/10.1093/bioinformatics/btaf647")
        self.assertEqual(normalize_doi("https://doi.org/10.1093/BIOINFORMATICS/btaf647."), PICNIC_DOI)

    def test_scope_keeps_application_notes_and_reviews_but_defers_generic_journal_article(self) -> None:
        for label in (
            "Original Paper", "ORIGINALS PAPERS", "Applications Notes", "Review",
            "Research Supplement", "research-article", "research article",
            "review-article", "review article", "DISCOVERY NOTE", "Discovery Notes; GENOME ANALYSIS",
        ):
            with self.subTest(label=label):
                self.assertEqual(classify_scope(label, "Research title"), ("include", None))
        self.assertEqual(classify_scope("Journal Article", "PICNIC web server"), ("unresolved", None))
        self.assertEqual(classify_scope("Editorial", "Editorial notes on the journal"), ("exclude", "editorial"))
        self.assertEqual(classify_scope("Correction", "Correction to an article"), ("exclude", "correction"))
        self.assertEqual(classify_scope("Corrections", "A publisher correction"), ("exclude", "correction"))
        self.assertEqual(classify_scope("Front Matter", "Journal front matter"), ("exclude", "front_matter"))
        for label, reason in (
            ("CORRIGENDA", "correction"), ("ERRATA", "erratum"),
            ("RETRACTED ARTICLE", "retraction"), ("AUTHOR INDEX", "front_matter"),
            ("LETTER TO THE EDITOR", "letter_to_editor"),
            ("LETTERS TO THE EDITOR", "letter_to_editor"),
            ("LETTERS TO THE EDITOR; SYSTEMS BIOLOGY", "letter_to_editor"),
            ("GENOME ANALYSIS; LETTER TO THE EDITOR", "letter_to_editor"),
            ("EXPRESSION OF CONCERN", "expression_of_concern"),
        ):
            with self.subTest(observed_nonresearch_section=label):
                self.assertEqual(classify_scope(label, "Publisher-listed item"), ("exclude", reason))
        self.assertEqual(classify_scope("Original Paper", "A novel author index algorithm"), ("include", None))
        self.assertEqual(classify_scope("Conference proceedings", "Author Index"), ("exclude", "front_matter"))
        self.assertEqual(classify_scope("Original Paper", "Letters in DNA sequences"), ("include", None))
        self.assertEqual(classify_scope("Original Paper", "Expression of concern in a text corpus"), ("include", None))
        self.assertEqual(classify_scope("Letters to the editor analysis", "Research title"), ("unresolved", None))
        self.assertEqual(classify_scope("Discovery Note", "Rebuttal to the Letter to the Editor in response to the paper: proper evaluation of alignment-free network comparison methods"), ("exclude", "letter_to_editor"))
        self.assertEqual(classify_scope("Discovery Note", "More challenges for machine-learning protein interactions"), ("include", None))
        self.assertEqual(classify_scope("Journal Article", "A discovery note about genomics"), ("unresolved", None))
        self.assertEqual(classify_scope("Discovery note commentary", "Research title"), ("unresolved", None))
        for label in ("research-article", "research article", "review-article", "review article", "Review"):
            with self.subTest(europe_pmc_type=label):
                self.assertEqual(_europe_pmc_scope_fallback_type([label]), label)
        self.assertEqual(_europe_pmc_scope_fallback_type([" research-article "]), " research-article ")
        self.assertIsNone(_europe_pmc_scope_fallback_type(["Journal Article"]))
        for conflict in (
            "Editorial", "Correction", "Corrigendum", "Erratum", "Retraction", "Retracted Publication", "Letter", "Comment", "News", "Expression of Concern",
        ):
            with self.subTest(conflicting_europe_pmc_type=conflict):
                self.assertIsNone(_europe_pmc_scope_fallback_type(["review-article", conflict]))

    def test_exact_publisher_notice_committee_and_award_profile_titles_override_generic_research_type(self) -> None:
        titles = (
            ("ISMB/ECCB 2017 PROCEEDINGS PAPERS COMMITTEE", "front_matter"),
            ("ISMB 2018 PROCEEDINGS PAPERS COMMITTEE", "front_matter"),
            ("ISMB/ECCB 2019 Proceedings Papers Committee", "front_matter"),
            ("2022 ISCB Overton Prize: Po-Ru Loh", "society_information"),
            ("2023 Outstanding Contributions to ISCB Award: Shoba Ranganathan", "society_information"),
            ("The 2024 ISCB Accomplishments by a Senior Scientist Award—Dr Tandy Warnow", "society_information"),
            ("The 2025 ISCB Innovator Award—Dr Fabian Theis", "society_information"),
            ("The 2026 ISCB Outstanding Service Award—Dr Philip E. Bourne", "society_information"),
            ("2019 Outstanding Contributions to ISCB Awarded to Barb Bryant", "society_information"),
            ("ISCB Honors 2021 Award Recipients Peer Bork, Barbara Engelhardt, Ben Raphael, Teresa Attwood", "society_information"),
            ("Retracted: DeepCRISTL: deep transfer learning to predict CRISPR/Cas9 functional and endogenous on-target editing efficiency", "retraction"),
            ("Publisher’s Note: ‘Expression of Concern: Cleavage-Stage Embryo Segmentation Using SAM-Based Dual Branch Pipeline’", "expression_of_concern"),
        )
        for title, reason in titles:
            for source_type in ("research-article", "Journal Article", "Awards Papers"):
                with self.subTest(title=title, source_type=source_type):
                    self.assertEqual(classify_scope(source_type, title), ("exclude", reason))
        for title in (
            "A committee learning method for protein prediction",
            "Analysis of the ISMB 2018 proceedings papers committee network",
            "An ISCB award-winning method for genome assembly",
            "Retracted sequence alignment using graph models",
            "Publisher note detection in scholarly text corpora",
        ):
            with self.subTest(research_title=title):
                self.assertEqual(classify_scope("Original Paper", title), ("include", None))
        self.assertEqual(classify_scope("Awards Papers", "An efficient sequence alignment algorithm"), ("unresolved", None))
        self.assertEqual(classify_scope("ISCB/ISMB 2022", "ISMB 2022 proceedings"), ("exclude", "front_matter"))
        self.assertEqual(classify_scope(None, "ISMB 2017 proceedings"), ("exclude", "front_matter"))
        self.assertEqual(classify_scope(None, "ISMB/ECCB 2017 proceedings"), ("exclude", "front_matter"))
        self.assertEqual(classify_scope(None, "ISMB 2017 proceedings: selected papers"), ("unresolved", None))
        self.assertEqual(
            classify_scope("Original Paper", "ISMB/ECCB 2017 proceedings: a specific graph method"),
            ("include", None),
        )

    def test_eccb_conference_parent_includes_research_and_preserves_child_exclusions(self) -> None:
        parent = "ECCB 2016: The 15th European Conference on Computational Biology"
        self.assertEqual(classify_scope(parent, "A research paper title"), ("include", None))
        self.assertEqual(classify_scope("ECCB 2016: The 15th European Conference on Computational Biology; EDITORIAL", parent), ("exclude", "editorial"))
        self.assertEqual(classify_scope("ECCB 2016 ORGANIZATION", "ECCB 2016 ORGANIZATION"), ("exclude", "front_matter"))
        self.assertEqual(classify_scope("ECCB 2017: The 16th European Conference on Computational Biology", "A conference paper"), ("include", None))
        self.assertEqual(classify_scope("International Conference on Computational Biology 2017", "A conference paper"), ("unresolved", None))
        for title in (
            "Organization of awards in a comparative genomics workflow",
            "An award-winning method for meeting gene annotation challenges",
        ):
            with self.subTest(ordinary_research_title=title):
                self.assertEqual(classify_scope("Original Paper", title), ("include", None))

        item_specs = [
            ("ECCB 2016: The 15th European Conference on Computational Biology", ["EDITORIAL"], parent),
            ("ECCB 2016 ORGANIZATION", ["ECCB 2016 ORGANIZATION"], parent),
            ("Author Index", ["AUTHOR INDEX"], parent),
            ("Estimating real cell size distribution from cross-section microscopy imaging", ["DATA"], parent),
            ("Organization of awards in a comparative genomics workflow", ["GENES"], parent),
            ("An award-winning method for meeting gene annotation challenges", ["GENES"], parent),
            ("Proceedings of the International Conference on Computational Biology", [], "International Conference on Computational Biology 2017"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            items = []
            for index, (title, categories, section) in enumerate(item_specs, 1):
                items.append({
                    "landing_url": f"https://academic.oup.com/bioinformatics/article/32/17/eccb-case/{9901000 + index}",
                    "title": title,
                    "doi": f"10.1093/bioinformatics/eccbcase{index}",
                    "citation": "Bioinformatics, Volume 32, Issue 17, 2016",
                    "section": section,
                    "categories": categories,
                })
            issue_url = "https://academic.oup.com/bioinformatics/issue/32/17"
            capture = sanitize_capture(make_capture("issue", issue_url, {
                "year": 2016, "volume": "32", "issue": "17", "items": items,
                "pagination": {"next_page_url": None, "terminal_observed": True},
            }))
            captures = [capture]
            for item in items:
                captures.append(sanitize_capture(make_capture(
                    "article",
                    item["landing_url"],
                    article_data(item["title"], item["doi"], "Journal Article"),
                )))
            index_path = browser_root / "index.jsonl"
            index_rows = []
            for index, saved_capture in enumerate(captures):
                relative = f"raw/browser/capture-{index}.json"
                (run_root / relative).write_text(json.dumps(saved_capture), encoding="utf-8")
                index_rows.append({
                    "page_type": saved_capture["page_type"], "source_url": saved_capture["source_url"],
                    "observed_at": saved_capture["observed_at"], "complete": saved_capture["complete"],
                    "file": relative,
                })
            index_path.write_text("".join(json.dumps(row) + "\n" for row in index_rows), encoding="utf-8")
            collect(index_path, run_root, run_root)
            staging = [json.loads(line) for line in (run_root / "metadata_staging.jsonl").read_text(encoding="utf-8").splitlines()]
            exclusions = [json.loads(line) for line in (run_root / "metadata_exclusions.jsonl").read_text(encoding="utf-8").splitlines()]
            unresolved = [json.loads(line) for line in (run_root / "unresolved.jsonl").read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(staging), 3)
        self.assertTrue(all(row["document_type"] == parent for row in staging))
        self.assertEqual(
            {row["document_type"] for row in exclusions},
            {"EDITORIAL", "ECCB 2016 ORGANIZATION", "Journal Article"},
        )
        self.assertEqual({row["title"] for row in exclusions}, {item[0] for item in item_specs[:3]})
        self.assertEqual(
            {row["exclusion_reason_code"] for row in exclusions},
            {"editorial", "front_matter"},
        )
        self.assertIn(
            "research_scope_unresolved",
            {row["kind"] for row in unresolved},
        )
        unknown = next(row for row in unresolved if row["kind"] == "research_scope_unresolved")
        self.assertEqual(unknown["title"], "Proceedings of the International Conference on Computational Biology")

    def test_iscb_messages_use_item_specific_nonresearch_evidence(self) -> None:
        cases = [
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: 2015 ISCB Accomplishment by a Senior Scientist Award: Cyrus Chothia", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: ISCB Ebola award for important future research on the computational biology of Ebola virus", "society_information"),
            ("MESSAGE FROM ISCB", "Message from ISCB: Outstanding contributions to ISCB award", "society_information"),
            ("MESSAGE FROM THE ISCB", "SNP-SIG 2013: the state of the art of genomic variant interpretation", None),
            ("MESSAGE FROM THE ISCB", "Summary of the BioLINK SIG 2013 meeting at ISMB/ECCB 2013", "society_information"),
            ("MESSAGE FROM THE ISCB", "The Bioinformatics Open Source Conference (BOSC) 2013", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: ISMB/ECCB Rebooted: 2015 Brings Major Update to the Conference Program", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: 2016 ISCB Accomplishment by a Senior Scientist Award Given to Søren Brunak", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: 2016 Outstanding Contributions to ISCB Award: Burkhard Rost", "society_information"),
            ("MESSAGE FROM THE ISCB", "ISCB’s initial reaction to New England Journal of Medicine editorial on data sharing", "editorial"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: The 5th ISCB Wikipedia competition: coming to a classroom near you?", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: 2017 ISCB Accomplishment by a Senior Scientist Award Given to Pavel Pevzner", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: 2017 ISCB Overton Prize Awarded to Christoph Bock", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: 2017 Outstanding Contributions to ISCB Award Given to Fran Lewitter", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: 2017 ISCB Innovator Award Given to Aviv Regev", "society_information"),
            ("MESSAGE FROM THE ISCB", "2018 ISCB Innovator Award recognizes M. Madan Babu", "society_information"),
            ("MESSAGE FROM THE ISCB", "2018 ISCB Overton Prize awarded to Cole Trapnell", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: 2018 ISCB Accomplishments by a Senior Scientist Award", "society_information"),
            ("MESSAGE FROM THE ISCB", "Message from the ISCB: 2018 Outstanding Contributions to ISCB Award: Russ Altman", "society_information"),
            ("MESSAGE FROM THE ISCB", "2020 ISCB Innovatory Award: Xiaole Shirley Liu", "society_information"),
            ("MESSAGE FROM THE ISCB", "2020 ISCB accomplishments by a Senior Scientist Award: Steven Salzberg", "society_information"),
            ("MESSAGE FROM THE ISCB", "2020 Outstanding contributions to ISCB award: Judith Blake", "society_information"),
            ("MESSAGE FROM ISCB", "Computational modelling in health and disease: highlights of the 6th annual SysMod meeting", "society_information"),
            ("MESSAGE FROM ISCB; SYSTEMS BIOLOGY", "Advancements in computational modelling of biological systems: seventh annual SysMod meeting", "society_information"),
        ]
        observed = {}
        for section, title, expected_reason in cases:
            with self.subTest(section=section, title=title):
                decision = classify_scope(section, title)
                if expected_reason is None:
                    self.assertEqual(decision, ("unresolved", None))
                else:
                    self.assertEqual(decision, ("exclude", expected_reason))
                    observed[expected_reason] = observed.get(expected_reason, 0) + 1
        self.assertEqual(len(cases), 24)
        self.assertEqual(observed, {"society_information": 22, "editorial": 1})
        self.assertEqual(
            classify_scope("MESSAGE FROM ISCB", "A computational model of gene expression"),
            ("unresolved", None),
        )
        self.assertEqual(
            classify_scope("MESSAGE FROM ISCB", "An award-winning method for protein interaction prediction"),
            ("unresolved", None),
        )
        self.assertEqual(
            classify_scope("Original Paper", "An award-winning method for meeting challenges in genome analysis"),
            ("include", None),
        )
        self.assertEqual(
            classify_scope("Original Paper", "ISCB’s initial reaction to a conference editorial"),
            ("include", None),
        )

    def test_exclusion_adapter_maps_venue_reasons_to_valid_catalog_taxonomy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            sections = [
                ("LETTERS TO THE EDITOR; SYSTEMS BIOLOGY", "Response to a systems-biology letter"),
                ("EXPRESSION OF CONCERN", "Expression of Concern: a published article"),
                ("MESSAGE FROM THE ISCB", "Message from the ISCB: an Outstanding Contributions Award"),
                ("MESSAGE FROM THE ISCB", "ISCB’s initial reaction to a journal editorial on data sharing"),
            ]
            items = []
            for index, (section, title) in enumerate(sections, 1):
                items.append({
                    "landing_url": f"https://academic.oup.com/bioinformatics/article/31/1/{200000 + index}",
                    "title": title,
                    "authors_preview": [],
                    "doi": None,
                    "citation": "Bioinformatics, Volume 31, Issue 1, January 2015",
                    "section": section,
                    "categories": [],
                    "pdf_url": None,
                })
            capture = sanitize_capture(make_capture("issue", ISSUE_URL.replace("42/1", "31/1"), {
                "year": 2015, "volume": "31", "issue": "1", "issue_state": None, "items": items,
                "pagination": {"next_page_url": None, "terminal_observed": True},
            }))
            capture_path = browser_root / "issue.json"
            capture_path.write_text(json.dumps(capture), encoding="utf-8")
            index_path = browser_root / "index.jsonl"
            index_path.write_text(json.dumps({
                "page_type": "issue", "source_url": capture["source_url"],
                "observed_at": capture["observed_at"], "complete": True,
                "file": "raw/browser/issue.json",
            }) + "\n", encoding="utf-8")

            collect(index_path, run_root, run_root)
            exclusions = [
                json.loads(line)
                for line in (run_root / "metadata_exclusions.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(len(exclusions), 4)
        by_type = {row["document_type"]: row for row in exclusions}
        expected = {
            "Response to a systems-biology letter": ("letter_to_editor", "non_research_content"),
            "Expression of Concern: a published article": ("expression_of_concern", "non_research_content"),
            "Message from the ISCB: an Outstanding Contributions Award": ("society_information", "society_information"),
            "ISCB’s initial reaction to a journal editorial on data sharing": ("editorial", "editorial"),
        }
        for row in exclusions:
            source_reason, catalog_reason = expected[row["title"]]
            self.assertEqual(row["exclusion_reason_code"], catalog_reason)
            self.assertEqual(row["exclusion_evidence"]["source_exclusion_reason_code"], source_reason)
            self.assertEqual(row["exclusion_evidence"]["catalog_exclusion_reason_code"], catalog_reason)
            self.assertEqual(row["field_provenance"]["exclusion_reason_code"]["method"], "official_type_or_title_scope_rule")
            self.assertIn(source_reason, row["exclusion_reason_detail"])
            self.assertEqual(
                _validate_exclusion(
                    row,
                    1,
                    "bioinformatics",
                    set(),
                    {"academic.oup.com": ["/bioinformatics/"]},
                ),
                [],
            )

    def _collect_single_europe_pmc_type_case(
        self,
        *,
        oup_section: str,
        publication_types: list[str],
        record_doi: str = "10.1093/bioinformatics/btu769",
        oup_doi: str = "10.1093/bioinformatics/btu769",
        supplement_abstract: str | None = "A substantive review of structural bioinformatics and computational biophysics.",
        complete_detail_without_abstract: bool = False,
    ) -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            landing_url = "https://academic.oup.com/bioinformatics/article/31/1/146/2366253"
            issue_url = "https://academic.oup.com/bioinformatics/issue/31/1"
            item = {
                "landing_url": landing_url,
                "title": "Achievements and challenges in structural bioinformatics and computational biophysics",
                "authors_preview": ["Ilan Samish and others"],
                "doi": oup_doi,
                "citation": "Bioinformatics, Volume 31, Issue 1, January 2015, Pages 146–150",
                "section": oup_section,
                "categories": [],
                "pdf_url": None,
            }
            capture = sanitize_capture(make_capture("issue", issue_url, {
                "year": 2015, "volume": "31", "issue": "1", "issue_state": None, "items": [item],
                "pagination": {"next_page_url": None, "terminal_observed": True},
            }))
            captures = [capture]
            if complete_detail_without_abstract:
                detail_data = article_data(item["title"], oup_doi, "Journal Article")
                detail_data["abstract"] = None
                captures.append(sanitize_capture(make_capture("article", landing_url, detail_data)))
            index_path = browser_root / "index.jsonl"
            index_rows = []
            for index, saved_capture in enumerate(captures):
                capture_relpath = f"raw/browser/capture-{index}.json"
                (run_root / capture_relpath).write_text(json.dumps(saved_capture), encoding="utf-8")
                index_rows.append({
                    "page_type": saved_capture["page_type"],
                    "source_url": saved_capture["source_url"],
                    "observed_at": saved_capture["observed_at"],
                    "complete": saved_capture["complete"],
                    "file": capture_relpath,
                })
            index_path.write_text("".join(json.dumps(row) + "\n" for row in index_rows), encoding="utf-8")
            supplement_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:10.1093/bioinformatics/btu769&format=json"
            supplement_record = {
                "source_url": supplement_url,
                "observed_at": "2026-10-05T08:37:08Z",
                "doi": record_doi,
                "pmid": "25664409",
                "journal": {"title": "Bioinformatics", "eissn": "1367-4811", "issn_values": ["1367-4811"]},
                "matches_target_eissn": True,
                "within_collection_scope": True,
                "year_window_margin_only": False,
                "issue_year": 2015,
                "volume": "31",
                "issue": "1",
                "title": "Achievements and challenges in structural bioinformatics and computational biophysics.",
                "authors": [{"order": 1, "name": "Ilan Samish"}],
                "abstract": supplement_abstract,
                "publication_types": publication_types,
                "dates_as_supplied": {},
                "pdf_links_as_supplied": [],
            }
            supplement_path = run_root / "supplement" / "normalized_records.jsonl"
            supplement_path.parent.mkdir()
            supplement_path.write_text(json.dumps(supplement_record) + "\n", encoding="utf-8")

            collect(index_path, run_root, run_root, supplement_records_path=supplement_path)
            read_output = lambda name: [
                json.loads(line) for line in (run_root / name).read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            return {
                "staging": read_output("metadata_staging.jsonl"),
                "exclusions": read_output("metadata_exclusions.jsonl"),
                "unresolved": read_output("unresolved.jsonl"),
                "source_occurrences": read_output("source_occurrences.jsonl"),
                "supplement_join": read_output("supplement_join_report.jsonl"),
            }

    def _collect_reviewed_scope_case(
        self,
        *,
        decision: str = "include_research",
        reason_code: str | None = None,
        reason: str = "The exact-identity abstract describes an evaluated computational contribution.",
        title: str = "A reviewed computational research contribution",
        section: str | None = None,
        publication_types: list[str] | None = None,
        abstract: str | None = "We develop a computational method and evaluate it on biological data.",
        receipt_mutator: Any = None,
        duplicate_decision: bool = False,
        review_source_file: str = "scope-reviews/test-review.json",
        review_source_state: str = "valid",
    ) -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            doi = "10.1093/bioinformatics/scope-review-1"
            source_id = "bioinformatics:990001"
            landing_url = "https://academic.oup.com/bioinformatics/article/42/1/example/990001"
            issue_url = ISSUE_URL
            item = {
                "landing_url": landing_url,
                "title": title,
                "doi": doi,
                "section": section,
                "categories": [],
            }
            issue_capture = sanitize_capture(make_capture("issue", issue_url, {
                "year": 2026, "volume": "42", "issue": "1", "items": [item],
                "pagination": {"next_page_url": None, "terminal_observed": True},
            }))
            capture_path = browser_root / "issue.json"
            capture_path.write_text(json.dumps(issue_capture), encoding="utf-8")
            index_path = browser_root / "index.jsonl"
            index_path.write_text(json.dumps({
                "page_type": "issue", "source_url": issue_url,
                "observed_at": issue_capture["observed_at"], "complete": True,
                "file": "raw/browser/issue.json",
            }) + "\n", encoding="utf-8")

            publication_types = publication_types or ["Journal Article"]
            epmc_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:{doi}&format=json"
            epmc_record = {
                "source_url": epmc_url,
                "observed_at": NOW,
                "doi": doi,
                "pmid": "39990001",
                "journal": {"title": "Bioinformatics", "eissn": "1367-4811", "issn_values": ["1367-4811"]},
                "matches_target_eissn": True,
                "within_collection_scope": True,
                "year_window_margin_only": False,
                "issue_year": 2026,
                "volume": "42",
                "issue": "1",
                "title": title,
                "authors": [{"order": 1, "name": "Example, Ada"}],
                "abstract": abstract,
                "publication_types": publication_types,
                "dates_as_supplied": {},
                "pdf_links_as_supplied": [],
            }
            supplement_path = run_root / "supplement" / "normalized_records.jsonl"
            supplement_path.parent.mkdir()
            supplement_path.write_text(json.dumps(epmc_record, ensure_ascii=False) + "\n", encoding="utf-8")
            abstract_sha256 = hashlib.sha256(abstract.encode("utf-8")).hexdigest() if abstract else None
            row = {
                "source_native_id": source_id,
                "doi": doi,
                "title": title,
                "landing_url": landing_url,
                "europe_pmc": {
                    "source_url": epmc_url,
                    "observed_at": NOW,
                    "abstract_sha256": abstract_sha256,
                    "publication_types": publication_types,
                },
                "decision": decision,
                "reason_code": reason_code or (
                    "substantive_abstract_tool_or_study" if decision == "include_research" else "front_matter"
                ),
                "reason": reason,
            }
            if receipt_mutator:
                receipt_mutator(row)
            decisions = [row, dict(row)] if duplicate_decision else [row]
            review_source_bytes = b'{"schema_version":"scope-review-fixture-v1"}\n'
            source_parts = review_source_file.split("/")
            safe_relative_source = (
                not review_source_file.startswith("/")
                and "\\" not in review_source_file
                and not any(part in {"", ".", ".."} for part in source_parts)
            )
            if safe_relative_source and review_source_state != "missing":
                source_path = run_root.joinpath(*source_parts)
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_bytes(
                    b'{"changed":true}\n' if review_source_state == "drifted" else review_source_bytes
                )
            scope_receipt = {
                "schema_version": SCOPE_DECISION_SCHEMA,
                "review_source": {
                    "file": review_source_file,
                    "sha256": hashlib.sha256(review_source_bytes).hexdigest(),
                },
                "reviewed_at_utc": NOW,
                "abstract_fingerprint_method": SCOPE_DECISION_FINGERPRINT_METHOD,
                "decisions": decisions,
            }
            scope_decisions_path = run_root / "scope-decisions.json"
            scope_decisions_path.write_text(json.dumps(scope_receipt, ensure_ascii=False), encoding="utf-8")

            stats = collect(
                index_path,
                run_root,
                run_root,
                supplement_records_path=supplement_path,
                scope_decisions_path=scope_decisions_path,
            )
            read_output = lambda name: [
                json.loads(line)
                for line in (run_root / name).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            return {
                "stats": stats,
                "staging": read_output("metadata_staging.jsonl"),
                "exclusions": read_output("metadata_exclusions.jsonl"),
                "unresolved": read_output("unresolved.jsonl"),
                "scope_decision_report": read_output("scope_decision_report.jsonl"),
            }

    def _collect_publisher_reviewed_scope_case(
        self,
        *,
        title: str = "SNP-SIG 2013: the state of the art of genomic variant interpretation",
        doi: str = "10.1093/bioinformatics/btu415",
        landing_url: str = "https://academic.oup.com/bioinformatics/article/31/3/449/2364825",
        section: str | None = "MESSAGE FROM THE ISCB",
        publication_types: list[str] | None = None,
        article_complete: bool = True,
        decision: str = "exclude_nonresearch",
        reason_code: str = "society_information",
        reason: str | None = None,
        abstract_text: str | None = None,
        publisher_abstract_sha256_override: str | None = None,
        capture_override: str | None = None,
        row_mutator: Any = None,
        receipt_mutator: Any = None,
    ) -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            source_id = article_native_id(landing_url)
            issue_parts = landing_url.split("/article/")[1].split("/")
            volume, issue = issue_parts[0], issue_parts[1]
            year = 2015 if volume == "31" else 2017 if volume == "33" else 2025 if volume == "41" else 2026
            issue_url = f"https://academic.oup.com/bioinformatics/issue/{volume}/{issue}"
            issue_capture = sanitize_capture(make_capture("issue", issue_url, {
                "year": year, "volume": volume, "issue": issue,
                "items": [{
                    "landing_url": landing_url,
                    "title": title,
                    "doi": doi,
                    "citation": f"Bioinformatics, Volume {volume}, Issue {issue}, {year}",
                    "section": section,
                    "categories": [],
                }],
                "pagination": {"next_page_url": None, "terminal_observed": True},
            }))
            article_capture = sanitize_capture(make_capture("article", landing_url, {
                **article_data(title, doi, None, authors=["Example, Ada"]),
                "abstract": (
                    abstract_text if abstract_text is not None
                    else "A visible publisher article abstract." if article_complete else None
                ),
            }, complete=article_complete))

            captures: list[tuple[str, dict[str, Any]]] = []
            for value in (issue_capture, article_capture):
                serialized = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                capture_sha = hashlib.sha256(serialized).hexdigest()
                relative_file = f"raw/browser/{capture_sha}.json"
                (run_root / relative_file).write_bytes(serialized)
                captures.append((relative_file, {
                    "capture_id": capture_sha,
                    "file": relative_file,
                    "page_type": value["page_type"],
                    "source_url": value["source_url"],
                    "observed_at": value["observed_at"],
                    "complete": value["complete"],
                }))
            index_path = browser_root / "index.jsonl"
            index_path.write_text("".join(json.dumps(row) + "\n" for _, row in captures), encoding="utf-8")
            issue_file, _issue_index = captures[0]
            article_file, article_index = captures[1]
            capture_file = issue_file if capture_override == "issue" else capture_override or article_file
            capture_sha = next(row["capture_id"] for file, row in captures if file == capture_file)
            captured_abstract = article_capture["data"].get("abstract")
            if publisher_abstract_sha256_override is not None:
                abstract_sha256 = publisher_abstract_sha256_override
            elif isinstance(captured_abstract, str):
                abstract_sha256 = hashlib.sha256(captured_abstract.encode("utf-8")).hexdigest()
            else:
                abstract_sha256 = None
            review_reason = reason or (
                "The reviewed publisher abstract describes a substantive computational study."
                if decision == "include_research"
                else "The observed publisher body is a meeting or community report rather than a research article."
            )
            decision_row = {
                "source_native_id": source_id,
                "doi": doi,
                "title": title,
                "landing_url": landing_url,
                "publisher_capture": {
                    "file": capture_file,
                    "sha256": capture_sha,
                    "source_url": landing_url,
                    "observed_at": article_index["observed_at"],
                },
                "decision": decision,
                "reason_code": reason_code,
                "reason": review_reason,
            }
            if decision == "include_research":
                decision_row["publisher_capture"]["abstract_sha256"] = abstract_sha256
            if row_mutator:
                row_mutator(decision_row)
            review_source_row = {
                "source_native_id": source_id,
                "doi": doi,
                "title": title,
                "landing_url": landing_url,
                "publisher_capture_file": decision_row["publisher_capture"]["file"],
                "publisher_capture_sha256": decision_row["publisher_capture"]["sha256"],
                "publisher_capture_observed_at": decision_row["publisher_capture"]["observed_at"],
                **({"publisher_abstract_sha256": abstract_sha256} if decision == "include_research" else {}),
                "decision": decision_row["decision"],
                "reason_code": decision_row["reason_code"],
                "reason": decision_row["reason"],
                "publisher_observation": {
                    "source_url": landing_url,
                    "source_capture_id": decision_row["publisher_capture"]["sha256"],
                },
            }
            source_path = run_root / "scope-reviews" / "publisher-source.json"
            source_path.parent.mkdir(parents=True)
            source_bytes = (json.dumps({"publisher_reviews": [review_source_row]}, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            source_path.write_bytes(source_bytes)
            if receipt_mutator:
                receipt_mutator(decision_row)
            scope_receipt = {
                "schema_version": SCOPE_DECISION_SCHEMA,
                "review_source": {
                    "file": "scope-reviews/publisher-source.json",
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                },
                "reviewed_at_utc": NOW,
                "abstract_fingerprint_method": SCOPE_DECISION_FINGERPRINT_METHOD,
                "decisions": [decision_row],
            }
            scope_decisions_path = run_root / "scope-decisions.json"
            scope_decisions_path.write_text(json.dumps(scope_receipt, ensure_ascii=False), encoding="utf-8")

            supplement_path = None
            if publication_types is not None:
                epmc_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:{doi}&format=json"
                epmc_record = {
                    "source_url": epmc_url,
                    "observed_at": NOW,
                    "doi": doi,
                    "pmid": "39990002",
                    "journal": {"title": "Bioinformatics", "eissn": "1367-4811", "issn_values": ["1367-4811"]},
                    "matches_target_eissn": True,
                    "within_collection_scope": True,
                    "year_window_margin_only": False,
                    "issue_year": year,
                    "volume": volume,
                    "issue": issue,
                    "title": title,
                    "authors": [],
                    "abstract": None,
                    "publication_types": publication_types,
                    "dates_as_supplied": {},
                    "pdf_links_as_supplied": [],
                }
                supplement_path = run_root / "supplement" / "normalized_records.jsonl"
                supplement_path.parent.mkdir()
                supplement_path.write_text(json.dumps(epmc_record) + "\n", encoding="utf-8")

            stats = collect(
                index_path,
                run_root,
                run_root,
                supplement_records_path=supplement_path,
                scope_decisions_path=scope_decisions_path,
            )
            read_output = lambda name: [
                json.loads(line) for line in (run_root / name).read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            return {
                "stats": stats,
                "staging": read_output("metadata_staging.jsonl"),
                "exclusions": read_output("metadata_exclusions.jsonl"),
                "unresolved": read_output("unresolved.jsonl"),
                "scope_decision_report": read_output("scope_decision_report.jsonl"),
                "issue_capture_file": issue_file,
                "article_capture_file": article_file,
                "article_capture_sha256": article_index["capture_id"],
                "article_capture_observed_at": article_index["observed_at"],
                "article_abstract_sha256": hashlib.sha256(
                    article_capture["data"].get("abstract", "").encode("utf-8")
                ).hexdigest() if isinstance(article_capture["data"].get("abstract"), str) else None,
                "review_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            }

    def test_europe_pmc_publication_type_fallback_is_exact_provenanced_and_oup_subordinate(self) -> None:
        real_btu769 = self._collect_single_europe_pmc_type_case(
            oup_section="MESSAGE FROM ISCB",
            publication_types=["research-article", "Journal Article"],
        )
        self.assertEqual(len(real_btu769["staging"]), 1)
        staged = real_btu769["staging"][0]
        self.assertEqual(staged["document_type"], "research-article")
        self.assertEqual(staged["europe_pmc_publication_types_as_supplied"], ["research-article", "Journal Article"])
        self.assertEqual(staged["field_provenance"]["document_type"], {
            "source_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:10.1093/bioinformatics/btu769&format=json",
            "observed_at": "2026-10-05T08:37:08Z",
            "method": "Europe_PMC_publication_types_scope_fallback",
        })
        self.assertEqual(staged["field_provenance"]["europe_pmc_publication_types"]["method"], "Europe_PMC_publication_types_as_supplied")
        self.assertEqual(real_btu769["source_occurrences"][0]["section"], "MESSAGE FROM ISCB")
        join = real_btu769["supplement_join"][0]
        self.assertEqual(join["europe_pmc_publication_types_as_supplied"], ["research-article", "Journal Article"])
        self.assertEqual(join["europe_pmc_publication_types_provenance"]["observed_at"], "2026-10-05T08:37:08Z")

        generic_only = self._collect_single_europe_pmc_type_case(
            oup_section="MESSAGE FROM ISCB",
            publication_types=["Journal Article"],
        )
        self.assertEqual(generic_only["staging"], [])
        self.assertIn("research_scope_unresolved", {row["kind"] for row in generic_only["unresolved"]})

        conflict = self._collect_single_europe_pmc_type_case(
            oup_section="MESSAGE FROM ISCB",
            publication_types=["review-article", "Journal Article", "Editorial"],
        )
        self.assertEqual(conflict["staging"], [])
        conflict_row = next(row for row in conflict["unresolved"] if row["kind"] == "research_scope_unresolved")
        self.assertEqual(conflict_row["document_type"], "MESSAGE FROM ISCB")

        unmatched = self._collect_single_europe_pmc_type_case(
            oup_section="MESSAGE FROM ISCB",
            publication_types=["research-article"],
            record_doi="10.1093/bioinformatics/other-item",
        )
        self.assertEqual(unmatched["staging"], [])
        self.assertFalse(unmatched["supplement_join"][0]["matched"])
        self.assertIn("research_scope_unresolved", {row["kind"] for row in unmatched["unresolved"]})

        oup_correction = self._collect_single_europe_pmc_type_case(
            oup_section="Corrections",
            publication_types=["research-article"],
        )
        self.assertEqual(oup_correction["staging"], [])
        self.assertEqual(len(oup_correction["exclusions"]), 1)
        self.assertEqual(oup_correction["exclusions"][0]["document_type"], "Corrections")
        self.assertEqual(
            oup_correction["exclusions"][0]["field_provenance"]["document_type"]["method"],
            "official_article_type_or_issue_section",
        )
        self.assertEqual(
            oup_correction["exclusions"][0]["europe_pmc_publication_types_as_supplied"],
            ["research-article"],
        )

        oup_include_wins = self._collect_single_europe_pmc_type_case(
            oup_section="Original Paper",
            publication_types=["research-article", "Editorial"],
        )
        self.assertEqual(len(oup_include_wins["staging"]), 1)
        self.assertEqual(oup_include_wins["staging"][0]["document_type"], "Original Paper")
        self.assertEqual(
            oup_include_wins["staging"][0]["field_provenance"]["document_type"]["method"],
            "official_article_type_or_issue_section",
        )

    def test_reviewed_scope_receipt_can_include_generic_type_with_independent_provenance(self) -> None:
        raw_abstract = "We develop a tested method.\n\nWe evaluate  it on biological data."
        supplied_types = ["Research Support, Non-U.S. Gov't", "Journal Article"]
        result = self._collect_reviewed_scope_case(abstract=raw_abstract, publication_types=supplied_types)
        self.assertEqual(len(result["staging"]), 1)
        staged = result["staging"][0]
        self.assertEqual(staged["document_type"], "Journal Article")
        self.assertEqual(staged["europe_pmc_publication_types_as_supplied"], supplied_types)
        self.assertEqual(
            staged["field_provenance"]["document_type"]["method"],
            "Europe_PMC_article_publication_type_as_supplied",
        )
        self.assertEqual(
            staged["field_provenance"]["inclusion_decision"]["method"],
            "reviewed_exact_identity_title_and_abstract_scope",
        )
        self.assertEqual(
            staged["field_provenance"]["inclusion_decision"]["source_url"],
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:10.1093/bioinformatics/scope-review-1&format=json",
        )
        self.assertEqual(staged["scope_decision_evidence"]["decision"], "include_research")
        self.assertEqual(staged["scope_decision_evidence"]["reason_code"], "substantive_abstract_tool_or_study")
        receipt_sha256 = result["scope_decision_report"][0]["review_receipt_sha256"]
        self.assertRegex(receipt_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(staged["scope_decision_evidence"]["review_receipt_sha256"], receipt_sha256)
        self.assertEqual(staged["field_provenance"]["inclusion_decision"]["review_receipt_sha256"], receipt_sha256)
        self.assertEqual(
            staged["scope_decision_evidence"]["europe_pmc"]["abstract_sha256"],
            hashlib.sha256(raw_abstract.encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(
            staged["scope_decision_evidence"]["europe_pmc"]["abstract_sha256"],
            hashlib.sha256(" ".join(raw_abstract.split()).encode("utf-8")).hexdigest(),
        )
        self.assertEqual(result["scope_decision_report"][0]["status"], "applied")
        self.assertFalse(any(row["kind"] == "research_scope_unresolved" for row in result["unresolved"]))

    def test_reviewed_scope_receipt_excludes_individual_front_matter_records(self) -> None:
        organization = self._collect_reviewed_scope_case(
            decision="exclude_nonresearch",
            reason="The exact OUP title identifies conference organization information; the matched API record has no abstract.",
            title="ECCB 2018 Organization",
            publication_types=["Journal Article"],
            abstract=None,
        )
        introduction = self._collect_reviewed_scope_case(
            decision="exclude_nonresearch",
            reason="The exact OUP conference-introduction title and supplied introductory type identify front matter; no abstract is supplied.",
            title="ECCB 2018: The 17th European Conference on Computational Biology",
            publication_types=["Introductory Journal Article"],
            abstract=None,
        )
        for result, expected_type in ((organization, "Journal Article"), (introduction, "Introductory Journal Article")):
            with self.subTest(document_type=expected_type):
                self.assertEqual(result["staging"], [])
                self.assertEqual(len(result["exclusions"]), 1)
                excluded = result["exclusions"][0]
                self.assertEqual(excluded["exclusion_reason_code"], "front_matter")
                self.assertEqual(excluded["document_type"], expected_type)
                self.assertEqual(excluded["exclusion_evidence"]["classification_method"], "reviewed_exact_identity_title_and_publication_type_scope")
                self.assertEqual(excluded["scope_decision_evidence"]["decision"], "exclude_nonresearch")
                self.assertIsNone(excluded["scope_decision_evidence"]["europe_pmc"]["abstract_sha256"])
                self.assertEqual(
                    excluded["field_provenance"]["inclusion_decision"]["review_receipt_sha256"],
                    excluded["scope_decision_evidence"]["review_receipt_sha256"],
                )
                self.assertEqual(result["scope_decision_report"][0]["status"], "applied")
                self.assertEqual(
                    _validate_exclusion(
                        excluded,
                        1,
                        "bioinformatics",
                        set(),
                        {
                            "academic.oup.com": ["/bioinformatics/"],
                            "www.ebi.ac.uk": ["/europepmc/webservices/rest/"],
                        },
                    ),
                    [],
                )

    def test_reviewed_scope_receipt_identity_and_europe_pmc_drift_fail_closed(self) -> None:
        mutations = {
            "source ID": lambda row: row.update(source_native_id="bioinformatics:990002"),
            "unmatched but well-formed source ID": lambda row: row.update(
                source_native_id="bioinformatics:990002",
                landing_url="https://academic.oup.com/bioinformatics/article/42/1/example/990002",
            ),
            "DOI": lambda row: row.update(doi="10.1093/bioinformatics/scope-review-2"),
            "OUP title": lambda row: row.update(title="A different title"),
            "OUP landing URL": lambda row: row.update(landing_url="https://academic.oup.com/bioinformatics/article/42/1/other/990001"),
            "Europe PMC source URL": lambda row: row["europe_pmc"].update(
                source_url="https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:different&format=json"
            ),
            "Europe PMC observed time": lambda row: row["europe_pmc"].update(observed_at="2026-10-05T09:00:00Z"),
            "abstract hash": lambda row: row["europe_pmc"].update(abstract_sha256="0" * 64),
            "publication types": lambda row: row["europe_pmc"].update(publication_types=["review-article"]),
            "illegal reason": lambda row: row.update(reason_code="not_a_shared_reason"),
            "malformed decision": lambda row: row.update(decision=[]),
        }
        for field, mutate in mutations.items():
            with self.subTest(mismatched_binding=field):
                result = self._collect_reviewed_scope_case(receipt_mutator=mutate)
                self.assertEqual(result["staging"], [])
                self.assertEqual(result["exclusions"], [])
                self.assertEqual(result["scope_decision_report"][0]["status"], "invalid")
                self.assertEqual(result["stats"]["scope_decisions_invalid"], 1)
                self.assertIn("scope_decision_invalid", {row["kind"] for row in result["unresolved"]})

        duplicate = self._collect_reviewed_scope_case(duplicate_decision=True)
        self.assertEqual(duplicate["staging"], [])
        self.assertEqual([row["status"] for row in duplicate["scope_decision_report"]], ["invalid", "invalid"])
        self.assertEqual(duplicate["stats"]["scope_decisions_invalid"], 2)

        abstract_missing = self._collect_reviewed_scope_case(abstract=None)
        self.assertEqual(abstract_missing["staging"], [])
        self.assertEqual(abstract_missing["exclusions"], [])
        self.assertEqual(abstract_missing["scope_decision_report"][0]["status"], "invalid")
        self.assertIn("scope_decision_invalid", {row["kind"] for row in abstract_missing["unresolved"]})

        with self.assertRaisesRegex(ValueError, "evidence-root-relative source file"):
            self._collect_reviewed_scope_case(review_source_file="/Users/example/private-review.json")

    def test_reviewed_scope_source_manifest_must_exist_match_and_stay_under_evidence_root(self) -> None:
        for state, expected in (("missing", "missing under evidence_root"), ("drifted", "SHA-256 mismatch")):
            with self.subTest(source_state=state), self.assertRaisesRegex(ValueError, expected):
                self._collect_reviewed_scope_case(review_source_state=state)
        with self.assertRaisesRegex(ValueError, "evidence-root-relative source file"):
            self._collect_reviewed_scope_case(review_source_file="../outside-review.json")

    def test_reviewed_scope_decision_cannot_override_source_resolved_scope(self) -> None:
        publisher_include = self._collect_reviewed_scope_case(
            decision="exclude_nonresearch",
            reason_code="editorial",
            section="Original Paper",
        )
        self.assertEqual(len(publisher_include["staging"]), 1)
        self.assertEqual(publisher_include["staging"][0]["document_type"], "Original Paper")
        self.assertEqual(publisher_include["scope_decision_report"][0]["status"], "already_resolved_by_source")
        self.assertEqual(publisher_include["scope_decision_report"][0]["source_scope_decision"], "include")

        publisher_exclude = self._collect_reviewed_scope_case(
            decision="include_research",
            title="A publisher editorial item",
            section="Editorial",
        )
        self.assertEqual(publisher_exclude["staging"], [])
        self.assertEqual(publisher_exclude["exclusions"][0]["exclusion_reason_code"], "editorial")
        self.assertEqual(publisher_exclude["scope_decision_report"][0]["status"], "already_resolved_by_source")
        self.assertEqual(publisher_exclude["scope_decision_report"][0]["source_scope_decision"], "exclude")

        epmc_explicit = self._collect_reviewed_scope_case(
            decision="exclude_nonresearch",
            reason_code="editorial",
            publication_types=["research-article"],
        )
        self.assertEqual(len(epmc_explicit["staging"]), 1)
        self.assertEqual(epmc_explicit["staging"][0]["document_type"], "research-article")
        self.assertEqual(epmc_explicit["scope_decision_report"][0]["status"], "already_resolved_by_source")

        epmc_nonresearch = self._collect_reviewed_scope_case(
            decision="include_research",
            publication_types=["Editorial"],
        )
        self.assertEqual(epmc_nonresearch["staging"], [])
        self.assertEqual(epmc_nonresearch["exclusions"], [])
        self.assertIn("research_scope_unresolved", {row["kind"] for row in epmc_nonresearch["unresolved"]})
        self.assertEqual(epmc_nonresearch["scope_decision_report"][0]["status"], "already_resolved_by_source")
        self.assertEqual(
            epmc_nonresearch["scope_decision_report"][0]["scope_review_not_applied_reason"],
            "explicit Europe PMC nonresearch publication type",
        )

    def test_publisher_article_content_review_excludes_without_europe_pmc_or_overriding_oup_type(self) -> None:
        no_epmc = self._collect_publisher_reviewed_scope_case()
        self.assertEqual(no_epmc["staging"], [])
        self.assertEqual(len(no_epmc["exclusions"]), 1)
        excluded = no_epmc["exclusions"][0]
        self.assertEqual(excluded["exclusion_reason_code"], "society_information")
        self.assertEqual(excluded["document_type"], "MESSAGE FROM THE ISCB")
        self.assertEqual(
            excluded["scope_decision_evidence"]["method"],
            "reviewed_exact_identity_publisher_article_content_scope",
        )
        self.assertNotIn("abstract_fingerprint_method", excluded["scope_decision_evidence"])
        capture_binding = excluded["scope_decision_evidence"]["publisher_capture"]
        self.assertEqual(capture_binding["source_url"], excluded["landing_url"])
        self.assertEqual(capture_binding["file"], no_epmc["article_capture_file"])
        self.assertEqual(capture_binding["sha256"], no_epmc["article_capture_sha256"])
        self.assertEqual(capture_binding["observed_at"], no_epmc["article_capture_observed_at"])
        self.assertEqual(
            excluded["scope_decision_evidence"]["review_source"]["sha256"],
            no_epmc["review_source_sha256"],
        )
        self.assertEqual(
            excluded["field_provenance"]["inclusion_decision"]["source_url"],
            excluded["landing_url"],
        )
        self.assertEqual(no_epmc["scope_decision_report"][0]["evidence_basis"], "publisher_article_capture")
        self.assertEqual(no_epmc["scope_decision_report"][0]["status"], "applied")
        self.assertEqual(
            _validate_exclusion(
                excluded,
                1,
                "bioinformatics",
                set(),
                {"academic.oup.com": ["/bioinformatics/"]},
            ),
            [],
        )

        generic_research_api = self._collect_publisher_reviewed_scope_case(
            title=(
                "The International Society for Computational Biology and WikiProject Computational Biology: "
                "celebrating 10 years of collaboration towards open access"
            ),
            doi="10.1093/bioinformatics/btx388",
            landing_url="https://academic.oup.com/bioinformatics/article/33/15/2429/3870481",
            section="MESSAGE FROM THE ISCB",
            publication_types=["research-article", "Journal Article"],
        )
        self.assertEqual(generic_research_api["staging"], [])
        self.assertEqual(len(generic_research_api["exclusions"]), 1)
        api_exclusion = generic_research_api["exclusions"][0]
        self.assertEqual(api_exclusion["exclusion_reason_code"], "society_information")
        self.assertEqual(api_exclusion["document_type"], "research-article")
        self.assertNotEqual(api_exclusion["document_type"], "society_information")
        self.assertEqual(
            api_exclusion["field_provenance"]["document_type"]["method"],
            "Europe_PMC_publication_types_scope_fallback",
        )
        self.assertEqual(api_exclusion["scope_decision_evidence"]["publisher_capture"]["source_url"], api_exclusion["landing_url"])
        self.assertEqual(generic_research_api["scope_decision_report"][0]["status"], "applied")

        explicit_oup_research = self._collect_publisher_reviewed_scope_case(
            section="Original Paper",
        )
        self.assertEqual(len(explicit_oup_research["staging"]), 1)
        self.assertEqual(explicit_oup_research["exclusions"], [])
        self.assertEqual(
            explicit_oup_research["scope_decision_report"][0]["status"],
            "already_resolved_by_source",
        )
        self.assertEqual(
            explicit_oup_research["scope_decision_report"][0]["source_scope_decision"],
            "include",
        )

        explicit_api_nonresearch = self._collect_publisher_reviewed_scope_case(
            publication_types=["Editorial", "Journal Article"],
        )
        self.assertEqual(explicit_api_nonresearch["exclusions"], [])
        self.assertEqual(
            explicit_api_nonresearch["scope_decision_report"][0]["status"],
            "already_resolved_by_source",
        )
        self.assertEqual(
            explicit_api_nonresearch["scope_decision_report"][0]["scope_review_not_applied_reason"],
            "explicit Europe PMC nonresearch publication type",
        )
        self.assertIn(
            "research_scope_unresolved",
            {row["kind"] for row in explicit_api_nonresearch["unresolved"]},
        )

    def test_reviewed_publisher_abstract_inclusion_is_exact_and_provenanced(self) -> None:
        title = "Efficient 3D kernels for molecular property prediction"
        doi = "10.1093/bioinformatics/btaf208"
        landing_url = "https://academic.oup.com/bioinformatics/article/41/Supplement_1/i58/8199352"
        included = self._collect_publisher_reviewed_scope_case(
            title=title,
            doi=doi,
            landing_url=landing_url,
            section="JOURNAL ARTICLE",
            decision="include_research",
            reason_code="substantive_abstract_tool_or_study",
        )
        self.assertEqual(len(included["staging"]), 1)
        self.assertEqual(included["exclusions"], [])
        self.assertEqual(included["scope_decision_report"][0]["status"], "applied")
        staged = included["staging"][0]
        self.assertEqual(staged["source_native_id"], "bioinformatics:8199352")
        self.assertEqual(staged["document_type"], "JOURNAL ARTICLE")
        self.assertNotEqual(staged["document_type"], "Research Article")
        evidence = staged["scope_decision_evidence"]
        self.assertEqual(evidence["method"], "reviewed_exact_identity_publisher_abstract_scope")
        self.assertNotIn("europe_pmc", evidence)
        provenance = staged["field_provenance"]["inclusion_decision"]
        self.assertEqual(provenance["method"], "reviewed_exact_identity_publisher_abstract_scope")
        self.assertEqual(provenance["source_url"], landing_url)
        self.assertEqual(provenance["observed_at"], included["article_capture_observed_at"])
        self.assertEqual(provenance["publisher_abstract_sha256"], included["article_abstract_sha256"])
        self.assertEqual(provenance["publisher_capture"]["sha256"], included["article_capture_sha256"])
        self.assertNotIn("europe_pmc_abstract_sha256", provenance)
        self.assertEqual(provenance["review_source_sha256"], included["review_source_sha256"])

        api_scope_fallback = self._collect_publisher_reviewed_scope_case(
            title=title,
            doi=doi,
            landing_url=landing_url,
            section="JOURNAL ARTICLE",
            publication_types=["research-article", "Journal Article"],
            decision="include_research",
            reason_code="substantive_abstract_tool_or_study",
        )
        self.assertEqual(len(api_scope_fallback["staging"]), 1)
        api_staged = api_scope_fallback["staging"][0]
        self.assertEqual(api_staged["document_type"], "JOURNAL ARTICLE")
        self.assertEqual(
            api_staged["field_provenance"]["document_type"]["method"],
            "official_article_type_or_issue_section",
        )
        self.assertEqual(
            api_staged["europe_pmc_publication_types_as_supplied"],
            ["research-article", "Journal Article"],
        )

        empty_abstract = self._collect_publisher_reviewed_scope_case(
            title=title,
            doi=doi,
            landing_url=landing_url,
            section="JOURNAL ARTICLE",
            decision="include_research",
            reason_code="substantive_abstract_tool_or_study",
            abstract_text="",
        )
        self.assertEqual(empty_abstract["staging"], [])
        self.assertEqual(empty_abstract["scope_decision_report"][0]["status"], "invalid")
        self.assertTrue(any(
            "abstract" in error.casefold()
            for error in empty_abstract["scope_decision_report"][0]["validation_errors"]
        ))

        stale_fingerprint = self._collect_publisher_reviewed_scope_case(
            title=title,
            doi=doi,
            landing_url=landing_url,
            section="JOURNAL ARTICLE",
            decision="include_research",
            reason_code="substantive_abstract_tool_or_study",
            publisher_abstract_sha256_override="0" * 64,
        )
        self.assertEqual(stale_fingerprint["staging"], [])
        self.assertEqual(stale_fingerprint["scope_decision_report"][0]["status"], "invalid")
        self.assertTrue(any(
            "matching non-empty abstract fingerprint" in error
            for error in stale_fingerprint["scope_decision_report"][0]["validation_errors"]
        ))

        explicit_oup_research = self._collect_publisher_reviewed_scope_case(
            title=title,
            doi=doi,
            landing_url=landing_url,
            section="Original Paper",
            decision="include_research",
            reason_code="substantive_abstract_tool_or_study",
        )
        self.assertEqual(len(explicit_oup_research["staging"]), 1)
        self.assertEqual(explicit_oup_research["scope_decision_report"][0]["status"], "already_resolved_by_source")

        explicit_api_nonresearch = self._collect_publisher_reviewed_scope_case(
            title=title,
            doi=doi,
            landing_url=landing_url,
            section="JOURNAL ARTICLE",
            publication_types=["Editorial"],
            decision="include_research",
            reason_code="substantive_abstract_tool_or_study",
        )
        self.assertEqual(explicit_api_nonresearch["staging"], [])
        self.assertEqual(explicit_api_nonresearch["scope_decision_report"][0]["status"], "already_resolved_by_source")

    def test_publisher_article_review_capture_binding_fails_closed(self) -> None:
        row_mutations = {
            "source ID": lambda row: row.update(source_native_id="bioinformatics:2364826"),
            "DOI": lambda row: row.update(doi="10.1093/bioinformatics/other"),
            "title": lambda row: row.update(title="Different publisher title"),
            "landing URL": lambda row: row.update(
                landing_url="https://academic.oup.com/bioinformatics/article/31/3/450/2364825"
            ),
            "capture SHA": lambda row: row["publisher_capture"].update(sha256="0" * 64),
            "capture time": lambda row: row["publisher_capture"].update(observed_at="2026-10-05T09:00:00Z"),
            "capture URL": lambda row: row["publisher_capture"].update(
                source_url="https://academic.oup.com/bioinformatics/article/31/3/450/2364825"
            ),
            "escaping capture path": lambda row: row["publisher_capture"].update(file="../raw/browser/article.json"),
        }
        for label, mutate in row_mutations.items():
            with self.subTest(binding=label):
                result = self._collect_publisher_reviewed_scope_case(row_mutator=mutate)
                self.assertEqual(result["exclusions"], [])
                self.assertEqual(result["scope_decision_report"][0]["status"], "invalid")
                self.assertIn("scope_decision_invalid", {row["kind"] for row in result["unresolved"]})

        source_tamper = self._collect_publisher_reviewed_scope_case(
            receipt_mutator=lambda row: row.update(reason="changed after the immutable source manifest was written"),
        )
        self.assertEqual(source_tamper["exclusions"], [])
        self.assertEqual(source_tamper["scope_decision_report"][0]["status"], "invalid")
        self.assertTrue(any(
            "immutable review_source" in error
            for error in source_tamper["scope_decision_report"][0]["validation_errors"]
        ))

        incomplete = self._collect_publisher_reviewed_scope_case(article_complete=False)
        self.assertEqual(incomplete["exclusions"], [])
        self.assertEqual(incomplete["scope_decision_report"][0]["status"], "invalid")
        self.assertTrue(any(
            "selected complete exact-identity article capture" in error
            for error in incomplete["scope_decision_report"][0]["validation_errors"]
        ))

        not_selected = self._collect_publisher_reviewed_scope_case(capture_override="issue")
        self.assertEqual(not_selected["exclusions"], [])
        self.assertEqual(not_selected["scope_decision_report"][0]["status"], "invalid")
        self.assertTrue(any(
            "selected complete exact-identity article capture" in error
            for error in not_selected["scope_decision_report"][0]["validation_errors"]
        ))
    def test_missing_abstract_waits_for_a_complete_oup_article_capture(self) -> None:
        api_only = self._collect_single_europe_pmc_type_case(
            oup_section="MESSAGE FROM ISCB",
            publication_types=["research-article"],
            supplement_abstract=None,
        )
        staged = api_only["staging"][0]
        self.assertIsNone(staged["abstract"])
        self.assertEqual(
            staged["missing_fields"]["abstract"]["reason_code"],
            "oup_article_detail_abstract_check_pending",
        )
        self.assertEqual(
            staged["field_provenance"]["abstract"]["method"],
            "Europe_PMC_abstract_not_returned; OUP_article_detail_pending",
        )
        pending = [row for row in api_only["unresolved"] if row["kind"] == "oup_article_detail_abstract_check_pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["source_native_id"], "bioinformatics:2366253")
        self.assertFalse(any(url.startswith("https://academic.oup.com/bioinformatics/article/") for url in staged["missing_fields"]["abstract"]["checked_sources"]))
        self.assertEqual(
            staged["missing_fields"]["abstract"]["checked_sources"],
            ["https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:10.1093/bioinformatics/btu769&format=json"],
        )

        checked = self._collect_single_europe_pmc_type_case(
            oup_section="MESSAGE FROM ISCB",
            publication_types=["research-article"],
            supplement_abstract=None,
            complete_detail_without_abstract=True,
        )
        checked_staged = checked["staging"][0]
        self.assertEqual(
            checked_staged["missing_fields"]["abstract"]["reason_code"],
            "not_present_on_official_page",
        )
        self.assertEqual(checked_staged["field_provenance"]["abstract"]["method"], "checked_no_abstract")
        self.assertFalse(any(row["kind"] == "oup_article_detail_abstract_check_pending" for row in checked["unresolved"]))

    def test_advance_listing_requires_observed_closed_pagination_chain(self) -> None:
        second_page = ADVANCE_URL + "?page=2"
        self.assertEqual(clean_listing_page_url(second_page), second_page)
        self.assertIsNone(clean_listing_page_url(ADVANCE_URL + "?page=2&token=secret"))

        first = sanitize_capture(make_capture("advance", ADVANCE_URL, {
            "items": [],
            "pagination": {"next_page_url": second_page, "terminal_observed": False},
        }))
        complete, blockers = _listing_chain_state(
            [first], "advance", require_navigation_evidence=True, entry_url=ADVANCE_URL,
        )
        self.assertFalse(complete)
        self.assertIn("pagination_next_page_not_captured", {row["kind"] for row in blockers})

        terminal = sanitize_capture(make_capture("advance", second_page, {
            "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        complete, blockers = _listing_chain_state(
            [first, terminal], "advance", require_navigation_evidence=True, entry_url=ADVANCE_URL,
        )
        self.assertTrue(complete)
        self.assertEqual(blockers, [])

        contradictory = sanitize_capture(make_capture("advance", ADVANCE_URL, {
            "items": [],
            "pagination": {"next_page_url": second_page, "terminal_observed": True},
        }))
        complete, blockers = _listing_chain_state(
            [contradictory, terminal], "advance", require_navigation_evidence=True, entry_url=ADVANCE_URL,
        )
        self.assertFalse(complete)
        self.assertIn("pagination_next_terminal_conflict", {row["kind"] for row in blockers})

        no_terminal = sanitize_capture(make_capture("advance", ADVANCE_URL, {
            "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": False},
        }))
        complete, blockers = _listing_chain_state(
            [no_terminal], "advance", require_navigation_evidence=True, entry_url=ADVANCE_URL,
        )
        self.assertFalse(complete)
        self.assertIn("pagination_terminal_not_observed", {row["kind"] for row in blockers})

        issue_page2 = ISSUE_URL + "?page=2"
        issue_first = sanitize_capture(make_capture("issue", ISSUE_URL, {
            "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": [],
            "pagination": {"next_page_url": issue_page2, "terminal_observed": False},
        }))
        issue_terminal = sanitize_capture(make_capture("issue", issue_page2, {
            "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        complete, blockers = _listing_chain_state([issue_first], "issue", require_navigation_evidence=True)
        self.assertFalse(complete)
        self.assertIn("pagination_next_page_not_captured", {row["kind"] for row in blockers})
        complete, blockers = _listing_chain_state([issue_first, issue_terminal], "issue", require_navigation_evidence=True)
        self.assertTrue(complete)
        self.assertEqual(blockers, [])

        issue_entry_terminal = sanitize_capture(make_capture("issue", ISSUE_URL, {
            "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        orphan_page = sanitize_capture(make_capture("issue", issue_page2, {
            "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        complete, blockers = _listing_chain_state([issue_entry_terminal, orphan_page], "issue", require_navigation_evidence=True)
        self.assertFalse(complete)
        self.assertIn("pagination_page_not_reachable", {row["kind"] for row in blockers})

        issue_cycle_first = sanitize_capture(make_capture("issue", ISSUE_URL, {
            "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": [],
            "pagination": {"next_page_url": issue_page2, "terminal_observed": False},
        }))
        issue_cycle_second = sanitize_capture(make_capture("issue", issue_page2, {
            "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": [],
            "pagination": {"next_page_url": ISSUE_URL, "terminal_observed": False},
        }))
        complete, blockers = _listing_chain_state(
            [issue_cycle_first, issue_cycle_second], "issue", require_navigation_evidence=True,
        )
        self.assertFalse(complete)
        self.assertIn("pagination_chain_cycle", {row["kind"] for row in blockers})

        cross_issue_link = sanitize_capture(make_capture("issue", ISSUE_URL, {
            "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": [],
            "pagination": {
                "next_page_url": "https://academic.oup.com/bioinformatics/issue/42/2?page=2",
                "terminal_observed": False,
            },
        }))
        complete, blockers = _listing_chain_state(
            [cross_issue_link], "issue", require_navigation_evidence=True,
        )
        self.assertFalse(complete)
        self.assertIn("pagination_evidence_invalid", {row["kind"] for row in blockers})

        wrong_identity_page = sanitize_capture(make_capture("issue", issue_page2, {
            "year": 2026, "volume": "42", "issue": "2", "issue_state": None, "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        complete, blockers = _listing_chain_state(
            [issue_first, wrong_identity_page], "issue", require_navigation_evidence=True,
        )
        self.assertFalse(complete)
        self.assertIn("pagination_issue_identity_conflict", {row["kind"] for row in blockers})

    def test_issue_pagination_observes_all_150_plus_29_listing_cards(self) -> None:
        issue_url = "https://academic.oup.com/bioinformatics/issue/39/1"
        second_page_url = issue_url + "?page=2"

        def observed_items(start: int, count: int) -> list[dict]:
            return [
                {
                    "landing_url": f"https://academic.oup.com/bioinformatics/article/39/1/item/{source_id}",
                    "title": f"Observed article {source_id}",
                    "doi": None,
                    "section": "Original Paper",
                }
                for source_id in range(start, start + count)
            ]

        first_page = sanitize_capture(make_capture("issue", issue_url, {
            "year": 2023, "volume": "39", "issue": "1", "issue_state": None,
            "items": observed_items(100000, 150),
            "pagination": {"next_page_url": second_page_url, "terminal_observed": False},
        }))
        second_page = sanitize_capture(make_capture("issue", second_page_url, {
            "year": 2023, "volume": "39", "issue": "1", "issue_state": None,
            "items": observed_items(100150, 29),
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))

        complete, blockers = _listing_chain_state(
            [first_page, second_page], "issue", require_navigation_evidence=True,
        )
        self.assertTrue(complete, blockers)
        self.assertEqual([len(page["data"]["items"]) for page in (first_page, second_page)], [150, 29])
        self.assertEqual(
            len({item["landing_url"] for page in (first_page, second_page) for item in page["data"]["items"]}),
            179,
        )
        incomplete, missing_page_blockers = _listing_chain_state(
            [first_page], "issue", require_navigation_evidence=True,
        )
        self.assertFalse(incomplete)
        self.assertIn("pagination_next_page_not_captured", {row["kind"] for row in missing_page_blockers})

    def test_issue_without_pagination_stays_useful_for_partial_staging_but_blocks_completeness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            landing_url = "https://academic.oup.com/bioinformatics/article/42/1/example/990001"
            item = {
                "landing_url": landing_url,
                "title": "A verified research article",
                "doi": "10.1093/bioinformatics/example990001",
                "section": "Original Paper",
            }
            issue_capture = sanitize_capture(make_capture("issue", ISSUE_URL, {
                "year": 2026, "volume": "42", "issue": "1", "items": [item],
                # Intentionally absent: old captures remain partial evidence only.
            }))
            detail_capture = sanitize_capture(make_capture("article", landing_url, article_data(
                item["title"], item["doi"], "Journal Article",
            )))
            index_rows = []
            for index, capture in enumerate((issue_capture, detail_capture)):
                relative = f"raw/browser/capture-{index}.json"
                (run_root / relative).write_text(json.dumps(capture), encoding="utf-8")
                index_rows.append({
                    "page_type": capture["page_type"], "source_url": capture["source_url"],
                    "observed_at": capture["observed_at"], "complete": capture["complete"], "file": relative,
                })
            index_path = browser_root / "index.jsonl"
            index_path.write_text("".join(json.dumps(row) + "\n" for row in index_rows), encoding="utf-8")
            stats = collect(index_path, run_root, run_root)
            staging = [json.loads(line) for line in (run_root / "metadata_staging.jsonl").read_text().splitlines()]
            unresolved = [json.loads(line) for line in (run_root / "unresolved.jsonl").read_text().splitlines()]

        self.assertEqual(len(staging), 1)
        self.assertFalse(stats["issue_pages_complete"])
        self.assertFalse(stats["enumeration_complete"])
        self.assertIn("pagination_evidence_missing", {row["kind"] for row in unresolved})

    def test_local_capture_server_saves_allowlisted_json_and_index_can_reload_it(self) -> None:
        sample = {
            "schema_version": CAPTURE_SCHEMA,
            "page_type": "article",
            "source_url": PICNIC_URL,
            "observed_at": "2026-10-05T08:05:59.504Z",
            "complete": True,
            "data": {
                "citation_title": "PICNIC web server for predicting proteins involved in biomolecular condensates",
                "citation_authors": ["Hadarovich, Anna", "Scheremetjew, Maxim"],
                "citation_doi": PICNIC_DOI,
                "citation_pmid": "41325268",
                "citation_journal_title": "Bioinformatics",
                "citation_volume": "42",
                "citation_issue": "1",
                "citation_publication_date": "2026/01/02",
                "citation_pdf_url": "https://academic.oup.com/bioinformatics/article-pdf/42/1/btaf647/65667502/btaf647.pdf",
                "visible_publication_date": "01 December 2025",
                "abstract": "Abstract text.",
                "document_type": "Journal Article",
                "cookies": "must never persist",
            },
            "cookies": "must never persist",
        }
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
            server.browser_root = browser_root
            server.saved_count = 0
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                payload = json.dumps(sample).encode("utf-8")
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request(
                    "POST", "/capture", payload,
                    {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.server_port}"},
                )
                response = connection.getresponse()
                result = json.loads(response.read())
                connection.close()
                self.assertEqual(response.status, 201)
                self.assertTrue((browser_root / f"{result['capture_id']}.json").is_file())
                self.assertEqual(server.saved_count, 1)
                loaded = _load_captures(browser_root / "index.jsonl", run_root)
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0]["data"]["citation_pdf_url"], sample["data"]["citation_pdf_url"])
                self.assertNotIn("cookies", loaded[0])
                self.assertNotIn("cookies", loaded[0]["data"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_year_links_and_one_issue_per_year_do_not_prove_archive_complete(self) -> None:
        last_year = datetime.now(timezone.utc).year
        target_years = set(range(2015, last_year + 1))
        year_links = [
            {"year": year, "url": f"https://academic.oup.com/bioinformatics/issue-archive/{year}", "label": str(year)}
            for year in sorted(target_years)
        ]
        archive_issues = [
            {
                "year": year,
                "volume": str(year),
                "issue": "1",
                "url": f"https://academic.oup.com/bioinformatics/issue/{year}/1",
                "label": f"Volume {year}, Issue 1",
            }
            for year in sorted(target_years)
        ]
        captures = [sanitize_capture(make_capture("archive", ARCHIVE_URL, {
            "year_links": year_links,
            "issues": archive_issues,
        }))]
        for row in archive_issues:
            captures.append(sanitize_capture(make_capture("issue", row["url"], {
                "year": row["year"],
                "volume": row["volume"],
                "issue": row["issue"],
                "issue_state": None,
                "items": [],
                "pagination": {"next_page_url": None, "terminal_observed": True},
            })))
        captures.append(sanitize_capture(make_capture("advance", ADVANCE_URL, {
            "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        })))

        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            rows = []
            for index, capture in enumerate(captures):
                filename = f"raw/browser/capture-{index}.json"
                (run_root / filename).write_text(json.dumps(capture), encoding="utf-8")
                rows.append({
                    "page_type": capture["page_type"],
                    "source_url": capture["source_url"],
                    "observed_at": capture["observed_at"],
                    "complete": capture["complete"],
                    "file": filename,
                })
            index_path = browser_root / "index.jsonl"
            index_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            stats = collect(index_path, run_root, run_root)
            unresolved = [json.loads(line) for line in (run_root / "unresolved.jsonl").read_text().splitlines()]

        self.assertEqual(stats["archive_issue_count_in_scope"], len(target_years))
        self.assertTrue(stats["issue_pages_complete"])
        self.assertTrue(stats["advance_pagination_complete"])
        self.assertFalse(stats["archive_directories_complete"])
        self.assertFalse(stats["enumeration_complete"])
        self.assertIn("archive_directory_year_index_not_attested_complete", {row["kind"] for row in unresolved})
        self.assertEqual(
            {row["year"] for row in unresolved if row["kind"] == "archive_directory_year_not_captured"},
            target_years,
        )

    def test_archive_directory_evidence_handles_direct_annual_list_and_observed_2026_dropdown(self) -> None:
        annual_url = "https://academic.oup.com/bioinformatics/issue-archive/2015"
        observed_2015_issues = [
            {
                "year": 2015,
                "volume": "31",
                "issue": str(issue),
                "url": f"https://academic.oup.com/bioinformatics/issue/31/{issue}",
                "label": f"Volume 31, Issue {issue}",
            }
            for issue in range(1, 25)
        ]
        observed_2026_dropdown = [
            ("1", "https://academic.oup.com/bioinformatics/issue/42/1"),
            ("2", "https://academic.oup.com/bioinformatics/issue/42/2"),
            ("3", "https://academic.oup.com/bioinformatics/issue/42/3"),
            ("4", "https://academic.oup.com/bioinformatics/issue/42/4"),
            ("5", "https://academic.oup.com/bioinformatics/issue/42/5"),
            ("6", "https://academic.oup.com/bioinformatics/issue/42/6"),
            ("7", "https://academic.oup.com/bioinformatics/issue/42/7"),
            ("Supplement_1", "https://academic.oup.com/bioinformatics/issue/42/Supplement_1"),
            ("8", "https://academic.oup.com/bioinformatics/issue/42/8"),
            ("Supplement_2", "https://academic.oup.com/bioinformatics/issue/42/Supplement_2"),
            ("9", "https://academic.oup.com/bioinformatics/issue/42/9"),
            ("10", "https://academic.oup.com/bioinformatics/issue/42/10"),
        ]
        year_index = sanitize_capture(make_capture("archive", ARCHIVE_URL, {
            "year_links": [
                {"year": 2015, "url": annual_url, "label": "2015"},
                {"year": 2026, "url": "https://academic.oup.com/bioinformatics/issue-archive/2026", "label": "2026"},
            ],
            "issues": [],
            "directory": {"kind": "year_index", "complete": True},
        }))
        annual = sanitize_capture(make_capture("archive", annual_url, {
            "year_links": [],
            "issues": observed_2015_issues,
            "directory": {
                "kind": "year",
                "year": 2015,
                "entry_url": annual_url,
                "complete": True,
            },
        }))
        current_dropdown = sanitize_capture(make_capture("archive", ISSUE_URL, {
            "year_links": [],
            "issues": [
                {"year": 2026, "volume": "42", "issue": issue, "url": url, "label": issue}
                for issue, url in observed_2026_dropdown
            ],
            "directory": {"kind": "volume", "year": 2026, "volume": "42", "complete": True},
        }))
        archives = [year_index, annual, current_dropdown]
        archive_issues, year_links = _archive_issue_map(archives)
        complete, blockers, year_count, volume_count = _archive_directory_state(
            archives, {2015}, year_links, archive_issues,
        )
        self.assertTrue(complete, blockers)
        self.assertEqual(len(observed_2015_issues), 24)
        self.assertEqual(sum(issue["year"] == 2026 for issue in archive_issues.values()), 12)
        self.assertEqual(year_count, 1)
        self.assertEqual(volume_count, 1)

    def test_complete_2020_annual_list_uses_only_a_linked_issue_page_dropdown(self) -> None:
        annual_url = "https://academic.oup.com/bioinformatics/issue-archive/2020"
        issue_labels = [str(issue) for issue in range(1, 21)] + ["Supplement_1", "Supplement_2", "21", "22-23", "24"]
        issue_rows = [
            {
                "year": 2020,
                "volume": "36",
                "issue": label,
                "url": f"https://academic.oup.com/bioinformatics/issue/36/{label}",
                "label": f"Volume 36, Issue {label}",
            }
            for label in issue_labels
        ]
        annual_rows = issue_rows[:22]
        year_index = sanitize_capture(make_capture("archive", ARCHIVE_URL, {
            "year_links": [{"year": 2020, "url": annual_url, "label": "2020"}],
            "issues": [],
            "directory": {"kind": "year_index", "complete": True},
        }))
        annual = sanitize_capture(make_capture("archive", annual_url, {
            "year_links": [],
            "issues": annual_rows,
            "directory": {"kind": "year", "year": 2020, "entry_url": annual_url, "complete": True},
        }))
        dropdown_url = "https://academic.oup.com/bioinformatics/issue/36/9"

        def dropdown_capture(rows: list[dict], *, page_url: str = dropdown_url, year: int = 2020, volume: str = "36") -> dict:
            return sanitize_capture(make_capture("archive", page_url, {
                "year_links": [],
                "issues": rows,
                "directory": {
                    "kind": "volume", "year": year, "volume": volume,
                    "entry_url": page_url, "complete": True,
                },
            }))

        linked_dropdown = dropdown_capture(issue_rows)
        archives = [year_index, annual, linked_dropdown]
        dropdown_source_issue = sanitize_capture(make_capture("issue", dropdown_url, {
            "year": 2020, "volume": "36", "issue": "9", "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        archive_issues, year_links = _archive_issue_map(archives, [dropdown_source_issue])
        complete, blockers, year_count, _volume_count = _archive_directory_state(
            archives, {2020}, year_links, archive_issues,
        )
        self.assertTrue(complete, blockers)
        self.assertEqual(len(annual_rows), 22)
        self.assertEqual(len(issue_rows), 25)
        self.assertEqual(len(archive_issues), 25)
        self.assertEqual(year_count, 1)

        unlinked_annual = [row for row in annual_rows if row["issue"] != "9"]
        unlinked = sanitize_capture(make_capture("archive", annual_url, {
            "year_links": [],
            "issues": unlinked_annual,
            "directory": {"kind": "year", "year": 2020, "entry_url": annual_url, "complete": True},
        }))
        unlinked_archives = [year_index, unlinked, linked_dropdown]
        unlinked_issues, unlinked_year_links = _archive_issue_map(unlinked_archives, [dropdown_source_issue])
        unlinked_complete, unlinked_blockers, _, _ = _archive_directory_state(
            unlinked_archives, {2020}, unlinked_year_links, unlinked_issues,
        )
        self.assertFalse(unlinked_complete)
        self.assertIn("archive_directory_year_issue_set_mismatch", {row["kind"] for row in unlinked_blockers})

        mismatched_rows = [dict(row) for row in issue_rows]
        mismatched_rows[-1]["volume"] = "35"
        mismatched_dropdown = dropdown_capture(mismatched_rows)
        mismatched_archives = [year_index, annual, mismatched_dropdown]
        mismatched_issues, mismatched_year_links = _archive_issue_map(mismatched_archives, [dropdown_source_issue])
        mismatched_complete, mismatched_blockers, _, _ = _archive_directory_state(
            mismatched_archives, {2020}, mismatched_year_links, mismatched_issues,
        )
        self.assertFalse(mismatched_complete)
        self.assertIn("archive_directory_volume_dropdown_issue_identity_conflict", {row["kind"] for row in mismatched_blockers})

        # The dropdown contributes expected identities, but every resulting
        # issue still requires its own complete listing-page capture.
        missing_issue_url = "https://academic.oup.com/bioinformatics/issue/36/24"
        all_captures = [year_index, annual, linked_dropdown]
        for row in issue_rows:
            if row["url"] == missing_issue_url:
                continue
            all_captures.append(sanitize_capture(make_capture("issue", row["url"], {
                "year": 2020, "volume": "36", "issue": row["issue"], "items": [],
                "pagination": {"next_page_url": None, "terminal_observed": True},
            })))
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            index_rows = []
            for index, capture in enumerate(all_captures):
                relative = f"raw/browser/capture-{index}.json"
                (run_root / relative).write_text(json.dumps(capture), encoding="utf-8")
                index_rows.append({
                    "page_type": capture["page_type"], "source_url": capture["source_url"],
                    "observed_at": capture["observed_at"], "complete": capture["complete"], "file": relative,
                })
            index_path = browser_root / "index.jsonl"
            index_path.write_text("".join(json.dumps(row) + "\n" for row in index_rows), encoding="utf-8")
            stats = collect(index_path, run_root, run_root)
            unresolved = [json.loads(line) for line in (run_root / "unresolved.jsonl").read_text().splitlines()]
        self.assertEqual(stats["archive_issue_count_in_scope"], 25)
        self.assertFalse(stats["issue_pages_complete"])
        self.assertFalse(stats["metadata_complete"])
        missing_rows = [row for row in unresolved if row["kind"] == "official_issue_pages_not_captured"]
        self.assertTrue(missing_rows)
        self.assertIn(missing_issue_url, missing_rows[0]["urls"])

    def test_archive_occurrence_year_and_issue_page_year_are_kept_separate(self) -> None:
        annual_2020_url = "https://academic.oup.com/bioinformatics/issue-archive/2020"
        annual_2021_url = "https://academic.oup.com/bioinformatics/issue-archive/2021"
        issue_2020_url = "https://academic.oup.com/bioinformatics/issue/36/21"
        issue_2021_url = "https://academic.oup.com/bioinformatics/issue/37/1"
        shared_issue = {"year": 2020, "volume": "36", "issue": "21", "url": issue_2020_url, "label": "Volume 36, Issue 21"}
        next_issue_2021 = {"year": 2021, "volume": "37", "issue": "1", "url": issue_2021_url, "label": "Volume 37, Issue 1"}
        root = sanitize_capture(make_capture("archive", ARCHIVE_URL, {
            "year_links": [
                {"year": 2020, "url": annual_2020_url, "label": "2020"},
                {"year": 2021, "url": annual_2021_url, "label": "2021"},
            ],
            "issues": [],
            "directory": {"kind": "year_index", "complete": True},
        }))
        annual_2020 = sanitize_capture(make_capture("archive", annual_2020_url, {
            "year_links": [], "issues": [shared_issue],
            "directory": {"kind": "year", "year": 2020, "entry_url": annual_2020_url, "complete": True},
        }))
        annual_2021 = sanitize_capture(make_capture("archive", annual_2021_url, {
            "year_links": [],
            "issues": [{**shared_issue, "year": 2021}, next_issue_2021],
            "directory": {"kind": "year", "year": 2021, "entry_url": annual_2021_url, "complete": True},
        }))
        captured_issue_2020 = sanitize_capture(make_capture("issue", issue_2020_url, {
            "year": 2020, "volume": "36", "issue": "21", "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        captures = [root, annual_2020, annual_2021]
        issue_map, year_links = _archive_issue_map(captures, [captured_issue_2020])
        self.assertEqual(issue_map["/bioinformatics/issue/36/21"]["year"], 2020)
        self.assertEqual(issue_map["/bioinformatics/issue/36/21"]["identity_source_url"], issue_2020_url)
        self.assertEqual(
            {row["year"] for row in issue_map["/bioinformatics/issue/36/21"]["archive_occurrences"]},
            {2020, 2021},
        )
        mismatched_heading = sanitize_capture(make_capture("issue", issue_2020_url, {
            "year": 2020, "volume": "36", "issue": "22-23", "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        with self.assertRaisesRegex(ValueError, "heading does not match"):
            _archive_issue_map(captures, [mismatched_heading])
        complete, blockers, _years, _volumes = _archive_directory_state(
            captures, {2020, 2021}, year_links, issue_map,
        )
        self.assertTrue(complete, blockers)

        incomplete, missing_directory_blockers, _, _ = _archive_directory_state(
            [root, annual_2020], {2020, 2021}, year_links, issue_map,
        )
        self.assertFalse(incomplete)
        self.assertIn("archive_directory_year_not_captured", {row["kind"] for row in missing_directory_blockers})

    def test_dropdown_uses_source_linked_from_prior_archive_year_and_issue_heading_year(self) -> None:
        annual_2021_url = "https://academic.oup.com/bioinformatics/issue-archive/2021"
        annual_2022_url = "https://academic.oup.com/bioinformatics/issue-archive/2022"
        source_issue_url = "https://academic.oup.com/bioinformatics/issue/38/1"
        row_38_1 = {"year": 2021, "volume": "38", "issue": "1", "url": source_issue_url, "label": "Volume 38, Issue 1"}
        annual_2022_rows = [
            {
                "year": 2022, "volume": "38", "issue": str(issue),
                "url": f"https://academic.oup.com/bioinformatics/issue/38/{issue}",
                "label": f"Volume 38, Issue {issue}",
            }
            for issue in range(2, 25)
        ] + [
            {
                "year": 2022, "volume": "38", "issue": f"Supplement_{number}",
                "url": f"https://academic.oup.com/bioinformatics/issue/38/Supplement_{number}",
                "label": f"Volume 38, Issue Supplement_{number}",
            }
            for number in (1, 2)
        ]
        dropdown_rows = [{**row_38_1, "year": 2022}, *annual_2022_rows]
        root = sanitize_capture(make_capture("archive", ARCHIVE_URL, {
            "year_links": [
                {"year": 2021, "url": annual_2021_url, "label": "2021"},
                {"year": 2022, "url": annual_2022_url, "label": "2022"},
            ],
            "issues": [], "directory": {"kind": "year_index", "complete": True},
        }))
        annual_2021 = sanitize_capture(make_capture("archive", annual_2021_url, {
            "year_links": [], "issues": [row_38_1],
            "directory": {"kind": "year", "year": 2021, "entry_url": annual_2021_url, "complete": True},
        }))
        annual_2022 = sanitize_capture(make_capture("archive", annual_2022_url, {
            "year_links": [], "issues": annual_2022_rows,
            "directory": {"kind": "year", "year": 2022, "entry_url": annual_2022_url, "complete": True},
        }))
        dropdown = sanitize_capture(make_capture("archive", source_issue_url, {
            "year_links": [], "issues": dropdown_rows,
            "directory": {"kind": "volume", "year": 2022, "volume": "38", "entry_url": source_issue_url, "complete": True},
        }))
        source_issue_capture = sanitize_capture(make_capture("issue", source_issue_url, {
            "year": 2022, "volume": "38", "issue": "1", "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        archives = [root, annual_2021, annual_2022, dropdown]
        issue_map, links = _archive_issue_map(archives, [source_issue_capture])
        self.assertEqual(issue_map["/bioinformatics/issue/38/1"]["year"], 2022)
        self.assertEqual(
            {row["year"] for row in issue_map["/bioinformatics/issue/38/1"]["archive_occurrences"]},
            {2021, 2022},
        )
        complete, blockers, _, _ = _archive_directory_state(archives, {2021, 2022}, links, issue_map)
        self.assertTrue(complete, blockers)

        without_2022_annual = [root, annual_2021, dropdown]
        incomplete, incomplete_blockers, _, _ = _archive_directory_state(
            without_2022_annual, {2021, 2022}, links, issue_map,
        )
        self.assertFalse(incomplete)
        self.assertIn("archive_directory_year_not_captured", {row["kind"] for row in incomplete_blockers})

    def test_reviewed_misfiled_issue_needs_actual_anchor_and_captured_identity(self) -> None:
        url = "https://academic.oup.com/bioinformatics/issue/35/15"
        annual_url = "https://academic.oup.com/bioinformatics/issue-archive/2019"
        link = {"year": 2018, "volume": "35", "issue": "15", "url": url, "label": "Volume 35, Issue 15"}
        raw_link_capture = make_capture("archive", "https://academic.oup.com/bioinformatics/issue-archive/2018", {
            "year_links": [], "issues": [link],
        })
        issue = sanitize_capture(make_capture("issue", url, {
            "year": 2019, "volume": "35", "issue": "15", "items": [],
            "pagination": {"next_page_url": None, "terminal_observed": True},
        }))
        root = sanitize_capture(make_capture("archive", ARCHIVE_URL, {
            "year_links": [{"year": 2019, "url": annual_url}], "issues": [],
            "directory": {"kind": "year_index", "complete": True},
        }))
        annual = sanitize_capture(make_capture("archive", annual_url, {
            "year_links": [],
            "issues": [{"year": 2019, "volume": "35", "issue": "14",
                        "url": "https://academic.oup.com/bioinformatics/issue/35/14"}],
            "directory": {"kind": "year", "year": 2019, "complete": True},
        }))
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            raw_file = evidence / "misfiled.json"
            raw_file.write_text(json.dumps(raw_link_capture))
            decision = {"issue_url": url, "reason": "Annual directory omits issue; official issue heading confirms 2019.",
                        "link_capture": {"file": raw_file.name, "sha256": hashlib.sha256(raw_file.read_bytes()).hexdigest()}}
            corrections = evidence / "reviewed.json"
            corrections.write_text(json.dumps({"schema_version": "bioinformatics-reviewed-archive-additions-v1", "decisions": [decision]}))
            archives = [root, annual]
            issues, years = _archive_issue_map(archives)
            additions = _reviewed_archive_additions(corrections, evidence, [issue], issues)
            added = next(iter(additions.values()))
            self.assertEqual(added["year"], 2019)
            self.assertEqual(added["reviewed_archive_addition"]["original_link_identity"]["year"], 2018)
            self.assertEqual(json.loads(raw_file.read_text()), raw_link_capture)
            issues.update(additions)
            self.assertFalse(_archive_directory_state(archives, {2019}, years, issues)[0])
            self.assertTrue(_archive_directory_state(archives, {2019}, years, issues, additions)[0])
            self.assertFalse(_archive_directory_state([root], {2019}, years, issues, additions)[0])
            with self.assertRaisesRegex(ValueError, "cannot override"):
                _reviewed_archive_additions(corrections, evidence, [issue], issues)
            with self.assertRaisesRegex(ValueError, "observed anchor and captured issue"):
                _reviewed_archive_additions(corrections, evidence, [], {})
            for wrong_url in ("https://academic.oup.com/bioinformatics/article/35/15",
                              "https://academic.oup.com/bioinformatics/foo"):
                decision["issue_url"] = wrong_url
                raw_link_capture["data"]["issues"][0]["url"] = wrong_url
                raw_file.write_text(json.dumps(raw_link_capture))
                decision["link_capture"]["sha256"] = hashlib.sha256(raw_file.read_bytes()).hexdigest()
                corrections.write_text(json.dumps({"schema_version": "bioinformatics-reviewed-archive-additions-v1", "decisions": [decision]}))
                bad_issue = {**issue, "source_url": wrong_url}
                with self.assertRaisesRegex(ValueError, "exact official issue route"):
                    _reviewed_archive_additions(corrections, evidence, [bad_issue], {})
            raw_link_capture["data"]["issues"][0]["url"] = url
            raw_file.write_text(json.dumps(raw_link_capture))
            decision["link_capture"]["sha256"] = hashlib.sha256(raw_file.read_bytes()).hexdigest()
            decision["issue_url"] = url
            corrections.write_text(json.dumps({"schema_version": "bioinformatics-reviewed-archive-additions-v1", "decisions": [decision]}))
            # Even matching anchor/capture fields must agree with the URL.
            bad_issue = {**issue, "data": {**issue["data"], "issue": "16"}}
            raw_link_capture["data"]["issues"][0]["issue"] = "16"
            raw_file.write_text(json.dumps(raw_link_capture))
            decision["link_capture"]["sha256"] = hashlib.sha256(raw_file.read_bytes()).hexdigest()
            corrections.write_text(json.dumps({"schema_version": "bioinformatics-reviewed-archive-additions-v1", "decisions": [decision]}))
            with self.assertRaisesRegex(ValueError, "identity does not match"):
                _reviewed_archive_additions(corrections, evidence, [bad_issue], {})
            decision["issue_url"] = "https://academic.oup.com/bioinformatics/issue/35/16"
            corrections.write_text(json.dumps({"schema_version": "bioinformatics-reviewed-archive-additions-v1", "decisions": [decision]}))
            with self.assertRaisesRegex(ValueError, "observed anchor and captured issue"):
                _reviewed_archive_additions(corrections, evidence, [issue], {})
            raw_file.write_text("{}")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                _reviewed_archive_additions(corrections, evidence, [issue], {})

    def test_expected_counts_keep_different_publisher_ids_with_same_doi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            captures = []
            year_links = [{"year": year, "label": str(year)} for year in range(2015, 2027)]
            captures.append(make_capture("archive", ARCHIVE_URL, {
                "year_links": year_links,
                "issues": [{"year": 2026, "volume": "42", "issue": "1", "url": ISSUE_URL, "label": "Volume 42 Issue 1"}],
            }))
            item_specs = [
                ("8362260", "PICNIC web server for predicting proteins involved in biomolecular condensates", PICNIC_DOI, "Applications Notes", "Journal Article"),
                ("8362261", "The same DOI represented by another publisher source ID", PICNIC_DOI, "Review", "Review"),
                ("8362262", "Correction to an analysis", "10.1093/bioinformatics/cor001", "Corrections", "Correction"),
                ("8362263", "An item whose article class needs review", "10.1093/bioinformatics/unknown1", None, "Journal Article"),
            ]
            issue_items = []
            for internal_id, title, doi, section, _doc_type in item_specs:
                slug = title.lower().split()[0]
                issue_items.append({
                    "landing_url": f"https://academic.oup.com/bioinformatics/article/42/1/{slug}/{internal_id}",
                    "title": title,
                    "authors_preview": ["Preview only"],
                    "doi": doi,
                    "citation": title,
                    "section": section,
                    "categories": [],
                    "pdf_url": None,
                })
            captures.append(make_capture("issue", ISSUE_URL, {
                "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": issue_items,
                "pagination": {"next_page_url": None, "terminal_observed": True},
            }))
            captures.append(make_capture("advance", ADVANCE_URL, {
                "items": [dict(issue_items[0])],
            }))
            for internal_id, title, doi, _section, doc_type in item_specs:
                slug = title.lower().split()[0]
                url = f"https://academic.oup.com/bioinformatics/article/42/1/{slug}/{internal_id}"
                captures.append(make_capture("article", url, article_data(title, doi, doc_type)))

            rows = []
            for index, capture in enumerate(captures):
                sanitized = sanitize_capture(capture)
                filename = f"raw/browser/capture-{index}.json"
                (run_root / filename).write_text(json.dumps(sanitized), encoding="utf-8")
                rows.append({
                    "page_type": sanitized["page_type"], "source_url": sanitized["source_url"],
                    "observed_at": sanitized["observed_at"], "complete": sanitized["complete"], "file": filename,
                })
            index_path = browser_root / "index.jsonl"
            index_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            stats = collect(index_path, run_root, run_root)
            expected = [row for year_file in (run_root / "expected").glob("*.jsonl") for row in (json.loads(line) for line in year_file.read_text().splitlines())]
            staging = [json.loads(line) for line in (run_root / "metadata_staging.jsonl").read_text().splitlines()]
            exclusions = [json.loads(line) for line in (run_root / "metadata_exclusions.jsonl").read_text().splitlines()]
            unresolved = [json.loads(line) for line in (run_root / "unresolved.jsonl").read_text().splitlines()]
            self.assertEqual(len(expected), 4)
            self.assertEqual(len({row["source_native_id"] for row in expected}), 4)
            self.assertEqual(sum(row["doi"] == PICNIC_DOI for row in staging), 2)
            self.assertEqual(len(staging), 2)
            self.assertEqual(len(exclusions), 1)
            self.assertIn("research_scope_unresolved", {row["kind"] for row in unresolved})
            self.assertIn("archive_issue_year_missing", {row["kind"] for row in unresolved})
            self.assertEqual(stats["waterline_status"], "BLOCKED")
            self.assertEqual(staging[0]["visible_publication_date"], "01 December 2025")
            self.assertEqual(staging[0]["year"], 2026)

    def test_advance_visible_publication_date_supplies_year_without_overriding_issue_year(self) -> None:
        def collect_case(*, issue_year: int | None) -> tuple[list[dict], list[dict]]:
            with tempfile.TemporaryDirectory() as temporary:
                run_root = Path(temporary)
                browser_root = run_root / "raw" / "browser"
                browser_root.mkdir(parents=True)
                doi = "10.1093/bioinformatics/visible-year-test"
                title = "A computational method with a visible publication date"
                landing_url = (
                    "https://academic.oup.com/bioinformatics/article/42/1/visible/990991"
                    if issue_year is not None
                    else "https://academic.oup.com/bioinformatics/advance-article/doi/10.1093/bioinformatics/visible-year-test"
                )
                item = {
                    "landing_url": landing_url,
                    "title": title,
                    "doi": doi,
                    "section": "Applications Notes",
                    "categories": [],
                }
                listing_url = (
                    "https://academic.oup.com/bioinformatics/issue/42/1"
                    if issue_year is not None else ADVANCE_URL
                )
                listing_data = {
                    "items": [item],
                    "pagination": {"next_page_url": None, "terminal_observed": True},
                }
                if issue_year is not None:
                    listing_data.update({"year": issue_year, "volume": "42", "issue": "1"})
                listing_capture = sanitize_capture(make_capture(
                    "issue" if issue_year is not None else "advance",
                    listing_url,
                    listing_data,
                ))
                detail_data = article_data(title, doi, "Applications Notes")
                detail_data["citation_publication_date"] = None
                detail_data["visible_publication_date"] = "01 December 2025"
                detail_capture = sanitize_capture(make_capture("article", landing_url, detail_data))
                rows = []
                for index, capture in enumerate((listing_capture, detail_capture)):
                    relative = f"raw/browser/capture-{index}.json"
                    (run_root / relative).write_text(json.dumps(capture), encoding="utf-8")
                    rows.append({
                        "page_type": capture["page_type"],
                        "source_url": capture["source_url"],
                        "observed_at": capture["observed_at"],
                        "complete": capture["complete"],
                        "file": relative,
                    })
                index_path = browser_root / "index.jsonl"
                index_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                collect(index_path, run_root, run_root)
                staging = [
                    json.loads(line)
                    for line in (run_root / "metadata_staging.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                unresolved = [
                    json.loads(line)
                    for line in (run_root / "unresolved.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                return staging, unresolved

        advance_staging, advance_unresolved = collect_case(issue_year=None)
        self.assertEqual(len(advance_staging), 1)
        advance = advance_staging[0]
        self.assertEqual(advance["year"], 2025)
        self.assertIsNone(advance["publication_date"])
        self.assertEqual(advance["visible_publication_date"], "01 December 2025")
        self.assertEqual(advance["field_provenance"]["year"], {
            "source_url": "https://academic.oup.com/bioinformatics/advance-article/doi/10.1093/bioinformatics/visible-year-test",
            "observed_at": NOW,
            "method": "official_visible_publication_date",
        })
        self.assertNotIn("source_year_unresolved", {row["kind"] for row in advance_unresolved})

        issue_staging, issue_unresolved = collect_case(issue_year=2026)
        self.assertEqual(len(issue_staging), 1)
        issue = issue_staging[0]
        self.assertEqual(issue["year"], 2026)
        self.assertEqual(issue["visible_publication_date"], "01 December 2025")
        self.assertNotIn("source_year_unresolved", {row["kind"] for row in issue_unresolved})

    def test_exact_reviewed_dagger_title_variant_resolution_is_narrow_and_audited(self) -> None:
        listing_title = "Normalization of single-cell RNA-seq counts by log(x + 1) or log(1 + x)"
        detail_title = "Normalization of single-cell RNA-seq counts by log(x + 1)† or log(1 + x)†"
        expected_doi = "10.1093/bioinformatics/btab085"

        def collect_case(
            *,
            native_id: str = "6155989",
            doi: str = expected_doi,
            extra_title: str | None = None,
            detail_url: str | None = None,
            detail_complete: bool = True,
        ) -> tuple[list[dict], list[dict], list[dict]]:
            with tempfile.TemporaryDirectory() as temporary:
                run_root = Path(temporary)
                browser_root = run_root / "raw" / "browser"
                browser_root.mkdir(parents=True)
                landing_url = f"https://academic.oup.com/bioinformatics/article/42/1/example/{native_id}"
                listing_item = {
                    "landing_url": landing_url,
                    "title": listing_title,
                    "doi": doi,
                    "section": "Original Paper",
                    "categories": [],
                }
                issue_capture = sanitize_capture(make_capture("issue", ISSUE_URL, {
                    "year": 2026, "volume": "42", "issue": "1", "items": [listing_item],
                    "pagination": {"next_page_url": None, "terminal_observed": True},
                }))
                captures = [issue_capture]
                if extra_title is not None:
                    captures.append(sanitize_capture(make_capture("advance", ADVANCE_URL, {
                        "items": [{**listing_item, "title": extra_title}],
                        "pagination": {"next_page_url": None, "terminal_observed": True},
                    })))
                detail_data = article_data(detail_title, doi, "Journal Article")
                detail_capture = sanitize_capture(make_capture(
                    "article", detail_url or landing_url, detail_data, complete=detail_complete,
                ))
                captures.append(detail_capture)
                rows = []
                for index, capture in enumerate(captures):
                    relative = f"raw/browser/capture-{index}.json"
                    (run_root / relative).write_text(json.dumps(capture), encoding="utf-8")
                    rows.append({
                        "page_type": capture["page_type"], "source_url": capture["source_url"],
                        "observed_at": capture["observed_at"], "complete": capture["complete"], "file": relative,
                    })
                index_path = browser_root / "index.jsonl"
                index_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                collect(index_path, run_root, run_root)
                read_output = lambda name: [
                    json.loads(line)
                    for line in (run_root / name).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                return (
                    read_output("metadata_staging.jsonl"),
                    read_output("unresolved.jsonl"),
                    read_output("expected_source_items.jsonl"),
                )

        staging, unresolved, expected = collect_case()
        self.assertEqual(len(staging), 1)
        self.assertEqual(staging[0]["title"], detail_title)
        self.assertNotIn("source_identity_collision", {row["kind"] for row in unresolved})
        reviewed = next(row for row in expected if row["source_native_id"] == "bioinformatics:6155989")
        self.assertEqual(reviewed["identity_resolution"], {
            "method": "reviewed_exact_identity_dagger_footnote_title_variant",
            "reason": (
                "The official issue listing and complete article detail share the same native ID, DOI, and landing URL; "
                "the only title difference is the two observed dagger footnote markers. The complete article detail title is retained."
            ),
            "source_native_id": "bioinformatics:6155989",
            "doi": expected_doi,
            "landing_url": "https://academic.oup.com/bioinformatics/article/42/1/example/6155989",
            "canonical_title": detail_title,
            "observed_titles": [listing_title, detail_title],
        })
        self.assertEqual({row["title"] for row in reviewed["source_occurrences"]}, {listing_title})

        rejected_cases = (
            {"native_id": "6155990"},
            {"doi": "10.1093/bioinformatics/btab086"},
            {"detail_url": "https://academic.oup.com/bioinformatics/article/42/2/example/6155989"},
            {"detail_complete": False},
            {"extra_title": "Normalization of single-cell RNA-seq counts by log(x + 1)‡ or log(1 + x)‡"},
        )
        for case in rejected_cases:
            with self.subTest(case=case):
                rejected_staging, rejected_unresolved, _ = collect_case(**case)
                self.assertEqual(rejected_staging, [])
                collision = next(row for row in rejected_unresolved if row["kind"] == "source_identity_collision")
                self.assertGreater(len(collision["titles"]), 1)

    def test_expected_identity_is_written_before_detail_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            issue_item = {
                "landing_url": "https://academic.oup.com/bioinformatics/article/42/1/example/900001",
                "title": "A verified research item",
                "authors_preview": ["Example, Ada"],
                "doi": "10.1093/bioinformatics/example1",
                "citation": "Bioinformatics 42(1), 2026",
                "section": "Original Paper",
                "categories": [],
                "pdf_url": None,
            }
            captures = [
                make_capture("archive", ARCHIVE_URL, {
                    "year_links": [{"year": year, "label": str(year)} for year in range(2015, 2027)],
                    "issues": [{"year": 2026, "volume": "42", "issue": "1", "url": ISSUE_URL, "label": "Volume 42 Issue 1"}],
                }),
                make_capture("issue", ISSUE_URL, {
                    "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": [issue_item],
                    "pagination": {"next_page_url": None, "terminal_observed": True},
                }),
            ]
            rows = []
            for index, capture in enumerate(captures):
                sanitized = sanitize_capture(capture)
                filename = f"raw/browser/capture-{index}.json"
                (run_root / filename).write_text(json.dumps(sanitized), encoding="utf-8")
                rows.append({
                    "page_type": sanitized["page_type"], "source_url": sanitized["source_url"],
                    "observed_at": sanitized["observed_at"], "complete": sanitized["complete"], "file": filename,
                })
            index_path = browser_root / "index.jsonl"
            index_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            stats = collect(index_path, run_root, run_root)
            expected_all = [json.loads(line) for line in (run_root / "expected_source_items.jsonl").read_text().splitlines()]
            expected_2026 = [json.loads(line) for line in (run_root / "expected" / "2026.jsonl").read_text().splitlines()]
            staging = (run_root / "metadata_staging.jsonl").read_text().splitlines()
            unresolved = [json.loads(line) for line in (run_root / "unresolved.jsonl").read_text().splitlines()]

            self.assertEqual(len(expected_all), 1)
            self.assertEqual(expected_all[0]["source_native_id"], "bioinformatics:900001")
            self.assertEqual(len(expected_2026), 1)
            self.assertEqual(staging, [])
            self.assertIn("article_detail_not_captured", {row["kind"] for row in unresolved})
            self.assertEqual(stats["unique_source_identities"], 1)
            self.assertFalse(stats["enumeration_complete"])

    def test_europe_pmc_supplements_only_exact_matches_and_retains_field_provenance(self) -> None:
        self.assertEqual(
            clean_europe_pmc_source_url("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:10.1093/bioinformatics/btaf647&format=json"),
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:10.1093/bioinformatics/btaf647&format=json",
        )
        pdf_url = "https://europepmc.org/articles/PMC9999999?pdf=render"
        self.assertEqual(clean_europe_pmc_pdf_url(pdf_url), pdf_url)
        self.assertIsNone(clean_europe_pmc_pdf_url("https://europepmc.org/other/guessed.pdf"))
        crossref_url = "https://api.crossref.org/journals/1367-4811/works?cursor=%2A"
        self.assertEqual(clean_crossref_request_url(crossref_url), crossref_url)
        self.assertEqual(_crossref_date({"published-online": {"date-parts": [[2025, 12]]}}), ("2025-12", "month"))
        vor_url = "https://academic.oup.com/bioinformatics/article-pdf/42/1/example/900002/paper.pdf"
        am_url = "https://academic.oup.com/bioinformatics/advance-article-pdf/doi/10.1093/bioinformatics/example1/paper.pdf"
        vor, am = _crossref_link_candidates({"link": [
            {"URL": am_url, "content-type": "application/pdf", "content-version": "am"},
            {"URL": vor_url, "content-type": "application/pdf", "content-version": "vor"},
            {"URL": "https://academic.oup.com/bioinformatics/article/42/1/example/900002", "content-type": "application/pdf", "content-version": "vor"},
            {"URL": "https://academic.oup.com/bioinformaticsadvances/article-pdf/1/1/vbaf230/paper.pdf", "content-type": "application/pdf", "content-version": "vor"},
        ]})
        self.assertEqual(vor[0]["url"], vor_url)
        self.assertEqual(am[0]["url"], am_url)

        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            browser_root = run_root / "raw" / "browser"
            browser_root.mkdir(parents=True)
            doi = "10.1093/bioinformatics/example1"
            landing = "https://academic.oup.com/bioinformatics/article/42/1/example/900002"
            issue_item = {
                "landing_url": landing,
                "title": "OUP listing title wins over the supplement title",
                "authors_preview": ["Preview only"],
                "doi": doi,
                "citation": "Bioinformatics 42(1), 2026",
                "section": "Applications Notes",
                "categories": [],
                "pdf_url": None,
            }
            captures = [
                make_capture("archive", ARCHIVE_URL, {
                    "year_links": [{"year": year, "label": str(year)} for year in range(2015, 2027)],
                    "issues": [{"year": 2026, "volume": "42", "issue": "1", "url": ISSUE_URL, "label": "Volume 42 Issue 1"}],
                }),
                make_capture("issue", ISSUE_URL, {
                    "year": 2026, "volume": "42", "issue": "1", "issue_state": None, "items": [issue_item],
                    "pagination": {"next_page_url": None, "terminal_observed": True},
                }),
            ]
            rows = []
            for index, capture in enumerate(captures):
                sanitized = sanitize_capture(capture)
                filename = f"raw/browser/capture-{index}.json"
                (run_root / filename).write_text(json.dumps(sanitized), encoding="utf-8")
                rows.append({"page_type": sanitized["page_type"], "source_url": sanitized["source_url"], "observed_at": sanitized["observed_at"], "complete": sanitized["complete"], "file": filename})
            index_path = browser_root / "index.jsonl"
            index_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            api_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:10.1093/bioinformatics/example1&format=json"
            supplement_record = {
                "source_url": api_url,
                "observed_at": "2026-10-05T08:30:00Z",
                "doi": doi,
                "pmid": "12345678",
                "journal": {"eissn": "1367-4811", "issn_values": ["0036-7481", "1367-4811"]},
                "matches_target_eissn": True,
                "within_collection_scope": True,
                "year_window_margin_only": False,
                "issue_year": 2026,
                "volume": "42",
                "issue": "1",
                "title": "oup listing title wins over the supplement title.",
                "authors": [
                    {"order": 2, "name": "Bo Researcher", "full_name_as_supplied": "Researcher, B"},
                    {"order": 1, "name": "Ada Example", "full_name_as_supplied": "Example, A"},
                ],
                "abstract": "API supplied abstract.",
                "dates_as_supplied": {"firstPublicationDate": {"value": "2025-12", "precision": "month"}},
                "pdf_links_as_supplied": [{"documentStyle": "pdf", "url": pdf_url}],
            }
            supplement_path = run_root / "supplement" / "normalized_records.jsonl"
            supplement_path.parent.mkdir()
            supplement_path.write_text(json.dumps(supplement_record) + "\n", encoding="utf-8")

            stats = collect(index_path, run_root, run_root, supplement_records_path=supplement_path)
            staging = [json.loads(line) for line in (run_root / "metadata_staging.jsonl").read_text().splitlines()]
            self.assertEqual(len(staging), 1)
            row = staging[0]
            self.assertEqual(row["title"], "OUP listing title wins over the supplement title")
            self.assertEqual(row["authors"], ["Ada Example", "Bo Researcher"])
            self.assertEqual(row["abstract"], "API supplied abstract.")
            self.assertEqual(row["publication_date"], "2025-12")
            self.assertEqual(row["publication_date_precision"], "month")
            self.assertEqual(row["pdf_url"], pdf_url)
            self.assertEqual(row["document_type"], "Applications Notes")
            self.assertEqual(row["field_provenance"]["authors"]["source_url"], api_url)
            self.assertEqual(row["field_provenance"]["abstract"]["observed_at"], "2026-10-05T08:30:00Z")
            self.assertEqual(stats["europe_pmc_exact_matches"], 1)

            crossref_request = "https://api.crossref.org/journals/1367-4811/works?cursor=%2A"
            crossref_record = {
                "DOI": doi,
                "ISSN": ["1367-4811"],
                "title": ["oup listing title wins over the supplement title."],
                "volume": "42",
                "issue": "1",
                "published-online": {"date-parts": [[2025, 12, 1]]},
                "link": [
                    {"URL": am_url, "content-type": "application/pdf", "content-version": "am", "intended-application": "syndication"},
                    {"URL": vor_url, "content-type": "application/pdf", "content-version": "vor", "intended-application": "syndication"},
                ],
                "license_entries": [{"URL": "https://creativecommons.org/licenses/by/4.0/", "content-version": "vor"}],
                "request_url": crossref_request,
                "requested_at_utc": "2026-10-05T08:35:00Z",
                "observed_at_utc": "2026-10-05T08:35:02Z",
            }
            crossref_path = run_root / "crossref" / "records.jsonl"
            crossref_path.parent.mkdir()
            neighboring_journal_record = {
                **crossref_record,
                "DOI": "10.1093/bioadv/vbaf230",
                "title": ["A Bioinformatics Advances record in the mixed journal feed"],
                "link": [{
                    "URL": "https://academic.oup.com/bioinformaticsadvances/article-pdf/1/1/vbaf230/paper.pdf",
                    "content-type": "application/pdf",
                    "content-version": "vor",
                }],
            }
            crossref_path.write_text(
                json.dumps(crossref_record) + "\n" + json.dumps(neighboring_journal_record) + "\n",
                encoding="utf-8",
            )
            loaded_crossref = _load_crossref_records(crossref_path)
            self.assertEqual(len(loaded_crossref), 1)
            self.assertEqual(loaded_crossref[0]["vor_pdf_candidates"][0]["url"], vor_url)

            collect(index_path, run_root, run_root, supplement_records_path=supplement_path, crossref_records_path=crossref_path)
            crossref_staged = json.loads((run_root / "metadata_staging.jsonl").read_text().splitlines()[0])
            self.assertEqual(crossref_staged["pdf_url"], vor_url)
            self.assertEqual(crossref_staged["crossref_pdf_content_version"], "vor")
            self.assertEqual(crossref_staged["crossref_pdf_license"][0]["URL"], "https://creativecommons.org/licenses/by/4.0/")
            self.assertEqual(crossref_staged["online_publication_date"], "2025-12-01")
            self.assertEqual(crossref_staged["online_publication_date_precision"], "day")
            self.assertEqual(crossref_staged["field_provenance"]["pdf_discovery_status"]["source_url"], crossref_request)

            supplement_record["pdf_links_as_supplied"] = []
            supplement_path.write_text(json.dumps(supplement_record) + "\n", encoding="utf-8")
            crossref_record["link"] = [
                {"URL": am_url, "content-type": "application/pdf", "content-version": "am", "intended-application": "syndication"},
            ]
            crossref_path.write_text(json.dumps(crossref_record) + "\n", encoding="utf-8")
            collect(index_path, run_root, run_root, supplement_records_path=supplement_path, crossref_records_path=crossref_path)
            staged_without_pdf = json.loads((run_root / "metadata_staging.jsonl").read_text().splitlines()[0])
            unresolved = [json.loads(line) for line in (run_root / "unresolved.jsonl").read_text().splitlines()]
            self.assertIsNone(staged_without_pdf["pdf_url"])
            self.assertEqual(staged_without_pdf["pdf_discovery_status"], "metadata_only")
            self.assertIn("oup_article_detail_pdf_check_pending", {row["kind"] for row in unresolved})
            self.assertIsNone(staged_without_pdf["crossref_pdf_content_version"])
            join = json.loads((run_root / "crossref_join_report.jsonl").read_text().splitlines()[0])
            self.assertEqual(join["observed_am_pdf_count"], 1)
            self.assertEqual(join["observed_vor_pdf_count"], 0)

    def test_duplicate_europe_pmc_doi_uses_exact_title_then_issue_identity(self) -> None:
        records = [
            {
                "source_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=doi",
                "doi": "10.1093/bioinformatics/btab223", "pmid": "34695175", "title": "GEM: scalable and flexible gene-environment interaction analysis in millions of samples.",
                "volume": "37", "issue": "20", "pmcid": "PMC9000001", "issue_year": 2021,
            },
            {
                "source_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=doi",
                "doi": "10.1093/bioinformatics/btab223", "pmid": "34037712", "title": "CLUE: Exact maximal reduction of kinetic models by constrained lumping of differential equations.",
                "volume": None, "issue": None, "pmcid": None, "issue_year": 2021,
            },
        ]
        selected, matched_by, error, audit = _match_europe_pmc(
            "10.1093/bioinformatics/btab223", None, records,
            title="GEM: scalable and flexible gene-environment interaction analysis in millions of samples",
            volume="37", issue="20",
        )
        self.assertIsNone(error)
        self.assertEqual(matched_by, "doi")
        self.assertEqual(selected["pmid"], "34695175")
        self.assertEqual(audit["exact_identifier_candidate_count"], 2)
        self.assertTrue(audit["candidates"][0]["selected"])
        self.assertEqual(audit["selection_rule"], "exact normalized title; exact volume; exact issue")

        oup_title = "GEM: scalable and flexible gene–environment interaction analysis in millions of samples"
        selected, _, error, audit = _match_europe_pmc(
            "10.1093/bioinformatics/btab223", None, records,
            title=oup_title, volume="37", issue="20",
        )
        self.assertIsNone(error)
        self.assertEqual(selected["pmid"], "34695175")
        self.assertIn("en-dash normalization", audit["selection_rule"])
        self.assertEqual(audit["metadata_discrepancies"][0]["oup_value"], oup_title)
        for title, volume, issue in ((oup_title, None, "20"), (oup_title, "37", "19"),
                                     (oup_title.replace("millions", "billions"), "37", "20")):
            with self.subTest(title=title, volume=volume, issue=issue):
                selected, _, error, _ = _match_europe_pmc(
                    "10.1093/bioinformatics/btab223", None, records,
                    title=title, volume=volume, issue=issue,
                )
                self.assertIsNone(selected)
                self.assertEqual(error, "exact_identifier_title_conflict")
        ambiguous_records = records + [{**records[0], "pmid": "99999999"}]
        selected, _, error, _ = _match_europe_pmc(
            "10.1093/bioinformatics/btab223", None, ambiguous_records,
            title=oup_title, volume="37", issue="20",
        )
        self.assertIsNone(selected)
        self.assertEqual(error, "exact_identifier_title_conflict")

        btx_records = [
            {
                "source_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=doi",
                "doi": "10.1093/bioinformatics/btx801", "pmid": "29240876", "title": "Machine learning for classifying tuberculosis drug-resistance from DNA sequencing data.",
                "volume": "34", "issue": "10", "pmcid": "PMC6000001", "issue_year": 2018,
            },
            {
                "source_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=doi",
                "doi": "10.1093/bioinformatics/btx801", "pmid": None, "title": "Machine Learning for Classifying Tuberculosis Drug-Resistance from DNA Sequencing Data",
                "volume": None, "issue": None, "pmcid": None, "issue_year": 2017,
            },
        ]
        selected, _, error, audit = _match_europe_pmc(
            "10.1093/bioinformatics/btx801", None, btx_records,
            title="Machine Learning for Classifying Tuberculosis Drug-Resistance from DNA Sequencing Data.",
            volume="34", issue="10",
        )
        self.assertIsNone(error)
        self.assertEqual(selected["pmid"], "29240876")
        self.assertEqual(audit["exact_identifier_candidate_count"], 2)
        self.assertTrue(audit["candidates"][0]["selected"])

    def test_europe_pmc_margin_rows_are_loaded_only_for_exact_supplement_matching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "normalized_records.jsonl"
            row = {
                "source_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=doi",
                "observed_at": NOW,
                "doi": "10.1093/bioinformatics/margin",
                "pmid": "11111111",
                "journal": {"eissn": "1367-4811", "issn_values": ["1367-4811"]},
                "within_collection_scope": False,
                "year_window_margin_only": True,
                "issue_year": 2014,
                "title": "A title for a 2015 OUP issue item",
                "authors": [],
                "dates_as_supplied": {},
                "pdf_links_as_supplied": [],
            }
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            loaded = _load_europe_pmc_records(path)
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0]["year_window_margin_only"])

            no_doi_row = {
                "source_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:12345678&format=json",
                "observed_at": NOW,
                "doi": None,
                "pmid": "12345678",
                "journal": {"eissn": "1367-4811", "issn_values": ["1367-4811"]},
                "within_collection_scope": False,
                "within_requested_year_window": True,
                "needs_expected_identity_join_for_scope": True,
                "year_window_margin_only": False,
                "issue_year": 2020,
                "title": "A DOI-less record requiring an OUP PMID match",
                "authors": [],
                "dates_as_supplied": {},
                "pdf_links_as_supplied": [],
            }
            path.write_text(json.dumps(no_doi_row) + "\n", encoding="utf-8")
            loaded = _load_europe_pmc_records(path)
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0]["needs_expected_identity_join_for_scope"])
            selected, matched_by, error, _audit = _match_europe_pmc(None, "12345678", loaded)
            self.assertIsNone(error)
            self.assertEqual(matched_by, "pmid")
            self.assertEqual(selected["pmid"], "12345678")


if __name__ == "__main__":
    unittest.main()

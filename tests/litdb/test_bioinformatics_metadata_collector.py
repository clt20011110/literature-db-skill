from __future__ import annotations

import http.client
import hashlib
import json
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
    def test_source_urls_drop_only_observed_volume_browse_parameter(self) -> None:
        capture = make_capture("issue", ISSUE_URL + "?browseBy=volume", {
            "year": 2026, "volume": "42", "issue": "1", "items": [],
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
            "review-article", "review article",
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
        ):
            with self.subTest(observed_nonresearch_section=label):
                self.assertEqual(classify_scope(label, "Publisher-listed item"), ("exclude", reason))
        self.assertEqual(classify_scope("Original Paper", "A novel author index algorithm"), ("include", None))
        self.assertEqual(classify_scope("Conference proceedings", "Author Index"), ("exclude", "front_matter"))
        self.assertEqual(classify_scope("Original Paper", "Letters in DNA sequences"), ("include", None))
        for label in ("research-article", "research article", "review-article", "review article", "Review"):
            with self.subTest(europe_pmc_type=label):
                self.assertEqual(_europe_pmc_scope_fallback_type([label]), label)
        self.assertEqual(_europe_pmc_scope_fallback_type([" research-article "]), " research-article ")
        self.assertIsNone(_europe_pmc_scope_fallback_type(["Journal Article"]))
        for conflict in (
            "Editorial", "Correction", "Corrigendum", "Erratum", "Retraction", "Retracted Publication", "Letter", "Comment", "News",
        ):
            with self.subTest(conflicting_europe_pmc_type=conflict):
                self.assertIsNone(_europe_pmc_scope_fallback_type(["review-article", conflict]))

    def _collect_single_europe_pmc_type_case(
        self,
        *,
        oup_section: str,
        publication_types: list[str],
        record_doi: str = "10.1093/bioinformatics/btu769",
        oup_doi: str = "10.1093/bioinformatics/btu769",
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
            }))
            capture_relpath = "raw/browser/issue.json"
            (run_root / capture_relpath).write_text(json.dumps(capture), encoding="utf-8")
            index_path = browser_root / "index.jsonl"
            index_path.write_text(json.dumps({
                "page_type": "issue",
                "source_url": capture["source_url"],
                "observed_at": capture["observed_at"],
                "complete": capture["complete"],
                "file": capture_relpath,
            }) + "\n", encoding="utf-8")
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
                "abstract": "A substantive review of structural bioinformatics and computational biophysics.",
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
        complete, blockers = _listing_chain_state([issue_first], "issue")
        self.assertFalse(complete)
        self.assertIn("pagination_next_page_not_captured", {row["kind"] for row in blockers})
        complete, blockers = _listing_chain_state([issue_first, issue_terminal], "issue")
        self.assertTrue(complete)
        self.assertEqual(blockers, [])

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

    def test_reviewed_misfiled_issue_needs_actual_anchor_and_captured_identity(self) -> None:
        url = "https://academic.oup.com/bioinformatics/issue/35/15"
        annual_url = "https://academic.oup.com/bioinformatics/issue-archive/2019"
        link = {"year": 2018, "volume": "35", "issue": "15", "url": url, "label": "Volume 35, Issue 15"}
        raw_link_capture = make_capture("archive", "https://academic.oup.com/bioinformatics/issue-archive/2018", {
            "year_links": [], "issues": [link],
        })
        issue = sanitize_capture(make_capture("issue", url, {
            "year": 2019, "volume": "35", "issue": "15", "items": [],
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

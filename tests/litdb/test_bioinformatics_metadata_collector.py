from __future__ import annotations

import http.client
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
    _capture_signature,
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
        for label in ("Original Paper", "Applications Notes", "Review", "Research Supplement"):
            with self.subTest(label=label):
                self.assertEqual(classify_scope(label, "Research title"), ("include", None))
        self.assertEqual(classify_scope("Journal Article", "PICNIC web server"), ("unresolved", None))
        self.assertEqual(classify_scope("Editorial", "Editorial notes on the journal"), ("exclude", "editorial"))
        self.assertEqual(classify_scope("Correction", "Correction to an article"), ("exclude", "correction"))
        self.assertEqual(classify_scope("Corrections", "A publisher correction"), ("exclude", "correction"))
        self.assertEqual(classify_scope("Front Matter", "Journal front matter"), ("exclude", "front_matter"))

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

    def test_archive_directory_evidence_covers_year_index_and_observed_2026_volume_dropdown(self) -> None:
        annual_url = "https://academic.oup.com/bioinformatics/issue-archive/2026"
        observed_dropdown = [
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
            "year_links": [{"year": 2026, "url": annual_url, "label": "2026"}],
            "issues": [],
            "directory": {"kind": "year_index", "complete": True},
        }))
        annual = sanitize_capture(make_capture("archive", annual_url, {
            "year_links": [],
            "issues": [],
            "directory": {
                "kind": "year",
                "year": 2026,
                "entry_url": annual_url,
                "complete": True,
                "volume_links": [{"volume": "42", "url": ISSUE_URL}],
            },
        }))
        volume = sanitize_capture(make_capture("archive", ISSUE_URL, {
            "year_links": [],
            "issues": [
                {"year": 2026, "volume": "42", "issue": issue, "url": url, "label": issue}
                for issue, url in observed_dropdown
            ],
            "directory": {"kind": "volume", "year": 2026, "volume": "42", "complete": True},
        }))
        archives = [year_index, annual, volume]
        archive_issues, year_links = _archive_issue_map(archives)
        complete, blockers, year_count, volume_count = _archive_directory_state(
            archives, {2026}, year_links, archive_issues,
        )
        self.assertTrue(complete, blockers)
        self.assertEqual(len(archive_issues), 12)
        self.assertEqual(year_count, 1)
        self.assertEqual(volume_count, 1)

        older_capture = sanitize_capture(make_capture("archive", ARCHIVE_URL, {
            "year_links": [{"year": 2026, "url": annual_url, "label": "2026"}],
            "issues": [],
        }))
        refreshed_capture = sanitize_capture(make_capture("archive", ARCHIVE_URL, {
            "year_links": [{"year": 2026, "url": annual_url, "label": "2026"}],
            "issues": [],
            "directory": {"kind": "year_index", "complete": True},
        }))
        self.assertEqual(_capture_signature(older_capture), _capture_signature(refreshed_capture))

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

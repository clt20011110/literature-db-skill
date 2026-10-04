from __future__ import annotations

import gzip
import json
import tempfile
import threading
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from tools.collect_cvf_metadata import (
    CVFDetailParser,
    CVFListingParser,
    RequestRateLimiter,
    apply_abstract_supplement,
    build_official_field_conflicts,
    collect_details,
    collect_rows,
    is_official_violation_notice,
    make_staging_row,
    official_listing_targets,
    official_menu_editions,
    open_cache,
    reconcile_pdf_link,
    reconcile_author_names,
    reconcile_refreshed_list_authors,
    approved_official_author_form_differences,
    classify_requested_editions,
    refresh_cached_listing_authors,
    record_cache_row,
    response_is_access_barrier,
    write_outputs,
    write_jsonl,
    write_gzip_bytes,
)


class CVFCollectorAdapterTests(unittest.TestCase):
    def test_only_officially_observed_missing_author_suffixes_are_reconciled(self) -> None:
        detail_authors = ["John W. Fisher", "James S. Supancic", "John Fisher"]
        list_authors = ["John W. Fisher III", "James S. Supancic III", "John Fisher Jr."]
        record = {
            "source_native_id": "CVPR2015:author-suffixes",
            "authors": detail_authors,
            "field_provenance": {"authors": {
                "source_url": "https://openaccess.thecvf.com/paper.html",
                "observed_at": "2026-10-04T02:00:00Z",
                "method": "citation_author_meta",
            }},
        }
        expected = {
            "authors": list_authors,
            "source_url": "https://openaccess.thecvf.com/CVPR2015.py",
            "source_observed_at": "2026-10-04T01:00:00Z",
        }
        normalized, evidence = reconcile_author_names(record, expected)
        self.assertEqual(normalized["authors"], list_authors)
        self.assertEqual(normalized["field_provenance"]["authors"]["source_url"], expected["source_url"])
        self.assertEqual(normalized["field_provenance"]["authors"]["observed_at"], expected["source_observed_at"])
        self.assertEqual(evidence["detail_authors"], detail_authors)
        self.assertEqual(evidence["detail_observed_at"], "2026-10-04T02:00:00Z")
        self.assertEqual(len(evidence["approved_author_form_differences"]), 3)

    def test_general_author_variants_are_not_silently_rewritten(self) -> None:
        detail = ["Jose M.Oramas"]
        official_list = ["José M. Oramas"]
        self.assertEqual(approved_official_author_form_differences(detail, official_list), [])

    def test_only_trailing_comma_or_list_final_token_author_differences_are_approved(self) -> None:
        differences = approved_official_author_form_differences(
            ["Aakanksha,", "Mausam,", "Aryan,", "Nikitha,"],
            ["Aakanksha", "Mausam", "Aryan", "Nikitha SR"],
        )
        self.assertEqual([item["kind"] for item in differences], [
            "detail_only_trailing_comma",
            "detail_only_trailing_comma",
            "detail_only_trailing_comma",
            "official_list_final_token_only",
        ])
        self.assertEqual(differences[-1]["list_final_token"], "SR")

    def test_official_conflict_receipt_keeps_detail_title_and_records_list_author_choice(self) -> None:
        record = {
            "source_native_id": "CVPR2024:paper",
            "title": "Just Add π!",
            "authors": ["Aakanksha,"],
            "field_provenance": {
                "title": {"source_url": "https://openaccess.thecvf.com/detail.html", "observed_at": "2026-10-04T02:00:00Z", "method": "citation_title_meta"},
                "authors": {"source_url": "https://openaccess.thecvf.com/detail.html", "observed_at": "2026-10-04T02:00:00Z", "method": "citation_author_meta"},
            },
        }
        expected = {
            "source_native_id": record["source_native_id"],
            "title": "Just Add ?!",
            "authors": ["Aakanksha"],
            "source_url": "https://openaccess.thecvf.com/CVPR2024?day=all",
            "source_observed_at": "2026-10-04T01:00:00Z",
        }
        selected, author_evidence = reconcile_author_names(record, expected)
        conflicts = build_official_field_conflicts(record, selected, expected, "cvpr")
        self.assertEqual(selected["title"], "Just Add π!")
        self.assertEqual(selected["authors"], ["Aakanksha"])
        self.assertEqual(author_evidence["approved_author_form_differences"][0]["kind"], "detail_only_trailing_comma")
        self.assertEqual({row["field"] for row in conflicts}, {"title", "authors"})
        title_conflict = next(row for row in conflicts if row["field"] == "title")
        author_conflict = next(row for row in conflicts if row["field"] == "authors")
        self.assertEqual(title_conflict["resolution_method"], "official_detail_value_retained_for_unapproved_difference")
        self.assertEqual(title_conflict["official_list_value"], expected["title"])
        self.assertEqual(author_conflict["resolution_method"], "official_list_used_for_narrow_author_terminal_form_difference")

    def test_cached_list_fallback_author_forms_refresh_without_mutating_input(self) -> None:
        prior_authors = ["Hsin-Ying Lee", "Hung-Yu Tseng", "Ming-Hsuan Yang"]
        list_authors = ["Hsin-Ying Lee", "Hung-Yu Tseng", "Hsin-Ying Lee", "Ming-Hsuan Yang"]
        record = {
            "source_native_id": "CVPR2024:lee-paper",
            "authors": prior_authors,
            "field_provenance": {"authors": {
                "source_url": "https://openaccess.thecvf.com/CVPR2024?day=all",
                "observed_at": "2026-10-04T06:18:32Z",
                "method": "official_cvf_list_author_forms",
            }},
        }
        expected = {
            "source_native_id": record["source_native_id"],
            "authors": list_authors,
            "source_url": "https://openaccess.thecvf.com/CVPR2024?day=all",
            "source_observed_at": "2026-10-04T06:18:32Z",
        }
        result, evidence = reconcile_refreshed_list_authors(record, expected)
        self.assertEqual(result["authors"], list_authors)
        self.assertEqual(record["authors"], prior_authors)
        self.assertEqual(evidence["prior_cached_list_authors"], prior_authors)
        self.assertEqual(evidence["selected_refreshed_list_authors"], list_authors)

    def test_violation_notice_classification_uses_exact_title_prefix(self) -> None:
        self.assertTrue(is_official_violation_notice(
            "Notice of Violation of IEEE Publication Principles: Original title"
        ))
        self.assertFalse(is_official_violation_notice(
            "Research on violations of IEEE Publication Principles"
        ))
        self.assertFalse(is_official_violation_notice(
            "A Notice of Violation of IEEE Publication Principles"
        ))

    def test_rate_limiter_enforces_at_most_three_request_starts_per_second(self) -> None:
        limiter = RequestRateLimiter(0.1)
        self.assertGreaterEqual(limiter.minimum_spacing, 1.0 / 3.0)

    def test_rate_limiter_cancels_reserved_request_after_access_barrier(self) -> None:
        limiter = RequestRateLimiter(1.0)
        limiter.wait_turn()
        waiting_started = threading.Event()
        errors = []

        def wait_for_request_turn() -> None:
            waiting_started.set()
            try:
                limiter.wait_turn()
            except RuntimeError as exc:
                errors.append(str(exc))

        waiter = threading.Thread(target=wait_for_request_turn)
        waiter.start()
        self.assertTrue(waiting_started.wait(timeout=1))
        limiter.stop_after_access_barrier()
        waiter.join(timeout=1)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(errors, ["request cancelled after official access barrier"])

    def test_menu_uses_only_main_conference_editions_including_py_suffix(self) -> None:
        menu = b"""<a href='CVPR2018.py'>Main Conference</a>
        <a href='CVPR2018_workshops.py'>Workshops</a>
        <a href='ICCV2025'>Main Conference</a>"""
        found = official_menu_editions(menu.decode(), "cvpr", 2015, 2026)
        self.assertEqual(found, {2018: "https://openaccess.thecvf.com/CVPR2018.py"})

    def test_menu_coverage_fails_for_interior_gaps_but_records_unpublished_tail(self) -> None:
        published = {2015: "u15", 2016: "u16", 2018: "u18"}
        with self.assertRaisesRegex(RuntimeError, "2017"):
            classify_requested_editions("cvpr", 2015, 2020, published)

        complete_through_2018 = {2015: "u15", 2016: "u16", 2017: "u17", 2018: "u18"}
        selected, not_yet_listed, frontier = classify_requested_editions(
            "cvpr", 2015, 2020, complete_through_2018,
        )
        self.assertEqual(list(selected), [2015, 2016, 2017, 2018])
        self.assertEqual(not_yet_listed, [2019, 2020])
        self.assertEqual(frontier, 2018)

    def test_iccv_legal_years_are_odd_and_future_only_range_is_not_an_error(self) -> None:
        selected, not_yet_listed, frontier = classify_requested_editions(
            "iccv", 2024, 2026, {2023: "u23"},
        )
        self.assertEqual(selected, {})
        self.assertEqual(not_yet_listed, [2025])
        self.assertEqual(frontier, 2023)

    def test_listing_targets_follow_visible_all_papers_link(self) -> None:
        html = b"""<a href='?day=1'>Day 1</a>
        <a href='?day=all'>All Papers</a>"""
        targets = official_listing_targets("https://openaccess.thecvf.com/CVPR2025", html, [])
        self.assertEqual(targets, [("All Papers", "https://openaccess.thecvf.com/CVPR2025?day=all")])

    def test_old_edition_uses_visible_day_pages_not_guessed_day_all(self) -> None:
        html = b"""<a href='CVPR2018.py?day=1'>Day 1</a>
        <a href='CVPR2018.py?day=2'>Day 2</a>
        <a href='CVPR2018.py?day=3'>Day 3</a>"""
        targets = official_listing_targets("https://openaccess.thecvf.com/CVPR2018.py", html, [])
        self.assertEqual(len(targets), 3)
        self.assertEqual([label for label, _ in targets], ["Day 1", "Day 2", "Day 3"])
        self.assertTrue(all("day=all" not in url for _, url in targets))

    def test_root_rows_are_used_when_no_pagination_is_exposed(self) -> None:
        target = official_listing_targets(
            "https://openaccess.thecvf.com/CVPR2015",
            b"<html><body>listing</body></html>",
            [{"title": "observed row"}],
        )
        self.assertEqual(target, [("Edition listing", "https://openaccess.thecvf.com/CVPR2015")])

    def test_listing_parser_extracts_title_author_and_official_pdf(self) -> None:
        html = b"""<dt class='ptitle'><br><a href='/content_cvpr_2018/html/a_paper.html'>Example title</a></dt>
        <dd><input name='query_author' value='Doe, Jane'></dd>
        <dd><a href='/content_cvpr_2018/papers/a_paper.pdf'>pdf</a></dd>"""
        parser = CVFListingParser("https://openaccess.thecvf.com/CVPR2018.py")
        parser.feed(html.decode())
        parser.close()
        self.assertEqual(parser.rows[0]["title"], "Example title")
        self.assertEqual(parser.rows[0]["authors"], ["Doe, Jane"])
        self.assertEqual(parser.rows[0]["pdf_url"], "https://openaccess.thecvf.com/content_cvpr_2018/papers/a_paper.pdf")

    def test_listing_parser_preserves_order_and_repeated_query_author_values(self) -> None:
        html = b"""<dt class='ptitle'><a href='/content/CVPR2024/html/paper.html'>Example</a></dt>
        <dd><input name='query_author' value='Lee, Hsin-Ying'>
        <input name='query_author' value='Tseng, Hung-Yu'>
        <input name='query_author' value='Lee, Hsin-Ying'></dd>"""
        parser = CVFListingParser("https://openaccess.thecvf.com/CVPR2024?day=all")
        parser.feed(html.decode())
        parser.close()
        self.assertEqual(parser.rows[0]["authors"], ["Lee, Hsin-Ying", "Tseng, Hung-Yu", "Lee, Hsin-Ying"])

    def test_refresh_cached_listing_authors_preserves_source_id_set_and_old_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected_dir = root / "expected"
            expected_dir.mkdir()
            page_url = "https://openaccess.thecvf.com/CVPR2024?day=all"
            landing_url = "https://openaccess.thecvf.com/content/CVPR2024/html/paper.html"
            row = {
                "venue_id": "cvpr",
                "year": 2024,
                "source_native_id": "CVPR2024:paper",
                "title": "Example",
                "authors": ["Lee, Hsin-Ying", "Tseng, Hung-Yu"],
                "landing_url": landing_url,
                "pdf_url": "https://openaccess.thecvf.com/content/CVPR2024/papers/paper.pdf",
                "source_url": page_url,
                "source_observed_at": "2026-10-04T01:00:00Z",
            }
            manifest = expected_dir / "2024.jsonl.gz"
            write_jsonl(manifest, [row])
            original_manifest = manifest.read_bytes()
            listing = b"""<dt class='ptitle'><a href='/content/CVPR2024/html/paper.html'>Example</a></dt>
            <dd><input name='query_author' value='Lee, Hsin-Ying'>
            <input name='query_author' value='Tseng, Hung-Yu'>
            <input name='query_author' value='Lee, Hsin-Ying'></dd>"""
            write_gzip_bytes(root / "evidence" / "official_listings" / "2024_page-1.html.gz", listing)
            identity_hash = __import__("hashlib").sha256("CVPR2024:paper".encode()).hexdigest()
            (root / "checkpoint.json").write_text(json.dumps({"enumeration": {"2024": {
                "status": "complete",
                "edition_url": "https://openaccess.thecvf.com/CVPR2024",
                "source_urls": [page_url],
                "source_item_set_sha256": identity_hash,
            }}}))

            receipt = refresh_cached_listing_authors(root, "cvpr", 2024, 2024)

            refreshed = json.loads(__import__("gzip").open(manifest, "rt").read())
            differences = [json.loads(line) for line in (root / "listing_author_refresh" / "author_list_differences.jsonl").read_text().splitlines()]
            snapshot = root / "listing_author_refresh" / "expected_before" / "2024.jsonl.gz"
            self.assertEqual(refreshed["authors"], ["Lee, Hsin-Ying", "Tseng, Hung-Yu", "Lee, Hsin-Ying"])
            self.assertEqual(snapshot.read_bytes(), original_manifest)
            self.assertEqual(len(differences), 1)
            self.assertEqual(receipt["id_set_unchanged"], True)

    def test_research_mentioning_captcha_is_not_a_barrier(self) -> None:
        paper = b"""<meta name='citation_title' content='CAPTCHA Recognition'>
        <meta name='citation_author' content='Example, A.'><div id='abstract'>We study CAPTCHA.</div>"""
        self.assertFalse(response_is_access_barrier(None, paper))

    def test_challenge_structure_or_missing_article_metadata_is_a_barrier(self) -> None:
        self.assertTrue(response_is_access_barrier(None, b"<form id='challenge-form'></form>"))
        self.assertTrue(response_is_access_barrier("detail_page_missing_article_metadata", b"<html>unrecognized page</html>"))

    def test_detail_page_missing_citation_title_uses_list_fallback_provenance(self) -> None:
        page = CVFDetailParser()
        page.feed("<div id='abstract'>Article with no author metadata.</div>")
        page.close()
        expected = {
            "source_native_id": "CVPR2018:paper",
            "year": 2018,
            "title": "List title",
            "authors": ["Jane Doe"],
            "landing_url": "https://openaccess.thecvf.com/content_cvpr_2018/html/paper.html",
            "pdf_url": "https://openaccess.thecvf.com/content_cvpr_2018/papers/paper.pdf",
            "source_url": "https://openaccess.thecvf.com/CVPR2018.py?day=1",
            "source_observed_at": "2026-10-04T01:00:00Z",
        }
        row, _ = make_staging_row("cvpr", expected, page, "2026-10-04T02:00:00Z", None)
        self.assertEqual(row["title"], "List title")
        self.assertEqual(row["field_provenance"]["title"]["source_url"], expected["source_url"])
        self.assertEqual(row["field_provenance"]["title"]["observed_at"], expected["source_observed_at"])
        self.assertEqual(row["field_provenance"]["authors"]["source_url"], expected["source_url"])
        self.assertEqual(row["missing_fields"]["doi"]["reason_code"], "not_present_on_official_page")

    def test_failed_detail_missingness_points_to_failed_detail_check(self) -> None:
        expected = {
            "source_native_id": "CVPR2018:paper",
            "year": 2018,
            "title": "List title",
            "authors": ["Jane Doe"],
            "landing_url": "https://openaccess.thecvf.com/content_cvpr_2018/html/paper.html",
            "pdf_url": "https://openaccess.thecvf.com/content_cvpr_2018/papers/paper.pdf",
            "source_url": "https://openaccess.thecvf.com/CVPR2018.py?day=1",
            "source_observed_at": "2026-10-04T01:00:00Z",
        }
        failed_at = "2026-10-04T03:00:00Z"
        row, _ = make_staging_row("cvpr", expected, None, failed_at, "curl_exit_22:404")
        self.assertEqual(row["title"], "List title")
        self.assertEqual(row["field_provenance"]["title"]["source_url"], expected["source_url"])
        self.assertEqual(row["field_provenance"]["title"]["observed_at"], expected["source_observed_at"])
        for field in ("abstract", "publication_date", "doi"):
            self.assertEqual(row["missing_fields"][field]["reason_code"], "source_unavailable")
            self.assertEqual(row["missing_fields"][field]["source_url"], expected["landing_url"])
            self.assertEqual(row["missing_fields"][field]["observed_at"], failed_at)

    def test_observed_https_list_pdf_anchor_wins_over_legacy_detail_citation(self) -> None:
        page = CVFDetailParser()
        page.feed("<meta name='citation_title' content='Example'><meta name='citation_author' content='Doe, Jane'><meta name='citation_pdf_url' content='http://www.cv-foundation.org/openaccess/papers/example.pdf'>")
        page.close()
        page._html_for_doi = ""
        expected = {
            "source_native_id": "CVPR2018:paper",
            "year": 2018,
            "title": "Example",
            "authors": ["Jane Doe"],
            "landing_url": "https://openaccess.thecvf.com/content_cvpr_2018/html/example.html",
            "pdf_url": "https://openaccess.thecvf.com/content_cvpr_2018/papers/example.pdf",
            "source_url": "https://openaccess.thecvf.com/CVPR2018.py?day=1",
            "source_observed_at": "2026-10-04T01:00:00Z",
        }
        row, _ = make_staging_row("cvpr", expected, page, "2026-10-04T02:00:00Z", None)
        self.assertEqual(row["pdf_url"], expected["pdf_url"])
        self.assertEqual(row["field_provenance"]["pdf_discovery_status"]["source_url"], expected["source_url"])
        self.assertEqual(row["observed_pdf_url_evidence"]["observations"][1]["url"], "http://www.cv-foundation.org/openaccess/papers/example.pdf")

    def test_cached_legacy_pdf_is_reconciled_and_preserved_as_evidence(self) -> None:
        record = {
            "source_native_id": "CVPR2015:paper",
            "landing_url": "https://openaccess.thecvf.com/content_cvpr_2015/html/example.html",
            "pdf_url": "http://www.cv-foundation.org/openaccess/content_cvpr_2015/papers/example.pdf",
            "observed_at": "2026-10-04T02:00:00Z",
            "pdf_discovery_status": "visible_url",
            "field_provenance": {"pdf_discovery_status": {
                "source_url": "https://openaccess.thecvf.com/content_cvpr_2015/html/example.html",
                "observed_at": "2026-10-04T02:00:00Z",
                "method": "citation_pdf_url_meta",
            }},
        }
        expected = {
            "source_native_id": record["source_native_id"],
            "pdf_url": "https://openaccess.thecvf.com/content_cvpr_2015/papers/example.pdf",
            "source_url": "https://openaccess.thecvf.com/CVPR2015.py",
            "source_observed_at": "2026-10-04T01:00:00Z",
        }
        normalized, report = reconcile_pdf_link(record, expected)
        self.assertEqual(normalized["pdf_url"], expected["pdf_url"])
        self.assertEqual(normalized["field_provenance"]["pdf_discovery_status"]["source_url"], expected["source_url"])
        self.assertEqual(report["observations"][0]["url"], expected["pdf_url"])
        self.assertEqual(report["observations"][1]["url"], record["pdf_url"])

    def test_official_bulk_abstract_fallback_keeps_its_own_provenance(self) -> None:
        record = {
            "source_native_id": "ICCV2025:paper",
            "abstract": None,
            "missing_fields": {"abstract": {"reason_code": "source_unavailable"}},
            "field_provenance": {"doi": {"source_url": "https://openaccess.thecvf.com/paper.html", "observed_at": "2026-10-04T02:00:00Z", "method": "official_detail_metadata_check"}},
        }
        supplement = {
            "abstract": "Cached official abstract.",
            "source_url": "https://iccv.thecvf.com/static/virtual/data/iccv-2025-orals-posters.json",
            "observed_at": "2026-10-04T06:21:51Z",
            "match_method": "normalized_unique_title_ordered_authors",
        }
        result = apply_abstract_supplement(record, supplement)
        self.assertEqual(result["abstract"], supplement["abstract"])
        self.assertNotIn("abstract", result["missing_fields"])
        self.assertEqual(result["field_provenance"]["abstract"]["source_url"], supplement["source_url"])
        self.assertEqual(result["field_provenance"]["doi"], record["field_provenance"]["doi"])

    def test_write_outputs_preserves_enumeration_and_partial_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "checkpoint.json").write_text(json.dumps({"enumeration": {"2018": {"status": "complete"}}}))
            connection = open_cache(root / "cache.sqlite")
            row = {
                "source_native_id": "CVPR2018:paper-1",
                "venue_id": "cvpr",
                "year": 2018,
                "observed_at": "2026-10-04T01:00:00Z",
                "landing_url": "https://openaccess.thecvf.com/content_cvpr_2018/html/paper-1.html",
                "missing_fields": {},
                "title": "Example",
                "authors": ["A. Author"],
                "abstract": "An abstract.",
            }
            record_cache_row(connection, row, "fetched", None)
            connection.commit()
            expected = [
                {"source_native_id": row["source_native_id"], "year": 2018},
                {"source_native_id": "CVPR2018:paper-2", "year": 2018},
            ]
            result = write_outputs(root, "cvpr", connection, expected, "partial", row["source_native_id"])
            connection.close()
            self.assertEqual(result["run_status"], "partial")
            self.assertEqual(result["enumeration"]["2018"]["status"], "complete")

    def test_write_outputs_isolates_cache_to_current_expected_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = open_cache(root / "cache.sqlite")
            current = {
                "source_native_id": "CVPR2018:current",
                "venue_id": "cvpr",
                "year": 2018,
                "observed_at": "2026-10-04T01:00:00Z",
                "landing_url": "https://openaccess.thecvf.com/content_cvpr_2018/html/current.html",
                "missing_fields": {},
                "title": "Current paper",
                "authors": ["A. Author"],
                "abstract": "Current abstract.",
            }
            stale = {**current,
                "source_native_id": "CVPR2017:stale",
                "year": 2017,
                "title": "Outside requested scope",
            }
            record_cache_row(connection, current, "fetched", None)
            record_cache_row(connection, stale, "fetched", None)
            connection.commit()

            result = write_outputs(
                root, "cvpr", connection,
                [{"source_native_id": current["source_native_id"], "year": 2018}],
                "details_collected", current["source_native_id"],
            )
            connection.close()
            staged = [json.loads(line) for line in (root / "metadata_staging.jsonl").read_text().splitlines()]
            outside = [json.loads(line) for line in (root / "out_of_scope_cache.jsonl").read_text().splitlines()]
            self.assertEqual([row["source_native_id"] for row in staged], [current["source_native_id"]])
            self.assertEqual([row["source_native_id"] for row in outside], [stale["source_native_id"]])
            self.assertEqual(result["included_count"], 1)
            self.assertEqual(result["out_of_scope_cache_count"], 1)

    def test_write_outputs_applies_late_cached_official_abstract_supplement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = open_cache(root / "cache.sqlite")
            identity = "ICCV2025:cached-paper"
            landing_url = "https://openaccess.thecvf.com/content_ICCV_2025/html/paper.html"
            record = {
                "source_native_id": identity,
                "venue_id": "iccv",
                "year": 2025,
                "title": "Cached paper",
                "authors": ["A. Author"],
                "abstract": None,
                "doi": None,
                "publication_date": "2025",
                "pdf_url": "https://openaccess.thecvf.com/content_ICCV_2025/papers/paper.pdf",
                "pdf_discovery_status": "official_list_pdf_anchor",
                "landing_url": landing_url,
                "source_url": landing_url,
                "observed_at": "2026-10-04T02:00:00Z",
                "missing_fields": {"abstract": {"reason_code": "source_unavailable"}},
                "field_provenance": {
                    "title": {"source_url": landing_url, "observed_at": "2026-10-04T02:00:00Z", "method": "citation_title_meta"},
                    "authors": {"source_url": landing_url, "observed_at": "2026-10-04T02:00:00Z", "method": "citation_author_meta"},
                },
            }
            record_cache_row(connection, record, "fetched", None)
            connection.commit()
            (root / "official_abstract_supplements.jsonl").write_text(json.dumps({
                "source_native_id": identity,
                "abstract": "An independently matched cached official abstract.",
                "source_url": "https://iccv.thecvf.com/static/virtual/data/iccv-2025-orals-posters.json",
                "observed_at": "2026-10-04T06:21:51.968Z",
                "match_method": "normalized_unique_title_ordered_authors",
            }) + "\n")

            result = write_outputs(root, "iccv", connection, [{"source_native_id": identity, "year": 2025}], "details_collected", identity)
            cached_record = collect_rows(connection)[0][0]
            connection.close()
            staged = json.loads((root / "metadata_staging.jsonl").read_text().splitlines()[0])
            self.assertEqual(result["totals"]["abstract"], 1)
            self.assertEqual(cached_record["abstract"], "An independently matched cached official abstract.")
            self.assertEqual(cached_record["observed_at"], "2026-10-04T02:00:00Z")
            self.assertEqual(staged["field_provenance"]["abstract"]["source_url"], "https://iccv.thecvf.com/static/virtual/data/iccv-2025-orals-posters.json")

    def test_write_outputs_separates_exact_official_notice_from_included_papers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = open_cache(root / "cache.sqlite")
            source_url = "https://openaccess.thecvf.com/CVPR2015.py"
            landing_url = "https://openaccess.thecvf.com/content_cvpr_2015/html/paper.html"
            observed_at = "2026-10-04T02:00:00Z"
            notice = {
                "schema_version": "literature-metadata-staging-v1",
                "source_native_id": "CVPR2015:notice",
                "venue_id": "cvpr",
                "year": 2015,
                "title": "Notice of Violation of IEEE Publication Principles: Paper title",
                "authors": ["Jane Doe"],
                "abstract": "Official notice text followed by the previous abstract.",
                "doi": None,
                "publication_date": "2015",
                "pdf_url": "https://openaccess.thecvf.com/paper.pdf",
                "pdf_discovery_status": "visible_url",
                "landing_url": landing_url,
                "source_url": landing_url,
                "observed_at": observed_at,
                "missing_fields": {},
                "field_provenance": {
                    "title": {"source_url": landing_url, "observed_at": observed_at, "method": "citation_title_meta"},
                    "source_native_id": {"source_url": source_url, "observed_at": "2026-10-04T01:00:00Z", "method": "official_cvf_list_link"},
                },
            }
            included = {**notice,
                "source_native_id": "CVPR2015:included",
                "title": "Research on violations in vision systems",
                "abstract": "A complete research abstract.",
            }
            record_cache_row(connection, notice, "fetched", None)
            record_cache_row(connection, included, "fetched", None)
            connection.commit()
            expected = [
                {"source_native_id": "CVPR2015:notice", "year": 2015, "source_url": source_url, "source_observed_at": "2026-10-04T01:00:00Z"},
                {"source_native_id": "CVPR2015:included", "year": 2015, "source_url": source_url, "source_observed_at": "2026-10-04T01:00:00Z"},
            ]
            result = write_outputs(root, "cvpr", connection, expected, "details_collected", "CVPR2015:included")
            connection.close()

            staging = [json.loads(line) for line in (root / "metadata_staging.jsonl").read_text().splitlines()]
            exclusions = [json.loads(line) for line in (root / "metadata_exclusions.jsonl").read_text().splitlines()]
            self.assertEqual([row["source_native_id"] for row in staging], ["CVPR2015:included"])
            self.assertEqual(exclusions[0]["source_native_id"], "CVPR2015:notice")
            self.assertEqual(exclusions[0]["exclusion_reason_code"], "non_research_content")
            self.assertEqual(exclusions[0]["exclusion_evidence"]["official_title"], notice["title"])
            self.assertEqual(exclusions[0]["exclusion_evidence"]["official_abstract"], notice["abstract"])
            self.assertEqual(exclusions[0]["field_provenance"]["title"]["source_url"], landing_url)
            self.assertEqual(result["included_count"] + result["excluded_count"], result["expected_count"])
            self.assertEqual(result["fetched_detail_count"], 2)
            self.assertEqual(result["yearly"]["2015"]["fetched"], 2)

    def test_detail_collection_resumes_and_max_details_stays_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected_dir = root / "expected"
            expected_dir.mkdir()
            expected = []
            for number in (1, 2):
                expected.append({
                    "venue_id": "cvpr",
                    "year": 2015,
                    "source_native_id": f"CVPR2015:paper-{number}",
                    "title": f"Paper {number}",
                    "authors": ["Jane Doe"],
                    "landing_url": f"https://openaccess.thecvf.com/content_cvpr_2015/html/paper-{number}.html",
                    "pdf_url": f"https://openaccess.thecvf.com/content_cvpr_2015/papers/paper-{number}.pdf",
                    "source_url": "https://openaccess.thecvf.com/CVPR2015.py",
                    "source_observed_at": "2026-10-04T01:00:00Z",
                })
            write_jsonl(expected_dir / "2015.jsonl.gz", expected)
            detail = b"<meta name='citation_title' content='Paper'><meta name='citation_author' content='Doe, Jane'><meta name='citation_publication_date' content='2015'><div id='abstract'>A complete abstract.</div>"
            args = Namespace(
                venue="cvpr", start_year=2015, end_year=2015, run_root=root,
                max_details=1, retry_unresolved=False, concurrency=3,
                delay_seconds=1.0 / 3.0, timeout_seconds=5,
            )
            with patch("tools.collect_cvf_metadata.fetch_public_html", return_value=(detail, None)) as fetch:
                first = collect_details(args, RequestRateLimiter(1.0 / 3.0))
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(first["run_status"], "partial")
            self.assertEqual(first["staged_count"], 1)

            args.max_details = 0
            with patch("tools.collect_cvf_metadata.fetch_public_html", return_value=(detail, None)) as fetch:
                second = collect_details(args, RequestRateLimiter(1.0 / 3.0))
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(second["run_status"], "details_collected")
            self.assertEqual(second["staged_count"], 2)

    def test_detail_collection_refills_a_free_slot_without_waiting_for_slowest_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected_dir = root / "expected"
            expected_dir.mkdir()
            expected = []
            for number in range(1, 5):
                expected.append({
                    "venue_id": "cvpr",
                    "year": 2015,
                    "source_native_id": f"CVPR2015:paper-{number}",
                    "title": f"Paper {number}",
                    "authors": ["Jane Doe"],
                    "landing_url": f"https://openaccess.thecvf.com/content_cvpr_2015/html/paper-{number}.html",
                    "pdf_url": f"https://openaccess.thecvf.com/content_cvpr_2015/papers/paper-{number}.pdf",
                    "source_url": "https://openaccess.thecvf.com/CVPR2015.py",
                    "source_observed_at": "2026-10-04T01:00:00Z",
                })
            write_jsonl(expected_dir / "2015.jsonl.gz", expected)
            detail = b"<meta name='citation_title' content='Paper'><meta name='citation_author' content='Doe, Jane'><meta name='citation_publication_date' content='2015'><div id='abstract'>A complete abstract.</div>"
            first_started = threading.Event()
            release_first = threading.Event()
            fourth_started = threading.Event()
            progress = []
            worker_errors = []

            def fake_fetch(url: str, *_: object) -> tuple[bytes, None]:
                if "paper-1.html" in url:
                    first_started.set()
                    release_first.wait(timeout=6)
                if "paper-4.html" in url:
                    fourth_started.set()
                return detail, None

            args = Namespace(
                venue="cvpr", start_year=2015, end_year=2015, run_root=root,
                max_details=0, retry_unresolved=False, concurrency=3,
                delay_seconds=1.0 / 3.0, timeout_seconds=5,
            )

            def run_collection() -> None:
                try:
                    progress.append(collect_details(args, RequestRateLimiter(1.0 / 3.0)))
                except BaseException as exc:  # surfaced in the test thread below
                    worker_errors.append(exc)

            runner = threading.Thread(target=run_collection)
            with patch("tools.collect_cvf_metadata.fetch_public_html", side_effect=fake_fetch):
                runner.start()
                self.assertTrue(first_started.wait(timeout=2))
                try:
                    fourth_was_submitted = fourth_started.wait(timeout=4)
                finally:
                    release_first.set()
                runner.join(timeout=6)

            self.assertTrue(fourth_was_submitted, "a finished request should release a slot while the first request is still pending")
            self.assertFalse(runner.is_alive())
            self.assertEqual(worker_errors, [])
            self.assertEqual(progress[0]["fetched_detail_count"], 4)


if __name__ == "__main__":
    unittest.main()

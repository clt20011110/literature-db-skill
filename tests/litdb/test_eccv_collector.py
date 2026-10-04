from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from collect_eccv_metadata import (  # noqa: E402
    CachedFetcher,
    DEFAULT_HOME,
    ECCVCollector,
    RUN_ID,
    VENUE_ID,
    exclusion_reason,
    decode_http_body,
    ecva_author_matches,
    normalized_title,
    parse_conference_series,
    parse_ecva_index,
    parse_springer_search_pagination,
    parse_springer_book_structure,
    parse_springer_book_toc,
    parse_springer_chapter,
    parse_springer_search_page,
    parse_virtual_papers_page,
    parse_virtual_poster,
    canonical_jsonl,
    is_springer_client_challenge,
    main,
    _search_url,
    PageFetchError,
)


class ECCVCollectorTests(unittest.TestCase):
    def _run_mocked_cli(self, argv: list[str]) -> tuple[int, Mock]:
        collector = Mock()
        collector.enumerate.return_value = {"status": "mocked"}
        output = StringIO()
        with patch("collect_eccv_metadata.ECCVCollector", return_value=collector) as constructor:
            with redirect_stdout(output):
                status = main([*argv, "--phase", "enumerate", "--years", "2016", "--interval", "0"])
        self.assertIn('"status": "mocked"', output.getvalue())
        return status, constructor

    def test_main_uses_litdb_home_for_omitted_home_and_derives_run_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_home = root / "user"
            with patch.dict(os.environ, {"LITDB_HOME": "~/configured-db", "HOME": str(user_home)}):
                status, constructor = self._run_mocked_cli([])

            expected_home = (user_home / "configured-db").resolve()
            expected_run_root = expected_home / "runs" / RUN_ID / VENUE_ID
            self.assertEqual(status, 0)
            constructor.assert_called_once_with(
                expected_home,
                expected_run_root,
                interval=0.0,
                refresh=False,
                ecva_index=expected_run_root / "raw" / "papers.php.html",
            )

    def test_main_falls_back_to_bundled_home_when_litdb_home_is_unset(self) -> None:
        with patch.dict(os.environ):
            os.environ.pop("LITDB_HOME", None)
            status, constructor = self._run_mocked_cli([])

        expected_home = DEFAULT_HOME.expanduser().resolve()
        expected_run_root = expected_home / "runs" / RUN_ID / VENUE_ID
        self.assertEqual(status, 0)
        constructor.assert_called_once_with(
            expected_home,
            expected_run_root,
            interval=0.0,
            refresh=False,
            ecva_index=expected_run_root / "raw" / "papers.php.html",
        )

    def test_main_explicit_paths_override_environment_and_expand_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_home = root / "user"
            with patch.dict(os.environ, {"LITDB_HOME": str(root / "env-db"), "HOME": str(user_home)}):
                status, constructor = self._run_mocked_cli(
                    [
                        "--home", "~/selected-db",
                        "--run-root", "~/selected-run",
                        "--ecva-index", "~/inputs/papers.php.html",
                    ]
                )

        self.assertEqual(status, 0)
        constructor.assert_called_once_with(
            (user_home / "selected-db").resolve(),
            (user_home / "selected-run").resolve(),
            interval=0.0,
            refresh=False,
            ecva_index=(user_home / "inputs" / "papers.php.html").resolve(),
        )

    def test_http_body_gzip_is_decoded_before_html_cache(self) -> None:
        body = b"<html><title>ECCV chapter</title></html>"
        self.assertEqual(decode_http_body(gzip.compress(body), "gzip"), body)
        self.assertEqual(decode_http_body(body, "identity"), body)

    def test_springer_client_challenge_is_blocking_even_when_cached_as_http_200(self) -> None:
        source = '<html><head><title>Client Challenge</title></head><script src="/_fs-ch-abcd/script.js"></script></html>'
        self.assertTrue(is_springer_client_challenge(source))
        with tempfile.TemporaryDirectory() as temp_dir:
            raw = Path(temp_dir) / "raw"
            fetcher = CachedFetcher(raw, interval=0)
            url = "https://link.springer.com/search?query=Computer+Vision+ECCV+2026&page=1"
            page = raw / "pages" / "challenge.html.gz"
            page.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(page, "wt", encoding="utf-8") as handle:
                handle.write(source)
            fetcher.cache[url] = {"path": "pages/challenge.html.gz", "status": 200}
            with self.assertRaises(PageFetchError) as raised:
                fetcher.fetch(url)
            self.assertTrue(raised.exception.blocked)

    def test_saved_ecva_index_requires_a_matching_observation_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "raw"
            raw.mkdir(exist_ok=True)
            index = raw / "papers.php.html"
            body = b"<!-- ECCV 2024 -->"
            index.write_bytes(body)
            receipt = {
                "source_url": "https://www.ecva.net/papers.php",
                "observed_at": "2026-10-04T05:50:19Z",
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            (raw / "ecva_index_observation.json").write_text(json.dumps(receipt), encoding="utf-8")
            collector = ECCVCollector(root / "home", root / "run", interval=0, ecva_index=index)
            self.assertEqual(collector.ecva_index_meta["observed_at"], receipt["observed_at"])
            (raw / "ecva_index_observation.json").unlink()
            with self.assertRaisesRegex(ValueError, "lacks its observation receipt"):
                ECCVCollector(root / "home2", root / "run2", interval=0, ecva_index=index)

    def test_springer_search_cards_keep_result_count_and_urls(self) -> None:
        source = """
        <span data-test="results-data-total">Showing 1-2 of 2 results</span>
        <ol>
          <li data-test="search-result-item">
            <h3 data-test="title"><a href="/book/10.1007/a">Computer Vision – ECCV 2026</a></h3>
          </li>
          <li data-test="search-result-item">
            <h3 data-test="title"><a href="/book/10.1007/b">Computer Vision – ECCV 2026 Part II</a></h3>
          </li>
        </ol>
        """
        rows, total = parse_springer_search_page(source)
        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["url"], "https://link.springer.com/book/10.1007/b")

    def test_search_pagination_uses_only_published_result_hrefs(self) -> None:
        source = """
        <li data-page="1"><span>1</span></li>
        <li data-page="2"><a href="/search?query=Computer+Vision+ECCV+2026&amp;content-type=Book&amp;page=2">2</a></li>
        <a href="/search?query=Computer+Vision+ECCV+2026&amp;content-type=Book&amp;page=3" data-test="next-page">Next</a>
        """
        self.assertEqual(
            parse_springer_search_pagination(source),
            [
                "https://link.springer.com/search?query=Computer+Vision+ECCV+2026&content-type=Book&page=2",
                "https://link.springer.com/search?query=Computer+Vision+ECCV+2026&content-type=Book&page=3",
            ],
        )

    def test_2026_search_url_uses_observed_official_filter_path(self) -> None:
        self.assertEqual(
            _search_url(2026, 3),
            "https://link.springer.com/search?query=Computer+Vision+ECCV+2026&content-type=Book&sortBy=relevance&page=3",
        )

    def test_official_series_counts_are_read_per_conference_year(self) -> None:
        source = """
        <li id="conference-list-2016">
          <li class="app-conference-series-timeline__item">
            <h3 data-test="bookTitle"><a href="/book/10.1007/eccv16">Computer Vision – ECCV 2016</a></h3>
            <span class="app-conference-series-timeline__item-count-value">415</span>
            <span class="app-conference-series-timeline__item-count-label">Papers</span>
            <span class="app-conference-series-timeline__item-count-value">8</span>
            <span class="app-conference-series-timeline__item-count-label">Volumes</span>
          </li>
          <li class="app-conference-series-timeline__item">
            <h3><a href="/book/10.1007/workshops">Computer Vision – ECCV 2016 Workshops</a></h3>
            <span class="app-conference-series-timeline__item-count-value">196</span>
            <span class="app-conference-series-timeline__item-count-label">Papers</span>
          </li>
        </li>
        """
        result = parse_conference_series(source)
        self.assertEqual(result[2016]["papers"], 415)
        self.assertEqual(result[2016]["volumes"], 8)
        self.assertIn("eccv16", result[2016]["book_url"])
        self.assertEqual(result[2016]["title"], "Computer Vision – ECCV 2016")

    def test_book_structure_reads_official_volume_and_pagination_links(self) -> None:
        source = """
        <ul>
          <li data-test="conferenceProceedingBook-0">
            <a href="/book/10.1007/volume-1">Computer Vision – ECCV 2026</a>
          </li>
          <li data-test="conferenceProceedingBook-1">
            <a href="/book/10.1007/workshops">Computer Vision – ECCV 2026 Workshops</a>
          </li>
        </ul>
        <nav data-test="book-pagination">
          <a href="/book/10.1007/volume-1?page=1#toc" aria-label="Page 1">1</a>
          <a href="/book/10.1007/volume-1?page=2#toc" aria-label="Page 2">2</a>
          <a href="/book/10.1007/volume-1?page=2#toc" data-test="next-page">Next</a>
        </nav>
        """
        result = parse_springer_book_structure(source)
        self.assertEqual(len(result["volumes"]), 2)
        self.assertEqual(result["volumes"][0]["title"], "Computer Vision – ECCV 2026")
        self.assertEqual(
            result["pagination_urls"],
            [
                "https://link.springer.com/book/10.1007/volume-1?page=1#toc",
                "https://link.springer.com/book/10.1007/volume-1?page=2#toc",
            ],
        )

    def test_book_toc_enumerates_chapters_and_dois(self) -> None:
        source = """
        <ol>
          <li data-test="chapter">
            <h3 data-test="chapter-title-Front Matter">
              <a href="/chapter/10.1007/978-1_1">Front Matter</a>
            </h3>
          </li>
          <li data-test="chapter">
            <h3 data-test="chapter-title-Example Paper">
              <a href="/chapter/10.1007/978-1_2">Example Paper</a>
            </h3>
          </li>
        </ol>
        """
        rows = parse_springer_book_toc(source)
        self.assertEqual([row["title"] for row in rows], ["Front Matter", "Example Paper"])
        self.assertEqual(rows[1]["doi"], "10.1007/978-1_2")
        self.assertEqual(exclusion_reason(rows[0]["title"]), "front_matter")
        self.assertIsNone(exclusion_reason(rows[1]["title"]))

    def test_only_explicit_correction_chapter_prefix_is_excluded(self) -> None:
        self.assertEqual(exclusion_reason("Correction to: Example Paper"), "correction")
        self.assertIsNone(exclusion_reason("Error Correction in Visual Recognition"))
        self.assertEqual(exclusion_reason("Front Matter"), "front_matter")
        self.assertEqual(exclusion_reason("Author Index"), "index")
        self.assertIsNone(exclusion_reason("Protecting NeRFs’ Copyright via Plug-And-Play Watermarking Base Model"))
        self.assertIsNone(exclusion_reason("High-Activation Feature Index Similarity and Object Detection"))

    def test_virtual_paper_page_lists_only_its_observed_year_poster_links(self) -> None:
        source = """
        <li><a href="/virtual/2026/poster/42">Example <em>Paper</em></a></li>
        <li><a href="/virtual/2026/poster/43">Another Paper</a></li>
        <li><a href="/virtual/2024/poster/42">Wrong year</a></li>
        """
        rows = parse_virtual_papers_page(source, 2026)
        self.assertEqual(rows, [
            {"native_id": "42", "title": "Example Paper"},
            {"native_id": "43", "title": "Another Paper"},
        ])

    def test_virtual_poster_parser_reads_schema_authors_and_full_abstract(self) -> None:
        source = """
        <link rel="canonical" href="https://eccv.ecva.net/virtual/2024/poster/42">
        <script type="application/ld+json">
        {"@type":"CreativeWork","name":"Example Paper","creditText":"ECCV 2024",
         "author":[{"name":"Ada First"},{"name":"Ben Second"}],"datePublished":"2024-08-28"}
        </script>
        <div class="abstract-text-inner"><p>First sentence.</p><p>Second <em>complete</em> sentence.</p></div>
        """
        row = parse_virtual_poster(source, 2024)
        self.assertEqual(row["title"], "Example Paper")
        self.assertEqual(row["authors"], ["Ada First", "Ben Second"])
        self.assertEqual(row["abstract"], "First sentence. Second complete sentence.")
        self.assertEqual(row["venue_year"], 2024)

    def test_math_markup_and_visible_greek_titles_normalize_equally(self) -> None:
        springer = r'D<span class="mathjax-tex">\(\epsilon \)</span>pS: Delayed <span class="mathjax-tex">\(\epsilon \)</span>-Shrinking'
        ecva = "DεpS: Delayed ε-Shrinking"
        self.assertEqual(normalized_title(springer), normalized_title(ecva))
        actual_chapter_title = "D $$" + chr(92) * 2 + "epsilon $$ pS: Delayed $$" + chr(92) * 2 + "epsilon $$ -Shrinking for Faster Once-for-All Training"
        ecva_full_title = "DεpS: Delayed ε-Shrinking for Faster Once-For-All Training"
        self.assertEqual(normalized_title(actual_chapter_title), normalized_title(ecva_full_title))

    def test_ecva_pdf_identity_requires_first_author_agreement(self) -> None:
        self.assertTrue(ecva_author_matches(["Yixuan Ren", "Yang Zhou"], "Yixuan Ren*, Yang Zhou"))
        self.assertFalse(ecva_author_matches(["Anay Majee"], "Yixuan Ren*, Yang Zhou"))

    def test_springer_chapter_metadata_uses_full_schema_authors_and_abstract(self) -> None:
        source = """
        <html><head>
          <link rel="canonical" href="https://link.springer.com/chapter/10.1007/x_1"/>
          <meta name="citation_doi" content="10.1007/x_1"/>
          <meta name="citation_title" content="Example Title"/>
          <meta name="citation_publication_date" content="2026"/>
          <meta name="citation_pdf_url" content="https://link.springer.com/content/pdf/10.1007/x_1.pdf"/>
          <script type="application/ld+json">
          {"@type":"ScholarlyArticle","headline":"Example Title",
           "author":[{"@type":"Person","name":"Ada First"},{"@type":"Person","name":"Ben Second"}],
           "description":"A complete abstract with all of its sentences.","datePublished":"2026",
           "isAccessibleForFree":true}
          </script>
        </head><body></body></html>
        """
        row = parse_springer_chapter(
            source, "https://link.springer.com/chapter/10.1007/x_1", 2026
        )
        self.assertEqual(row["authors"], ["Ada First", "Ben Second"])
        self.assertEqual(row["abstract"], "A complete abstract with all of its sentences.")
        self.assertEqual(row["doi"], "10.1007/x_1")
        self.assertEqual(row["publication_year"], 2026)
        self.assertTrue(row["pdf_url"].endswith(".pdf"))

    def test_visible_springer_abstract_precedes_schema_description(self) -> None:
        source = """
        <html><head>
          <meta name="citation_doi" content="10.1007/x_16">
          <meta name="citation_title" content="A Unified Framework">
          <script type="application/ld+json">
            {"@type":"ScholarlyArticle","headline":"A Unified Framework",
             "author":[{"name":"Ada Author"}],
             "description":"One[NOSPACE] Hager, Gregory [NOSPACE][SPACE]core challenge."}
          </script>
        </head><body>
          <section data-title="Abstract"><p>One[NOSPACE] [NOSPACE][SPACE]core challenge in object pose estimation.</p></section>
        </body></html>
        """
        row = parse_springer_chapter(source, "https://link.springer.com/chapter/10.1007/x_16", 2018)
        self.assertEqual(row["abstract"], "One[NOSPACE] [NOSPACE][SPACE]core challenge in object pose estimation.")
        self.assertNotIn("Hager, Gregory", row["abstract"])

    def test_springer_abstract_heading_and_external_keywords_are_not_abstract_text(self) -> None:
        source = """
        <html><head>
          <meta name="citation_doi" content="10.1007/x_17">
          <meta name="citation_title" content="Heading Parse Test">
        </head><body>
          <section data-title="Abstract">
            <div class="c-article-section">
              <h2 class="c-article-section__title">Abstract</h2>
              <div class="c-article-section__content">
                <p>Abstractive methods should keep this body text exactly.</p>
              </div>
            </div>
          </section>
          <section data-title="Keywords"><h2>Keywords</h2><p>outside-term</p></section>
        </body></html>
        """
        row = parse_springer_chapter(source, "https://link.springer.com/chapter/10.1007/x_17", 2018)
        self.assertEqual(row["abstract"], "Abstractive methods should keep this body text exactly.")
        self.assertNotIn("Keywords", row["abstract"])
        self.assertNotIn("outside-term", row["abstract"])

    def test_venue_year_remains_2024_when_chapter_was_published_in_2025(self) -> None:
        source = """
        <html><head>
          <link rel="canonical" href="https://link.springer.com/chapter/10.1007/x_24"/>
          <meta name="citation_doi" content="10.1007/x_24"/>
          <meta name="citation_title" content="ECCV 2024 Paper"/>
          <meta name="citation_publication_date" content="2025-01-15"/>
          <script type="application/ld+json">
          {"@type":"ScholarlyArticle","headline":"ECCV 2024 Paper",
           "author":[{"@type":"Person","name":"Ada First"}],
           "description":"Abstract text.","datePublished":"2025-01-15"}
          </script>
        </head><body></body></html>
        """
        chapter = parse_springer_chapter(
            source, "https://link.springer.com/chapter/10.1007/x_24", 2024
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            collector = ECCVCollector(
                Path(temp_dir) / "home",
                Path(temp_dir) / "run",
                interval=0,
                ecva_index=None,
            )
            record = collector._stage_record(
                2024,
                {"source_native_id": "10.1007/x_24"},
                chapter,
                "2026-10-04T00:00:00Z",
                None,
                "https://link.springer.com/chapter/10.1007/x_24",
            )
        self.assertEqual(chapter["publication_year"], 2025)
        self.assertEqual(record["year"], 2024)
        self.assertEqual(record["publication_date"], "2025-01-15")

    def test_virtual_listed_paper_with_springer_404_keeps_candidate_doi_unassigned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "run"
            collector = ECCVCollector(Path(temp_dir) / "home", run_root, interval=0, ecva_index=None)
            collector.state.setdefault("enumerated", {})
            candidate_doi = "10.1007/978-3-99999-000-0_21"
            chapter_url = f"https://link.springer.com/chapter/{candidate_doi}"
            poster_url = "https://eccv.ecva.net/virtual/2024/poster/674"
            canonical_jsonl(
                collector.expected_root / "2024.jsonl.gz",
                [],
                compress=True,
            )
            (run_root / "virtual_poster_metadata.json").write_text(
                json.dumps(
                    {
                        "source_url": "https://eccv.ecva.net/virtual/2024/papers.html",
                        "records": [
                            {
                                "poster_id": "674",
                                "poster_url": poster_url,
                                "title": "Zero-shot Text-guided Infinite Image Synthesis with LLM guidance",
                                "authors": ["Ada First", "Ben Second"],
                                "abstract": "A complete abstract.",
                                "poster_observed_at": "2026-10-04T07:23:51Z",
                                "ecva_index": {
                                    "doi_candidate": candidate_doi,
                                    "chapter_url": chapter_url,
                                    "pdf_url": "https://www.ecva.net/papers/eccv_2024/papers_ECCV/papers/00001.pdf",
                                    "authors_text": "Ada First, Ben Second",
                                    "source_url": "https://www.ecva.net/papers.php",
                                    "observed_at": "2026-10-04T05:50:19Z",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (run_root / "springer_ecva_identity_audit_2024.json").write_text(
                json.dumps(
                    {
                        "springer_chapter_checks": [
                            {"url": chapter_url, "status": 404, "observed_at": "2026-10-04T07:31:31Z"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_root / "enumeration_report.json").write_text(
                json.dumps({"year_reports": {"2024": {"source_urls": []}}}),
                encoding="utf-8",
            )
            raw = run_root / "raw"
            raw.mkdir(exist_ok=True)
            (raw / "eccv2024_virtual_papers.html").write_text(
                '<li><a href="/virtual/2024/poster/674">Zero-shot Text-guided Infinite Image Synthesis with LLM guidance</a></li>',
                encoding="utf-8",
            )
            collector.ecva_records[2024] = [{"title": "Zero-shot Text-guided Infinite Image Synthesis with LLM guidance"}]

            result = collector._apply_virtual_poster_exceptions((2024,))

            self.assertEqual(result["status"], "updated")
            with gzip.open(collector.expected_root / "2024.jsonl.gz", "rt", encoding="utf-8") as handle:
                expected = [json.loads(line) for line in handle if line.strip()]
            staged = collector._read_jsonl(run_root / "metadata_staging.jsonl")
            self.assertEqual(len(expected), 1)
            self.assertEqual(expected[0]["source_native_id"], "eccv-virtual-2024-poster-674")
            self.assertEqual(len(staged), 1)
            self.assertIsNone(staged[0]["doi"])
            self.assertEqual(staged[0]["missing_fields"]["doi"]["candidate_doi_retained_in_run_evidence"], candidate_doi)
            self.assertEqual(staged[0]["pdf_url"], "https://www.ecva.net/papers/eccv_2024/papers_ECCV/papers/00001.pdf")

            # Re-finalizing an already-unioned expected manifest must repair a
            # missing staging row without duplicating the expected identity.
            (run_root / "metadata_staging.jsonl").unlink()
            repaired = collector._apply_virtual_poster_exceptions((2024,))
            self.assertEqual(repaired["status"], "updated")
            with gzip.open(collector.expected_root / "2024.jsonl.gz", "rt", encoding="utf-8") as handle:
                repaired_expected = [json.loads(line) for line in handle if line.strip()]
            repaired_staging = collector._read_jsonl(run_root / "metadata_staging.jsonl")
            self.assertEqual(len(repaired_expected), 1)
            self.assertEqual(len(repaired_staging), 1)
            self.assertEqual(repaired_staging[0]["source_native_id"], "eccv-virtual-2024-poster-674")

    def test_cache_reparse_preserves_observation_and_uses_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            collector = ECCVCollector(Path(temp_dir) / "home", Path(temp_dir) / "run", interval=0, ecva_index=None)
            identity = "10.1007/x_16"
            source_url = f"https://link.springer.com/chapter/{identity}"
            expected = {
                "venue_id": "eccv",
                "year": 2018,
                "source_native_id": identity,
                "title": "A Unified Framework",
                "landing_url": source_url,
                "enumeration_source_url": "https://link.springer.com/book/10.1007/x",
                "enumeration_observed_at": "2026-10-04T00:00:00Z",
            }
            canonical_jsonl(collector.expected_root / "2018.jsonl.gz", [expected], compress=True)
            collector.fetcher.fetch = lambda _url: (_ for _ in ()).throw(AssertionError("network was called"))  # type: ignore[method-assign]
            page = f"""
            <html><head>
              <meta name="citation_doi" content="{identity}">
              <meta name="citation_title" content="A Unified Framework">
              <script type="application/ld+json">
                {{"@type":"ScholarlyArticle","headline":"A Unified Framework",
                 "author":[{{"name":"Ada Author"}}],
                 "description":"One[NOSPACE] Hager, Gregory [NOSPACE][SPACE]core challenge."}}
              </script>
            </head><body>
              <section data-title="Abstract"><p>One[NOSPACE] [NOSPACE][SPACE]core challenge in object pose estimation.</p></section>
            </body></html>
            """
            cached_path = collector.raw_root / "pages" / "cached.html.gz"
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(cached_path, "wt", encoding="utf-8") as handle:
                handle.write(page)
            cache_sha = hashlib.sha256(page.encode("utf-8")).hexdigest()
            observed_at = "2026-10-04T07:59:56Z"
            collector.fetcher.cache[source_url] = {
                "path": "pages/cached.html.gz",
                "source_url": source_url,
                "observed_at": observed_at,
                "sha256": cache_sha,
            }
            (collector.run_root / "metadata_staging.jsonl").write_text(
                json.dumps({"source_native_id": identity, "year": 2018, "abstract": "old polluted abstract"}) + "\n",
                encoding="utf-8",
            )

            report = collector.reparse_cached_details((2018,))

            staged = collector._read_jsonl(collector.run_root / "metadata_staging.jsonl")
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["updated_count"], 1)
            self.assertEqual(report["abstract_changed_count"], 1)
            self.assertEqual(report["field_change_counts"]["abstract"], 1)
            self.assertEqual(report["abstract_changes"][0]["source_native_id"], identity)
            self.assertEqual(staged[0]["abstract"], "One[NOSPACE] [NOSPACE][SPACE]core challenge in object pose estimation.")
            self.assertNotIn("Hager, Gregory", staged[0]["abstract"])
            self.assertEqual(staged[0]["observed_at"], observed_at)
            self.assertEqual(report["observations"][0]["sha256"], cache_sha)

    def test_cache_reparse_excludes_virtual_union_rows_from_publisher_detail_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            collector = ECCVCollector(Path(temp_dir) / "home", Path(temp_dir) / "run", interval=0, ecva_index=None)
            publisher_id = "10.1007/978-3-99999-000-0_1"
            publisher_url = f"https://link.springer.com/chapter/{publisher_id}"
            virtual_id = "eccv-virtual-2026-poster-674"
            publisher_expected = {
                "venue_id": "eccv",
                "year": 2026,
                "source_native_id": publisher_id,
                "title": "Publisher Chapter",
                "landing_url": publisher_url,
            }
            virtual_expected = {
                "venue_id": "eccv",
                "year": 2026,
                "source_native_id": virtual_id,
                "title": "Virtual-only Paper",
                "landing_url": "https://eccv.ecva.net/virtual/2026/poster/674",
            }
            canonical_jsonl(
                collector.expected_root / "2026.jsonl.gz",
                [publisher_expected, virtual_expected],
                compress=True,
            )
            page = f"""
            <html><head>
              <meta name="citation_doi" content="{publisher_id}">
              <meta name="citation_title" content="Publisher Chapter">
              <script type="application/ld+json">
                {{"@type":"ScholarlyArticle","headline":"Publisher Chapter",
                 "author":[{{"@type":"Person","name":"Ada Author"}}],
                 "description":"Publisher abstract."}}
              </script>
            </head><body><section data-title="Abstract"><p>Publisher abstract.</p></section></body></html>
            """
            cached_path = collector.raw_root / "pages" / "publisher.html.gz"
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(cached_path, "wt", encoding="utf-8") as handle:
                handle.write(page)
            collector.fetcher.cache[publisher_url] = {
                "path": "pages/publisher.html.gz",
                "source_url": publisher_url,
                "observed_at": "2026-10-04T00:00:00Z",
                "sha256": hashlib.sha256(page.encode("utf-8")).hexdigest(),
            }
            canonical_jsonl(
                collector.run_root / "metadata_staging.jsonl",
                [
                    {"source_native_id": publisher_id, "year": 2026},
                    {"source_native_id": virtual_id, "year": 2026, "abstract": "Virtual abstract."},
                ],
            )

            report = collector.reparse_cached_details((2026,))

            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["checked_count"], 1)
            self.assertEqual(report["updated_count"], 1)
            self.assertEqual(report["unavailable_or_invalid_cache"], [])
            self.assertEqual(report["ecva_crosswalk"]["2026"]["springer_expected_count"], 1)
            staged = collector._read_jsonl(collector.run_root / "metadata_staging.jsonl")
            self.assertEqual({row["source_native_id"] for row in staged}, {publisher_id, virtual_id})

    def test_details_use_a_rolling_three_request_window_and_stop_refilling_on_block(self) -> None:
        def prepare(root: Path) -> ECCVCollector:
            collector = ECCVCollector(root / "home", root / "run", interval=0, ecva_index=None)
            book_url = "https://link.springer.com/book/10.1007/978-3-319-test"
            expected = [
                {
                    "venue_id": "eccv",
                    "year": 2016,
                    "source_native_id": f"10.1007/978-3-319-test_{index}",
                    "title": f"Paper {index}",
                    "landing_url": f"https://link.springer.com/chapter/10.1007/978-3-319-test_{index}",
                    "book_url": book_url,
                    "enumeration_source_url": book_url,
                    "enumeration_observed_at": "2026-10-04T00:00:00Z",
                }
                for index in range(1, 6)
            ]
            canonical_jsonl(collector.expected_root / "2016.jsonl.gz", expected, compress=True)
            (collector.run_root / "enumeration_report.json").write_text(
                json.dumps({"year_reports": {"2016": {"source_urls": [book_url]}}}),
                encoding="utf-8",
            )
            return collector

        def chapter_html(identity: str, title: str) -> str:
            return f"""
            <html><head>
              <link rel="canonical" href="https://link.springer.com/chapter/{identity}">
              <meta name="citation_doi" content="{identity}">
              <meta name="citation_title" content="{title}">
              <meta name="citation_publication_date" content="2016-09-01">
              <script type="application/ld+json">
                {{"@type":"ScholarlyArticle","headline":"{title}",
                 "author":[{{"@type":"Person","name":"Ada Author"}}],
                 "description":"An abstract."}}
              </script>
            </head></html>
            """

        with tempfile.TemporaryDirectory() as temp_dir:
            collector = prepare(Path(temp_dir))
            lock = threading.Lock()
            barrier = threading.Barrier(3)
            release_slow = threading.Event()
            fallback = threading.Timer(2.0, release_slow.set)
            fallback.start()
            started: list[int] = []
            active = 0
            max_active = 0
            fourth_started_before_slow_release = False

            def rolling_fetch(url: str) -> str:
                nonlocal active, max_active, fourth_started_before_slow_release
                index = int(url.rsplit("_", 1)[1])
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                    started.append(index)
                try:
                    if index <= 3:
                        barrier.wait(timeout=2)
                    if index in {2, 3}:
                        release_slow.wait(timeout=2)
                    if index == 4:
                        fourth_started_before_slow_release = not release_slow.is_set()
                        release_slow.set()
                    collector.fetcher.last_meta = {"observed_at": "2026-10-04T00:00:01Z"}
                    return chapter_html(f"10.1007/978-3-319-test_{index}", f"Paper {index}")
                finally:
                    with lock:
                        active -= 1

            collector.fetcher.fetch = rolling_fetch  # type: ignore[method-assign]
            result = collector.collect_details((2016,))
            fallback.cancel()
            self.assertFalse(result["blocked"])
            self.assertEqual(result["staging_records"], 5)
            self.assertEqual(max_active, 3)
            self.assertTrue(fourth_started_before_slow_release)

        with tempfile.TemporaryDirectory() as temp_dir:
            collector = prepare(Path(temp_dir))
            lock = threading.Lock()
            barrier = threading.Barrier(3)
            release_slow = threading.Event()
            fallback = threading.Timer(0.3, release_slow.set)
            fallback.start()
            started: list[int] = []

            def blocking_fetch(url: str) -> str:
                index = int(url.rsplit("_", 1)[1])
                with lock:
                    started.append(index)
                if index <= 3:
                    barrier.wait(timeout=2)
                if index in {2, 3}:
                    release_slow.wait(timeout=2)
                collector.fetcher.last_meta = {"observed_at": "2026-10-04T00:00:01Z"}
                if index == 1:
                    return "<html><body>interstitial</body></html>"
                return chapter_html(f"10.1007/978-3-319-test_{index}", f"Paper {index}")

            collector.fetcher.fetch = blocking_fetch  # type: ignore[method-assign]
            result = collector.collect_details((2016,))
            fallback.cancel()
            self.assertTrue(result["blocked"])
            self.assertEqual(len(started), 3)
            self.assertTrue(collector.state["detail_collection_blocked"])

    def test_stale_keyword_exclusion_is_removed_and_recollected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            collector = ECCVCollector(Path(temp_dir) / "home", Path(temp_dir) / "run", interval=0, ecva_index=None)
            identity = "10.1007/978-3-031-test_7"
            source_url = f"https://link.springer.com/chapter/{identity}"
            title = "Protecting NeRFs’ Copyright via Plug-And-Play Watermarking Base Model"
            expected = {
                "venue_id": "eccv",
                "year": 2024,
                "source_native_id": identity,
                "title": title,
                "landing_url": source_url,
                "book_url": "https://link.springer.com/book/10.1007/978-3-031-test",
                "enumeration_source_url": "https://link.springer.com/book/10.1007/978-3-031-test",
                "enumeration_observed_at": "2026-10-04T00:00:00Z",
            }
            canonical_jsonl(collector.expected_root / "2024.jsonl.gz", [expected], compress=True)
            stale = {
                "venue_id": "eccv",
                "source_native_id": identity,
                "title": title,
                "year": 2024,
                "inclusion_decision": "exclude",
                "exclusion_reason_code": "front_matter",
                "source_url": "https://link.springer.com/book/10.1007/978-3-031-test",
                "landing_url": source_url,
                "observed_at": "2026-10-04T00:00:00Z",
            }
            canonical_jsonl(collector.run_root / "metadata_exclusions.partial.jsonl", [stale])
            body = f"""
            <html><head>
              <meta name="citation_doi" content="{identity}">
              <meta name="citation_title" content="{title}">
              <script type="application/ld+json">
                {{"@type":"ScholarlyArticle","headline":"{title}",
                 "author":[{{"name":"Ada Author"}}],"description":"Abstract."}}
              </script>
            </head><body><section data-title="Abstract"><p>Abstract.</p></section></body></html>
            """

            def fake_fetch(url: str) -> str:
                self.assertEqual(url, source_url)
                collector.fetcher.last_meta = {"observed_at": "2026-10-04T00:00:01Z"}
                return body

            collector.fetcher.fetch = fake_fetch  # type: ignore[method-assign]
            result = collector.collect_details((2024,))

            staged = collector._read_jsonl(collector.run_root / "metadata_staging.jsonl")
            exclusions = collector._read_jsonl(collector.run_root / "metadata_exclusions.jsonl")
            repairs = json.loads((collector.run_root / "exclusion_repairs.json").read_text())
            self.assertFalse(result["blocked"])
            self.assertEqual([row["source_native_id"] for row in staged], [identity])
            self.assertEqual(exclusions, [])
            self.assertEqual(repairs["repairs"][0]["source_native_id"], identity)

    def test_2026_details_can_resume_from_incomplete_expected_without_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            collector = ECCVCollector(Path(temp_dir) / "home", Path(temp_dir) / "run", interval=0, ecva_index=None)
            identity = "10.1007/978-3-032-36984-0_1"
            source_url = f"https://link.springer.com/chapter/{identity}"
            book_url = "https://link.springer.com/book/10.1007/978-3-032-36984-0"
            row = {
                "venue_id": "eccv",
                "year": 2026,
                "source_native_id": identity,
                "source_document_type": "Springer ECCV proceedings chapter",
                "title": "Example ECCV 2026 Paper",
                "landing_url": source_url,
                "book_url": book_url,
                "enumeration_source_url": book_url,
                "enumeration_observed_at": "2026-10-04T00:00:00Z",
            }
            partial_path = collector.expected_root / "2026.incomplete-series-only.jsonl.gz"
            canonical_jsonl(partial_path, [row], compress=True)
            self.assertFalse((collector.expected_root / "2026.jsonl.gz").exists())
            body = f"""
            <html><head>
              <meta name="citation_doi" content="{identity}">
              <meta name="citation_title" content="Example ECCV 2026 Paper">
              <script type="application/ld+json">
                {{"@type":"ScholarlyArticle","headline":"Example ECCV 2026 Paper",
                 "author":[{{"name":"Ada Author"}}],"description":"Example abstract."}}
              </script>
            </head><body><section data-title="Abstract"><p>Example abstract.</p></section></body></html>
            """

            def fake_fetch(url: str) -> str:
                self.assertEqual(url, source_url)
                collector.fetcher.last_meta = {"observed_at": "2026-10-04T00:00:01Z"}
                return body

            collector.fetcher.fetch = fake_fetch  # type: ignore[method-assign]
            result = collector.collect_details((2026,))

            staged = collector._read_jsonl(collector.run_root / "metadata_staging.jsonl")
            self.assertFalse(result["blocked"])
            self.assertEqual(result["staging_records"], 1)
            self.assertEqual([item["source_native_id"] for item in staged], [identity])
            self.assertTrue(partial_path.is_file())
            self.assertFalse((collector.expected_root / "2026.jsonl.gz").exists())

            reparse = collector.reparse_cached_details(
                (2026,), report_filename="cache_reparse_2026.partial.json"
            )
            self.assertEqual(reparse["checked_count"], 0)
            self.assertEqual(
                reparse["unavailable_or_invalid_cache"][0]["source_native_id"], identity
            )
            self.assertTrue((collector.run_root / "cache_reparse_2026.partial.json").is_file())
            self.assertFalse((collector.expected_root / "2026.jsonl.gz").exists())

    def test_2026_union_virtual_rows_are_accounted_but_not_publisher_detail_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            collector = ECCVCollector(Path(temp_dir) / "home", Path(temp_dir) / "run", interval=0, ecva_index=None)
            publisher_id = "10.1007/978-3-99999-000-0_1"
            virtual_id = "eccv-virtual-2026-poster-7"
            publisher_row = {
                "venue_id": "eccv",
                "year": 2026,
                "source_native_id": publisher_id,
                "title": "Publisher Paper",
                "landing_url": f"https://link.springer.com/chapter/{publisher_id}",
            }
            virtual_row = {
                "venue_id": "eccv",
                "year": 2026,
                "source_native_id": virtual_id,
                "title": "Virtual-only Paper",
                "landing_url": "https://eccv.ecva.net/virtual/2026/poster/7",
            }
            canonical_jsonl(collector.expected_root / "2026.jsonl.gz", [publisher_row, virtual_row], compress=True)
            (collector.run_root / "metadata_staging.partial.jsonl").write_text(
                json.dumps(publisher_row) + "\n" + json.dumps(virtual_row) + "\n",
                encoding="utf-8",
            )
            collector.fetcher.fetch = lambda _url: (_ for _ in ()).throw(AssertionError("network was called"))  # type: ignore[method-assign]

            result = collector.collect_details((2026,))

            self.assertFalse(result["blocked"])
            self.assertEqual(result["staging_records"], 2)
            self.assertTrue(collector.state["details_complete"])
            self.assertEqual(result["ecva_crosswalk"]["2026"]["springer_expected_count"], 1)
            self.assertEqual(result["ecva_crosswalk"]["2026"]["unmatched_springer_items"], 1)

    def test_details_complete_checks_target_ids_not_global_staging_count(self) -> None:
        expected = {
            2026: [
                {"source_native_id": "springer-2026-a"},
                {"source_native_id": "springer-2026-b"},
            ]
        }
        old_year_ids = {f"old-year-{index}" for index in range(10_000)}
        self.assertFalse(
            ECCVCollector._details_manifest_complete(
                expected,
                old_year_ids | {"springer-2026-a"},
                set(),
                set(),
                False,
            )
        )
        self.assertTrue(
            ECCVCollector._details_manifest_complete(
                expected,
                old_year_ids | {"springer-2026-a"},
                {"springer-2026-b"},
                set(),
                False,
            )
        )
        self.assertFalse(
            ECCVCollector._details_manifest_complete(
                expected,
                old_year_ids | {"springer-2026-a", "springer-2026-b"},
                set(),
                {"springer-2026-b"},
                False,
            )
        )

    def test_finalize_validation_is_scoped_to_requested_years(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "run"
            collector = ECCVCollector(Path(temp_dir) / "home", run_root, interval=0, ecva_index=None)
            expected = {
                "venue_id": "eccv",
                "year": 2024,
                "source_native_id": "eccv-2024-example",
                "title": "Example Paper",
                "landing_url": "https://link.springer.com/chapter/10.1007/example_1",
                "enumeration_source_url": "https://link.springer.com/book/10.1007/example",
                "enumeration_observed_at": "2026-10-04T00:00:00Z",
            }
            canonical_jsonl(collector.expected_root / "2024.jsonl.gz", [expected], compress=True)
            canonical_jsonl(
                run_root / "metadata_staging.jsonl",
                [
                    {"source_native_id": "eccv-2024-example", "year": 2024, "title": "Example Paper"},
                    {"source_native_id": "eccv-2026-partial", "year": 2026, "title": "Partial Paper"},
                ],
            )
            (run_root / "enumeration_report.json").write_text(
                json.dumps({"year_reports": {}}), encoding="utf-8"
            )
            result = collector.finalize((2024,))
            waterline = result["waterline"]
            stats = result["statistics"]
            self.assertEqual(waterline["scope_years"], [2024])
            self.assertEqual(waterline["status"], "PASS")
            self.assertEqual(stats["extra_ids"], [])
            self.assertEqual(stats["total_staged"], 1)
            full_stage = collector._read_jsonl(run_root / "metadata_staging.jsonl")
            self.assertEqual(len(full_stage), 2)

    def test_saved_ecva_index_can_be_parsed_without_detail_requests(self) -> None:
        source = """
        <!-- ECCV 2024 -->
        <dt class="ptitle"><a href="papers/eccv_2024/papers_ECCV/html/7_ECCV_2024_paper.php">Example Paper</a></dt>
        <dd>Ada Author, Ben Writer*</dd>
        <dd>[<a href="papers/eccv_2024/papers_ECCV/papers/00007.pdf">pdf</a>]
        [<a href="https://link.springer.com/chapter/10.1007/978-3_1">DOI</a>]</dd>
        """
        rows = parse_ecva_index(source, "2026-10-04T05:50:19Z")
        self.assertEqual(len(rows[2024]), 1)
        self.assertEqual(rows[2024][0]["doi"], "10.1007/978-3_1")
        self.assertTrue(rows[2024][0]["pdf_url"].endswith("/00007.pdf"))
        self.assertEqual(rows[2024][0]["observed_at"], "2026-10-04T05:50:19Z")


if __name__ == "__main__":
    unittest.main()

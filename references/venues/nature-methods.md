# Nature Methods playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Current production state (2026-10-02)

- Venue ID and canonical name: `nature-methods`, Nature Methods. The verified research range is 2015–2026 and the current waterline is 2026-10-01 UTC. The campaign state and catalog venue state are `ACTIVE`.
- The production merge receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/prepared/merge_receipt.json` is `PASS` with `committed: true`: 1,892 records merged, 2,773 explicit exclusions, and an advanced watermark. The prepared metadata SHA-256 is `4ffb8c137e69faabfdb7f50b355a24202beaa7fecf7c0bd705cf9546b27af75d`; the prepared exclusion SHA-256 is `3c332ade3addf40277b089aa459f1494cc7286f49d3fff57c9e05c7f8dee33c9`.
- The finalization receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/finalization/nature_finalize_receipt.json` is `PASS` with `committed: true` and mode `ordinary_initialization`. The reconcile receipt and report under `finalization/reconcile/` are `PASS`; the report has 1,892 canonical works, 4,665 source items, 2,773 exclusions, zero missing/extra/duplicate identities, and `catalog_ready: true`.
- The read-only production check `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/controller_production_check.json` is `PASS`: title, authors, abstract, DOI, landing URL, PDF URL, and publication date are each present for 1,892/1,892 included records. No PDF bytes were downloaded.
- The 12 expected manifests were installed under `<db-home>/manifests/expected/nature-methods/`; installation is recorded in `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/finalization/expected_install_receipt.json`.
- Search verification `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/search_verification.json` is `PASS` at `2026-10-01T18:31:44.110193+00:00`, with `ready: true`, `stale: false`, 114,853 indexed papers, and 1,892 Nature Methods records. Both an English and a Chinese scope query returned Nature Methods records with titles and stored landing/PDF links.

The run-local candidate playbook at `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/playbook_candidate.md` and the raw collection receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/run_receipt.json` are preserved unchanged as pre-ingest historical artifacts. Their `verified_collection_and_staging_complete_pending_production_ingest` status predates the committed merge and finalization and must not be used as the current venue state. The global playbook here is the current reusable reference.

## Identity and scope

- Use the official public Nature archive at `https://www.nature.com/nmeth/articles` and its year form `https://www.nature.com/nmeth/articles?year=YYYY`. Detail pages use the stable `/articles/...` path, with the normalized DOI retained when available. The allowed source domain is `nature.com`.
- The completed enumeration has 241 listing pages, 4,665 raw cards, 4,665 unique stable paths, 1,892 candidates, 2,773 explicit exclusions, and zero duplicate paths. Candidate types are Article 1,129; Brief Communication 499; Perspective 84; Resource 53; Review Article 55; and Analysis 72.
- Candidate/exclusion totals by year are: 2015 `157/327`, 2016 `131/272`, 2017 `141/251`, 2018 `143/301`, 2019 `161/232`, 2020 `120/204`, 2021 `147/194`, 2022 `146/217`, 2023 `179/228`, 2024 `219/211`, 2025 `225/204`, and 2026 `123/132`.
- Excluded types and decisions remain in the exclusion ledger. Do not silently promote an unfamiliar visible type or replace an excluded correction with an article.

## Official listing and pagination procedure

- Start from the archive or the requested year filter and follow each observed `Next` URL. Preserve the publisher's `searchType=journalSearch`, `sort=PubDate`, `year=YYYY`, and `page=N` parameters; do not synthesize a page grid.
- Record source URL, selected year facet, page number, card position, stable path, title, listing summary, listing authors, visible type, date, open-access flag, card count, and observed Next URL. A normal page has 20 cards. A terminal page is accepted only when the selected facet, card count, and missing Next link agree; empty HTML, a missing selector, or a transport warning is not terminal evidence.
- The original 2026 traversal is retained under `enumeration/original-2026-drift-20261001/` because it saw a facet/raw mismatch and a repeated `/articles/s41592-026-03109-7`. The fresh 13-page 2026 traversal superseded it with 255 raw/255 unique paths, a stable `2026 (255)` facet, a complete Next chain, no duplicate path, and the added `/articles/s41592-026-03134-6`. The supersession receipt is `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/enumeration/2026_supersession_receipt.json`.

## Detail extraction contract

- Use the ordered complete `meta[name=citation_author]` list. If citation authors and JSON-LD are absent, use the ordered visible byline at `ul[data-test=authors-list] a[data-test=author-name]`. For `/articles/nmeth.3440`, the verified primary byline is the institutional group `the Mutation Consequences and Pathway Analysis working group of the International Cancer Genome Consortium`; retain that group name and do not expand its 20 observable members into production authors.
- Select the publisher's explicit Abstract section at an `AbsN-content` locator, with an Abstract heading/data-title or paired `AbsN-section`/`AbsN-content` publisher container inside the article body. Exclude standfirst and listing summaries. Preserve a structured missing reason if an official page genuinely has no abstract; multiple true candidates or ambiguous sections require review.
- For document type, prefer the specific visible article category, then `citation_article_type`, `dc.type`, and `prism.section`. A generic `Article` may remain compatible with a more specific visible/listing type; a concrete conflict requires review. The final ledger records two corrections: `/articles/nmeth.3440` (`nmeth.3440-author-fallback-20261001`) applies the verified institutional group-byline fallback, and `/articles/nmeth.3312` (`nmeth.3312-document-type-visible-category-20261002`) changes generic `Article` to the visible `Brief Communication` category. Exact per-position surname/given-name conversion and punctuation-before-comma spacing were used only as audit equivalences; they do not permit sorting or fuzzy matching.
- Keep DOI, publication date, landing URL, observed PDF URL, selected locators, source URLs, and field-level provenance. A PDF URL is metadata evidence; this run did not fetch PDF bytes.

## Browser recovery and audit evidence

- The independent detail review `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/controller_cached_detail_review.json` is `PASS` for 1,892 unique identities. It covers 1,880 cached-HTML records and 17 Browser-DOM records; five of those Browser records overlap cached HTML, so the union is 1,892 records. Title, ordered authors, abstract, DOI, publication date, document type, journal identity, and PDF/link checks all pass for the full union, with no cache conflicts.
- The 17 Browser records correspond to the 17 bounded detail recoveries. The original captured evidence is preserved at `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/provenance/browser_dom_evidence_pre_digest_20261002.jsonl`. The digest receipt `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/provenance/browser_dom_digest_receipt.json` records 17/17 PASS digests computed at `2026-10-01T18:24:35.132Z` during post-capture normalization; that timestamp is not a claim about Browser capture time. The receipt-integrity review `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/controller_receipt_integrity_review.json` is `PASS` at `2026-10-01T18:28:09.128151+00:00`, with 18 artifacts and no hash mismatches, while preserving the original bytes.
- The strict detail audit and production reconcile have no missing fields, manual-review rows, provenance gaps, or unresolved errors. The two correction records above are the recorded resolutions, not open issues.

## Evidence index

- Enumeration: `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/controller_enumeration_review.json`, `enumeration/listing_cards.jsonl`, `enumeration/page_evidence.jsonl`, `enumeration/exclusions.jsonl`, and `enumeration/2026_supersession_receipt.json`.
- Detail and provenance: `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/staging/metadata.jsonl`, `staging/metadata_corrections.jsonl`, `staging/detail_manifest.json`, `discovery/detail_recovery.jsonl`, and `provenance/field_provenance.jsonl`.
- Preparation and merge: `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/prepared/prepare_report.json`, `prepared/prepare_validation.json`, `prepared/metadata_staging.jsonl`, `prepared/metadata_exclusions.jsonl`, and `prepared/merge_receipt.json`.
- Finalization: `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/finalization/nature_finalize_receipt.json`, `finalization/reconcile/reconcile_receipt.json`, `finalization/reconcile/venue_coverage_report.json`, and `finalization/expected_install_receipt.json`.

## Update and reuse

- Reuse the installed expected manifests, stable identities, exclusion ledger, complete metadata, and field provenance before retrieving unchanged records. For a later update, enumerate the current waterline freshly, follow the observed Next chain, and fetch details only for new, incomplete, stale, or conflict-prone candidates. Preserve old current-year evidence when a supersession is needed.
- Require complete candidate coverage as accepted metadata or explicit exclusions and a PASS strict staging validation before a transactional merge. After merging, run production reconcile and finalize the venue to `ACTIVE` from the actual receipts. Keep the run-local pre-ingest artifacts immutable and use this installed playbook for future updates.
- Keep TLS verification enabled and stop on HTTP 403/429, CAPTCHA, login/access gates, or explicit publisher denial. Bound retries and retain transport errors with their recovery evidence.

## Download

- Metadata contains observed article landing and PDF links. A later PDF task must use the stored canonical links and the authorized Browser workflow; do not infer access or content from a URL alone.

Last verified: 2026-10-02. This playbook records the Nature Methods run that passed complete enumeration, strict preparation, independent detail review, production merge, reconcile, finalization, expected-manifest installation, receipt-integrity review, and search verification.

# Nature

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

Venue ID: `nature`. This playbook records the accepted full metadata initialization and the evidence needed for future incremental updates.

## Current production state (2026-10-03)

- Nature is `ACTIVE` in the production catalog and campaign state. The accepted range is 2015–2026, through the official waterline observed at 2026-10-01T20:16:04Z: volume 658, issue 8134, dated 1 October 2026. This is the recorded snapshot waterline, not a claim of continuous live coverage.
- 12,403 included records and 2,345 explicit exclusions reconcile exactly to 14,748 unique source identities. The original candidate set of 12,402 plus the approved Registered Report overlay yields the 12,403 accepted metadata records.
- Titles, ordered authors, DOI, publication dates, landing URLs and observed PDF links are present for all 12,403 records. Abstracts are present for 12,400; three source-verified absent abstracts remain null with reasons. No PDF bytes were downloaded.
- 12,289 records have volume and issue. The remaining 114 have no assignment in the saved official sources (2 records dated 2025 and 112 dated 2026). Keep these fields null; their per-record evidence and reasons are in `scope/controller_missing_issue_review_20261003.json`, independently checked in `scope/controller_missing_issue_binding_check_20261003.json`.
- Collection, strict staging, the full nine-field source audit, transactional merge and strict reconciliation all passed. The catalog contains 12,403 Nature canonical works, 14,748 source items and 24,806 article/PDF locations. Search is ready and not stale, with all 12,403 Nature papers exposed by the venue filter.
- Historical candidate, pending-ingest and initial-failure reports are retained for audit. Their status is superseded by the accepted receipts below; raw enumeration and source caches were not rewritten to erase that history.

## Official sources and identity rules

Use first-party Nature HTML only:

- Issue archive: `https://www.nature.com/nature/browse-issues`
- Volumes: `https://www.nature.com/nature/volumes`
- Volume and issue pages: `https://www.nature.com/nature/volumes/658` and `https://www.nature.com/nature/volumes/658/issues/8134`
- Current feed: `https://www.nature.com/nature/articles?searchType=journalSearch&sort=PubDate&year=2026`
- Closed-year surface: `https://www.nature.com/nature/research-articles?year=<year>`
- Detail URL pattern: `https://www.nature.com/articles/<native-id>`

The collection is issue-based from 2015 through 2026. Assign `venue_year` from the observed issue year, falling back to the observed online publication year when no issue is assigned; retain null volume/issue values for unassigned records. Deduplicate by canonical `/articles/<native-id>` path while preserving every listing, issue, page, and cache provenance. Keep the observed landing URL, DOI, and source-page URL; do not synthesize page grids or identities from a facet count.

The scope combines 11,242 closed-year identities, 3,388 current-feed identities, and 118 fresh 2026 issue-TOC boundary/cross-year identities. The post-baseline issue range is 8122–8134, with 8122–8128 retained as an explicit deduplicated overlap review range. The current-feed facet/page drift and the excluded Book Review duplicate are recorded as explained warnings; the run does not claim exhaustive enumeration of non-research news facets.

The retained scope evidence is under `legacy-evidence://workspace/nature-expansion-20261001/nature/enumeration/`, especially `listing_cards.jsonl`, `page_evidence.jsonl`, `exclusions.jsonl`, and `scope_supplement_cards.jsonl`. Historical seed evidence is `historical_seed.jsonl` plus `historical_seed_receipt.json`. The exact closeout and source bindings are in the scope closeout cited above.

## Inclusion and type handling

Include the approved Article, Letter, Analysis, Review/Review Article, Perspective and Brief Communication types, and preserve every excluded card with its raw type and exclusion reason. The one approved exception is `/articles/s41586-026-10536-1`: official listing type `Registered Report`, detail type `Registered report`, and final staging `include`. The raw enumeration exclusion row remains immutable; the overlay is what changes the effective partition from 12,402/2,346 to 12,403/2,345.

Use the visible detail category as the primary type, while retaining raw `citation_article_type`, `dc.type`, `prism.section`, listing type, and detail type with provenance. Broad schema values such as `Article`, `OriginalPaper`, and `ReviewPaper` can remain compatible with a more specific visible/listing type. The only observed concrete alias pair is `Review` and `Review Article`; preserve both spellings. Any other concrete type conflict is a review condition.

## Detail extraction contract

Extract title, ordered authors, abstract, publication date, document type, DOI, landing URL, and observed PDF/article links from the official detail HTML. Listing summaries are identity evidence and never substitute for a detail abstract.

Use ordered `citation_author` values first, then JSON-LD where available. If both are absent, use visible primary bylines from `ul[data-test="authors-list"] a[data-test="author-name"]`, preserving a collective institution name and excluding hidden group members and controls such as “Show authors”. Record the source chosen.

Accept an abstract only from a paired `AbsN-section`/`AbsN-content` publisher container inside the article body. Prefer a clear `Abstract` heading or `data-title`. The five exact observed heading labels are:

- `Abtract` — `/articles/s41586-019-1347-4`
- `Abstratct` — `/articles/s41586-021-04214-7`
- `Asbtract` — `/articles/s41586-021-04129-3`
- decoded `Abstract>` — `/articles/s41586-022-04975-9`
- `Abstarct` — `/articles/s41586-022-04513-7`

These are an explicit observed set, not a fuzzy spelling rule. The outer section, the paired `AbsN-section` itself, and its heading/data-title must have no concrete non-Abstract conflict. Preserve the raw label, exact locator, and basis in `field_provenance`; exclude standfirsts, ordinary body text, unrelated `AbsN` sections, and isolated containers. Multiple true candidates or a locator conflict requires review.

On verified legacy pages, a paired publisher container without a heading is usable only inside the article body and without a conflicting non-Abstract label. Record the basis as an unlabeled publisher abstract container; an arbitrary or isolated AbsN element is insufficient.

The three verified true-null exceptions remain `abstract: null` with `missing_fields.abstract=visible_abstract_section_absent`: `/articles/nature19086`, `/articles/nature19087`, and `/articles/nature16540`. Their retained official HTML has no usable Abstract heading, paired abstract container, or abstract data-title. Evidence is in `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/missing_abstract_review_2016_20261002.json` and `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/missing_abstract_review_nature16540_20261002.json`; no standfirst or body text is promoted.

Prefer exact `citation_online_date`, `prism.publicationDate`, or `dc.date`, then an exact visible `Published` time. Preserve source precision and never invent a day from a month-only value. A PDF URL requires an observed same-origin `citation_pdf_url` or visible PDF/download link; do not download PDF bytes or infer access from the URL shape. Reject unsafe or non-HTTP destinations.

## Canonical-link representation

Eight saved 2026 pages point their unique canonical link to the same article's PDF. The independent auditor accepts this observed representation only when the canonical is exactly the observed `citation_pdf_url`, both are the same official HTTPS native-path PDF without query/fragment, the unique `og:url` is exactly the detail URL, and the source DOI matches both the stored DOI and native article ID. Preserve the original canonical value and `accepted_variant=same_article_pdf`. Do not accept unrelated PDFs, redirects, multiple canonicals, wrong/missing identifiers, or a title-only match. The exact-detail canonical behavior remains unchanged.

The candidate passed 13 positive/negative fixtures and all eight real pages; the applied auditor then passed all nine fields for all 12,403 records. See `scope/canonical_identity_candidate_20261003/controller_auditor_apply_receipt.json` and `controller_full_cached_detail_review_20261003_final.json`.

## Access, transport, and recovery

Use public HTML without login, cookies, credentials, third-party identity sources, or remote APIs. The collector interval is 6.2 seconds (at most 10 source pages per minute), with bounded retries and the existing 5/15-second retry delays. Keep TLS verification enabled. Stop the batch immediately on 403, 429, CAPTCHA/human-verification, login/access gates, or a clear non-article challenge page; do not cache challenge text as article metadata. Preserve response status, errors, cache hash, and recovery provenance.

Retain sanitized listing/detail HTML, cache hashes, source URL, year/issue filter, page and card positions, visible `Next`, and observed type/date evidence under `cache/`, `provenance/`, `enumeration/`, and `staging/`. The 58 detail error events and 106 retry events are historical provenance; the latest retry state has no unresolved path.

Resume from `run_state.json` and the detail retry ledger only after validating cache hashes and identity. A future incremental run must create a new run/delta, re-enumerate the current feed and visible issue pages, and record a new waterline. Closed-year identities and field provenance can be reused only after identity/hash checks. This run's 2026 cache must remain archived evidence and must not be presented as a fresh future listing.

## Accepted run and future updates

The accepted merge input is `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/candidate_prepare/controller_final_20261003/standard_prepared/`. Its strict validator reports 12,403 included, 2,345 excluded, zero missing/extra identities and no validation errors. Use the Nature-specific scope adapter when replaying this run: the generic preparer alone does not represent the historical/current-feed/118 issue-boundary union and Registered Report overlay.

The full source audit covered 12,398 cached HTML records and six preserved Browser records, with one overlap, yielding 12,403 unique records. All retry paths are resolved and the manual queue is empty. The external provenance audit has one documented harmless representation difference: the historical `nature14661` external event has additional field-level observation timestamps; field values and missing-field reasons agree.

Future updates start from the accepted watermark, expected manifests and catalog. Discover a fresh current feed and issue boundary in a new run, then retrieve new, changed or incomplete details. Apply the same field/provenance checks, strict reconciliation, single-writer merge and search refresh. Do not recrawl closed years solely because this initial run used historical reuse, and do not treat its retained waterline as a fresh future observation.

Production storage uses the existing ordered author objects and NFKC/whitespace text normalization. The original prepared fields and official caches remain available; all stored values were checked against that exact normalization contract in `controller_stored_field_normalization_audit_20261003.json`.

## Evidence index

- Full detail source audit: `legacy-evidence://workspace/nature-expansion-20261001/nature/controller_full_cached_detail_review_20261003_final.json`
- Pre-merge acceptance: `legacy-evidence://workspace/nature-expansion-20261001/nature/controller_premerge_acceptance_20261003.json`
- Committed merge: `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/candidate_prepare/controller_final_20261003/standard_prepared/merge_receipt.json`
- Finalization and ACTIVE promotion: `legacy-evidence://workspace/nature-expansion-20261001/nature/finalization/nature_finalize_receipt.json`
- Production field/count verification: `legacy-evidence://workspace/nature-expansion-20261001/nature/controller_production_check.json`
- Search verification: `legacy-evidence://workspace/nature-expansion-20261001/nature/search_verification.json`

- Scope closeout: `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/controller_2026_scope_closeout_20261002.json`
- Collection/staging receipt: `legacy-evidence://workspace/nature-expansion-20261001/nature/run_receipt.json`
- Detail completion manifest: `legacy-evidence://workspace/nature-expansion-20261001/nature/staging/detail_manifest.json`
- Final identity/provenance audit: `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/controller_final_identity_provenance_audit_20261003.json`
- Prepared strict staging validation: `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/candidate_prepare/controller_final_20261003/standard_prepared/staging_validation.json`
- Registered Report resolution/detail evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/controller_registered_report_resolution_20261002.json`, `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/controller_registered_report_scope_evidence_20261002.json`, and `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/controller_registered_report_detail_review_20261002.json`
- Abstract repair receipts and cached source evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature/staging/` and `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/abstract_typo_candidate/`, `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/abstract_typo_abstratct_candidate/`, `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/abstract_typo_asbtract_candidate/`, `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/abstract_heading_suffix_candidate/`, and `legacy-evidence://workspace/nature-expansion-20261001/nature/scope/abstract_typo_abstarct_candidate/`

Last verified: 2026-10-03. Collection, source audit, merge, reconciliation, ACTIVE state, search exposure and playbook installation are complete; the accepted source waterline is stated above.

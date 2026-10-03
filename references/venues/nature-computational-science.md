# Nature Computational Science playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Current production state (2026-10-01)

- Venue ID and canonical name: `nature-computational-science`, Nature Computational Science.
- The verified research range is 2021–2026 with waterline 2026-10-01. The campaign state in `<db-home>/campaign_state.json` and the catalog venue state in `<db-home>/catalog.sqlite` are both `ACTIVE`.
- The production merge receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/prepared/merge_receipt.json` is `PASS` with `committed: true`: 451 records merged, 603 exclusions, and the watermark advanced.
- The production finalization receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/finalization/nature_finalize_receipt.json` is `PASS` with `committed: true`. It links the merge, reconcile, promotion, and installation of the six expected manifests; campaign and catalog writes were true, while registry and watermark writes were false.
- The reconcile receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/finalization/reconcile/reconcile_receipt.json` and report at `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/finalization/reconcile/venue_coverage_report.json` are `PASS`. The report has 451 canonical works, 1,054 source items, 603 exclusions, zero duplicate/missing/extra identities, and `catalog_ready: true`.
- The raw collection receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/run_receipt.json` says `verified_collection_and_staging_complete_pending_production_ingest`. That status describes the pre-production collection checkpoint and is historical; it is superseded by the PASS/committed merge and finalization receipts above. Do not report NCS as currently pending.

## Identity and scope

- The official public Nature archive was observed for calendar years 2021 through 2026. The scope starts in 2021 because that is the registry and journal start for this run.
- Allowed source domain: `nature.com`. Collection used public HTML without login, cookies, credentials, or authorization material. The raw collector is `tools/nature_metadata_collector.py`, with the fresh current-year helper at `legacy-evidence://workspace/tools/ncs_2026_fresh_enumeration.py`.
- Allowlisted staged types were Article (292), Brief Communication (57), Perspective (51), Resource (33), Review Article (14), and Analysis (4), for 451 metadata records.
- The exclusion ledger contains 603 identities, all recorded with `type_not_in_baseline_allowlist`: Author Correction, Comment, Correspondence, Editorial, News & Views, News Feature, Publisher Correction, Q&A, Research Briefing, Research Highlight, and Viewpoint.
- The accepted production field coverage is title 451/451, authors 451/451, abstract 451/451, DOI 451/451, publication date 451/451, landing URL 451/451, and PDF link 451/451. No required field is missing.
- No PDF bytes were downloaded. The 451/451 PDF values are observed article PDF links recorded in metadata.

## Official entry points

- Browse archive: https://www.nature.com/natcomputsci/articles
- Year filter: `https://www.nature.com/natcomputsci/articles?year=<year>`
- Detail URL pattern observed: `https://www.nature.com/articles/s43588-<year>-<suffix>`
- The visible Next links preserve `searchType=journalSearch&sort=PubDate&year=YYYY&page=N`. Follow those observed links rather than synthesizing a page grid.
- PDF metadata is same-origin and comes from `meta[name=citation_pdf_url]`. Treat the recorded PDF URL as metadata; do not infer that its bytes were fetched.

## Enumeration and 2026 drift handling

- The final enumeration observed 55 listing pages and 1,054 unique article paths. Year totals were 2021: 180, 2022: 208, 2023: 197, 2024: 167, 2025: 182, and fresh 2026: 120.
- Record year filter, page number, card position, canonical path, title, listing summary, listing authors, listed type, listing date, open-access flag, source URL, selected facet, and visible Next link in the page evidence.
- The original 2026 traversal observed 120 raw cards but only 119 unique paths. `/articles/s43588-026-01010-z` appeared on page 2 position 20 and page 3 position 1; its two occurrences are retained in the original duplicate evidence. The original selected facets were `[119, 119, 120, 120, 120, 120]`.
- Preserve the original drift run under `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/enumeration/original-2026-drift-20261001/` and its original listing cache. Do not silently overwrite or discard that evidence.
- Re-enumerate the current year in a fresh isolated directory and cache. The fresh run observed six pages, 120 raw cards, 120 unique paths, first and last facet `2026 (120)`, a complete Next chain, no duplicate path, and the added path `/articles/s43588-026-01050-5`. The supersession receipt records the replacement and integrity checks.
- The closed years 2021–2025 were not rewritten during supersession. The preservation check reconstructs the prior complete listing, page-evidence, and exclusion files from current closed-year bytes plus the archived old 2026 bytes; all three reconstructed SHA-256 values equal the prior independent audit values.
- Deduplicate by stable canonical `/articles/s43588-...` path while retaining normalized DOI. Corrections and other excluded types remain in the exclusion ledger and never replace an included article.

## Browser and detail behavior

- Official detail pages normally stabilized in a few seconds in Edge and visibly showed type, published date, title, ordered authors, journal/volume/pages, and an Abstract region.
- Listing pages normally expose 20 cards. A page is terminal only when its card count, selected facet, and missing Next link are recorded. A page-card mismatch must remain explicit in evidence until a fresh isolated run resolves it.
- On detail pages, use `meta[name=citation_author]` for the ordered complete author list. For abstracts, select the publisher's explicit Abstract section at any `AbsN-content` locator, using an explicit Abstract heading/data-title or a paired `AbsN-section`/`AbsN-content` publisher container inside the article body; exclude standfirst and other non-Abstract containers, record the locator and selection basis, and route multiple true candidates or ambiguity to review. If the official page has no visible abstract, preserve `abstract: null` with a structured missing reason; absence alone is not a manual-review failure. For document type, prefer the visible article category, then `citation_article_type`, `dc.type`, and `prism.section`; preserve raw values. A generic `Article` or broad `OriginalPaper`/`ReviewPaper` value can remain compatible with a more specific visible/listing type, while concrete conflicts require review. Use publisher date/DOI/PDF metadata for the remaining fields. Never promote a listing summary as a detail abstract.
- Two detail requests initially had transport failures and were recovered with focused same-origin `curl --disable --http1.1 -L` requests after Browser verification. Original errors remain in the detail-error evidence; recovery records and Browser DOM evidence retain the provenance. No access restriction or challenge was observed.
- Stop the source surface on HTTP 403/429, CAPTCHA, login/access gates, or explicit publisher denial. Keep TLS verification enabled, respect Retry-After, and bound retries.

## Collection evidence

- Raw run receipt: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/run_receipt.json`.
- Raw run-local playbook (preserved and not edited): `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/nature-computational-science.md`, SHA-256 `e7ce0e40f361e2e112f1a835aa84e422b33d9f37083e782d3aa9a7b37f2a129d`.
- Final enumeration cards: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/enumeration/listing_cards.jsonl`, 1,054 rows; page evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/enumeration/page_evidence.jsonl`; exclusions: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/enumeration/exclusions.jsonl`, 603 rows; duplicate ledger: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/enumeration/duplicates.jsonl`.
- Original current-year archive: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/enumeration/original-2026-drift-20261001/`; archive manifest: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/enumeration/original-2026-drift-20261001/archive_manifest.json`.
- Fresh current-year enumeration: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/enumeration/2026-fresh-20261001/`; fresh cache: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/cache/listings-2026-fresh-20261001/`; supersession receipt: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/enumeration/2026_supersession_receipt.json`.
- Closed-year preservation audit: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/controller_closed_year_preservation_check.json` is `PASS`. Its reconstructed hashes are listing cards `10a912772a98c0f1023756be050ba3567cf2b03050ad6709eb82cf2d3f6187f5`, page evidence `0e7fbddddce7a1443da8310c29ec30cf9fb61aa6309acf1154d0269d6b1f3fd8`, and exclusions `0e1d95b681ad34f3ff07276739e871955bdef4a603ecb31aee0a35acfd272d2a`; each equals its previous audit hash.
- Staged metadata: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/staging/metadata.jsonl`, 451 rows; field provenance: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/provenance/field_provenance.jsonl`; detail errors and recovery: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/staging/detail_errors.jsonl` and `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/discovery/detail_recovery.jsonl`; Browser DOM evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/provenance/browser_dom_evidence.jsonl`.
- Strict preparation evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/prepared/prepare_validation.json` and `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/prepared/staging_validation.json` are `PASS` with no reported errors.
- Merge evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/prepared/merge_receipt.json`; staged input: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/prepared/metadata_staging.jsonl` with SHA-256 `927d320db8cd6c0cfa57dfaa6acd8a1579384fc8d15d6cf2ce2d3952fc4d7a99`.
- Finalization evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/finalization/nature_finalize_receipt.json`, promotion receipt `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/finalization/nature_promotion_receipt.json`, reconcile receipt `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/finalization/reconcile/reconcile_receipt.json`, and coverage report `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/finalization/reconcile/venue_coverage_report.json`.
- The six installed expected manifests are under `<db-home>/manifests/expected/nature-computational-science/`. The source expected-manifest root used by finalization is `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/prepared/expected/`, with the installation receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-computational-science/finalization/expected_install_receipt.json`.

## Update and reuse

- Reuse the installed expected manifests, closed-year identities, exclusion ledger, complete metadata, and field provenance before retrieving unchanged records. Revalidate the current-year listing, selected facet stability, Next chain, cross-page duplicates, missing/stale fields, and any layout-sensitive behavior.
- When a current-year listing drifts, preserve the old current-year listing and cache in a separate archive, run a fresh isolated enumeration/cache, and write a supersession receipt. The fresh run must prove raw-card count, unique paths, first/last facet, complete Next chain, and no duplicate paths before it replaces the current-year enumeration used for staging.
- Keep the production catalog and campaign state as the current state source. The registry file `<db-home>/registry/venues/nature-computational-science.yml` was not modified by this run.
- Future detail updates should request details only for new or incomplete candidates. Historical/current-year listing evidence is scope evidence; it does not replace detail-validated metadata.
- Nature Methods is now separately documented as `ACTIVE` and search-verified in `references/venues/nature-methods.md`; its run-local candidate playbook remains preserved at `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/playbook_candidate.md`. This NCS playbook's production status comes from its own merge, reconcile, finalization, and search-verification receipts.

## Download

- The metadata records contain observed article PDF links, but no PDF or supporting-information bytes were downloaded in this run.
- A later download task must use the stored canonical landing/PDF links and the authorized Browser workflow. It must not infer access or content from the URL alone.

Last verified: 2026-10-01. This playbook records the NCS run that passed fresh 2026 supersession, closed-year preservation, strict preparation, production merge, reconcile, and finalization.

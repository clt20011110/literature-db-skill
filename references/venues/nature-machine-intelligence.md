# Nature Machine Intelligence playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Current production state (2026-10-01)

- Venue ID and canonical name: `nature-machine-intelligence`, Nature Machine Intelligence.
- The current waterline is 2026-10-01 and the research range is 2019–2026. The campaign state in `<db-home>/campaign_state.json` and the catalog venue state in `<db-home>/catalog.sqlite` are both `ACTIVE`.
- The production merge receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/prepared/merge_receipt.json` is `PASS` with `committed: true`: 845 records merged, 460 exclusions, and the watermark advanced.
- The production finalization receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/finalization/nature_finalize_receipt.json` is `PASS` with `committed: true`. It links the merge, reconcile, promotion, and installation of the eight expected manifests; campaign and catalog writes were true, while registry and watermark writes were false.
- The reconcile receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/finalization/reconcile/reconcile_receipt.json` and report at `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/finalization/reconcile/venue_coverage_report.json` are both `PASS`. The report has 845 canonical works, 1,305 source items, 460 exclusions, zero duplicate/missing/extra identities, and `catalog_ready: true`.
- The raw collection receipt at `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/run_receipt.json` says `verified_collection_and_staging_complete_pending_production_ingest`. That status describes the pre-production collection checkpoint at 2026-10-01T09:58:21Z and is historical; it is superseded by the PASS/committed merge and finalization receipts above. Do not report NMI as currently pending.

## Identity and scope

- The official public Nature archive was observed for calendar years 2019 through 2026. The scope starts in 2019 because that is the observed journal range; it does not claim a 2015 backfill.
- Allowed source domain: `nature.com`. Collection used public HTML without login, cookies, credentials, or authorization material. The raw collector is `tools/nature_metadata_collector.py`.
- Allowlisted staged types were Article (713), Perspective (90), Review Article (29), and Analysis (13). The exclusion ledger contains 460 identities, including corrections, editorials, news/views, correspondence, and other non-research page types.
- The accepted production field coverage is title 845/845, authors 845/845, DOI 845/845, landing URL 845/845, PDF link 845/845, and publication date 845/845. Abstracts are present for 844/845; one record is retained with a documented missing abstract.
- No PDF bytes were downloaded. The 845/845 PDF values are observed article PDF links recorded in metadata.

## Official entry points

- Browse archive: https://www.nature.com/natmachintell/articles
- Year filter: `https://www.nature.com/natmachintell/articles?year=<year>`
- Detail URL pattern observed: `https://www.nature.com/articles/s42256-<year>-<suffix>`
- The observed RSS link is https://www.nature.com/natmachintell.rss, but the verified run used the HTML archive and detail pages as its source of record.
- PDF metadata is same-origin and follows the article path, for example https://www.nature.com/articles/s42256-026-01309-6.pdf. Treat it as a recorded link; do not infer that its bytes were fetched.

## Enumeration and detail workflow

- Follow the visible `Next` link and its observed year/search parameters. Do not synthesize a page grid. The completed run observed 70 listing pages and 1,305 unique article paths. Listing totals were 2019: 130, 2020: 142, 2021: 154, 2022: 167, 2023: 178, 2024: 184, 2025: 206, and 2026: 144.
- Record year filter, page number, card position, canonical path, title, listing summary, listing authors, listed type, listing date, open-access flag, source URL, and the visible Next link in the enumeration evidence.
- On detail pages, use `meta[name=citation_author]` for the ordered complete author list. For abstracts, select the publisher's explicit Abstract section at any `AbsN-content` locator, using an explicit Abstract heading/data-title or a paired `AbsN-section`/`AbsN-content` publisher container inside the article body; exclude standfirst and other non-Abstract containers, record the locator and selection basis, and route multiple true candidates or ambiguity to review. If the official page has no visible abstract, preserve `abstract: null` with a structured missing reason; absence alone is not a manual-review failure, as shown by the accepted missing-abstract record. For document type, prefer the visible article category, then `citation_article_type`, `dc.type`, and `prism.section`; preserve raw values. A generic `Article` or broad `OriginalPaper`/`ReviewPaper` value can remain compatible with a more specific visible/listing type, while concrete conflicts require review. Use `citation_doi` for DOI and `meta[name=citation_pdf_url]` for the observed PDF link. Never promote a listing summary as a detail abstract.
- Deduplicate by the stable canonical `/articles/s42256-...` path while retaining the normalized DOI. Keep excluded correction and other non-allowlisted identities in the exclusion ledger; do not replace an article with a correction.
- Stop the source surface on HTTP 403/429, CAPTCHA, login/access gates, or explicit publisher denial. Keep TLS verification enabled, respect Retry-After, and bound transport retries.

## Collection evidence

- Raw run receipt: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/run_receipt.json`.
- Raw run-local playbook (preserved and not edited): `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/nature-machine-intelligence.md`, SHA-256 `03610d78cf0f715513b4ced60e32a5d2084e1ce8becb4d53c105b396a1156c48`.
- Enumeration cards: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/enumeration/listing_cards.jsonl`, 1,305 records, SHA-256 `05e60d7a729e6ab8beba2004d73ed1704072064f06445ca372a5844b2b58bec7`.
- Listing page evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/enumeration/page_evidence.jsonl`; exclusions: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/enumeration/exclusions.jsonl`, 460 records.
- Staged metadata: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/staging/metadata.jsonl`, 845 records; field provenance: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/provenance/field_provenance.jsonl`, 845 records.
- Detail errors and recovery: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/staging/detail_errors.jsonl` and `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/discovery/detail_recovery.jsonl`. Six transient detail failures were all recovered; four recovery records use the Browser DOM evidence at `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/provenance/browser_dom_evidence.jsonl`.
- Strict preparation evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/prepared/prepare_validation.json` and `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/prepared/staging_validation.json` are `PASS` with no reported errors.
- Merge evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/prepared/merge_receipt.json`; its staged input is `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/prepared/metadata_staging.jsonl` with SHA-256 `7f8e591c46e10425d7b0bb73965df46bba10849a103a302638b422dc99180269`.
- Finalization evidence: `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/finalization/nature_finalize_receipt.json`, promotion receipt `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/finalization/nature_promotion_receipt.json`, reconcile receipt `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/finalization/reconcile/reconcile_receipt.json`, and coverage report `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/finalization/reconcile/venue_coverage_report.json`.
- The installed expected manifests are under `<db-home>/manifests/expected/nature-machine-intelligence/`. The source expected-manifest root used by finalization is `legacy-evidence://workspace/nature-expansion-20261001/nature-machine-intelligence/prepared/expected/`.

## Known exceptions

- `/articles/s42256-019-0109-1` has no visible abstract section. It is included with `abstract: null` and the documented reason `visible_abstract_section_absent`.
- Six detail URLs initially had bounded transport failures (curl 18/28/35/52 or incomplete status-200 bodies); all six were recovered and retained in the error/recovery evidence.
- A standard-library TLS trial failed with `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`. TLS verification remained enabled; this did not become a production bypass.
- There are zero manual-review records and zero provenance gaps in the final reconcile report.

## Update and reuse

- Reuse the installed expected manifests, canonical identities, exclusion ledger, complete metadata, and field provenance before retrieving unchanged records. Revalidate current-waterline listings, missing abstracts, stale/conflicting fields, and any layout-sensitive fields.
- Keep the production catalog and campaign state as the current state source. The registry file `<db-home>/registry/venues/nature-machine-intelligence.yml` was not modified by this run.
- The active campaign records the portfolio patch as `collector_portfolio.status=APPLIED_OFFLINE_VERIFIED` with six offline tests and live per-venue validation. The historical review and fixture remain at `legacy-evidence://workspace/nature-expansion-20261001/collector_portfolio_review.json` and `legacy-evidence://workspace/nature-expansion-20261001/test_collector_portfolio.py`; they document the earlier candidate-only review and are not the current venue status.
- The campaign and this run's receipts are the current state source: Nature Machine Intelligence, Nature Computational Science, and Nature Methods are complete, merged, reconciled, finalized, and search-verified. Nature's 2015–2025 historical enumeration can seed identities only after its preflight checks, while 2026 must be freshly listed. Historical abstracts, authors, and detail metadata must be discarded.
- Sample, enumeration, and detail checkpoint caches in the portfolio run only restore their associated run. A future incremental update must create a new run/delta and perform fresh current-waterline validation; do not reuse an old 2026 cache as fresh evidence. NCS has its separate current playbook at `references/venues/nature-computational-science.md`; Methods has its installed current playbook at `references/venues/nature-methods.md` and preserves the run-local candidate at `legacy-evidence://workspace/nature-expansion-20261001/nature-methods/playbook_candidate.md` as historical evidence.

## Download

- The metadata records contain observed article PDF links, but no PDF or supporting-information bytes were downloaded in this run.
- A later download task must use the stored canonical landing/PDF links and the authorized Browser workflow. It must not infer access or content from the URL alone.

Last verified: 2026-10-01. This playbook records the NMI run that passed collection, strict preparation, production merge, reconcile, and finalization.

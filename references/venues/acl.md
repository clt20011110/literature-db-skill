# ACL Anthology — ACL main-conference research papers

> Portable playbook. The scope and counts below were verified against the ACL Anthology site build observed on 2026-10-04, pinned at commit `0e1bf6e`. Read the current registry, campaign, expected manifests, and strict staging report before a future update; this dated run is not a claim about a later site build.

## Identity and scope

- Venue ID: `acl`; annual conference proceedings. Covered years are 2015–2026, including only main-conference research papers published by the date of the pinned site build.
- Include the official main conference's long and short research-paper volumes. The 2015 proceedings are the joint ACL-IJCNLP meeting; the source XML tags its main volumes for both ACL and IJCNLP. For the remaining selected collection years, follow the ACL-tagged main volumes listed below.
- Exclude Findings as a separate publication series, and exclude sibling workshop, student research, system demonstration, tutorial, industry, and other non-main volumes. The current run recorded 39 out-of-scope sibling volumes in `scope_exclusions.json`; each has its official volume ID, title, year, ACL Anthology venue tags, and paper count. The selected IDs by year are in `source_manifest.json`.
- Preserve front matter as explicit `front_matter` exclusions. Review non-research-looking titles and Roman-numeral page ranges individually; do not exclude a paper just because a term such as “editorial” appears in a research title. The ACL 2023 Program Chairs' peer-review report is excluded as `non_research_content` after checking its official abstract and Roman-numeral pagination.

## Official sources and collection method

- The [ACL Anthology API FAQ](https://aclanthology.org/faq/api/) points to the official [`acl-org/acl-anthology` repository](https://github.com/acl-org/acl-anthology/) for metadata and says PDFs are hosted by ACL Anthology. The [development page](https://aclanthology.org/info/development/) describes the XML metadata and records the site build used to pin the collection.
- Read the per-year XML collections at `data/xml/P15.xml` through `P19.xml` and `data/xml/2020.acl.xml` through `2026.acl.xml` from the pinned repository commit. XML provides the official volume and paper identities, titles, ordered authors, pages, DOI, abstract when present, and publication month/year. XML comments provide observed canonical ACL Anthology IDs and links.
- Enumerate each selected volume from its exact official volume page. Use the page's visible article and PDF anchors for `landing_url` and `pdf_url`; save the observed href and page provenance. Do not construct a PDF URL from the documented URL convention. No PDF bytes are downloaded by the metadata collector.
- For records without an XML abstract, use the abstract shown on that official volume page when present. A missing abstract or DOI receives a structured missing reason and remains visible in field coverage. The 2015 and 2016 volume pages do not expose abstracts in the captured source.
- The [linking FAQ](https://aclanthology.org/faq/linking/) documents canonical IDs and link conventions. It supports interpretation of official source formats; the collector records the links actually observed for each paper.

## Identity, provenance, and validation

- Native identity is the exact ACL Anthology ID from the XML URL comment, cross-checked against the official volume page. The XML pointer is recorded per field that comes from XML, including a fully quoted XPath-like pointer such as `/collection[@id='P15']/volume[@id='1']/paper[@id='1']`.
- Title, authors, pages, DOI, abstract, year/month, landing page, and PDF href each have source URL, observation time, and extraction method in `field_provenance`. Volume-level metadata uses the official XML volume record. `publication_date` preserves the source month and year at month precision.
- The official volume-page identity set and selected XML paper set must match exactly. The expected manifests include every paper identity and each front-matter identity in the selected volumes. Compare the current set with the prior ACL expected manifest; investigate additions, missing records, and any scope reclassification before accepting a waterline.
- For the 2026-10-04 pinned source, strict validation passed with 10,186 included papers, 23 accounted exclusions (22 front matter and one non-research report), and 10,209 expected source identities. There were no missing, extra, or unresolved identities. Every included row had an observed landing page and PDF href. Abstracts were present for 9,538 rows and DOI for 10,183; remaining absences have explicit reasons. See `staging_validation.json`, `waterline_evidence.json`, `collection_stats.json`, and `content_scope_review.json` in the dated run directory.

## Durable run and updates

- Collector: `tools/collect_acl_metadata.py`.
- Run evidence: `<db-home>/runs/expand-20261004/acl/`, including yearly compressed `expected/<year>.jsonl.gz`, `metadata_staging.jsonl`, `metadata_exclusions.jsonl`, `unresolved.jsonl`, `source_manifest.json`, `official_source_evidence.json`, `scope_exclusions.json`, `content_scope_review.json`, and `waterline_evidence.json`.
- The source build pinned by the official development page was `0e1bf6e` (2026-10-04). For future updates, discover the current official build commit from that page, fetch that commit's XML, and inspect actual official volume pages before extending the year range. Run `tools/litdb.py staging validate --strict` with the matching venue/run and expected files before handing staging to the single-writer merge workflow.

## Last verified

2026-10-04 — official XML and selected volume-page captures were reviewed, the prior expected IDs were reconciled, the ACL 2023 Program Chairs report was excluded after manual review, and strict staging validation passed. No PDF files were downloaded.

The 2026-10-04 expansion merged these 10,186 research records through the single writer and is included in the `v1.1.0` catalog. Production expected-manifest reconciliation passed with zero missing, extra, duplicate, or provenance-gap identities, and the pre-release local hybrid-search check passed. The catalog stores abstracts for 9,538 records; remaining missing fields retain their source-specific reasons. Receipts are in `<db-home>/runs/expand-20261004/acl/merge_receipt.json`, `final-reconcile/venue_coverage_report.json`, and `integration_summary.json`. No paper PDF files were downloaded.

# ECCV (Springer LNCS and ECCV Virtual)

Last verified: 2026-10-04. Venue ID: `eccv`. Include the main-conference research papers for the biennial editions held in 2016, 2018, 2020, 2022, 2024, and 2026. Do not treat absent years as zero-paper editions. Collect metadata and links only; never download paper PDFs.

## Official sources and access boundaries

- SpringerLink's [ECCV conference series](https://link.springer.com/conference/eccv) identifies the proceedings series and links its main book. For each year, follow only the book-volume and TOC-pagination links observed in those official pages. The chapter rows provide the stable chapter DOI and title; the chapter page provides the ordered authors, abstract, publication date, DOI, article URL, and visible PDF URL.
- For ECCV 2026, the official Springer series/related-volume evidence contains 77 observed books and 2,799 chapter IDs; the earliest saved search page reports 78 results, while normal browser observations of the public URL pattern `https://link.springer.com/search?query=Computer+Vision+ECCV+2026&content-type=Book&sortBy=relevance&page=...` vary between 77 and 78 visible results and include duplicate rows. The observed books identify Parts I–LXXVIII except LIX. The old `facet-content-type=Book` URL returned an HTTP 200 Client Challenge; preserve that response and do not retry it or synthesize query variants. The publisher and Virtual identities are now closed by the frozen official one-to-one crosswalk: 2,798 paired records, 36 Virtual-only papers, and 1 Springer-only chapter, for a 2,835-record union. The Part LIX clue does not account for all 36 Virtual-only records and is not used to invent an ISBN or DOI. The original 2,799-row Springer manifest is preserved in `provisional_expected/2026.incomplete-series-only.jsonl.gz`; the canonical union is `expected/2026.jsonl.gz`.
- The one saved [ECVA paper index](https://www.ecva.net/papers.php) supplies observed title, author-list, DOI-link, and PDF-link evidence. `www.ecva.net/robots.txt` disallows all paths, so do not request the index or per-paper HTML pages again. The collector requires the index's sidecar receipt to contain its actual source URL, observation time, and matching file hash.
- The ECCV Virtual `papers.html` list is an independent official main-conference paper list. Its robots rules disallow `/static`, including referenced bulk abstract JSON files; do not request those files. A poster page may be fetched when its exact URL is linked by the observed `papers.html` list. Poster-page metadata can supply a Virtual-only title, ordered authors, abstract, article page, and actual Paper PDF link; it does not validate a DOI or publisher publication date by itself. For 2026, the poster pages expose Paper PDF links under the specifically scoped `media.eventhosts.cc/Conferences/ECCV2026/pdfs/` path. Keep those links as metadata and do not fetch PDF bodies.
- `eccv.ecva.net`, `ecva.net`, and `link.springer.com` are the runtime-allowed official metadata domains. Stay on HTTPS and follow normal rate limits. Stop new requests on HTTP 403/429 or an interstitial/challenge; keep the checkpoint and unresolved identities for a later authorized retry.

## Observed counts and scope reconciliation

Springer's timeline counts below are source-reported counts, not assumed canonical research-paper totals. The ECVA index and Virtual page count main-conference papers. Apply only a title that explicitly begins `Correction to:` as a correction exclusion; do not exclude a normal research title that discusses error correction.

| ECCV year | Springer series report | ECVA main-index rows | Virtual paper links | Explicit correction chapters | Reconciliation |
| --- | ---: | ---: | ---: | ---: | --- |
| 2016 | 415 papers / 8 volumes | — | — | 0 | 415 Springer chapter IDs |
| 2018 | 778 / 16 | 776 | — | 2 | 776 research papers after correction exclusions |
| 2020 | 1,361 / 30 | 1,358 | — | 3 | 1,358 research papers after correction exclusions |
| 2022 | 1,648 / 39 | 1,645 | — | 3 | 1,645 research papers after correction exclusions |
| 2024 | 2,388 / 89 | 2,387 | 2,387 | 2 | Frozen ECVA/Springer/Virtual identity closure: 2,386 Springer research chapters map to 2,387 ECVA/Virtual papers; the remaining paper is the verified Virtual/ECVA `Zero-shot Text-guided Infinite Image Synthesis with LLM guidance` item. Its ECVA DOI link ends in `_21`, but the Springer chapter route returned 404, so withhold that DOI and retain it as an unverified candidate in run evidence. |
| 2026 | 2,799 / 77 | — | 2,834 | 0 | Frozen identity reconciliation: 2,798 one-to-one Springer/Virtual pairs, 36 Virtual-only papers, and 1 Springer-only chapter produce a 2,835-ID union (net Virtual–Springer difference 35). The publisher-only item is SpaMEM (`10.1007/978-3-032-37235-2_25`) and remains in Springer staging. Search/series counts and the missing Part LIX remain documented waterline differences; they do not alter the reconciled source set. |

The 2024 frozen bridge maps 2,287 works by identical DOI and normalized title, corrects two known bad ECVA DOI hrefs using unique title and complete ordered authors, and resolves 97 DOI-linked title variants with author-roster evidence (95 multi-author and 2 single-author cases). ECVA and Virtual map 2,386 papers by exact title plus one reviewed MONTAGE/MONTRAGE spelling variant; the single Virtual-only paper remains in scope with no verified DOI. ECVA's DεpS row points to DOI `10.1007/978-3-031-73024-5_20`, while the verified Springer chapter `_20` is Customize-A-Video; the actual DεpS Springer chapter is `_19`. ECVA's Customize-A-Video row points to `10.1007/978-3-031-73411-3_20`, which resolves to SMILe. Preserve each DOI URL, PDF URL, and observation as separate evidence; never join by DOI alone or silently overwrite a conflicting record. The offline closure receipt is under `runs/expand-20261004/eccv-2024-identity-closure/`.

The DOI for a paper missing from a Springer chapter page remains empty unless an official source verifies it. A 404 does not remove a paper that is present in the official ECVA/Virtual main-conference list. Use a stable Virtual poster identity for such an expected item, preserve its candidate DOI in the run evidence, and record `source_unavailable` for the missing DOI.

## Collector and validation

`tools/collect_eccv_metadata.py` is resumable. It saves TOC enumerations, compressed publisher pages, per-paper progress, expected manifests, exclusions, and unresolved identities under `<db-home>/runs/expand-20261004/eccv/`. On an existing run, use the same `details` command to resume; it checks each year's target IDs and reuses cached pages. Run only one collector process against this run directory at a time because checkpoint and page-cache writes are shared.

```bash
python3 tools/collect_eccv_metadata.py \
  --home "$litdb_home" \
  --run-root "$litdb_home/runs/expand-20261004/eccv" \
  --years 2016,2018,2020,2022,2024,2026 --phase details --interval 0.4

python3 tools/collect_eccv_metadata.py \
  --home "$litdb_home" \
  --run-root "$litdb_home/runs/expand-20261004/eccv" \
  --years 2016,2018,2020,2022,2024,2026 --phase finalize
```

The 2026 `details` phase is resumable against the canonical union; publisher chapter pages already in cache are reused, and Virtual-only rows are not treated as Springer chapter fetch targets. If rebuilding from a fresh run, first collect the observed Springer subset and retain it under `provisional_expected/`, then import the frozen official Virtual crosswalk with:

```bash
python3 tools/integrate_eccv_virtual_union.py \
  --run-root "$litdb_home/runs/expand-20261004/eccv" \
  --gap-root "$litdb_home/runs/expand-20261004/eccv-2026-virtual-gap" \
  --workspace-root .

python3 tools/collect_eccv_metadata.py \
  --home "$litdb_home" \
  --run-root "$litdb_home/runs/expand-20261004/eccv" \
  --years 2026 --phase details --interval 0.4
```

The importer is offline: it verifies the frozen SHA receipts, the one-to-one source-ID partition, and each poster raw-page receipt, then writes the 2,835-row expected union and staging records. It keeps the original Springer-only manifest in `provisional_expected/`. DOI and publication date stay missing for Virtual-only papers unless separately verified; the poster's `datePublished` value is retained as page evidence, not converted into a paper publication date. Reparse chapter details only from saved publisher HTML. After the 2024 Virtual/ECVA identity closure is recorded, finalize all six editions and run strict staging validation against the six canonical expected files. Save the validation report in the run directory. A venue worker does not merge the production catalog or edit shared registry/campaign files; the designated single writer performs any production merge and reconciliation.

## Verified integration included in `v1.1.0` on 2026-10-04

Strict staging validation and the single-writer production merge passed for 9,416 research papers and 10 explicit correction exclusions. The research counts are 415 / 776 / 1,358 / 1,645 / 2,387 / 2,835 for 2016 / 2018 / 2020 / 2022 / 2024 / 2026. All research records have titles, authors, abstracts, article URLs, and observed PDF links; 9,379 have verified DOI and publication-date fields. The remaining 37 keep source-specific missing-field reasons.

All six complete expected manifests were published to `<db-home>/manifests/expected/eccv/`. Production reconciliation passed at `2026-10-04T12:14:41.221205Z`, with zero missing, extra, duplicate, or provenance-gap records. The refreshed local hybrid index includes all 9,416 ECCV papers; exact-title searches verified the publisher-only SpaMEM paper, a Virtual-only 2026 paper, and the 2024 Zero-shot exception with their stored abstracts and PDF links. Evidence is saved under `<db-home>/runs/expand-20261004/eccv/` in `merge_receipt.json`, `final-reconcile/venue_coverage_report.json`, `integration_summary.json`, and `search-smoke.json`.

This acceptance is included in the `v1.1.0` catalog, which contains 177,031 papers across 26 venues. The 9,416 ECCV records all have stored abstracts and observed article and PDF links; 9,379 have verified DOI and publication-date fields, with source-specific missing reasons retained for the remaining 37. No paper PDF files were downloaded.

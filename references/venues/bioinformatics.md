# Bioinformatics

## Identity and scope

- Venue ID: `bioinformatics`; canonical name: *Bioinformatics*.
- Publisher: Oxford University Press (Oxford Academic); official eISSN observed in the journal record: `1367-4811`.
- Calendar: annual volumes with numbered issues and supplements; the current volume is 42 (2026). The OUP archive year selector exposed years 1985–2026 during the 2026-10-05 observation.
- Requested collection scope: 2015 through currently public 2026 content, including issue and supplement research articles and advance articles.
- Include research entries identified by an official research article type or issue section, including `Original Paper`, `Applications Notes`, `Review`, and explicitly research-designated supplements. Do not treat generic `Journal Article` metadata as a research classification by itself. Preserve unknown types as unresolved.
- Exclude only explicitly identified editorials, corrections/errata/retractions, front matter and other nonresearch content. Keep each exclusion with its official reason; do not infer exclusions from a broad title prefix.
- Collection state: **pending**. The archive selector, 2026 volume 42 issue selector and issue 42/1 listing have been observed and saved. All target years/issues and the advance listing have not yet been enumerated. Only the PICNIC OUP article-detail page has been captured; Europe PMC and Crossref supplements currently enrich a 48-item pilot, with no records accepted into the catalog.

## Official entry points

- Issue archive: <https://academic.oup.com/bioinformatics/issue-archive>
- Advance articles: <https://academic.oup.com/bioinformatics/advance-articles>
- Observed current issue: <https://academic.oup.com/bioinformatics/issue/42/1>
- Example article: <https://academic.oup.com/bioinformatics/article/42/1/btaf647/8362260>
- OUP issue links use `/bioinformatics/issue/<volume>/<issue>`; observed volume 31 included issue 10, and volumes 32–42 exposed issue 1 links. Follow only links displayed by the archive/volume selector; do not infer unobserved issue URLs.
- OUP article links contain a terminal numeric internal article ID. Use `bioinformatics:<numeric-id>` as `source_native_id`. Preserve every issue/advance occurrence. If the same numeric ID appears in both lists, treat it as one source item with multiple occurrences; distinct numeric IDs remain distinct even if their DOI matches. Let the metadata pipeline merge canonical works by exact DOI.
- PDF links: use only an actual `citation_pdf_url` metadata value or PDF anchor observed in the article/listing DOM. Never construct a PDF URL from the DOI, slug or article path.

## Browser and capture behavior

- Use the official OUP page in a normal browser. The archive issue page showed `div.al-article-items`; each listing title was `h5.item-title a.at-articleLink`. `.al-authors-list` was only a truncated preview. The `.ww-citation-primary` region displayed citation text and a DOI link.
- On an article page, capture only the observed metadata whitelist: repeated ordered `citation_author`, `citation_title`, `citation_doi`, `citation_pmid`, `citation_journal_title`, `citation_volume`, `citation_issue`, `citation_publication_date`, and `citation_pdf_url`, plus visible `.abstract` text and the visible publication date. Retain the issue year separately from both citation and visible dates.
- PICNIC illustrates why the dates must remain distinct: issue year 2026, `citation_publication_date` `2026/01/02`, and visible Published date `01 December 2025`.
- Listing pages and article pages are separate captures. `complete: true` attests only that the individual visible page/listing was fully loaded and inspected; the collector derives whole-run completeness from archive years, issue pages, advance captures and unresolved identities.
- Capture page URLs from the actual browser location. Remove the observed `browseBy=volume` view parameter, but preserve an observed numeric pagination parameter such as `page=2` so separate pages keep separate identities. Do not invent query parameters or save unrelated/sensitive query values, page HTML, browser headers, cookies or session state. Save the allowlisted JSON through the local capture form, which hashes the normalized object under `raw/browser/` and writes an index row.
- Listing captures may include `data.pagination` with either an observed `next_page_url` and `terminal_observed: false`, or `next_page_url: null` and `terminal_observed: true` when the page visibly has no next page. A page-level `complete: true` alone does not prove a listing chain is closed. Advance captures require this evidence from the official `/bioinformatics/advance-articles` entry page through a saved chain to an observed terminal page; missing pages, absent terminal evidence, cycles, orphan captures or contradictory next/terminal evidence keep enumeration blocked. If pagination is visible on archive or issue pages, capture the same observed links and terminal state there too. The adapter intentionally does not encode an unverified OUP selector or pagination parameter.
- Whole-archive completion has a separate directory-evidence gate: capture the complete root year selector (`directory.kind: year_index`), a complete annual directory for every in-scope year (`directory.kind: year`, linked to that year URL and listing its observed volume URLs), and a complete volume directory for every such volume (`directory.kind: volume`, with every issue and supplement URL). Each captured issue set must agree with its volume directory. A page's `complete: true`, year links alone, or one issue per year cannot stand in for these directory observations. The observed 2026 selector provides the year-index link; the current volume 42 dropdown provides 12 issue/supplement links, but neither establishes annual-directory coverage for 2015–2026 by itself.
- A default strict-TLS anonymous request to the observed issue URL returned HTTP 403 with `cf-mitigated: challenge`. `robots.txt` was readable and did not disallow the article path, but that does not override the challenge. A normal browser observation of the advance page remained on “正在进行安全验证” after a 20-second wait, without an actionable control. On 2026-10-05, separate archive navigation to the official 2015 archive page also remained on that verification screen after two ordinary waits totaling about 40 seconds and exposed no issue links. Stop those routes, preserve their tabs/checkpoints and resume only after ordinary browser access is available. Do not retry through alternate URL forms or bypass verification, CAPTCHA, access denial or rate limits.

## Europe PMC supplementary metadata

- OUP archive and issue listings remain the independent expected-item enumeration source. Europe PMC does not add expected identities or replace the OUP research-scope decision.
- The supplemental adapter may fill only missing fields for an OUP-enumerated item after an exact normalized DOI or exact observed PMID match. It requires a target-journal eISSN match and an in-scope record. Never join by title or author similarity.
- Prefer OUP for title, volume, issue/year, document type, landing/native ID, dates and PDF. Prefer captured OUP detail for authors and abstract; use Europe PMC ordered authors or abstract only when those OUP detail fields were not captured. Record each supplied field with the Europe PMC request URL and timestamp.
- Keep the date value and precision as returned by the API; do not relabel a Europe PMC first-publication/indexing date as an OUP online publication date, and do not invent a day for a month-precision value. Use a Europe PMC PDF only when the API returned it with `documentStyle=pdf` and the actual URL passes the registered path allowlist. If no API PDF was returned, leave OUP detail/PDF discovery pending until the article page is inspected.
- Retain 2014 margin rows only as candidates for exact DOI/PMID matches to in-scope OUP entries; keep the final year from OUP. If a DOI/PMID has multiple Europe PMC candidates, select only a unique exact normalized title and matching OUP volume/issue, and preserve all candidate/selection decisions in the join audit. Unresolved collisions do not reduce the OUP expected set.
- A year-in-scope row without a DOI may be considered only when the supplement marks it `needs_expected_identity_join_for_scope`; it must then match an OUP-observed PMID exactly. That flag does not allow a title-only join or add an expected item.

## Crossref publisher-deposit supplement

- Crossref may supplement an OUP-enumerated item only through an exact DOI match. The journal-by-eISSN feed was observed to include a neighboring journal, *Bioinformatics Advances* (`10.1093/bioadv/...`), so retain only `10.1093/bioinformatics/...` DOI records with the target eISSN, then match to the OUP DOI. Crossref does not enumerate expected items or establish research scope.
- The captured registry endpoint is `https://api.crossref.org/journals/1367-4811/works`; page request URLs and timestamps are retained in each normalized record. The broader Crossref date query included 2014 margin records for early online publication; they may supplement only an exact matching 2015+ OUP identity, and must not create 2014 expected items.
- Preserve the Crossref request URL and observation time for every supplied field. Keep its `published-online` value at the returned year/month/day precision; the visible OUP Published date takes precedence, and neither date should overwrite the separate OUP issue/citation publication date.
- A Crossref PDF candidate is usable only when the API returned `application/pdf`, explicitly labeled it `content-version=vor`, and the actual URL is inside the exact Oxford Academic `/bioinformatics/` path scope. Keep the Crossref VOR license metadata with that candidate. An `am` link is retained for audit only and is never selected as the paper PDF. Prefer a PDF directly observed in OUP article metadata/DOM, then a qualifying Crossref VOR link, then an actual Europe PMC `documentStyle=pdf` link. If Crossref supplies only an AM link and no other observed PDF, keep the article-detail PDF check pending.

## Collection adapter and checkpoints

The venue-specific adapter is `tools/collect_bioinformatics_metadata.py`; focused tests are in `tests/litdb/test_bioinformatics_metadata_collector.py`. It does not request OUP URLs. A local-only capture form accepts the versioned JSON envelope, filters to documented fields and stores private run evidence. Run commands from the repository root (the literature-db skill root); `LITDB_HOME` is honored. Set `BIOINFORMATICS_RUN_ID` to resume a prior run.

```bash
litdb_home="${LITDB_HOME:-data/literature-db}"
litdb_run_id="${BIOINFORMATICS_RUN_ID:-bioinformatics-$(date -u +%Y%m%d)}"
litdb_root="$litdb_home/runs/$litdb_run_id"
litdb_run="$litdb_root/venues/bioinformatics"
litdb_crossref="$litdb_root/crossref/records.jsonl"

rtk python3 tools/collect_bioinformatics_metadata.py serve-capture-form \
  --run-root "$litdb_run" \
  --port 8769

rtk python3 tools/collect_bioinformatics_metadata.py collect \
  --pages-index "$litdb_run/raw/browser/index.jsonl" \
  --evidence-root "$litdb_run" \
  --output-root "$litdb_run" \
  --supplement-records "$litdb_run/supplement/normalized_records.jsonl" \
  --crossref-records "$litdb_crossref"

rtk python3 tools/litdb.py staging validate --home "$litdb_home" \
  --venue bioinformatics --run "$litdb_run" \
  --expected-root "$litdb_run/expected" --strict
```

To create fresh, resumable supplement snapshots from the official public APIs, run the fetchers from the repository/skill root. They keep page responses, normalized JSONL and checkpoints in the specified run directories; reusing the same directories resumes completed work. The 2014 margin year supports exact matches to 2015+ OUP identities but never adds expected items:

```bash
rtk python3 tools/fetch_bioinformatics_europepmc.py \
  --run-dir "$litdb_run/supplement" --year-from 2014 --year-to "$(date -u +%Y)"
rtk python3 tools/fetch_bioinformatics_crossref.py \
  --run-dir "$litdb_root/crossref" --year-from 2014 --year-to "$(date -u +%Y)"
```

Run either fetcher's `--help` for paging, delay and resumable pilot options. If normalized snapshots already exist, skip fetching and pass their JSONL files to `collect` using `--supplement-records` and `--crossref-records`; this rebuilds staging and audits offline without contacting either API. The run writes `expected_source_items.jsonl` from listing identities before detail resolution, per-year `expected/*.jsonl` manifests where years are evidenced, all source occurrences, staging, exclusions, unresolved items, Europe PMC/Crossref join audits and waterline evidence. Run strict staging validation at any point for partial diagnostics. Do not merge, publish expected manifests or advance watermarks unless the complete expected set is explained by staging/exclusions and strict validation/reconciliation pass.

## History and update

- Latest local run: `data/literature-db/runs/bioinformatics-20261005/venues/bioinformatics/` (private and unaccepted). The captured 2026 issue 42/1 listing currently supplies 48 expected source identities; the observed Europe PMC and Crossref snapshots match all 48, yielding 48 staged metadata rows with 100% coverage of the required staging fields. This is only a pilot for one issue and does not establish the requested archive or advance-article total.
- Observed archive year selector: 1985–2026. Observed current volume dropdown: 12 entries, including `Supplement_1`, `Supplement_2`, and issue 10 marked in progress. Only issue 42/1 currently has a saved article listing (48 rows); PICNIC is the one saved OUP article-detail capture. The pilot’s 48 metadata staging rows are supplemented by Europe PMC/Crossref, remain private, and have not been accepted into the catalog.
- Still required before complete validation: capture the full root year selector, each annual directory and every linked volume/issue/supplement directory for 2015–2026, a complete advance-article listing, resolve research scope for every source item, and detail coverage for included research items. An issue-in-progress stays a source state, not evidence that its future entries have been enumerated.
- Strict validation is useful for diagnosing incomplete runs and may be run before the source set is complete. Do not merge staging, publish expected manifests, advance watermarks or refresh search until the complete expected set is explained by staging/exclusions and strict validation/reconciliation pass.

## Last verified

Verified 2026-10-05 from normal OUP browser observations, the public PICNIC article DOM, a structured localhost capture roundtrip and the Europe PMC/Crossref pilot joins. The anonymous OUP route, archive-year navigation and advance page were challenged, so this playbook records the verified workflow and current access stop, not completed collection.

# Bioinformatics

## Identity and scope

- Venue ID: `bioinformatics`; canonical name: *Bioinformatics*.
- Publisher: Oxford University Press (Oxford Academic); official eISSN observed in the journal record: `1367-4811`.
- Calendar: annual volumes with numbered issues and supplements; the current volume is 42 (2026). The OUP archive year selector exposed years 1985–2026 during the 2026-10-05 observation.
- Requested collection scope: 2015 through currently public 2026 content, including issue and supplement research articles and advance articles.
- Include research entries identified by an official research article type or issue section, including `Original Paper` (and the observed spelling variant `ORIGINALS PAPERS`), `Applications Notes`, `Review`, and explicitly research-designated supplements. Do not treat generic `Journal Article` metadata as a research classification by itself. Preserve unknown types as unresolved.
- Exclude only explicitly identified editorials, corrections/errata/retractions, front matter and other nonresearch content. Keep each exclusion with its official reason; do not infer exclusions from a broad title prefix.
- The observed exact section labels `LETTER TO THE EDITOR` / `LETTERS TO THE EDITOR` are outside this research-article scope; preserve their `letter_to_editor` exclusion reason. An exact `Author Index` section or whole title is front matter. This does not exclude research titles merely containing “letters” or “author index”. Preserve Discovery Notes and heterogeneous ISCB messages as unresolved until official type/content evidence resolves them.
- Collection state: **pending**. The current private run has 129 issue listings: 2015–2019, 2020 issues 1–8, and the 2026 issue 1 pilot. Its 4,828 observed entries include nonresearch and unresolved types. Later issues/years and advance articles remain incomplete; no Bioinformatics records have been accepted into the catalog.

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
- Whole-archive completion has a separate directory-evidence gate: capture the complete root year selector (`directory.kind: year_index`) and a complete annual directory for every in-scope year (`directory.kind: year`, linked to that year URL). When the annual page directly lists issues, retain every observed issue/supplement anchor in `data.issues`; the collector checks this set against all issue identities seen elsewhere. If the annual page instead links volume directories, record those observed `volume_links` and capture each linked volume as a complete `directory.kind: volume` page with its issue/supplement anchors. Follow the page's actual shape; do not invent a volume layer. A page's `complete: true`, year links alone, or one issue per year cannot stand in for these observations. The restored 2015 annual page directly lists 24 issue links for volume 31; the current volume 42 dropdown provides 12 issue/supplement links. Those observations do not establish annual coverage for every 2015–2026 year.
- A default strict-TLS anonymous request to the observed issue URL returned HTTP 403 with `cf-mitigated: challenge`. `robots.txt` was readable and did not disallow the article path, but that does not override the challenge. Earlier normal browser observations of the advance and 2015 archive pages remained on a verification screen. On 2026-10-05, the official 2015 archive page became normally readable; its visible heading and 24 issue links were captured. The advance route must be checked through ordinary browser navigation and stopped if it again shows verification without an actionable control. Do not retry alternate URL forms or bypass verification, CAPTCHA, access denial or rate limits.

- Do not restrict annual issue anchors to numeric issue strings: the 2020 annual container includes `Supplement_1` and `Supplement_2` alongside 20 regular issue links. Preserve the complete container's actual set.
- A publisher directory can misfile an issue. The observed 2018 page linked volume 35 issue 15, while the issue page identifies August 2019 and the 2019 annual directory lists only 23 other issues. Keep those original captures unchanged. A reviewed `--archive-additions` JSON may add this issue to the expected directory set only with a checksum-bound saved official link capture and a complete selected issue capture confirming its year, volume and issue. The document uses schema `bioinformatics-reviewed-archive-additions-v1` and a `decisions` array with `issue_url`, `reason`, and `link_capture: {file, sha256}` (file relative to the evidence root; checksum over saved file bytes). This cannot override existing issue identities, invent links or replace the complete annual-directory requirement. The adapter retains both identities and their source URLs/times in `archive_additions_report.jsonl`.

## Europe PMC supplementary metadata

- OUP archive and issue listings remain the independent expected-item enumeration source. Europe PMC does not add expected identities or override an explicit OUP research-scope decision.
- The supplemental adapter may fill only missing fields for an OUP-enumerated item after an exact normalized DOI or exact observed PMID match. It requires a target-journal eISSN match and an in-scope record. Never join by title or author similarity.
- For research-scope classification only, an exact-identity matched Europe PMC `publication_types` value may be used when every OUP article type, issue section and category remains unresolved. Accept only the exact labels `research-article`, `research article`, `review-article`, `review article` or `Review`; a generic `Journal Article` alone is insufficient. Any explicit conflicting nonresearch API type (including editorial, correction/erratum, retraction, letter, comment or news) blocks this fallback. An explicit OUP include or exclude decision always wins. Preserve the API's original type string in `document_type`, retain the complete supplied `publication_types` list with Europe PMC source URL/time and an explicit Europe PMC method, and keep the observed OUP section in `source_occurrences`; do not label an API type as an OUP type. This fallback never adds expected items or changes the OUP enumeration threshold.
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

- Latest local run: `data/literature-db/runs/bioinformatics-20261005/venues/bioinformatics/` (private and unaccepted). The offline snapshot through 2020 issue 8 contains 4,828 expected source identities, 4,521 staging rows, and 91 explicit nonresearch exclusions. Staging has 4,500 abstracts and 4,519 observed PDF links. The remaining items need scope or metadata resolution, and all these figures cover only saved listings, not the full requested archive.
- Observed archive year selector: 1985–2026. Saved listing totals are 789 (2015), 816 (2016), 813 (2017), 889 (2018), 1,027 (2019), 446 (2020 issues 1–8), and 48 (2026 issue 1 pilot). The 2019 total includes the reviewed misfiled issue described above. The 2020 directory includes 20 regular issues and two supplements; its remaining pages are still pending. The observed current volume dropdown has 12 entries, including `Supplement_1`, `Supplement_2`, and issue 10 marked in progress. These observations do not establish completeness for uncaptured years or advance articles.
- Still required before complete validation: capture the full root year selector, each annual directory and every issue/supplement link it exposes (or linked volume directory only when the annual page actually uses that navigation), a complete advance-article listing, resolve research scope for every source item, and detail coverage for included research items. An issue-in-progress stays a source state, not evidence that its future entries have been enumerated.
- Strict validation is useful for diagnosing incomplete runs and may be run before the source set is complete. Do not merge staging, publish expected manifests, advance watermarks or refresh search until the complete expected set is explained by staging/exclusions and strict validation/reconciliation pass.

## Last verified

Verified 2026-10-05 from normal OUP browser observations, the public PICNIC article DOM, structured localhost captures and exact-identity Europe PMC/Crossref joins. Collection currently stops at a verification challenge on volume 36 issue 9. The saved evidence and offline joins remain resumable; this is not completed collection or a released database update.

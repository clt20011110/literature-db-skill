# TVLSI playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

Last verified: 2026-08-25. Venue ID: `tvlsi`; IEEE journal; issue calendar; `punumber=92`; initialize from 2015.

## Sources and roles

- Recent issue: `https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=92`
- All Issues/archive: `https://ieeexplore.ieee.org/xpl/issues?punumber=92&isnumber=11626298` at the verified cutoff. The `isnumber` is only a waterline hint; use the currently visible archive URL on later runs.
- Issue TOCs: official visible links shaped like `https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=<issue-id>&punumber=92`.
- Early Access: `https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=4359553` at the verified cutoff. Reconfirm that the visible page still identifies TVLSI before using it.
- Article identity: IEEE arnumber at `https://ieeexplore.ieee.org/document/<arnumber>`; DOI is the cross-source identity.

For initialization, enumerate All Issues by visible year/volume, visit every visible issue TOC, and enumerate Early Access separately. Do not use search-result totals as the completeness source. At the 2026-08-24 cutoff this produced 140 issue units, 3,707 issue identities, 91 Early Access identities, zero overlap, and 3,798 unique identities.

## Browser behavior

- IEEE pages can first appear blank, incomplete, or stale even when they are usable. Wait and re-inspect for roughly 10–20 seconds; allow a second bounded wait on slow networks. Do not classify an item as missing merely because the first shell is empty.
- Use one controller-owned tab and one in-flight detail page at a time, with about eight seconds between document starts. Rotate only that controller-owned tab after roughly 15 document navigations during a long run. Never close or replace a user-owned tab.
- If the metadata script remains absent or shows the preceding article, retry the exact same official document URL once after a real cool-down. A cache-busting query is acceptable only on the same official article path, with the stored landing URL normalized back to the canonical query-free URL.
- Stop on CAPTCHA/access denial. A transient local network error or empty shell is recoverable only through bounded waiting/retry; preserve the failed observation in the historical error log and mark it recovered after successful fresh detail validation.
- Use 50 rows per TOC page when the visible control is stable. End an issue only when the visible range/total reconciles and Next is absent or disabled.

## Scope, normalization, and identity

- Include authored research articles. Explicitly exclude advertisements, announcements/calls, copyright or author-information pages, corrections/errata, editorials, indexes, mastheads, obituaries, promotional/publication/society information, tables of contents, and visibly blank pages.
- The current Early Access listing can retain legacy records. Treat its enumeration year as a crawl bucket, then use fresh detail metadata for the publication year. In the verified run, arnumbers `5645727` and `6607243` resolved to 2010 and 2013 and were retained only as `outside_scope_year` exclusions; `7094312` resolved to 2015 and remained included.
- Decode publisher HTML entities in titles, abstracts, author names, and affiliations in the clean layer while retaining immutable raw Browser artifacts. The verified fresh repair for `9560724` resolved the author surface to `Selçuk Köse`.
- Preserve author order and native author IDs. Distinct authors can share the same displayed name: arnumber `6866902` contains two different `Jun Han` identities and must not be name-deduplicated.
- Preserve arnumber as the source identity and DOI as the canonical cross-source identifier. Early Access and numbered-issue appearances are versions/locations, not separate works when the identity agrees.

## Modes

- **Initialize:** reuse the accepted expected manifests and enumeration evidence, but fetch/revalidate formal detail metadata for every identity before merge. Validate exact identity accounting, the formal schema, provenance, current waterline, and drift. Count-only rows are never catalog metadata.
- **Update:** load the active recipe, last successful watermark, expected manifests, exclusions, and catalog. Re-enumerate the current Early Access page and latest issue(s), compare native-ID sets, refresh new/changed/stale rows, and reconcile items assigned from Early Access into an issue. Do not recrawl closed historical years without detected drift or a repair need.
- **Download:** use a visible stable IEEE PDF URL when accessible; otherwise open the official article landing page and click its PDF action in the authorized in-app Browser session. Record access-restricted/not-visible status and stop at unresolved login, paywall, or CAPTCHA barriers.

## Historical anchors

- Active recipe/history: `<db-home>/recipes/tvlsi/`
- Discovery evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tvlsi/b462f517-83b0-4747-9a95-fff937045629`
- Pilot evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tvlsi/14a405ab-74d7-4791-8c4d-d3efe6afccfa`
- Replay evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tvlsi/f555bce6-3466-491a-b477-470c5fb5f310`
- Count baseline: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tvlsi/7108fdcc-8aa8-4b3a-8726-e934b9b340db`
- Formal metadata bootstrap/clean/validation/merge/reconcile root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tvlsi/8081a419-41a2-4cbe-a7ec-7fe5b6991626`
- Controller closeout audit: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tvlsi/8081a419-41a2-4cbe-a7ec-7fe5b6991626/controller_closeout.json`. The worker receipt remains explicitly staging-only (`NOT_RUN_BY_WORKER`); use the closeout plus merge/reconcile receipts to establish catalog promotion.

At the verified cutoff, the catalog state is `ACTIVE`: all 3,798 source identities are accounted for, with 3,319 included canonical works and 479 explicit exclusions. Strict reconciliation found no missing, extra, duplicate, required-provenance, or exclusion-provenance gaps. Use the formal root's `merge_receipt.json`, `reconcile_receipt.json`, `venue_coverage_report.json`, and watermark before beginning an update.

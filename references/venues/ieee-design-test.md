# IEEE Design & Test playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

Last verified: 2026-08-25. Venue ID: `ieee-design-test`; IEEE magazine/journal; issue calendar; `punumber=6221038`; initialize from 2015.

## Sources and roles

- Recent issue: `https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=6221038`.
- All Issues/archive: `https://ieeexplore.ieee.org/xpl/issues?punumber=6221038&isnumber=11613037` at the verified cutoff. Treat the `isnumber` as a waterline hint and use the currently visible archive URL on later runs.
- Issue TOCs: official visible links shaped like `https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=<issue-id>&punumber=6221038`.
- Early Access: `https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=6461917`. Reconfirm the visible publication identity before use.
- Article identity: IEEE arnumber at `https://ieeexplore.ieee.org/document/<arnumber>`; DOI is the cross-source identity.

For initialization, enumerate the visible All Issues archive by year and issue, then enumerate Early Access separately. Do not use search-result totals as the completeness source. At the verified cutoff this produced 70 issue units, 1,446 issue identities, 16 Early Access identities, zero overlap, and 1,462 unique identities for 2015 through the current 2026 waterline.

## Browser behavior

- IEEE pages can initially be blank, incomplete, or show a stale article. Wait about 10–20 seconds and inspect again; allow one more bounded wait on a slow connection before treating the page as a failure.
- Use one controller-owned in-app Browser tab and one detail page in flight at a time. The verified crawl used eight-item checkpoints with roughly 5.5 seconds between document starts and rotated only the controller-owned tab after about 24 successful details, or immediately after a noticeably slow batch.
- Never close or replace a user-owned tab. Persist a checkpoint after every bounded batch so a tab or network failure resumes only the unfinished native IDs.
- On a transient `ERR_BLOCKED_BY_CLIENT`, network error, blank shell, or stale metadata script, preserve the observation, open a fresh controller-owned tab, and retry the exact same official document URL once. The verified run recovered arnumber `11540151` this way. Stop on unresolved CAPTCHA, login, access denial, or repeated ambiguity.
- Use the visible TOC pagination contract and reconcile displayed ranges, parsed rows, unique arnumbers, and disabled/absent Next. The verified archive used 25 rows per listing page.

## Scope and magazine-content handling

- Include authored technical/research articles with complete official detail metadata.
- IEEE Design & Test has much more magazine front matter than a conventional transactions journal. Explicitly exclude covers and cover variants, tables of contents, mastheads, blank pages, advertisements, promotions, publication information, society/CEDA/TTTC news, calls and announcements, corrections/errata, interviews or reports that are not research articles, guest-editor introductions, From the EIC, Last Byte, Best in Test, and special-issue header pages.
- An accepted official TOC listing may preclassify these deterministic non-research types when the title/type rule is already validated. Record the listing URL, native ID, visible title/type, rule, and `detail_page_revalidated=false`. Every remaining candidate still requires fresh official detail metadata. Never infer an exclusion solely from missing authors.
- Signed single-page editorials may surface author names only on detail pages. At the verified cutoff the known editorial-author configuration included Scott Davidson, Jörg/Jorg Henkel, Partha Pratim Pande, and Andre Ivanov; revalidate the title/type and do not use the name list as an unconditional exclusion.
- Preserve immutable raw Browser rows. Decode HTML entities only in the clean layer, retain author order and native author IDs, use arnumber as the source identity, and use DOI as the canonical cross-source identifier.

## Modes

- **Initialize:** reuse accepted count, pilot, replay, canonical-identity, listing, fresh-detail, and Browser-evidence artifacts, then revalidate the formal metadata schema, field completeness/missing reasons, source consistency, current waterline, and drift. Count-only or listing-only rows are never promoted to catalog metadata. Require exact included-plus-excluded identity accounting before merge.
- **Update:** load the active recipe, last successful watermark, expected manifests, catalog, and exclusions. Re-enumerate current Early Access and the latest visible issue(s), diff arnumber sets, refresh new/changed/stale candidates, and reconcile Early Access-to-issue movements. Revisit closed years only when drift or an explicit repair requires it.
- **Download:** use a visible stable IEEE PDF URL when one is present. Otherwise open the official article landing page in the authorized in-app Browser session and click the visible PDF action. Record access-restricted or not-visible status; do not bypass login, paywall, CAPTCHA, or publisher controls.

## Historical anchors

- Active recipe/history: `<db-home>/recipes/ieee-design-test/`.
- Discovery evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/ieee-design-test/6ff86652-399e-4df7-9f1b-0d29694250df`.
- Pilot evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/ieee-design-test/08494b5c-2d01-4b33-be82-f0934c0c4fc9`.
- Replay evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/ieee-design-test/c15b3129-f117-4672-a41a-72e09a246af6`.
- Count baseline: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/ieee-design-test/9b0b4e31-54ca-421a-9e9a-818f1eea7f48`.
- Formal metadata bootstrap/clean/validation root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/ieee-design-test/97e375d0-bdfe-45c8-8966-1090f14a7ad5`.
- Controller closeout audit: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/ieee-design-test/97e375d0-bdfe-45c8-8966-1090f14a7ad5/controller_closeout.json`. The worker receipt remains explicitly staging-only (`PENDING`); use the closeout plus merge/reconcile receipts to establish catalog promotion.

At the verified cutoff, the catalog state is `ACTIVE`: all 1,462 source identities are accounted for as 717 canonical works plus 745 explicit non-research exclusions. Strict reconciliation found zero missing, extra, duplicate, required-provenance, or exclusion-provenance gaps, and every year has coverage 1.0. Consult the formal root's `merge_receipt.json`, `reconcile_receipt.json`, `venue_coverage_report.json`, and watermark before beginning an update.

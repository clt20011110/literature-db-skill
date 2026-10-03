# IEEE Embedded Systems Letters playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

Last verified: 2026-08-26. Venue ID: `embedded-systems-letters`; IEEE publication number `4563995`; journal initialization target is 2015 onward. The recovered initialization is now `ACTIVE` after strict staging, merge, reconcile, and controller ingest.

## Official sources and known state

- Primary candidate: `https://ieeexplore.ieee.org/xpl/issues?punumber=4563995`.
- Current-issue cross-check: `https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=4563995`.
- Allowed formal domain: `ieeexplore.ieee.org`.
- Historical blocked evidence is retained in the formal run as `controller_network_block_evidence.json`: two exact archive attempts and one recent-issue attempt ended with `net::ERR_CONNECTION_CLOSED` before page content. Authentication and CAPTCHA state were not reached. This remains environmental source-block evidence, not an empty archive.
- Recovery evidence is retained as `controller_browser_recovery_evidence.json`. The official All Issues surface subsequently rendered and the accepted visible baseline contains 50 issue units (2015–2026), 843 issue rows, and 110 Early Access rows. The combined canonical identity set is 953 items with SHA-256 `6731cfb9bbd5ff36833fad280ad278061bdcb1a4e116f414c0f2e45b88439bed`; issue and Early Access overlap is zero.
- Issue navigation and each TOC use visible terminal ranges with rows-per-page 25. Early Access terminated in five visible pages: `1–25`, `26–50`, `51–75`, `76–100`, and `101–110`. Use `listing_browser_evidence.jsonl` as the saved controller evidence; do not infer additional units.

- Formal metadata root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/embedded-systems-letters/48a6388f-bd15-466e-a43d-68c4d7733282`.
- Final accepted accounting is 953 source identities: 775 included canonical works and 178 explicit exclusions. Final staging SHA-256 is `a7ffd8928c6eb4e6c60b07f065ea425d0b09e6ca2a0e11d4c6756d7b58398b36`; exclusions SHA-256 is `e1dd1f3bc4f22d15cf3d6e50c0896ea37203f6f5ac52022272e4964cc0e5b5f8`; source identity-set SHA-256 is `6731cfb9bbd5ff36833fad280ad278061bdcb1a4e116f414c0f2e45b88439bed`.
- The current waterline is 2026 with formal proceedings visible; the accepted source set is `NO_CHANGE / NO_DRIFT`. Three Early Access identities (`11299084`, `11300882`, `11301780`) were reclassified from the listing's 2026 baseline to 2025 from fresh official detail dates and are recorded in `staging_validation.json` and `venue_coverage_report.json`.
- Strict reconciliation found zero missing, extra, duplicate, required-provenance, or exclusion-provenance gaps. The final `thread_receipt.json` and `output_manifest.json` report controller ingest `PASS`, exit 0, and transition to `ACTIVE` (88 artifact hashes/byte counts cross-checked).

The recovered listing baseline initially permitted only a `CANDIDATE_ONLY` recipe and expected identity contract; it became catalog-ready only after fresh detail validation. The formal audit classified 178 visible listing rows (50 table-of-contents, 50 publication-information, 14 promotional, 20 information-for-authors, 21 blank/cover, 6 advertisements, 5 indexes, 1 announcement, 10 editorials, 1 correction) and validated all 775 detail candidates. The four authorless, research-looking Early Access rows (`11666970`, `11666974`, `11666953`, `11666965`) were retained for detail validation rather than excluded from the listing; missing listing authors alone is not an exclusion.

For the detail stage, use one controlled tab, wait 15–30 seconds for a slow first paint, and follow the bounded IEEE recovery rules in the Browser reliability reference. Preserve the exact publication number and canonical URL. Do not replace the source with guessed endpoints or non-allowlisted sites. Expected manifests are inclusive identity/count contracts; fresh detail title, ordered authors, abstract, date, type, DOI status and PDF discovery remain mandatory before any catalog claim.

## Initialize and update

After the archive renders, verify the journal title and publication number, enumerate every visible issue from 2015 through the current waterline, and save year/volume/issue, visible range/total, pagination termination, native `arnumber`, and listing evidence before opening detail pages. Include official research article types; preserve explicit editorials, news, corrections, retractions, front matter, and indexes as exclusions with reason codes.

Use numeric IEEE `arnumber` as the stable native identity. Revalidate ordered authors, affiliations, abstract, publication date, document type, DOI status, landing URL, and PDF action on the official detail page. For updates, reuse the accepted expected identities and watermark, recheck the visible current/latest issue, and fetch only new, changed, missing, stale, or drift-sensitive rows.

Formal run root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/embedded-systems-letters/48a6388f-bd15-466e-a43d-68c4d7733282`. Reuse the historical block and recovered controller evidence, listing/count baseline, canonical identities, expected manifests, fresh detail evidence, exclusions, and watermark only after the preparation audit revalidates source, schema, field completeness, waterline and drift. Do not promote count-only artifacts to metadata. For future updates, compare the current archive and Early Access identity set with the accepted watermark, then fetch only new, changed, missing, stale, conflicting, or drift-sensitive rows.

## Download

Prefer the saved visible IEEE PDF action for an accepted metadata record. If it is missing or stale, open the canonical `/document/<arnumber>` page in the authorized in-app Browser and use the visible PDF control. Validate the downloaded file header, parseability, page count, size, identity/title, and SHA-256; do not persist session data or transient signed parameters.

# TCAD playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

Last verified: 2026-08-24. Venue ID: `tcad`; IEEE journal; issue calendar; `punumber=43`; crawl from 2015.

## Sources and roles

- Recent issue: `https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=43`
- All issues/archive: `https://ieeexplore.ieee.org/xpl/issues?punumber=43&isnumber=11614964` at the verified cutoff. Treat the `isnumber` as a waterline hint, not a permanent constant; use the current visible All Issues page.
- Issue TOCs: visible same-origin links shaped like `https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=<issue-id>&punumber=43` or the equivalent visible URL.
- Article identity: numeric `https://ieeexplore.ieee.org/document/<arnumber>`.
- IEEE Advanced Search is diagnostic only; relevance/search totals can mix regular issues and Early Access and are not the primary completeness method.
- `https://ieeexplore.ieee.org/rss/TOC43.XML` is useful for recent-change hints, not full historical enumeration.

Primary enumeration is All Issues → visible year/volume → every visible issue → issue TOC. Include current Early Access separately when visible so later issue assignment can be versioned rather than duplicated.

## Browser behavior

- Wait 10–20 seconds for IEEE issue/archive and article pages to stabilize; allow longer after changing year/volume.
- A newly opened document can temporarily show an empty or stale IEEE metadata shell even though the page is usable. Wait a bounded interval and re-inspect visible state before classifying it as missing or blocked. If the shell remains stale, retry the same official document URL in a new controller-created tab; do not reinterpret the shell as metadata absence.
- For full detail bootstrap, use one controlled tab and one in-flight detail request at a time, with about eight seconds between document navigations. In the verified 3,886-identity run, rotating only the controller-created tab after roughly 20 document navigations avoided the cumulative in-app Browser tab crash. Never rotate or close a user-owned tab.
- Issue TOCs expose 10/25/50 items-per-page choices. Fifty can reduce clicks, but use whichever visible setting behaves reliably and checkpoint the native-ID set.
- Terminate an issue only when the visible end/total reconciles and Next is absent/disabled. Re-resolve Next by role because the DOM element type can change.
- Metadata/abstract pages were publicly visible in the verified run. PDF/full text may require sign-in; that is a download-mode concern, not a reason to discard public metadata.

## Scope, identity, and updates

- Include official research article types. Exclude editorial/news/correction/retraction/front matter/index and any PDF-only artifact without a public metadata identity, unless the user requests a broader corpus.
- Primary identity is IEEE arnumber; DOI is the cross-source identity. Preserve Early Access and assigned issue/volume as versions/locations of the same canonical work when DOI/arnumber agrees.
- Treat the year attached to an Early Access enumeration as a crawl bucket, not final publication metadata. Fresh official detail reclassified 27 identities in the verified bootstrap: 23 to 2025 and one each to 2024, 2019, 2018, and 2016. Preserve the baseline year and the detail-year reclassification provenance.
- For updates, inspect the current Early Access set, the latest issue, and anything newer than the stored watermark. Reconcile articles that moved from Early Access into a numbered issue.
- Advanced-search totals are useful as a sanity check but can overcount the current year by combining journals and Early Access.

## Historical anchors

- Active recipe/history: `<db-home>/recipes/tcad/`
- Discovery evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tcad/0f9aa9eb-d972-4a5f-adb8-3520a9cddff3`
- Pilot evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tcad/01a023a5-eb0e-7361-9a55-b25049943fa6`
- Replay evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tcad/01a02782-2a3d-7062-acfc-f9048fa767d6`
- Count baseline: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tcad/763f1e31-fa20-4bc0-a41e-35c9cfcb380f`
- The accepted 2015–2026 count baseline was 3,886 items at its cutoff. It is count/enumeration evidence, not complete catalog metadata.
- Formal metadata bootstrap, clean-build audit, waterline evidence, merge receipt, and reconcile receipt: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tcad/47d987c1-59ce-425b-bf49-8463530c3a74`
- Controller closeout audit: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/tcad/47d987c1-59ce-425b-bf49-8463530c3a74/controller_closeout.json`. This explicitly records the verified controller merge/reconcile/catalog snapshot; the separate count receipt is count-only, and the formal metadata root does not contain a standard venue-bootstrap worker receipt.
- Final verified catalog state at the 2026-08-24 cutoff: `ACTIVE`; 3,886 accounted source identities, 3,875 included canonical works, 11 explicit exclusions, 100% required-field coverage, no missing/extra/duplicate identities, and no provenance gaps.
- Reuse the accepted expected manifests, canonical identities, listing metadata, fresh-detail samples, and Browser evidence for later bootstrap repair or updates. Reuse never promotes count-only rows into catalog metadata: rerun the formal schema, official-source consistency, current-waterline, and drift checks before merge.

## Download

Use the visible public PDF URL directly only when stable and accessible. Otherwise open the IEEE article landing page and click its PDF action in the authorized Browser session. Stop on sign-in/access barriers that the user has not legitimately resolved; never copy Browser credentials or signed links into artifacts.

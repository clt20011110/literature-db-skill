# ICML (International Conference on Machine Learning)

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Current catalog status (2026-10-01)

- The accepted snapshot merge in `legacy-evidence://workspace/metadata-merge-20261001/production_receipt.json` left **13,887 included records** in the read-only catalog, with **13,688 records updated** and **199 2026 records preserved**. The catalog currently contains **13,887 `canonical_work` rows** for `icml`; campaign state is `ACTIVE`, with zero exclusions and zero pending records in the accepted snapshot.
- The accepted snapshot counters record 10,627 PDF additions, 3,061 unchanged PDFs, and 10,627 resolved repair tasks. It was accepted from existing evidence: `fresh_network_validation: false` and `fresh_watermark_advanced: false`.
- The current state above supersedes any earlier run-local “not merged” wording. Prior waterline and formal-run details below remain historical evidence of their own checks and should not be read as a statement that the current catalog is empty.

## Identity and scope

- Venue ID: `icml`; annual proceedings; crawl start 2015.
- Official sources: `proceedings.mlr.press` for closed ICML volumes and `icml.cc` for conference/current-year pages. Exclude workshops and side events; retain documented position-paper decisions when they are on the official main track.

## Official entry points and enumeration

- PMLR home: <https://proceedings.mlr.press/>; observed main volumes: v37 (2015), v48 (2016), v70 (2017), v80 (2018), v97 (2019), v119 (2020), v139 (2021), v162 (2022), v202 (2023), v235 (2024), v267 (2025).
- Current fallback: `https://icml.cc/virtual/{year}/papers.html`; require a stable visible N-of-N count and URL set in two snapshots.
- Closed-volume termination is the single static index: consume every exact-text `abs` anchor and verify the footer sentinel/unique count.
- Fresh controller observation 2026-08-30: the eleven closed PMLR volumes expose 270, 322, 434, 621, 773, 1,084, 1,183, 1,233, 1,828, 2,610, and 3,330 visible `abs` anchors respectively (13,887 total); each volume ended on its visible static index with no pagination control.
- Fresh current-waterline observation 2026-08-30: `https://icml.cc/virtual/2026/papers.html?filter=titles` visibly reports `showing 199 of 199 papers` and 199 unique poster links. Preserve the URL-set hash and sample IDs in the controller evidence before using it as a waterline.

## Browser and metadata

- Wait for client-rendered pages to stabilize; never treat an initial `Loading`/empty shell as zero papers. A top-navigation `Login` label on ICML is not itself an auth wall; stop only for an explicit login-required banner, CAPTCHA, access denial, count drift, or a changed selector contract.
- Canonical identity is the PMLR article URL slug (or stable official ICML poster ID for the current fallback), with DOI validated when reported. Listing rows seed detail work but are not catalog metadata.
- Existing replay/recipe evidence is reusable scope evidence only. The count baseline is 13,887 eligible rows for 2015–2025 plus the observed current-year set; formal staging must revalidate every required field.
- PMLR volume pages visibly link a bulk `bib` asset, but a direct in-app Browser navigation to that asset may return `ERR_BLOCKED_BY_CLIENT`; retain the exact block evidence and use the visible article HTML/detail page instead. Do not promote a shell/error page or infer bulk export coverage.

## History and update

- Registry: `<db-home>/registry/venues/icml.yml`.
- Locked recipe and prior pilot/replay evidence: `<db-home>/recipes/icml/`.
- Expected manifests are published under `<db-home>/manifests/expected/icml/`; query the active recipe, latest receipt, catalog, and watermark before updates.

## Download

- Use a validated official PDF link only when its host is allowlisted; otherwise open the official article page and use its visible PDF action. Do not promote an external raw-file URL merely because citation metadata points to it.

Historical source/repair verification: 2026-08-30 (13,887/13,887 detail rows; 2026 virtual JSON-LD repair applied with backups; strict staging, merge, reconcile, and security all PASS; catalog waterline advanced to 2026). Current catalog status is stated above.

# ASP-DAC playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Current catalog status (2026-10-01)

- The accepted snapshot merge in `legacy-evidence://workspace/metadata-merge-20261001/production_receipt.json` reports `PASS` and `committed: true` for **1,412 included records**, 530 exclusions, and zero pending records. The read-only catalog currently contains **1,412 `canonical_work` rows** for `asp-dac`; campaign state is `ACTIVE`.
- The accepted snapshot verification reports title/authors/landing coverage for all 1,412 rows, abstracts for 1,404, DOI for 300, PDF URLs for 971, article URLs for 1,206, and publication dates for 411. It was accepted from existing evidence: `fresh_network_validation: false` and `fresh_watermark_advanced: false`.
- The consolidated `CATALOG_NOT_READY`, no-merge, and source-blocker statements below are historical snapshots from 2026-08-31. They remain useful run-local evidence and are not the current catalog status; use the production receipt and campaign state above for the current state.

Historical run verification: 2026-08-31. Venue ID: `asp-dac`; annual conference; official program defines scope; IEEE supplies native identity/DOI where indexed. Current catalog status is stated above.

## Sources and roles

- Annual home pattern: `https://www.aspdac.com/aspdac<year>/`, but always reuse the explicit year map in the active recipe instead of guessing a path variant.
- 2025 official program: `https://www.aspdac.com/aspdac2025/archive/program/program.html`
- 2026 agenda: `https://www.aspdac.com/aspdac2026/archive/program/agenda_overview.html`
- 2026 detail/abstract page: `https://www.aspdac.com/aspdac2026/archive/program/program-abstract.html`
- 2024 official detail/abstract page: `https://www.aspdac.com/aspdac2024/archive/program/program_abst.html`
- 2023 official detail/abstract page: `https://www.aspdac.com/aspdac2023/archive/program/program_abst.html`
- 2022 official archived detail/abstract page (linked from the official 2022 archive): `https://www.aspdac.com/aspdac2022/taoka/program/program_abst.html`
- 2019 official archived detail/abstract page (linked from the official 2019 archive): `https://www.aspdac.com/aspdac2019/archive/program/program_abst.html`
- 2018 official archived detail/abstract page (linked from the official 2018 archive): `https://www.aspdac.com/aspdac2018/archive/program/program_abst.html`
- 2017 official archived detail/abstract page (linked from the official 2017 archive): `https://www.aspdac.com/aspdac2017/archive/program/program_abst.html`
- 2016 official archived detail/abstract page (linked from the official 2016 technical-program page): `https://www.aspdac.com/aspdac2016/technical_program/program/program_abst.html`
- 2015 official archived detail/abstract page (linked from the official 2015 archive): `https://www.aspdac.com/aspdac2015/archive/program/program_abst.html`
- 2020 official program scope PDF: `https://www.aspdac.com/aspdac2020/program/program.pdf`
- 2020 IEEE proceedings listing: `https://ieeexplore.ieee.org/xpl/conhome/9036752/proceeding`
- 2021 official technical-program referrer: `https://www.aspdac.com/aspdac2021/technical_program/`
- 2021 user-approved delegated program: `https://tsys.jp/aspdac/2021/program/program_abst.html` (approved for 2021 only)
- 2021 IEEE proceedings listing: `https://ieeexplore.ieee.org/xpl/conhome/9371508/proceeding`
- IEEE annual archive: `https://ieeexplore.ieee.org/xpl/conhome/1000194/all-proceedings`
- IEEE 2026 proceedings: `https://ieeexplore.ieee.org/xpl/conhome/11420221/proceeding`

Use official session labels to decide main research scope. Exclude tutorial, keynote, luncheon, panel, special session, Designer Forum, University Design Contest, and other non-regular program material. Use identity `year + official session code + normalized official title`; enrich with IEEE arnumber/DOI only after exact official crosswalk.

## Browser behavior and known results

- Wait for official program pages and IEEE Xplore to finish rendering. A newly created tab can remain `about:blank` until explicitly navigated; do not classify that as a source failure.
- The 2025 program is a large static page. A document-order/sibling parser worked; DOM `Range.setStartAfter` was unavailable in one Browser execution environment. Do not depend on that Range API.
- The 2025 verified two-pass result was 57 sessions, 39 regular sessions, 244 detail links, and 168 eligible regular identities with zero duplicate identities. Four extra detail links were tutorials `T1-1` through `T4-1`. IEEE did not index 2025 in the observed archive; keep official-only identities and do not invent IEEE IDs/DOIs.
- IEEE 2026 showed 216 displayed items across nine pages and 214 unique document IDs after two front-matter items. Sequential visible Next traversal ended at `Showing 201-216 of 216` with no Next. The control was a button in the successful parent Browser context; a worker that assumed an anchor found zero elements.
- The official 2026 agenda exposed 234 paper-label links. The detail page had one extra paper-shaped `9D-1` block under a panel; exclude it by parent session scope.
- The 2015–2019 and 2022–2024 archive pages above are static, fully visible HTML program/detail surfaces on the allowlisted `aspdac.com` host. Year-local strict checks yielded 2015: 160 expected / 106 regular / 54 excluded, 2016: 138 / 94 / 44, 2017: 165 / 111 / 54, 2018: 137 / 85 / 52, 2019: 138 / 93 / 45, 2022: 147 / 95 / 52, 2023: 158 / 102 / 56, and 2024: 206 / 147 / 59. They expose title, ordered authors, and (usually) abstract; DOI and publication date are generally not present, and visible slide links are not publisher PDFs. Treat these as year-scoped evidence and still perform an IEEE crosswalk before catalog merge.
- The 2020 official ASP-DAC program PDF states 86 accepted regular papers in 25 regular sessions. A fresh visible IEEE Browser enumeration found 119 items (114 document identities plus 5 front-matter rows); title/session crosswalk produced 86 included regular papers and 28 explicit exclusions. The run-local full detail pass is `legacy-evidence://workspace/bootstrap-asp-dac-20260830/program-2020-ieee-full`, with strict staging `PASS` (86/86, all required fields present), waterline `PASS/NO_DRIFT`, and security scan `PASS`. This is year-scoped evidence; it was not merged by that run and is not the current catalog status.
- A run-local source-scope repair concluded that the saved 2020 official program-PDF statement plus exact IEEE crosswalk (86 regular papers in 25 sessions) and the saved 2022 first-party `aspdac.com/taoka` program (147 visible items, 95 regular, 52 excluded) are sufficient scope evidence without visiting `tsys.jp`: `legacy-evidence://workspace/bootstrap-asp-dac-20260830/source_scope_repair_20260831.json`.

## 2021 delegated source and detail status

The official ASP-DAC 2021 technical-program page visibly delegates its full HTML program to `tsys.jp`. The user explicitly approved that host for 2021 only; the registry records the scoped approval and the run preserved the official ASP-DAC referrer. The completed year-local run is `legacy-evidence://workspace/bootstrap-asp-dac-20260830/program-2021-tsys-ieee`: 111 regular detail rows + 69 explicit exclusions, strict staging `PASS`, waterline `PASS/NO_DRIFT`, and security `PASS`. The independent DOI/PDF audit found all 111 rows have checked-missing DOI and no stable PDF URL/action, so this was metadata staging evidence from that run and its `catalog_ready=false` gate is historical; the accepted snapshot above is the current catalog status.

The historical consolidated run-local artifact is `legacy-evidence://workspace/bootstrap-asp-dac-20260830/consolidated`: 1,412 included rows + 530 exclusions across 2015–2026, expected union 1,942, strict staging/waterline/security `PASS`. It was not catalog-promoted in that run because the acceptance DOI/PDF-location gate was not met (111 delegated 2021 rows were `metadata_only`, and older official-only rows also retained structured missing-PDF statuses). No 2020/2022 `tsys.jp` surface was used. The accepted snapshot above is the current catalog state.

## Historical anchors

- Candidate recipe/history: `<db-home>/recipes/asp-dac/`
- Current repair/evidence root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/asp-dac/73cd4879-28a8-4567-9e35-eb9cb993187a`
- 2020 IEEE crosswalk/detail root: `legacy-evidence://workspace/bootstrap-asp-dac-20260830/program-2020-ieee-full`
- 2021 delegated detail root: `legacy-evidence://workspace/bootstrap-asp-dac-20260830/program-2021-tsys-ieee`
- Consolidated run-local expected/staging root: `legacy-evidence://workspace/bootstrap-asp-dac-20260830/consolidated`
- Earlier partial discovery: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/asp-dac/00c7fcd1-75c5-4a5f-a888-8cb2f36a564c`

The historical evidence resolved the 2015–2026 expected union and the 2021 source decision, but that consolidated venue was `CATALOG_NOT_READY` until DOI/PDF-location requirements and the remaining formal controller gates were satisfied. Keep the older source-blocked audit as historical boundary evidence; do not treat it as the current 2021 or catalog status.

## Update and download

For updates, revisit the newest official program/current waterline and the newest indexed IEEE proceedings only. Reuse the verified 2025 official identity set unless the page fingerprint or content changes.

Official program pages may expose detail links but not publisher PDFs. When a stable public PDF link exists, validate and download it. Otherwise use the exact official/IEEE landing page and visible PDF action; stop on login/paywall/CAPTCHA and never synthesize an IEEE link for 2025.

# ICCAD playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

Last verified: 2026-08-25. Venue ID: `iccad`; annual IEEE/ACM International Conference on Computer-Aided Design; target main research proceedings from 2015 onward.

## Sources and scope

- Official conference history: `https://iccad.com/2026/history`.
- Current conference: `https://iccad.com/2026`. The verified 2026 event is November 8–12, 2026 and had no formal proceedings link at the observation time. Treat this as a negative current waterline, not a closed-proceedings zero.
- Official IEEE proceedings collections:
  - 2015: `https://ieeexplore.ieee.org/xpl/conhome/7367889/proceeding`
  - 2016: `https://ieeexplore.ieee.org/xpl/conhome/7787013/proceeding`
  - 2017: `https://ieeexplore.ieee.org/xpl/conhome/8167715/proceeding`
  - 2018: `https://ieeexplore.ieee.org/xpl/conhome/8572681/proceeding`
  - 2019: `https://ieeexplore.ieee.org/xpl/conhome/8931666/proceeding`
  - 2020: `https://ieeexplore.ieee.org/xpl/conhome/9256265/proceeding`
  - 2021: `https://ieeexplore.ieee.org/xpl/conhome/9643423/proceeding`
  - 2022: `https://ieeexplore.ieee.org/xpl/conhome/10068856/proceeding`
  - 2023: `https://ieeexplore.ieee.org/xpl/conhome/10323590/proceeding`
  - 2024: `https://ieeexplore.ieee.org/xpl/conhome/11126043/proceeding`
  - 2025: `https://ieeexplore.ieee.org/xpl/conhome/11240608/proceeding`
- Allowed formal domains in the verified run: `iccad.com` and `ieeexplore.ieee.org`.

Include research papers in the formal ICCAD proceedings. Preserve and exclude front matter/non-research records with reason codes. Verified labels include cover/copyright pages, TOC/table of contents, preface, proceedings title pages, sponsors and organizers, committees, indexes, panels/keynotes, and other explicit non-research content. A wide page range plus no authors is a review signal, not by itself an automatic exclusion.

## Browser behavior

- IEEE proceedings pages normally show 25 records and a visible range/total. Terminate on the visible final range and disabled/absent Next, not numbered-page buttons alone.
- Detail pages often load in a few seconds but may intermittently return `ERR_BLOCKED_BY_CLIENT`. Use one tab, about eight records per batch, four seconds between starts, and a 15–20 second inter-batch cooldown as a conservative starting profile.
- On a transient block, retain the exact `/document/<arnumber>` URL and error row, wait about 20 seconds, navigate that same URL, wait another 15 seconds for visible IEEE/article markers, then resume the saved checkpoint. Cache-busting is a bounded second attempt only; canonical provenance stays query-free.
- If a slow batch exceeds the browser-control time budget, valid JSONL rows may already be on disk even when `detail_summary.json` is stale. Compare staging plus exclusions against the expected identity count before resuming. Reacquire the same tab ID after a control reset rather than opening another tab.
- The proceedings Export dialog visibly offered up to 100 selected rows and “Citation and Abstract” on 2022, but produced no actual download for both a 100-row test and a one-row test. Record that method as blocked until a real file can be verified; it is not the primary method.

## Identity, fields, and completeness

- Stable native identity is the numeric IEEE `arnumber` from `/document/<arnumber>`; DOI is secondary and may be explicitly `checked_missing` after detail inspection.
- Listing fields provide year, title, visible authors, pages, native ID, PDF action, collection/isnumber, and pagination evidence. Detail pages are authoritative for ordered authors/affiliations, abstract, DOI status, publication/conference dates, document type, canonical landing URL, and PDF URL.
- Pre-scan included rows with no authors before a long detail pass. In the verified run this revealed prefixed labels such as `ICCAD 2025 Cover Page`, `ICCAD 2025 Copyright`, `ICCAD 2025 TOC`, `ICCAD 2025 Preface`, and `ICCAD 2025 Author Index`, plus `Sponsors and Organizers` and an aggregate proceedings title. Reclassify saved listing rows with an audit and rebuild expected manifests; do not refetch all listing pages.
- Final verified enumeration contained 1980 identities for 2015–2025. The completed Browser detail run accounted for 1915 research metadata rows and 65 explicit exclusions, with no unexplained identity gap. Confirm the current receipt/state and hashes before reuse.

## History and update

- Formal run root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/iccad/f5f5983b-455f-4f86-a249-298d8fb5731e`.
- Reuse `count_observed_manifest.jsonl`, `expected/`, `metadata_staging.jsonl`, `metadata_exclusions.jsonl`, Browser evidence, classification-repair audits, detail checkpoints, and resolved-error evidence. Count-only listing data is not complete catalog metadata.
- For updates, recheck the ICCAD history/current pages for the newest official proceedings link, then compare only the current/latest closed collection identity set against the accepted watermark. Reopen new, changed, missing, stale, conflicting, or drift-sensitive records rather than replaying 2015–2025.

## Download

Use the stable visible IEEE PDF URL already stored in metadata when it remains valid. Otherwise open the canonical detail page in the authorized in-app Browser and use its visible PDF action. Validate `%PDF-`, parseability, page count, size, and SHA-256; never persist session tokens or a transient signed URL.

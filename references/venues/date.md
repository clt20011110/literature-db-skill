# DATE playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

Last verified: 2026-08-26. Venue ID: `date`; annual Design, Automation and Test in Europe Conference; target main research proceedings from 2015 onward.

## Sources and scope

- Current official site: `https://www.date-conference.com/`.
- Official archive: `https://www.date-conference.com/archive`. This page is the authoritative navigation source for past event sites, public proceedings, and the official IEEE/ACM library links.
- Official IEEE all-proceedings page, reached from the archive: `https://ieeexplore.ieee.org/xpl/conhome/1000198/all-proceedings`.
- Official IEEE proceedings collections:
  - 2015: `https://ieeexplore.ieee.org/xpl/conhome/7076741/proceeding`
  - 2016: `https://ieeexplore.ieee.org/xpl/conhome/7454909/proceeding`
  - 2017: `https://ieeexplore.ieee.org/xpl/conhome/7919927/proceeding`
  - 2018: `https://ieeexplore.ieee.org/xpl/conhome/8337149/proceeding`
  - 2019: `https://ieeexplore.ieee.org/xpl/conhome/8704855/proceeding`
  - 2020: `https://ieeexplore.ieee.org/xpl/conhome/9112295/proceeding`
  - 2021: `https://ieeexplore.ieee.org/xpl/conhome/9473901/proceeding`
  - 2022: `https://ieeexplore.ieee.org/xpl/conhome/9774496/proceeding`
  - 2023: `https://ieeexplore.ieee.org/xpl/conhome/10136870/proceeding`
  - 2024: `https://ieeexplore.ieee.org/xpl/conhome/10546498/proceeding`
  - 2025: `https://ieeexplore.ieee.org/xpl/conhome/10992638/proceeding`
  - 2026: `https://ieeexplore.ieee.org/xpl/conhome/11539023/proceeding`
- Public official proceedings for 2015–2024 are linked from the archive as `https://www.date-conference.com/proceedings-archive/<year>` and redirect to the official `past.date-conference.com` host. They visibly expose session, paper title, authors, and stable direct PDF links. Use them as a PDF/source cross-check and download fallback; they do not replace full IEEE detail metadata without schema validation.
- Allowed formal domains in the verified run: `date-conference.com` and `ieeexplore.ieee.org`.

Include main research papers in the formal DATE proceedings. Preserve and exclude front matter and explicit non-main content with reason codes. Verified DATE labels include cover/title/copyright pages, proceedings title pages, sponsor societies/committees, executive and technical programme committees, awards, keynotes, special days, focus sessions, PhD Forum, media partners, calls for papers, embedded tutorials, panels, and other explicitly labeled non-main records. Do not exclude a normal paper only because a listing temporarily omits authors; verify its detail page.

## Browser behavior

- The official archive may require the current `www.date-conference.com/archive` page. The same `/archive` route on `date26.date-conference.com` returned a Drupal `MenuBasedBreadcrumbBuilder` server error in the verified run; retain that bounded failure but continue through the working current archive.
- The responsive menu can render as an empty blue overlay in a narrow side panel even though the official `Archive` item exists. Prefer the current archive link already saved in history/evidence. If rediscovery is necessary, widen/zoom the visible Browser UI or inspect the actual official menu target once; do not guess year URLs.
- IEEE listing pages visibly offer 10, 25, 50, 75, and 100 items per page. Selecting 100 produced a stable URL with `rowsPerPage=100`, exactly 100 cards on full pages, and explicit visible ranges. Use 100 for enumeration only after that control is visibly available; verify every page range, total, `isnumber`, native-ID set, and final page.
- A 63-record `Citation and Abstract` / `Plain Text` export was given a 90-second bounded wait but produced no download event, new tab, or file, and the dialog remained open. Treat bulk export as blocked until a real file is observed and validated.
- Detail navigation was stable to completion at one tab, six records per batch, about four seconds between starts, and a 20-second inter-batch cooldown. Eight-record batches usually worked but one long response exceeded the control budget; attempts at 16–20 records with 2–2.5-second starts eventually caused resets even though already written rows survived. Start at six for a resumed long run and increase only from sustained evidence, not one pilot batch.
- After any control reset, compare `metadata_staging.jsonl` plus `metadata_exclusions.jsonl` with the frozen expected identity set, reacquire the same tab ID, and resume. The JSONL files can be ahead of a stale `detail_checkpoint.json` because records are appended atomically before a batch summary is written.
- IEEE record `7092501` is a real main-track paper whose IEEE detail surface omits authors. The official DATE 2015 paper page `https://past.date-conference.com/proceedings-archive/2015/html/0134.html`, reached from the visible archive and Technical Program, supplies the matching title, five ordered authors and affiliations, DOI `10.7873/DATE.2015.0134`, abstract, and PDF action. Apply this only through the validated `official_metadata_supplements.json` evidence artifact; do not weaken the general author-required gate.

## Identity, fields, and completeness

- Stable native identity is the numeric IEEE `arnumber` from `/document/<arnumber>`; DOI is secondary and can be formally `checked_missing` only after the official detail page is inspected.
- Listing fields provide title, visible author order, year, pages, PDF action, collection/isnumber, and pagination evidence. Detail pages are authoritative for ordered authors/affiliations, full abstract, DOI status, publication date, document type, canonical landing URL, and PDF URL.
- The accepted listing enumeration contains 4226 unique identities for 2015–2026 with identity-set SHA-256 `7910d6eb700cdddd2b5711ee89643efe9915e8b4d94909685f76c2f205ced529`.
- DATE-specific classification revision `conference-front-matter-v13-date` retains the same identity set. The completed formal detail pass accepted 4119 research records and 107 explicit exclusions, accounting for all 4226 identities. Strict staging validation passed with no errors or warnings: title, ordered authors, abstract, document type, publication date, pages, landing URL, PDF URL/status, and provenance were complete for all 4119 included records; 3715 records had valid DOI values and 404 had structured `checked_missing` DOI evidence.
- Final postprocess hashes: `metadata_staging.jsonl` SHA-256 `2be112a3217d1b01055a3fcaf83a8d29e571af319dcb35956dd81b3d5c202496`; `metadata_exclusions.jsonl` SHA-256 `8e8e73f40d6481d154ceb8357d0f4e2fd147db5876fd07125d0f813446d7075d`. Historical detail errors were 2/2 resolved and no identity remained unresolved.
- Per-year listing totals: 2015 323; 2016 309; 2017 338; 2018 312; 2019 332; 2020 325; 2021 359; 2022 283; 2023 341; 2024 397; 2025 444; 2026 463.
- Per-year included/excluded closure: 2015 320/3; 2016 305/4; 2017 336/2; 2018 310/2; 2019 331/1; 2020 323/2; 2021 356/3; 2022 282/1; 2023 323/18; 2024 379/18; 2025 425/19; 2026 429/34.

## Waterline, history, and update

- At the verified waterline the current site was DATE 2027, scheduled for March 22–24, 2027. Its archive stated that proceedings would become available to registered delegates 14 days before the event. Treat 2027 as an open negative waterline, not a closed-year zero.
- Latest closed proceedings year was 2026, with 463 visible IEEE identities.
- Formal run root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/date/77e2748f-aea3-4962-ae6d-4da724efc430`.
- Reuse `controller_browser_discovery_evidence.json`, `controller_current_waterline.json`, `waterline_evidence.json`, `ieee_collection_units.jsonl`, `count_observed_manifest.jsonl`, `expected/`, listing evidence and classification audits, `metadata_staging.jsonl`, `metadata_exclusions.jsonl`, `staging_validation.json`, `detail_error_resolution.json`, public-proceedings sample evidence, and the bulk-export probe. Revalidate hashes, metadata schema, field completeness, source consistency, current waterline, and drift gates before merge. Never promote count-only rows.
- Reuse and revalidate `official_metadata_supplements.json` for the bounded `7092501` source repair. The supplement must match native ID and normalized title, come from the allowlisted official DATE host, retain its observation time and field-level provenance, and remain unnecessary for records whose IEEE detail metadata is complete.
- For updates, open the current official archive, verify whether the open edition now has an official proceedings collection, then compare only the new/latest closed collection identity set against the accepted watermark. Reopen new, changed, missing, stale, conflicting, or drift-sensitive records rather than replaying 2015–2026.

## Download

Prefer a stable visible IEEE PDF action already stored in metadata. For public editions 2015–2024, the official DATE proceedings page also exposes direct `past.date-conference.com/proceedings-archive/<year>/DATA/...pdf` links. If neither saved URL remains valid, open the canonical IEEE detail page in the authorized in-app Browser and use its visible PDF action. Validate `%PDF-`, parseability, page count, size, title/identity, and SHA-256; never persist cookies, session tokens, or transient signed parameters.

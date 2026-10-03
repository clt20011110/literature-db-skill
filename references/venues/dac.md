# DAC playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

Last verified: 2026-08-25. Venue ID: `dac`; annual conference; target main research track from 2015 onward.

## Sources and roles

- IEEE archive: `https://ieeexplore.ieee.org/xpl/conhome/1000196/all-proceedings`. Strong historical enumeration/native-ID source for visible DAC proceedings.
- ACM conference hub: `https://dl.acm.org/conference/dac` and `https://dl.acm.org/conference/dac/proceedings`. Official fallback/cross-check and useful for ACM DOI/proceedings identities.
- Current official discovery surface: `https://dac.com/sitemap-dac26.xml`. It exposed 399 visible URLs in the verified 2026 observation and is useful for discovering current program/accepted-paper pages, not as a complete 2015–current article list.
- `https://63dac.conference-program.com/` was observed for the current conference program. The present registry file lists only `dac.com`, `dl.acm.org`, and `ieeexplore.ieee.org`; do not use the conference-program host unless the active registry/recipe or user explicitly approves it.

Use official DAC scope/session labels to exclude front matter, keynotes, tutorials, workshops, posters, demos, doctoral consortium, and other non-main items. IEEE/ACM proceedings identities alone do not always encode the program-track decision.

## Browser behavior

- IEEE Xplore and ACM DL can be slow. Wait at least 15 seconds; allow 30 seconds before calling an ACM proceedings page empty.
- IEEE proceedings commonly show 25 rows per page. Use the visible `Next page of search results` control and visible range/total. Numbered buttons may stop at 10 while Next continues.
- The Next element can be a button even when an old parser expects an anchor. Re-resolve by role/name after each page.
- Do not interpret visible item ranges such as 403–408 as HTTP 403.
- If the user opens a slow ACM/IEEE page successfully, claim that tab in the same task instead of reopening it in a delegated Browser context.
- Bounded IEEE client/network failures can recover after a 15-second cooldown. If a tab becomes a `data:` network-error page, preserve the error/checkpoint and resume the exact native ID in one fresh controller-owned DAC tab.

## Identity and metadata

- Prefer numeric IEEE `/document/<arnumber>` or ACM DOI/proceedings identity; retain DOI as a cross-source identity when visible.
- Collect title, ordered authors, abstract, DOI, conference year/date, document type, landing URL, PDF-discovery status, official session/track decision, and provenance.
- Deduplicate exact native IDs first, DOI second, then exact normalized official title/year only with a compatible track decision.
- IEEE document `8060380` visibly reports the malformed placeholder `10.475/123 4`. Treat it as checked-missing DOI, preserve the reported string and Browser evidence, and retain the valid IEEE native ID/PDF action. Do not promote the placeholder to a DOI identity.
- Normalize detailed listing rules to the stable catalog taxonomy while preserving their source labels: invited/late-breaking/lightning-only variants map to `non_main_track`; keynote/panel abstracts map to `non_research_content`; front matter remains `front_matter`.

## Historical anchors

- Locked/current recipe: `<db-home>/recipes/dac/recipe_candidate.yml` or `recipe.yml` when present.
- Replay evidence: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/dac/5d0fefcc-4fe1-475c-a4be-c840e869ff2f`
- Count baseline: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/dac/7f27d48c-a9da-4b03-b689-0b10586d986c`
- Accepted count evidence covered 2015–2025 with 2,617 eligible items; 2026 had no accepted closed-proceedings count at that cutoff. This is enumeration evidence, not full catalog metadata.
- Formal Browser metadata and strict-validation root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/dac/4fc77c6c-2e93-4491-a265-f05ede940823`. It accounts for 2,917 expected identities as 2,617 included metadata records plus 300 explicit exclusions, with zero missing/extra identities and 100% yearly accounting for 2015–2025. Confirm its latest receipt/state before treating it as active catalog history.

For updates, begin with the IEEE/ACM formal-proceedings waterline and the last closed year. The observed `63dac.conference-program.com` page is useful discovery context but is not in the current venue allowlist, so do not place it in formal source URLs without a registry change. Reuse the saved 2015–2025 expected identities and complete metadata; reopen only new, changed, missing, stale, conflicting, or drift-sensitive items.

## Download

Use an existing stable IEEE/ACM PDF URL directly when public and valid. Otherwise open the article landing page and click the visible PDF action. ACM/IEEE full text may require an authorized session; stop on an unresolved paywall or CAPTCHA and never persist session URLs or credentials.

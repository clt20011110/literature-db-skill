# NeurIPS (Conference on Neural Information Processing Systems)

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Current catalog status (2026-10-01)

- The accepted snapshot merge in `legacy-evidence://workspace/metadata-merge-20261001/production_receipt.json` reports `PASS` and `committed: true` for **23,529 included records**, 1,481 exclusions, and zero pending records. The read-only catalog currently contains **23,529 `canonical_work` rows** for `neurips`; campaign state is `ACTIVE`.
- The accepted snapshot verification reports title/authors/abstract/landing/publication-date coverage for all 23,529 rows, PDF URLs for 23,528, and DOI for 15,209. It was accepted from existing evidence: `fresh_network_validation: false` and `fresh_watermark_advanced: false`.
- The discovery-candidate, pending-identity, and no-merge statements below are historical snapshots from 2026-08-31. They remain evidence of that run and are not the current catalog status; use the production receipt and campaign state above for the current state.

## Identity and scope

- Venue ID: `neurips`; annual proceedings; crawl start 2015. Main-track official pages observed on `proceedings.neurips.cc` (with `papers.nips.cc` retained as a fallback); a separate Datasets & Benchmarks host is treated as an extension surface.
- Include main conference research papers; exclude workshops, tutorials, invited/front matter, indexes, and other explicitly non-main tracks.

## Official entry points and browser contract

- Start from the visible official proceedings home at `proceedings.neurips.cc`; follow its visible 2015–2025 year links. Use `papers.nips.cc` only for the official paper/proceedings detail surface reached by a visible link.
- Do not infer year URLs or paper IDs. Record every visible year/edition link, paper-count/abstract-link termination, current waterline (2025 at the last observation), and source identity set before detail crawling. The 2022–2025 pages visibly label Datasets & Benchmarks and 2025 Position Paper rows; keep them as formal non-main exclusions unless an extension profile is enabled.
- Wait 8–15 seconds after normal navigation and longer for client-rendered lists. Stop on CAPTCHA, login/access denial, persistent blank shell, changed count/selector contract, or unapproved redirect.

## Metadata, history, and update

- Use the stable official paper ID/URL as primary identity; validate DOI when present and record checked-missing status otherwise. Listing metadata is reusable enumeration evidence, not complete metadata.
- Historical discovery candidate (2026-08-30; catalog empty at that time): `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/ai-discovery-20260830/venues/neurips/bd88f173-fad2-4af2-8197-12f84ff10a87` (25,010 visible main/side-track identities, 2015–2025; 23,529 main-track candidates and 1,481 formal exclusions). State was `RECIPE_CANDIDATE`; the accepted snapshot above supersedes its catalog status.
- For updates, query the locked recipe, expected manifests, latest receipt, catalog, and watermark; re-enumerate only the current/changed edition plus drift-sensitive samples.

## Download

- Use a visible official PDF link from the validated paper page. If absent, use the visible download action on the official landing page; never bypass a restriction or copy session URLs.

## Historical detail status (2026-08-31; superseded for current catalog state)

- The formal detail run is `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/ai-discovery-20260830/venues/neurips/bd88f173-fad2-4af2-8197-12f84ff10a87/detail-prep-20260830`. Strict validation at that time accounted for 23,528 included rows plus 1,481 exclusions out of 25,010 expected; exactly one identity remained pending: `neurips:2016:5d616dd38211ebb5d6ec52986674b6e4:Conference` (`Only H is left: Near-tight Episodic PAC RL`). The official abstract/detail, Metadata/BibTeX, 60-second Browser wait, and official site-search result exposed no ordered authors, and the linked official PDF reported a different title. The bounded rechecks are `neurips_2016_repair_recheck_20260831_fast_blocker.json`, `neurips_2016_controller_recheck_60s_20260831.json`, and `neurips_2016_search_recheck_20260831.json` in that run.
- The pending statement above is historical; the accepted snapshot records zero pending rows for the current catalog. Do not infer authors from the mismatched PDF or use an alternate source when repairing the historical evidence.

Last verified for that historical run: 2026-08-31 (official Browser detail recheck; no catalog write in that run). Current catalog status is stated above.

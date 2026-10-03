# ISCAS (IEEE International Symposium on Circuits and Systems)

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Current catalog status (2026-10-01)

- The accepted snapshot merge in `legacy-evidence://workspace/metadata-merge-20261001/production_receipt.json` reports `PASS` and `committed: true` for **9,747 included records**, 373 exclusions, and one pending record. The read-only catalog currently contains **9,747 `canonical_work` rows** for `iscas`; campaign state is `ACTIVE` with `pending_records: 1`.
- The accepted snapshot verification reports title/authors/abstract/DOI/PDF/landing/publication-date coverage for all 9,747 included rows. It was accepted from existing evidence: `fresh_network_validation: false` and `fresh_watermark_advanced: false`. Keep the one pending identity unresolved unless new authorized official evidence supplies it.
- The “no catalog write” and bootstrap-blocker statements below are historical snapshots from 2026-08-31. They remain evidence of the pending identity and prior run boundary, but are not the current catalog status; use the production receipt and campaign state above for the current state.

## Identity and scope

- Venue ID: `iscas`; annual conference; crawl start 2015.
- Canonical source: IEEE Xplore (`ieeexplore.ieee.org`).
- Include authored research papers in the official ISCAS proceedings. Exclude tables of contents, front matter, indexes, copyright forms, committees, keynotes, tutorials, special sessions, contests/FoodCAS, demonstrations, and other non-main-track material. Keep every exclusion with a reason code.
- Discovery found official annual collections for 2015–2026. At the discovery cutoff, anchor counts were 786 (2015), 933 (2020), and 1,018 (2026).

## Official entry points

- Annual collection links are recorded in the discovery artifact at `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/iscas-discovery-20260828/venues/iscas/67271f37-9086-4dce-84ff-3f6558e63c17/collection_units.jsonl`.
- Detail pattern: `https://ieeexplore.ieee.org/document/<native-id>`; preserve the numeric native ID and query-free canonical URL.
- PDF: use the visible IEEE PDF action after identity and access checks.

## Browser behavior

- Wait 15–30 seconds for IEEE Xplore detail/listing pages; use one tab and about eight detail records per batch with a 15–20 second cooldown between batches.
- The full pilot listing re-used the saved visible enumeration (10,121 rows), then was reclassified without refetching: 9,776 included and 345 excluded. Listing identity SHA remained unchanged; repaired manifest SHA is `72456db62c8666b816b7eea9271fdc2624bb5a8fdcf84be19397e17144b45b96`.
- A fresh controller-controlled listing replay with the visible IEEE `Items per Page=100` contract completed all 12 annual collections (2015–2026), 10,121/10,121 rows, at `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/iscas-listing-excerpt-20260828/venues/iscas`. Its identity-set SHA is `670985a462f3a52d36aa147287a7c7a1f9b684c528a3245e3491f3ae7b4627a7`, matching the accepted expected manifests. The run recorded one transient blank-shell retry at 2020 page 7 and completed with `catalog_ready=false`; listing excerpts are partial evidence only and must not be stored as full abstracts.
- During detail bootstrap, IEEE occasionally returned `ERR_BLOCKED_BY_CLIENT`/`ERR_CONNECTION_CLOSED`. Preserve the exact error and checkpoint, wait at least 20 seconds, then retry the same official URL or a fresh official tab. Detail starts use a minimum 6,000 ms interval; do not accelerate to the listing-page cadence.
- A crash/network `data:text/html` shell is never evidence. Preserve the exact checkpoint/error and stop if Browser URL policy rejects recovery; ask the user to reopen the same official URL rather than using an alternate source or URL grid.

## Metadata and identity

- Native IEEE document ID is the primary identity; DOI is validated when present and checked-missing when absent. Detail metadata must include title, ordered authors, year, document type, landing/source URLs, observed time, PDF status, and field provenance.
- Pilot root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/iscas-pilot-20260828/venues/iscas/f9a8ad68-5b22-44d4-9679-56f0c240dfba` (30 fresh detail samples; PASS).
- Replay root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/iscas-replay-20260828/venues/iscas/78316cd4-8b80-47dc-aca1-852187febd56`; 30/30 fresh detail samples completed PASS, set agreement 1.0 and field agreement 1.0. A transient Browser crash was checkpointed and recovered on a fresh controller tab; replay remains evidence-only and is not catalog metadata.

## History and update

- Registry: `<db-home>/registry/venues/iscas.yml`; candidate recipe: `<db-home>/recipes/iscas/recipe_candidate.yml`.
- Reuse `count_observed_manifest.jsonl`, `listing_summary.json`, `listing_classification_repair.json`, the completed listing-excerpt evidence above, `pilot_manifest.jsonl`, and fresh samples before any new enumeration. Revalidate identity set, source consistency, fields, current waterline, and drift thresholds; count-only or listing-excerpt artifacts never become catalog metadata by themselves.
- Do not advance from `REPLAY_RUNNING` until the replay checkpoint completes and its report meets set ≥99.9% and field ≥99.5%; this replay passed those gates and the candidate was locked on 2026-08-28. Next publish the current count expected manifests, then run formal metadata staging/merge/reconcile.

## Download

- Prefer a validated visible IEEE PDF link; otherwise click the visible PDF action on the canonical detail page and validate the downloaded bytes before recording a work location.

## Historical bootstrap checkpoint (2026-08-30; superseded for current catalog state)

- Formal detail run: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/iscas-bootstrap-20260828/venues/iscas/fbb1670a-0385-4f90-9c18-c1ec933270f2`.
- The saved identity union at that time was 10,120/10,121 (9,747 included detail rows plus 373 reused and schema-revalidated listing exclusions); no catalog write had occurred in that worker run. The crawler checkpoint and JSONL union are authoritative when a call ends before checkpoint serialization. One expected identity (`9401120`) remained pending because the official detail page, BibTeX, and RIS surfaces exposed an empty author field; a controller-owned 60-second wait on the exact official detail URL still showed no ordered authors. The current accepted snapshot still records one pending identity; it must not be silently included or excluded.
- Twenty-three transient Browser/error records are preserved in `metadata_errors.jsonl`; the bounded, redacted view for finalization is `detail_errors.jsonl`. Every error identity must later appear exactly once in staging or an explicit exclusion before finalization.
- Pending evidence is recorded in `official_bibtex_author_probe_9401120.json`, `skip_evidence_9401120.json`, and the latest `repair_blocker_9401120_20260831.json`. The PDF control visibly reports that the current session has no access; no paywall or access-control bypass was attempted. Resume only after an authorized official authenticated view or approved official archive/program supplement supplies ordered authors.
- The accepted expected-build summary is copied to `expected_build_summary.json`; it records identity-set SHA `670985a462f3a52d36aa147287a7c7a1f9b684c528a3245e3491f3ae7b4627a7` and 10,121 expected rows. It remains a scope contract, not catalog metadata.
- Recovery profile that has remained usable: one fresh official tab per small batch, 30–60 seconds before a retry, 4–8 seconds between starts, 20–30 seconds transient cooldown, and no concurrent requests. A `data:text` crash shell is never navigated or used as evidence.

Last verified for that historical worker view: 2026-08-31 (official detail recheck still exposed an empty author field; 10,120/10,121 accounted; no catalog write in that run). Current catalog status is stated above.

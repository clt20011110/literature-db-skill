# AAAI Conference on Artificial Intelligence

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Current catalog status (2026-10-01)

- The read-only catalog currently contains **16,810 `canonical_work` rows** for `aaai`. The formal run receipt records 16,810 included rows, 2,524 explicit exclusions, zero pending identities, strict staging/waterline/merge/reconcile/security `PASS`, and campaign state `ACTIVE`.
- The current campaign-state record was updated on 2026-08-30 and points to `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/aaai/86117c4e-b714-443e-a566-3d2cdff4f7f2/reconcile_receipt.json`. AAAI is not one of the five venues listed in the 2026-10-01 accepted-snapshot production receipt, so do not attribute this AAAI status to that receipt.
- The formal-run “no catalog write” wording below is a historical statement about the 2026-08-31 worker view. The fallback-repair and count-only artifacts remain evidence-only and must not replace the detail-validated catalog rows.

## Identity and scope

- Venue ID: `aaai`; annual proceedings; crawl start 2015. Canonical metadata source is the public AAAI OJS landing page (`ojs.aaai.org`); `aaai.org` is an official cross-check/older fallback.
- Include sections explicitly belonging to AAAI Technical/Main Track. Exclude IAAI/EAAI, special/social-impact/alignment tracks, student and doctoral consortium material, demonstrations, tutorials, workshops, journal/new-faculty/senior-member tracks, front matter, corrections, and retractions.

## Official entry points and enumeration

- OJS archive: <https://ojs.aaai.org/index.php/AAAI/issue/archive>.
- Enumerate visible issue anchors through the displayed terminal range; inside each issue consume all visible article cards and prove no remaining pagination/load-more control.
- Use the visible numeric article ID plus normalized DOI (`10.1609/aaai...`) as identity. Select only the canonical visible PDF galley.
- Fresh controller observation 2026-08-30: archive pages 1–7 showed 25, 25, 25, 25, 25, 25, and 22 issue anchors; the visible ranges terminate at `151-172 of 172` with no Next control. Page 7's dated body includes 2015, while page 1 exposes AAAI-26 (2026); year/track classification must still be applied against the expected manifests.

## Browser and metadata

- OJS pages can be slow; wait and inspect the visible issue/card state before diagnosing an empty result. Stop on login/SSO, CAPTCHA, 403/429, access denial, zero-result anomalies, mixed-track ambiguity, or unresolved pagination.
- A fresh 2026 issue sample (`/issue/view/683`) rendered after a 9–10 second wait with 94 article/detail links and 94 visible PDF galley links; its visible track heading is `AAAI Technical Track on Application Domains I`. Treat issue pages as listing/detail seeds until the article HTML supplies full field provenance.
- Prior pilot/replay evidence: 10,945 card observations, 9,300 main-track candidates, 1,645 exclusions, 60 fresh detail samples, all PASS. The count-only baseline (19,334 rows, 2015–2026) is not catalog-ready metadata.
- Revalidate abstract missingness, DOI, PDF galley, section label, publication date, and all field provenance during formal staging.

## History and update

- Registry: `<db-home>/registry/venues/aaai.yml`.
- Locked recipe/replay evidence: `<db-home>/recipes/aaai/`.
- Expected manifests are published under `<db-home>/manifests/expected/aaai/` (published 2026-08-28 after strict count verification). Before an update, query these manifests, the latest receipt, catalog, and watermark; revisit only the current/changed issue window.

## Download

- Prefer the visible OJS PDF galley link. If no link is in the listing, open the canonical article landing page, click its visible PDF action, and validate the file before recording it.

## Historical detail status (2026-08-31; superseded for current catalog state)

- Formal run `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/aaai/86117c4e-b714-443e-a566-3d2cdff4f7f2` now has 16,810 included detail rows, 2,524 explicit exclusions, and zero pending identities out of 19,334 expected. Strict staging validation is `PASS` with `catalog_ready=true`; all required title/authors/date/DOI/PDF fields and provenance are complete. Twenty-nine abstracts carry the allowed reason `not_present_on_official_page`.
- The formal run's waterline is `PASS / NO_DRIFT` with `enumeration_complete=true`; single-writer merge and strict reconcile both passed, and campaign state is `ACTIVE`. Security scan is `PASS` with `secret_findings=[]`. The earlier CAPTCHA/Browser retry evidence remains historical recovery evidence only; count-only/replay artifacts must not be promoted to catalog metadata.
- An official `aaai.org` fallback audit (`aaai_official_fallback_audit_20260831.json` and `aaai_fallback_2015_crosswalk_audit_20260831.json`) found 652 visible 2015 paper/PDF links and an exact ID overlap of 537/538 with the expected 2015 main-track manifest (the remaining title appears under fallback ID 459 vs expected 9459). The migrated HTML paper pages expose titles/authors/PDF links but no article abstract field. This remains cross-check evidence only; no PDF batch extraction or metadata promotion was performed. Use it only in a separately authorized repair stage, preserving the OJS identity and abstract-missing reason.
- The bounded fallback candidate staging is under `fallback-repair-2015/` in the formal run. It contains 537 rows with complete visible title/author/PDF provenance and explicit missing-abstract/DOI/date reasons; strict validation reports only the one expected ID not present in the fallback listing. It is not part of the primary OJS staging and is not catalog-ready.

Last verified for that historical worker view: 2026-08-31 (fresh detail batches through the CAPTCHA stop; no catalog write in that worker view). Current catalog status is stated above.

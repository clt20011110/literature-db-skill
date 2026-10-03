# ICCD (IEEE International Conference on Computer Design)

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Identity and scope

- Venue ID: `iccd`; annual conference; crawl start 2015.
- Canonical source: IEEE Xplore (`ieeexplore.ieee.org`).
- Include authored ICCD research papers from the official proceedings. Exclude visible front matter, committee/reviewer/index/copyright/title pages, tutorials, panels, posters, demos, and other explicitly non-main-track material.
- The current verified run covers 2015–2025. No formal 2026 ICCD proceedings was visible at the discovery cutoff; do not infer a future edition.

## Official entry points

- Proceedings index: <https://ieeexplore.ieee.org/xpl/conhome/1000129/all-proceedings>.
- Detail pattern: `https://ieeexplore.ieee.org/document/<native-id>`; preserve the numeric IEEE native ID and canonical query-free URL.
- PDF: use the visible IEEE PDF action only after the detail identity is validated.

## Browser behavior

- IEEE pages commonly need 15–30 seconds after navigation. Use one tab, small batches, and a checkpoint after each batch.
- A successful page visibly contains the expected title, authors, publication/DOI metadata, and PDF/eReader action. Validate the native ID against the expected listing row.
- Enumerate every visible annual proceedings collection and terminate only after the final collection is accounted for; do not infer missing years from URL patterns.
- On transient connection closure, wait and retry the same canonical URL with bounded attempts. A `data:text/html` crash/network shell is not source evidence; preserve the error and ask the user to reopen the exact URL if Browser policy blocks recovery.

## Metadata and identity

- Required detail fields are title, ordered authors, year, document type, landing URL, source URL, native ID, DOI status, PDF discovery status, observed time, and field-level provenance. Missing abstract/publication date must carry an allowed reason.
- Reclassify authorless listing rows before detail crawling; explicit exclusions remain auditable JSONL rows.
- Verified formal run: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/iccd/c83330db-55b2-4081-9132-2417dffe5572` (1,220 accounted; 1,093 included; 127 excluded).

## History and update

- Registry: `<db-home>/registry/venues/iccd.yml`.
- Locked recipe: `<db-home>/recipes/iccd/recipe.yml`.
- Final reconcile run: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/finalize-iccd-20260828/venues/iccd/ab171b10-6976-41a1-b817-b1929dc4fcae`.
- For updates, query the locked recipe, `manifests/expected/iccd`, latest receipt, catalog rows, and `update_watermark`; revisit the current/last closed proceedings and only changed or incomplete records.

## Download

- Prefer a validated visible IEEE PDF URL. If absent, open the canonical detail page and click the visible PDF control; validate `%PDF-`, content type, size, page count, and checksum.

Last verified: 2026-08-28.

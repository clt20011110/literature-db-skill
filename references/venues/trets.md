# ACM Transactions on Reconfigurable Technology and Systems (TRETS)

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Canonical source and scope

- Venue id: `trets`.
- Canonical name: ACM Transactions on Reconfigurable Technology and Systems.
- Official source: `https://dl.acm.org` only; do not use alternate domains, hidden APIs, guessed endpoints, or authentication secrets.
- Archive: `https://dl.acm.org/loi/trets`.
- Just Accepted: `https://dl.acm.org/toc/trets/justaccepted`.
- Issue pattern: `https://dl.acm.org/toc/trets/<year>/<volume>/<issue>`.
- The verified 2015–2026 waterline contains 49 official TOC units: 48 issue pages plus Just Accepted. The accepted source set contains 484 listing observations and 484 unique DOIs; issue/Just Accepted overlap is zero.

## Accepted enumeration and metadata policy

- Reuse the locked recipe, accepted TOC-unit manifest, canonical DOI identities, listing metadata, and detail evidence for updates. Revalidate the current schema, field provenance, source URLs, waterline, and drift each run.
- Eligible content types are `Research Article` and `Technical Note`. Exclude corrections, editorials, introductions/front matter, opinions, and other non-research material by explicit reason code. Unknown types and authorless eligible items fail closed.
- Listing pages provide identity/type and an abstract teaser only. The full abstract is taken from the official DOI detail page at `section#abstract [role="paragraph"]`; retain `abstract_status=partial_teaser` for listing-only evidence.
- Detail pages expose publication date and `meta[name="dc.Date"]`; preserve the accepted issue/enumeration year in formal `year` and retain the fresh detail publication date/year separately. Do not reclassify regular issue rows as Early Access solely because their online date is earlier.
- Use the official eReader/PDF action when visible and retain field-level provenance. Do not promote count-only or expected-manifest artifacts to catalog metadata.

## Browser/runtime observations

- Use one worker-controlled in-app Browser tab and bounded slow-page waits. A blank/loading page is not a zero-result page: wait for the content marker and for literal visible `Loading ...` text to disappear; fail closed on persistent blank, CAPTCHA, access denial, or unknown content type.
- Expand visible issue sections before reading cards. Author expansion must be scoped to `.issue-item-container button[aria-label^="View other"][aria-expanded="false"]`; journal-level topic/tag controls can navigate away. Listing `.loa a` links are the useful author surface.
- Reuse the current page when the same DOI is already loaded. Do not scan the whole DOM or every link on long review pages. `Offloading` is not a loading marker, and a forbidden body/body-text condition is not by itself an access block.
- Full author completeness is determined from the DOI detail page; listing author samples can be incomplete.

## Accepted formal run

- Formal run root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/trets/a14e9232-2560-40da-b630-20d8ead4ddd4`.
- Waterline: `NO_CHANGE / NO_DRIFT`; source-set SHA256 `94a96484a5d6cd077e7fcb2a269d4476f341b50993a246e122901b5f2f9f13c9`.
- Strict staging: 484 accounted, 453 included, 31 excluded; staging SHA256 `8823dcb426b59938aeb4c3fa573080d6c65b3f317e268cb9e048a35845f4a457`; exclusion SHA256 `f28f88a9890eb8124e835b582991ddc830a85822e3d0932955fc949d0e123f6e`.
- Strict security scan: PASS, `secret_findings=[]`. Merge and reconcile: PASS; 453 canonical works, 484 catalog source identities, 31 excluded source items, and no reconciliation gaps. Venue state: `ACTIVE`.
- For subsequent updates, follow the normal ACTIVE update path and reuse the recipe, watermark, canonical identities, and unresolved queues. Last verified: 2026-08-25.

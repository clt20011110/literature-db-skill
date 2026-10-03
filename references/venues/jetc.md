# ACM Journal on Emerging Technologies in Computing Systems (JETC)

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Identity and scope

- Venue ID and canonical name: `jetc` — ACM Journal on Emerging Technologies in Computing Systems.
- Journal and calendar model: ACM journal, volume/issue archive.
- Crawl start and accepted waterline: 2015 through the accepted 2026 issue scope; current formal run covers 2015–2026.
- Allowed/approved domain: `dl.acm.org` only.
- Accepted source scope: 44 issue TOC units plus the visible Just Accepted unit, 45 units total.
- Eligible types in the formal run: Research Article and Technical Note with DOI and ordered authors.
- Excluded types/decisions: Editorial/guest editorial, Introduction, Tutorial, and other explicit non-research content; unknown types and authorless eligible rows fail closed.

## Official entry points

- Archive/issues URL: `https://dl.acm.org/loi/jetc`.
- Journal landing fallback: `https://dl.acm.org/journal/jetc` (visible official links only).
- Current/early-access URL: `https://dl.acm.org/toc/jetc/justaccepted`.
- Issue/detail URL patterns observed in the accepted manifest: `https://dl.acm.org/toc/jetc/<year>/<volume>/<issue>` and `https://dl.acm.org/doi/<doi>`.
- PDF action/source: official DOI detail eReader/PDF action; formal metadata records retain the visible `pdf_url` and `pdf_discovery_status` provenance.

## Browser behavior

- The accepted run used visible ACM TOC cards and DOI detail pages. Listing-page evidence records the card count and DOI uniqueness per unit; the detail surface is authoritative for full abstract, ordered authors, date, type, and PDF discovery.
- Dynamic pages may need bounded waiting before card extraction. If the source shows a CAPTCHA, login redirect, access denial, persistent blank shell, unknown type, or an authorless eligible item, stop and preserve the checkpoint.
- The accepted listing contract expands visible section controls before extraction and scopes author expansion to `.issue-item-container button[aria-label^="View other"][aria-expanded="false"]`; listing `.loa` authors are observation-only and detail authors are authoritative.
- Keep listing teaser and full detail abstract separate. Do not promote a teaser or count-only identity manifest to catalog metadata.

## Metadata and identity

- Stable identity: ACM DOI (`10.1145/...`) and the official DOI landing URL; native ACM ID is retained in `source_native_id`.
- Listing fields: DOI, title, visible author subset, teaser status, article type, issue/year, article number/pages, section, and official detail/abstract/eReader links.
- Detail-only fields: ordered authors, full abstract at `section#abstract [role=paragraph]`, visible publication date and `meta[name="dc.Date"]`, formal type, and PDF discovery status.
- Enumeration-year rule: retain the accepted issue/listing year in the formal `year`; preserve detail publication date/year separately when they differ.
- Completeness checks: accepted 45-unit manifest, 478 listing observations, 478 unique DOIs, 442 included and 36 explicitly excluded, no missing or extra identities, 100% year coverage for 2015–2026, and zero provenance gaps after reconcile.

## History and update

- Locked recipe: `<db-home>/recipes/jetc/recipe.yml`.
- Formal run root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/jetc/2e98b654-d7ec-4165-ac26-a8d94db3a758`.
- Accepted source set SHA256: `1e00d1b43c3b36fbccf360ec3ea2bbe5f9e273e1c5e69bc02dafee429e23bad8`.
- Metadata staging SHA256: `faf1014269686907107181cb03525a23f1bd70cbcf29736e5c8e94756ac8406b`.
- Metadata exclusions SHA256: `5f139a787b6df94430d0519fe028898300b96309c69357c89e75cafe0f9f1e3e`.
- Waterline: `waterline_evidence.json` reports `NO_CHANGE / NO_DRIFT`.
- Current state after formal merge/reconcile: `ACTIVE`; reuse the locked recipe, accepted expected identities, canonical DOI identities, watermark, and unresolved queues for future updates.
- Before a future update, revisit the current issue/Just Accepted delta and any DOI with missing or conflicting detail fields; do not recrawl the full archive without drift evidence.

## Download

- Use only the visible official DOI detail eReader/PDF action in an authorized Browser session. Do not infer a PDF URL when the action is absent, and do not bypass authentication, CAPTCHA, or access restrictions.

Last verified: 2026-08-25. Formal JETC bootstrap completed with strict staging/security PASS, single-writer merge PASS, strict reconcile PASS, and controller state `ACTIVE`.

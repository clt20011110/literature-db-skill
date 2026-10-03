# ACM Transactions on Design Automation of Electronic Systems (TODAES)

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Scope and official sources

- Venue ID: `todaes`; publisher family: ACM Digital Library; crawl start: 2015.
- Journal home: `https://dl.acm.org/journal/todaes`.
- Archive: `https://dl.acm.org/loi/todaes`.
- Just Accepted: `https://dl.acm.org/toc/todaes/justaccepted`.
- Issue pages: `https://dl.acm.org/toc/todaes/{year}/{volume}/{issue}`.
- Article pages: `https://dl.acm.org/doi/{doi}`.
- Allowed domain: `dl.acm.org`. Basic Edition exposes the metadata surfaces needed for enumeration and detail extraction; PDF/full-text access is a separate download decision.

## Browser behavior and patience

- ACM DL often renders the journal shell before its issue contents. Wait 15–30 seconds and inspect again. If `tab.goto` times out but the tab is already at the exact requested official URL, continue a bounded wait instead of declaring the page empty.
- An issue can show `Loading ...` after its heading has appeared. Expand every visible `a.section__title[aria-expanded="false"]`, then wait until all section controls are expanded, no `Loading ...` marker remains, and at least one `.issue-item-container` is visible.
- Expand truncated author lists only inside article cards: `.issue-item-container button[aria-label^="View other"][aria-expanded="false"]`. Do not click the similarly named journal-level topic/tag control.
- Stop on CAPTCHA, access denial, an unresolved login wall for required metadata, a persistent blank shell after bounded waiting, or a visibly populated issue that parses as zero. Preserve a checkpoint before recovery.

## Enumeration and fields

- On the Archive page, click the visible decade tab, then every visible year tab from 2015 through the current waterline, and collect all visible issue links. Do not invent issue URLs from a numerical pattern.
- Enumerate every issue and Just Accepted. DOI is the primary source-native and canonical identity; prefer issue assignment over Just Accepted when the same DOI appears on both surfaces.
- The issue listing exposes DOI, title, visible author surface, document type, issue month/year, article number/pages, landing URL, abstract link, and eReader/PDF action.
- Listing abstracts are visibly truncated teasers. Store them only as `abstract_status=partial_teaser`; never promote them to the formal abstract field. Open `https://dl.acm.org/doi/{doi}` for authoritative detail metadata. The verified full abstract surface is `section#abstract [role="paragraph"]`; the visible Published date and `meta[name="dc.Date"]` provide the exact date.
- Treat the listing manifest as enumeration/count evidence until detail fields, provenance, DOI, current waterline, and drift gates are revalidated. It is not catalog-ready metadata by itself.

## Inclusion and identity

- Eligible candidates include visible `research-article` and `survey` types with authors and DOI.
- Explicitly exclude introduction, editorial, correction/erratum, retraction, index/front matter, and other non-research types. Preserve each exclusion and its visible type/title evidence. Fail for manual review on an unknown type or an authorless otherwise-eligible item.
- Normalize DOI to lowercase and remove URL prefixes. Retain the numeric ACM native identifier derived from the DOI suffix when present, but do not replace the DOI identity with a guessed number.

## Modes

- **Initialize:** reuse accepted manifests/evidence first, enumerate Archive 2015-current plus Just Accepted, deduplicate by DOI, fetch authoritative detail pages for eligible records, validate field coverage, stage, merge, reconcile, then advance the watermark.
- **Update:** query the active recipe, prior receipt, expected manifests, catalog, unresolved queues, and watermark. Revisit Just Accepted, the current in-progress issue, and the last closed issue; reconcile DOI movement from Just Accepted into an issue and retrieve only new, changed, or incomplete detail records.
- **Download:** use a validated stable public PDF action when it resolves without session secrets. Otherwise open the DOI landing page in the user-authorized in-app Browser and click the visible PDF/eReader action. Record `AUTH_REQUIRED` or `SOURCE_BLOCKED` for unresolved access; never bypass a paywall or CAPTCHA or persist signed/session URLs.

## Verified history and current discovery waterline

- Discovery/evidence root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/todaes/c1814062-63d1-4b2c-a739-42c85129e530/`.
- As observed on 2026-08-25, Archive exposed 65 issue links across 2015–2026. The current visible issue was Volume 31, Issue 6 (November 2026, in progress), with 34 visible items after expanding two sections. Just Accepted exposed 71 unique DOI items: 69 research articles and 2 surveys.
- These numbers are Browser discovery/count anchors only. They do not claim that TODAES has been merged or made catalog-ready; confirm campaign state, receipts, and catalog rows before update work.

## Verified operational observations (2026-08-25)

- Treat slow and blank-looking ACM shells as an explicit wait state: after an exact-target `goto` timeout, continue bounded waits and re-check the visible page before classifying the source as blocked. A heading or section control alone is not readiness while literal `Loading ...` text remains.
- Reuse the current page when the requested DOI is already loaded. This avoids unnecessary navigation and keeps the detail observation tied to the official DOI URL.
- Do not classify the browser's `Offloading ...` transition text as an ACM `Loading ...` marker. Conversely, a literal visible `Loading ...` marker must keep an issue in the waiting state.
- A forbidden-body/content-policy marker is not evidence of publisher access denial. Classify access blocking only from visible CAPTCHA, login/access-denied, or a persistent empty shell after the bounded wait contract.
- Long review/article pages must not be scanned by traversing the entire DOM or every link. Use the bounded, allowlisted selectors for the detail fields and the DOI target; listing completeness comes from the accepted TOC units and per-page card counts.
- Listing author expansion can accidentally activate a journal-level topic/tag control when the selector is broad. Scope it to `.issue-item-container button[aria-label^="View other"][aria-expanded="false"]`; use the DOI detail page's ordered author surface as authoritative when the listing and detail surfaces differ.
- Keep the issue/enumeration year for regular issue records even when the detail page's online publication year is earlier; retain the detail `publication_year` separately and do not relabel such records as Early Access.

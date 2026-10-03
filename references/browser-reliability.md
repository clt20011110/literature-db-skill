# In-app Browser reliability

Publisher sites are often slow, dynamic, and inconsistent. Judge the final visible state, not the first transient state.

## Opening and waiting

1. Prefer the user's already-opened matching tab when it contains a useful signed-in session. Otherwise open one controlled tab for the venue.
2. A newly created tab may legitimately begin at `about:blank`. Navigate it to the intended URL before diagnosing a blank-page failure.
3. After navigation, wait about 8–15 seconds before deciding what rendered. IEEE Xplore and ACM DL often need 15–30 seconds. Wait in short intervals so the user still receives progress updates.
4. Inspect the current URL, title, document readiness, visible body length, loading spinner/skeleton, expected venue/year text, and obvious access banners. A slow or initially empty body is not evidence of an empty source.
5. If the correct URL remains a blank/home shell, wait again and reload once. If it is still unusable after roughly 30–60 seconds, ask the user to inspect or open the exact URL. Do not open a grid of replacement tabs.
6. On a retry, first check whether the current tab is already on the intended canonical DOI/native-ID URL. If it is, wait for and reuse that page instead of refreshing a slow page that has finally loaded.

## Dynamic controls and pagination

- Resolve visible controls by accessible role/name and current DOM. The same `Next page of search results` control may be a `button` on one rendering and an `a` element on another; a failed old selector is not a site failure when the control is visibly present.
- After a click, wait for an observable change: URL, page/range text, native-ID set, or main-content fingerprint. Allow up to about 15 seconds on slow pages.
- If a click appears to do nothing, wait once more and re-read visible state. One controlled retry or reload is reasonable for a transient network/UI failure. Stop after repeated no-op or contradictory ranges.
- Do not infer termination from numbered page buttons alone. Some sites show only pages 1–10 while semantic Next continues. Terminate on the visible end range/total and absence or disabled state of Next.
- Do not treat page/range numbers such as 403–408 as HTTP 403. Access failures require an actual response/banner/state, not a substring match.

## Recoverable versus hard stops

Recover locally after waiting when the issue is a blank first paint, slow script execution, stale locator, detached element, temporary missing content, or tab created at `about:blank`.

For a bounded IEEE `ERR_CONNECTION_CLOSED`, `ERR_BLOCKED_BY_CLIENT`, or similar transient navigation failure, keep the exact canonical native-ID URL and retry up to five times with roughly 15 seconds between starts. Use a cache-busting query only on the second attempt; return to the exact canonical URL on later attempts. If the browser replaces the tab with a `data:text/html` network-error shell, do not use that shell as source evidence and do not try to navigate through its encoded URL. Keep the failed attempt in the error log, create one fresh controller-owned tab for the same venue, resume the exact saved native-ID checkpoint, and prove every historical error ID later appears in staging or an explicit exclusion.

For long IEEE proceedings/detail runs, start conservatively with one tab, about eight detail records per batch, roughly four seconds between navigation starts, and a 15–20 second cooldown between batches. These are adaptive defaults, not acceptance constants. If a canonical article is temporarily blocked, preserve the checkpoint/error, wait about 20 seconds, navigate the same canonical URL in the same tab, wait another 15 seconds for visible article markers, then resume. Reduce the batch before increasing retries. Never reopen already completed records just to make the run look deterministic.

Do not infer that a faster detail profile is safe from one clean pilot. In the DATE run, 16 records at a 2.5-second start interval succeeded once, but subsequent 2–2.5-second runs stalled after already writing part of the batch and reset browser control. Eight-record batches at four-second starts were mostly stable but one exceeded the control budget; six records, four-second starts, and a 20-second cooldown then completed the run. Preserve the atomically written rows, compare actual JSONL counts with the stale checkpoint, reacquire the same controlled tab, and resume from disk without duplicating rows.

Stop and ask the user when there is a CAPTCHA, explicit access denial, persistent login/paywall for requested content, repeated navigation/network failure, unapproved domain redirect, or an unresolved scope ambiguity that would change which papers belong in the database.

## Avoid false loading and access signals

- Match loading text as a standalone UI state, not a raw substring. A paper title containing `Offloading` is not a `Loading` shell.
- Treat `Forbidden`, `access denied`, or similar text as an access stop only when it appears in the page shell/banner and the expected article or listing markers are absent. Research prose can legitimately discuss “forbidden regions.”
- A visible article title, DOI, abstract, or expected listing cards outweigh an unrelated hidden/loading-class element elsewhere on a fully rendered page.
- A publisher may temporarily append a Cloudflare-style query parameter while still rendering the correct article. Accept it only when the official origin and canonical DOI path are unchanged, then save the query-free canonical URL. Never persist the parameter value in evidence, errors, provenance, or logs. An actual human-verification page remains a hard stop.

## Task and tab discipline

- Use one active venue and a small number of tabs. Close or reuse controller-created tabs when finished; do not close user tabs without permission.
- User tabs and claimed Browser objects may not exist in a delegated task. If a user-opened tab matters, do the Browser interaction in the current task and export a compact evidence/checkpoint file for the Luna Max worker.
- Do not duplicate an already accepted full traversal merely because a new task lacks the original tab. Reuse the saved native-ID set and refresh only the current waterline or drift-sensitive sample.
- If the Browser reports `target closed` but the tab still appears in the tab list, reacquire a fresh controlled handle for that exact controller-created tab ID and resume from the saved item checkpoint. Do not switch to or overwrite a user tab.
- Keep each Browser batch below the current control-call time budget. If the browser-control connection resets after a slow batch, first compare on-disk staging/exclusion counts with the last summary, then reacquire the existing tab from the current tab list and reload the crawler from the saved checkpoint. Do not create a duplicate tab or roll back rows that were written before the reset.

## Efficient extraction

Use normal visible pages and public official HTML/API data. Batch-read listing rows and native IDs, then open detail pages only where needed. Avoid screenshots or full raw DOM for every record; save normalized fields, hashes, counts, and small evidence samples instead. On very long reviews or proceedings pages, query only the article header, author block, abstract, identity meta tags, and PDF/eReader controls; do not scan every DOM node or every reference DOI link. Never read or persist cookies, tokens, local storage, authorization headers, or signed download URLs.

Listing controls that expand hidden authors can occasionally navigate to an author/profile page instead of expanding in place. If that happens, stop using the expander, mark the listing author set as partial, and obtain the authoritative ordered authors from the canonical detail page.

Before a long detail pass, list every included listing row whose author list is empty. Classify clear front matter such as cover/copyright pages, TOC, preface, proceedings title pages, sponsors/organizers, committees, and author indexes with explicit reason codes. Update both listing and detail classifiers, reclassify the saved listing without refetching, rebuild expected manifests, and preserve a repair audit. Leave genuinely ambiguous authorless items as scope-review stops.

If an authorless detail record is visibly a research paper, do not silently accept an empty author list and do not exclude it. Follow a visible official venue-archive/program link, require the same native identity or a normalized-title match, and save a bounded metadata-supplement artifact with the official URL, observation time, ordered authors, affiliations/DOI when present, and field-level provenance. Allowlist the supplemental official host and fail on any title, author, or DOI conflict; this is an explicit record repair, not a global relaxation of required fields.

If the canonical page and its visible `Cite This`/BibTeX or RIS export both report an empty author field (for example `author={}`), treat that as a source-surface metadata blocker, not as proof that the work is authorless. Preserve the citation-export observation and leave the expected identity pending until an authorized official archive/program supplement or user-authenticated publisher view supplies ordered authors; never convert the item to a front-matter exclusion or invent names.

When one pending identity blocks an otherwise healthy long IEEE pass, an explicit `skipSourceNativeIds` continuation may process later identities only if the run records a controller-authored pending-evidence file. Skipped IDs remain outside the staging/exclusion union, are carried in checkpoint/summary, and keep finalization blocked until resolved; never silently delete them from the expected manifest or treat the continuation as complete.

A visible bulk-export dialog is only a candidate method. Verify that the requested scope, fields, and actual file download work for a one-record sample and the intended batch size before adopting it. If the UI says “Citation and Abstract” but no file/download event is produced, record the probe as blocked and return to the checkpointed detail method; do not infer exported coverage from the dialog alone.

When an IEEE proceedings page visibly offers `Items Per Page` values up to 100, selecting 100 can safely reduce listing pagination only if the resulting URL, visible range, card count, `isnumber`, total, and final-page termination all validate. This optimization applies to enumeration; it is not evidence that detail pages can be navigated at a higher rate.

## Validate publisher-reported identifiers

Visible publisher fields can still contain placeholders or malformed values. Validate every reported DOI after normalization; do not accept a whitespace-containing or otherwise invalid string merely because the official page labels it `DOI`. Reopen the exact canonical page, wait for the visible article content, and save a compact evidence record containing the title, source native ID, reported string, source URL, observation time, and validation result. Preserve the reported value in an audit field, clear it from the canonical DOI field, and write a structured checked-missing reason. Never invent or substitute a DOI from an unofficial source.

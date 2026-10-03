# Operating modes

Use the same venue playbook and provenance model in every mode. The difference is the starting point and the amount of material that must be visited.

## Common metadata target

For each eligible source item, retain when available:

- venue ID, year/volume/issue or conference edition/session;
- title, ordered authors, abstract, document type, publication/conference date;
- DOI and publisher-native ID;
- canonical landing URL and visible PDF URL or PDF-discovery status;
- inclusion/exclusion decision and reason;
- source URL, observed time, field-level provenance, and current watermark;
- normalized identity used for deduplication, plus the raw official identity.

An unavailable field may be null with provenance; it must not be synthesized. Listing rows may seed detail work, but they are not complete metadata until required fields have been checked.

## Mode 1: initialize from 2015

Use when a venue has no production metadata or the user requests a fresh bootstrap.

1. Read the venue registry, allowed domains, crawl start, active/candidate recipe, matching playbook, campaign state, and prior receipts. Reuse existing enumeration/count evidence rather than rediscovering it.
2. Cover `max(2015, registry.crawl_from)` through the current waterline. For journals, enumerate volumes/issues plus current early-access material. For conferences, enumerate annual proceedings and official main-track/session scope.
3. Work year by year. Save an enumeration checkpoint before opening many detail pages. A checkpoint should contain the source item/native ID set, source URL, observed time, and resume position.
4. Extract listing metadata in batches. Open detail pages only for missing or authoritative fields such as abstract, DOI, full author list, document type, landing URL, and PDF discovery.
5. Apply the venue's main-track/research-type rules. Preserve excluded items with reason codes; do not silently drop front matter, editorials, tutorials, special sessions, contests, or other non-target material.
6. Deduplicate first by stable publisher-native ID or DOI. When neither exists, use a documented venue-specific identity such as year + session + normalized title. Fuzzy title matching may suggest review but must not silently merge conflicts.
7. Perform practical checks: first/middle/latest/current samples, adjacent-year count sanity, no unexplained empty year, required-field coverage, stable pagination termination, and duplicate review. Do not require formal replay machinery unless formal acceptance was requested.
8. Stage and merge only validated metadata rows. Record the receipt, update watermark/state, and update the venue playbook.

## Mode 2: incremental update

Use when a venue already has a recipe, history, catalog rows, or a watermark.

1. Query the active recipe, latest receipt, expected manifests, count reports, catalog rows, unresolved queues, and `update_watermark`. If database tables are empty, fall back to campaign state and run artifacts; do not pretend count baselines are catalog metadata.
2. Determine the delta window. Normally revisit the current year, early access/online-first, the latest closed issue or proceedings, and anything newer than the accepted watermark. Expand farther back only for drift, corrections, missing fields, or explicit repair.
3. Re-enumerate the delta window and compare native IDs/DOIs with existing source items. Classify records as new, changed, unchanged, missing, or ambiguous.
4. Reuse existing complete fields. Fetch only fields that are absent, stale, conflicting, or whose source changed. Preserve earlier provenance rather than overwriting it without explanation.
5. Treat corrections, retractions, issue reassignment, early-access-to-issue movement, DOI changes, and title/author changes as versioned updates, not blind inserts.
6. Merge the validated delta, then advance the watermark. If the run stops mid-page/year, keep the old watermark and save a resume checkpoint.
7. If the publisher layout changed, update the venue playbook with the new visible workflow and retain the previous method as dated history.

## Delegating to Luna Max

Give one Luna Max task the venue, selected mode, database root, playbook path, and requested year/waterline. Tell it to reuse history first, checkpoint continuously, use the in-app Browser patiently when that task can actually access it, and return a compact receipt. If the Luna Max task cannot see the controller's claimed/user tab, keep Browser navigation in the controller task and hand Luna Max the saved expected manifests, normalized Browser evidence, staging/exclusion files, and exact closeout commands. Avoid delegating isolated page clicks to separate tasks because Browser tabs and claimed user state do not reliably transfer between tasks.

## Merge boundary

Initialization or update authorization permits normal staging and merge for the requested venue. Resolve ordinary conflicts from authoritative evidence when the user has authorized this; preserve the alternatives and decision. Do not infer permission for destructive deletion or new restricted-source access from a collection request.

---
name: literature-db
description: Search a bundled local literature catalog, initialize or incrementally update journal and conference metadata from official sources, and collect or download paper PDF links using per-venue playbooks. Use for local related-paper search and venue-oriented metadata maintenance.
---

# Literature DB

Use this directory as the skill root. Run `python3 <skill-root>/tools/litdb.py` from any working directory. The default database home is `<skill-root>/data/literature-db`; `--home` or `LITDB_HOME` can select another home. Installation and full database download are documented in [README.md](README.md). If the catalog is absent, install the bundled release before searching; do not silently create an empty replacement.

## Search and export

For a related-paper request, search the local catalog first:

```bash
python3 <skill-root>/tools/litdb.py search query "research topic or idea" --limit 12 --format json
```

Honor venue/year constraints. For Chinese ideas, search both the original and a precise English technical translation. Split complex ideas into distinct retrieval questions when useful. Deduplicate results by `id`, read their abstracts, and distinguish direct relevance from background. Return real titles, authors, venue/year and stored article/PDF links. Scores are rankings, not relevance probabilities; title/abstract search does not imply full-text reading.

Check `search status`; run `search index` when absent or stale. Search opens the source catalog read-only and writes derived artifacts under `<db-home>/search`. Embedding inference is local. `search start --open` opens the workbench, including export of the current displayed results or selected papers as CSV, JSON, BibTeX or RIS with full stored abstracts.

## Collect or update one venue

For adding venues, collecting a registered but empty venue, or expanding year/field coverage, read [references/expand-venues.md](references/expand-venues.md). It includes a user task template, source/runtime registry differences, staging inputs, verified CLI commands and current registration limitations.

Read [references/modes.md](references/modes.md) and [references/history-and-reuse.md](references/history-and-reuse.md), then only the matching [venue playbook](references/venues). There is one playbook per venue, plus a [template](references/venues/template.md) for a newly verified source. [references/venue-index.md](references/venue-index.md) lists available venues. Use [references/browser-reliability.md](references/browser-reliability.md) when browser work is needed, and [references/pdf-download.md](references/pdf-download.md) for requested PDF downloads.

1. Inspect registry, catalog, watermarks, expected manifests and checkpoints. Existing complete years normally need incremental updates, not recrawling.
2. Enumerate official years/issues/proceedings and main research scope. Preserve every exclusion and unresolved identity; a list count is not full metadata.
3. Use stable public official HTML/API where available, or a normal authorized browser session for dynamic/login-dependent pages. Keep credentials inside that browser; never export cookies or tokens. Stop at CAPTCHA, access denial or rate-limit barriers and preserve the checkpoint.
4. Collect title, ordered authors, abstract, date/type, DOI/native ID, article and observed paper-PDF links with source URLs, timestamps and field provenance. Preserve checked-missing reasons; do not guess identifiers or construct unobserved PDF links. Slides and supplementary files are not paper PDFs.
5. Validate staging, merge with a single writer, reconcile counts/identities, then confirm completion and refresh search. The current merge writes the database watermark transactionally; its presence alone is not proof that reconciliation passed. [references/maintenance.md](references/maintenance.md) has CLI examples. Ordinary runs do not require historical pilot/replay rituals unless formal acceptance is requested.
6. Update the matching venue playbook after verified source/layout changes. Keep durable extraction rules in the playbook and per-run bulk evidence under the database home.

Keep one active venue worker/browser context per source. When delegation is authorized, one worker should own a venue end to end. Saved evidence transfers between workers; a signed-in browser tab may not. Resolve reasonable conflicts within the user's authorization and retain the decision evidence.

Report actual new/updated/skipped/unresolved counts, covered waterline, field/link coverage, checkpoint and merge status. PDF-link collection, live URL checks, and PDF downloads are separate outcomes.

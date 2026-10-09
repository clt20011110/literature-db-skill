---
name: literature-db
description: Search a bundled local literature catalog, initialize or incrementally update journal and conference metadata from official sources, and collect or download paper PDF links using per-venue playbooks. Use for local related-paper search and venue-oriented metadata maintenance.
---

# Literature DB

Use this directory as the skill root. Run `python3 <skill-root>/tools/litdb.py` from any working directory. The default database home is `<skill-root>/data/literature-db`; `--home` or `LITDB_HOME` can select another home. Installation and full database download are documented in [README.md](README.md). If the catalog is absent, install the bundled release before searching; do not silently create an empty replacement.

## Search and export

For a related-paper request, search the local catalog first. Prefer the two-stage `discover` workflow: recall a bounded candidate set with multiple topic-preserving queries and explicit literal phrases, then use a local Kev service for conservative filtering and fuse recall and Kev ranks. Read [references/multi-retrieval.md](references/multi-retrieval.md) for setup, controls, and result interpretation.

```bash
python3 <skill-root>/tools/litdb.py search discover "用图神经网络优化芯片布局和拥塞" \
  --retrieval-query "graph neural network chip placement congestion" \
  --retrieval-query "GNN placement congestion prediction" \
  --keyword "graph neural network" --keyword "GNN" --keyword "placement" --keyword "congestion" \
  --venue dac --venue iccad --candidate-limit 50 --limit 12 --include-candidates --format json
```

Honor all venue/year and essential topic constraints in the positional topic and query variants; Kev judges against the full positional topic. For Chinese ideas, retain the original and add precise English technical translations; `combined` and `zvec` include the original topic in recall; `keyword` recalls only the explicit phrases and uses the original topic for Kev judgments. Choose meaningful literal technical phrases, synonyms, and acronyms as repeatable `--keyword` arguments. Phrase matching uses contiguous normalized tokens, with OR between phrases, so keywords alone do not enforce all topic constraints. Do not turn the entire request into an automatic literal keyword or silently broaden its research scope. `combined` (the default) and `keyword` require explicit phrases; `zvec` can run without them. The combined retriever fuses zvec retrieval and catalog phrase matches before Kev sees at most `--candidate-limit` deduplicated papers. Kev does not infer over the full catalog.

Read returned abstracts, including results marked `uncertain`, and distinguish direct relevance from background. Kev choice distributions are model confidence that has not been calibrated for literature relevance, not relevance truth. A candidate with an abstract is removed only when `unrelated` wins at or above `--unrelated-threshold` (default 0.60); low-confidence retained candidates remain available for review. Candidates missing an abstract are retained as `uncertain` even if Kev strongly chooses `unrelated`. An empty result means this bounded search has no accepted candidates, not that the whole catalog has no relevant papers. Deduplicate by `id` and return real titles, authors, venue/year and stored article/PDF links. Retrieval scores and reranking scores are rankings, not relevance probabilities; title/abstract search does not imply full-text reading.

For broad literature reviews or requests for model lists, use `--include-candidates --format json` (usually `--candidate-limit 100`, subject to local service capacity). Review the union of the leading final, zvec, and Kev ranks in `candidates`, including filtered records when they ranked highly at recall. `ranks.zvec` is the original multi-query zvec ranking before the independent keyword route; `ranks.retrieval` is the combined recall rank. Do not interpret a low final rank or omission from the display limit as irrelevance. Read abstracts and organize the final list by research direction so that text/image-conditioned or multi-domain approaches do not disappear behind a single dominant approach. Identify adjacent work separately according to the user's scope: e.g. for small-molecule model requests, protein-only generation, conformation-only work and benchmarks may need separate lists; mixed-domain models still qualify when their abstracts explicitly demonstrate small-molecule generation. Do not infer model quality from relevance rank.

The default `--ranking balanced` combines recall and Kev ranks with equal-weight RRF (k=60). `--ranking kev` reproduces the confidence-only ordering; the candidate selection and rejection rule stay the same. Rank fusion limits confidence-only displacement but does not guarantee direction coverage or higher precision. For requested comparisons, use the same topic, translated queries, venue/year scope and candidate limit with (1) `--retriever zvec --reranker none`, (2) `--retriever combined --reranker none`, and (3) the default combined + Kev workflow. The explicit `none` mode needs no Kev service and never reports relevance judgments. Preserve earlier reports; save fresh experiment artifacts under `<db-home>/search/evaluations/`. Record actual candidate counts, stage timings, cache conditions and the scope of abstract review. Separate saved-score replay from live inference, and candidate-only tests from full-catalog inference or extrapolation. See the reference for a reproducible command template.

The existing baseline and workbench remain available. If Kev is unavailable, report the failed reranking step and use the baseline when useful, describing its results as retrieval candidates:

```bash
python3 <skill-root>/tools/litdb.py search query "research topic or idea" --limit 12 --format json
```

Alternatively rerun `discover` with `--reranker none` to retain the same multi-query/phrase recall. This must be an explicit fallback with its missing Kev stage disclosed; never label an errored or skipped rerank as completed.

Check `search status`; run `search index` when absent or stale. All discovery retrievers require the ready derived lookup snapshot (`papers.sqlite` and `state.json`) produced by `search index`. Once that snapshot is ready, discovery's `keyword` queries do not require the Node/zvec runtime; there is no separate keyword-only bootstrap command. Search opens the source catalog read-only and writes derived artifacts under `<db-home>/search`. Embedding and Kev inference remain local; the Kev URL must be a loopback endpoint. `search start --open` opens the existing workbench, including export of the current displayed results or selected papers as CSV, JSON, BibTeX or RIS with full stored abstracts.

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

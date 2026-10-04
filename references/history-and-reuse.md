# History, state, and reuse

The default database is `data/literature-db` inside the resolved skill/repository root. `--home` overrides `LITDB_HOME`, which overrides this default. There is no dependency on another agent framework or its directories.

## Stored state

- `catalog.sqlite`: full canonical works, source records/fields, provenance, identifiers, locations, yearly coverage, merge events and watermarks.
- `registry/venues/<venue>.yml`: identity, official domains, inclusion/exclusion policy; registry availability does not mean collected coverage.
- `campaign_state.json`: current per-venue state and receipt references.
- `manifests/expected/<venue>/`: accepted source identity sets by year.
- `recipes/<venue>/`: extraction recipes, where present.
- `runs/`, `staging/`, `raw/`, `reports/`, `queues/`: new local collection evidence and checkpoints.
- `search/`: derived lookup, title/abstract corpus, local vectors and server state. Rebuildable; excluded from releases.

## Bundled snapshot

The current `v1.1.0` release contains 177,031 canonical papers across 26 venues. It extends the `v1.0.0` snapshot of 127,256 papers across 22 venues with CVPR (21,482), ICCV (8,691), ECCV (9,416), and ACL (10,186); `v1.0.1` was an installer-only fix. Each release is a versioned snapshot, not continuous live coverage. The wider registry has 107 possible venues; most have no collected papers.

The release manifest and snapshot audit under `data/` record exact counts, checksums and transformations. Database provenance paths referring to the original machine are replaced by archival references; publisher URLs and metadata are retained. Historical raw pages, downloaded files, browser sessions, chat runs and search vectors are not bundled. A `legacy-evidence://` reference is a provenance label, not a downloadable URL. Historical evidence must be supplied separately or replaced with a new observed source before claiming a fresh audit.

## Reuse workflow

Read catalog rows, accepted identity manifests, current campaign state and watermarks before starting work. Prefer incremental updates to closed-year recrawls. Distinguish canonical-paper counts from source-item or conference-edition counts: multiple source identities can refer to one work. Excluded and unresolved source records remain auditable but are not searchable papers.

An existing metadata value plus source identity can be reused with its original observation date. Do not label it newly fetched, live-tested or PDF-downloaded. Count manifests prove scope only. If old raw evidence is unavailable, record that limitation and obtain fresh evidence only for the new validation being claimed.

After a validated transactional merge, reconcile identity coverage and rebuild search. Failed or partial collection does not advance the accepted watermark.

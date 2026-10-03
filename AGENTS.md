# Literature DB development and search

Read `SKILL.md` for collection/search behavior and `README.md` for setup. The package is standalone. Default database home is `data/literature-db`, overridden by `LITDB_HOME` or CLI `--home`. Never hardcode a developer's home directory.

For related-paper requests, search the local catalog before answering from memory. Search Chinese ideas in both Chinese and technical English, preserve scope, read abstracts, deduplicate IDs, and cite actual stored article/PDF links. Do not imply full PDF reading from a metadata search.

Search writes only derived files under the configured `search/` directory. Keep inference local by default. Metadata mutations must use validated staging, a single-writer merge and reconciliation.

Run applicable tests with `python3 -m unittest discover -s tests/litdb`. Do not commit database binaries, caches, node_modules, secrets, sessions, raw publisher pages, private evidence or temporary collection outputs. Full database snapshots are checksum-verified release assets; `data/` holds their tracked manifests and audit summaries. Keep historical source receipts separate from new network observations.

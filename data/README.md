# Full literature database

The release installer places the verified catalog and its maintenance files in
`data/literature-db/`. It checks every packaged file's SHA-256, runs SQLite's
`integrity_check`, and verifies all table counts and the bundled entity and
canonical-field digests before installing.

Install from the public v1.0.0 release:

```bash
python3 scripts/install_database.py --base-url https://github.com/clt20011110/literature-db-skill/releases/download/v1.0.0
```

To restore from local release assets instead:

```bash
python3 scripts/install_database.py --release-manifest dist/database-release-manifest.json
```

When the release is a single archive, this direct form also works:

```bash
python3 scripts/install_database.py --archive dist/literature-db-v1.0.0.tar.gz
```

The installer defaults to `data/literature-db`. It refuses to replace a
non-empty destination; use `--force` only when replacing that destination is
intended.

The bundle includes the full SQLite catalog, expected metadata manifests,
venue registry, current campaign state, and recipe/candidate configuration
history. Absolute local file references and internal thread/request/session
identifiers are represented by `legacy-evidence://` references; those omitted
local files are not included. Scholarly source identifiers, official URLs, and
conference `session_code`/`scope_session` values are preserved. Raw or browser
evidence, PDFs, HTML captures, chat receipts, and run reports are excluded.

To create a new bundle from a complete literature-db home:

```bash
python3 scripts/bundle_database.py --source-home /path/to/literature-db --version v1.0.0
```

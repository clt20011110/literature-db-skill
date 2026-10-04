# Full literature database

The release installer places the verified catalog and its maintenance files in
`data/literature-db/`. It checks every packaged file's SHA-256, runs SQLite's
`integrity_check`, and verifies all table counts and the bundled entity and
canonical-field digests before installing.

The v1.1.0 snapshot contains **177,031 papers across 26 venues**, including
49,775 papers added from CVPR, ICCV, ECCV, and ACL. Install it from the public release:

```bash
python3 scripts/install_database.py --base-url https://github.com/clt20011110/literature-db-skill/releases/download/v1.1.0
```

To restore from local release assets instead:

```bash
python3 scripts/install_database.py --release-manifest dist/database-release-manifest.json
```

When the release is a single archive, this direct form also works:

```bash
python3 scripts/install_database.py --archive dist/literature-db-v1.1.0.tar.gz
```

If Python's configured OpenSSL CA file is missing, the installer can use an installed standard system CA bundle; explicit `SSL_CERT_FILE` and `SSL_CERT_DIR` settings are honored, and TLS verification stays enabled.

The installer defaults to `data/literature-db`. It refuses to replace a
non-empty destination; use `--force` only when replacing that destination is
intended.

For an existing installation, preserve any locally collected data before
replacement. You can verify the new snapshot in a separate directory first:

```bash
python3 scripts/install_database.py --base-url https://github.com/clt20011110/literature-db-skill/releases/download/v1.1.0 --target .local/literature-db-v1.1.0
python3 tools/litdb.py search index --home .local/literature-db-v1.1.0
python3 tools/litdb.py search start --home .local/literature-db-v1.1.0 --port 8766
```

Rebuild the search index after installing a new database. Release archives do
not contain machine-specific search indexes, model caches, or paper PDFs.
The older v1.0.0 database assets and v1.0.1 installer release remain available.

The bundle includes the full SQLite catalog, expected metadata manifests,
venue registry, current campaign state, and recipe/candidate configuration
history. Absolute local file references and internal thread/request/session
identifiers are represented by `legacy-evidence://` references; those omitted
local files are not included. Scholarly source identifiers, official URLs, and
conference `session_code`/`scope_session` values are preserved. Raw or browser
evidence, PDFs, HTML captures, chat receipts, and run reports are excluded.

To create a new bundle from a complete literature-db home:

```bash
python3 scripts/bundle_database.py --source-home /path/to/literature-db --destination dist/v1.1.0-snapshot --dist dist/v1.1.0 --version v1.1.0
```

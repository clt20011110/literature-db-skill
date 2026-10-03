# Metadata maintenance

Run from the skill root, or use an absolute `tools/litdb.py` path. Add `--home <directory>` to select another database.

For venue registration and first-time collection, see [扩增 venue 指南](expand-venues.md). The `--strict` registry check below targets the original 107-venue configuration; custom additions require structural validation or an explicitly updated source baseline.

```bash
python3 tools/litdb.py registry validate --strict
python3 tools/litdb.py bootstrap plan --venue tcad
python3 tools/litdb.py staging validate --venue tcad --run <run-root> --input <staging.jsonl> --exclusions <exclusions.jsonl> --strict
python3 tools/litdb.py merge venue --venue tcad --run <run-root> --input <staging.jsonl> --exclusions <exclusions.jsonl> --single-writer --verify-before-commit
python3 tools/litdb.py reconcile venue --venue tcad --run <run-root> --strict
python3 tools/litdb.py search index
```

Use `init --home <new-directory>` only for a deliberately empty catalog. Installing the bundled snapshot already supplies the catalog and registry; initialization is not a database-download step.

The CLI supplies validation, identity reconciliation and transactional persistence. Source acquisition is driven by the corresponding venue playbook and official pages. Reusable source adapters are in `tools/browser/` and the selected `tools/*detail*` / `tools/nature_*` helpers; inspect `--help` and input contracts before using one. This is not an unattended universal crawler, and a new publisher layout may need a new adapter.

Legacy formal pilot/replay/count commands remain available for explicit formal-acceptance workflows. The initial 107-entry registry is a configuration baseline, not evidence that 107 venues have collected metadata.

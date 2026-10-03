"""Run-local validation helpers for ICLR 2015-2016 arXiv detail rows.

This helper deliberately keeps the abstract variable named ``abstract_text``
to avoid the historical ReferenceError in the first retry attempt.
"""
import json
from pathlib import Path

REQUIRED_PROVENANCE = (
    "source_native_id", "title", "authors", "year", "document_type",
    "landing_url", "abstract", "doi", "publication_date",
    "pdf_discovery_status",
)
ALLOWED_PDF_STATUS = {"direct_public", "visible_url", "landing_page_action", "not_visible", "access_restricted", "not_available", "checked_missing", "metadata_only"}
ALLOWED_MISSING = {"not_present_on_official_page", "not_assigned", "not_visible", "access_restricted", "publisher_does_not_supply", "source_unavailable", "checked_missing"}

def validate_row(row: dict) -> list[str]:
    errors = []
    if row.get("schema_version") != "literature-metadata-staging-v1": errors.append("schema_version")
    for field in REQUIRED_PROVENANCE:
        if field not in row: errors.append("missing:" + field)
    provenance = row.get("field_provenance")
    if not isinstance(provenance, dict): errors.append("field_provenance_type")
    else:
        for field in REQUIRED_PROVENANCE:
            if not isinstance(provenance.get(field), dict): errors.append("provenance:" + field)
    missing = row.get("missing_fields", {})
    if not isinstance(missing, dict): errors.append("missing_fields_type")
    for field, value in missing.items():
        reason = value.get("reason_code") if isinstance(value, dict) else value
        if reason not in ALLOWED_MISSING: errors.append("missing_reason:" + field)
    if row.get("pdf_discovery_status") not in ALLOWED_PDF_STATUS: errors.append("pdf_status")
    return errors

def mark_superseded(error_path: Path, output_path: Path) -> int:
    rows = []
    if error_path.exists():
        for line in error_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["status"] = "SUPERSEDED_BY_RETRY"
                row["superseded_by"] = "metadata_staging_older_arxiv.jsonl"
                rows.append(row)
    output_path.write_text("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in rows), encoding="utf-8")
    return len(rows)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--mark-superseded", nargs=2, metavar=("ERRORS", "OUTPUT"))
    args = p.parse_args()
    if args.mark_superseded:
        print(mark_superseded(Path(args.mark_superseded[0]), Path(args.mark_superseded[1])))

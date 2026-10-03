from __future__ import annotations

import sqlite3
from pathlib import Path

TABLES = (
    "venue", "venue_state", "crawl_recipe", "recipe_run", "thread_run",
    "browser_page_observation", "source_item", "source_item_field",
    "canonical_work", "work_identifier", "work_version", "work_location",
    "field_provenance", "venue_year_baseline", "venue_year_coverage",
    "update_watermark", "repair_task", "auth_task", "drift_event",
    "merge_event", "waiver",
)


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','3')")
        for table in TABLES:
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
        indexes = {
            "idx_source_item_venue_native": (
                "source_item",
                "json_extract(payload_json, '$.venue_id'), json_extract(payload_json, '$.source_native_id')",
            ),
            "idx_source_item_canonical": ("source_item", "json_extract(payload_json, '$.canonical_key')"),
            "idx_source_item_field_source_field": (
                "source_item_field",
                "json_extract(payload_json, '$.venue_id'), json_extract(payload_json, '$.source_native_id'), json_extract(payload_json, '$.field_name')",
            ),
            "idx_canonical_work_key": ("canonical_work", "json_extract(payload_json, '$.canonical_key')"),
            "idx_work_identifier_key": (
                "work_identifier",
                "json_extract(payload_json, '$.canonical_key'), json_extract(payload_json, '$.scheme'), json_extract(payload_json, '$.value')",
            ),
            "idx_work_location_key": (
                "work_location",
                "json_extract(payload_json, '$.canonical_key'), json_extract(payload_json, '$.location_type')",
            ),
            "idx_work_location_exact": (
                "work_location",
                "json_extract(payload_json, '$.canonical_key'), json_extract(payload_json, '$.location_type'), json_extract(payload_json, '$.url')",
            ),
            "idx_work_version_key": ("work_version", "json_extract(payload_json, '$.version_key')"),
            "idx_provenance_source_field": (
                "field_provenance",
                "json_extract(payload_json, '$.venue_id'), json_extract(payload_json, '$.source_native_id'), json_extract(payload_json, '$.field_name')",
            ),
            "idx_coverage_venue_year": (
                "venue_year_coverage",
                "json_extract(payload_json, '$.venue_id'), json_extract(payload_json, '$.year')",
            ),
            "idx_watermark_venue": ("update_watermark", "json_extract(payload_json, '$.venue_id')"),
            "idx_repair_task_source_field": (
                "repair_task",
                "json_extract(payload_json, '$.venue_id'), json_extract(payload_json, '$.source_native_id'), json_extract(payload_json, '$.field_name')",
            ),
            "idx_venue_key": ("venue", "json_extract(payload_json, '$.venue_id')"),
            "idx_venue_state_key": ("venue_state", "json_extract(payload_json, '$.venue_id')"),
            "idx_thread_run_key": ("thread_run", "json_extract(payload_json, '$.thread_id')"),
            "idx_merge_event_key": ("merge_event", "json_extract(payload_json, '$.event_id')"),
        }
        for name, (table, expression) in indexes.items():
            connection.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({expression})")
        connection.commit()
    finally:
        connection.close()


def table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row[0] for row in rows}
    finally:
        connection.close()

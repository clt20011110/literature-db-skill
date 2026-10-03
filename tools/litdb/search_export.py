"""Export complete indexed paper metadata in common reference formats."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from .search import SearchError, lookup_connection


EXPORT_FORMATS = {
    "csv": ("text/csv", "csv"),
    "json": ("application/json", "json"),
    "bibtex": ("application/x-bibtex", "bib"),
    "ris": ("application/x-research-info-systems", "ris"),
}
MAX_EXPORT_IDS = 50
ID_PATTERN = re.compile(r"[a-f0-9]{64}\Z")
CSV_FIELDS = (
    "id", "title", "authors", "venue", "year", "venues", "abstract",
    "doi", "article_url", "landing_url", "pdf_url",
)


class UnknownPaperIds(ValueError):
    """Raised when an export request references papers outside the lookup DB."""


def normalize_ids(identities: object) -> list[str]:
    if not isinstance(identities, list) or not identities:
        raise ValueError("请至少选择一篇论文。")
    if len(identities) > MAX_EXPORT_IDS:
        raise ValueError(f"一次最多导出 {MAX_EXPORT_IDS} 篇论文。")
    result = []
    seen = set()
    for identity in identities:
        if not isinstance(identity, str) or not ID_PATTERN.fullmatch(identity):
            raise ValueError("论文 ID 格式无效。")
        if identity not in seen:
            seen.add(identity)
            result.append(identity)
    return result


def load_export_records(home: Path, identities: object) -> list[dict]:
    """Read full metadata from the local lookup DB, in request order."""
    ordered_ids = normalize_ids(identities)
    placeholders = ",".join("?" for _ in ordered_ids)
    with lookup_connection(home) as connection:
        rows = connection.execute(
            f"SELECT id,payload_json FROM papers WHERE id IN ({placeholders})",
            ordered_ids,
        ).fetchall()
    by_id = {}
    for row in rows:
        try:
            paper = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise SearchError("本地论文元数据无法读取，请重新建立索引。") from exc
        if not isinstance(paper, dict):
            raise SearchError("本地论文元数据格式无效，请重新建立索引。")
        by_id[row["id"]] = paper
    missing = [identity for identity in ordered_ids if identity not in by_id]
    if missing:
        raise UnknownPaperIds("部分论文已不在当前索引中，请重新检索后再导出。")

    records = []
    for identity in ordered_ids:
        paper = by_id[identity]
        records.append({
            "id": identity,
            "title": _as_text(paper.get("title")),
            "authors": _as_text_list(paper.get("authors")),
            "venue": _as_text(paper.get("venue")),
            "year": paper.get("year") if isinstance(paper.get("year"), int) else "",
            "venues": _normalize_venues(paper.get("venues")),
            "abstract": _as_text(paper.get("abstract")),
            "doi": _as_text(paper.get("doi")),
            "article_url": _as_text(paper.get("article_url")),
            "landing_url": _as_text(paper.get("landing_url")),
            "pdf_url": _as_text(paper.get("pdf_url")),
        })
    return records


def build_export(records: list[dict], export_format: object) -> tuple[bytes, str, str]:
    """Return download bytes, MIME type, and a safe attachment filename."""
    if not isinstance(export_format, str) or export_format not in EXPORT_FORMATS:
        raise ValueError("导出格式应为 CSV、JSON、BibTeX 或 RIS。")
    content_type, extension = EXPORT_FORMATS[export_format]
    if export_format == "csv":
        content = _to_csv(records).encode("utf-8-sig")
    elif export_format == "json":
        content = (json.dumps(records, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    elif export_format == "bibtex":
        content = _to_bibtex(records).encode("utf-8")
    else:
        content = _to_ris(records).encode("utf-8")
    return content, content_type, f"litdb-search-results.{extension}"


def _to_csv(records: list[dict]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for record in records:
        row = dict(record)
        row["authors"] = "; ".join(record.get("authors", []))
        row["venues"] = "; ".join(
            f"{edition['id']} ({edition['year']})" for edition in record.get("venues", [])
        )
        writer.writerow({field: _spreadsheet_safe(row.get(field, "")) for field in CSV_FIELDS})
    return stream.getvalue()


def _spreadsheet_safe(value: object) -> str:
    text = _as_text(value)
    if re.match(r"^[\s\x00-\x1f]*[=+\-@]", text):
        return "'" + text
    return text


def _to_bibtex(records: list[dict]) -> str:
    entries = []
    for record in records:
        fields = [
            ("title", record.get("title")),
            ("author", " and ".join(record.get("authors", []))),
            ("year", record.get("year")),
            ("venue", record.get("venue")),
            ("abstract", record.get("abstract")),
            ("doi", record.get("doi")),
            ("url", record.get("article_url") or record.get("landing_url")),
            ("pdf", record.get("pdf_url")),
        ]
        rendered = [f"  {name} = {{{_bibtex_escape(value)}}}" for name, value in fields if _as_text(value)]
        citation_key = f"litdb_{record['id'][:12]}"
        entries.append("@misc{" + citation_key + ",\n" + ",\n".join(rendered) + "\n}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def _bibtex_escape(value: object) -> str:
    substitutions = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "#": r"\#",
        "%": r"\%",
        "_": r"\_",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
    }
    return "".join(substitutions.get(char, char) for char in _as_text(value))


def _to_ris(records: list[dict]) -> str:
    lines = []
    for record in records:
        lines.append("TY  - GEN")
        _ris_field(lines, "TI", record.get("title"))
        for author in record.get("authors", []):
            _ris_field(lines, "AU", author)
        _ris_field(lines, "T2", record.get("venue"))
        _ris_field(lines, "PY", record.get("year"))
        _ris_field(lines, "AB", record.get("abstract"))
        _ris_field(lines, "DO", record.get("doi"))
        _ris_field(lines, "UR", record.get("article_url") or record.get("landing_url"))
        _ris_field(lines, "L1", record.get("pdf_url"))
        lines.append("ER  -")
        lines.append("")
    return "\n".join(lines)


def _ris_field(lines: list[str], tag: str, value: object) -> None:
    text = _as_text(value)
    if text:
        # RIS uses one field per line. Flatten metadata line breaks to avoid
        # injecting a new tag or record while retaining the complete abstract.
        text = re.sub(r"[\r\n\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", text)
        lines.append(f"{tag}  - {text}")


def _normalize_venues(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        venue = _as_text(item.get("id"))
        year = item.get("year")
        if venue:
            result.append({"id": venue, "year": year if isinstance(year, int) else ""})
    return result


def _as_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_as_text(item) for item in value if _as_text(item)]


def _as_text(value: object) -> str:
    return "" if value is None else str(value)

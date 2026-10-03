#!/usr/bin/env python3
"""Checkpointed ICLR virtual-paper detail runner.

The accepted ICLR manifest already contains canonical ``iclr.cc/virtual``
landing URLs for 2020 onward.  This runner reuses that identity set, fetches
only those official visible HTML pages, and writes metadata in one process.
Older 2015--2019 OpenReview identities remain pending and are never silently
converted into exclusions.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


SCHEMA = "literature-metadata-staging-v1"
VENUE = "iclr"
ALLOWED_HOSTS = {"iclr.cc", "openreview.net"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_url(value: str) -> str:
    p = urlsplit(value)
    # OpenReview identifies papers in the query, unlike ICLR virtual pages.
    # Retain only public identity fields, never session or tracking parameters.
    query = ""
    if (p.hostname or "").lower() == "openreview.net":
        query = urlencode([(key, val) for key, val in parse_qsl(p.query)
                           if key in {"id", "name"}])
    return urlunsplit((p.scheme, p.netloc, p.path, query, ""))


def official_url(value: str) -> bool:
    try:
        p = urlsplit(value)
    except ValueError:
        return False
    return p.scheme == "https" and (p.hostname or "").lower().rstrip(".") in ALLOWED_HOSTS


def clean(value: str | None) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).replace("\xa0", " ").split()).strip()


def meta(body: str, name: str) -> str:
    pat = re.escape(name)
    patterns = [
        rf'<meta[^>]+(?:name|property)=["\']{pat}["\'][^>]*content=["\']([^"\']*)',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\']{pat}["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, body, re.IGNORECASE)
        if m:
            return clean(m.group(1))
    return ""


def metas(body: str, name: str) -> list[str]:
    """Return all meta values, accepting either attribute order."""
    pat = re.escape(name)
    values: list[str] = []
    for pattern in (
        rf'<meta[^>]+(?:name|property)=["\']{pat}["\'][^>]*content=["\']([^"\']*)',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\']{pat}["\']',
    ):
        values.extend(clean(x) for x in re.findall(pattern, body, re.IGNORECASE))
    return [x for x in values if x]


def class_text(body: str, class_name: str) -> str:
    m = re.search(
        rf'<[^>]+class=["\'][^"\']*\b{re.escape(class_name)}\b[^"\']*["\'][^>]*>(.*?)</[^>]+>',
        body,
        re.IGNORECASE | re.DOTALL,
    )
    return clean(m.group(1)) if m else ""


def jsonld_date(body: str) -> str:
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', body, re.IGNORECASE | re.DOTALL):
        try:
            value = json.loads(html.unescape(raw))
        except Exception:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and item.get("datePublished"):
                return str(item["datePublished"])
    return ""


def jsonld_record(body: str) -> dict:
    """Read the public schema.org CreativeWork block on virtual poster pages."""
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', body, re.IGNORECASE | re.DOTALL):
        try:
            value = json.loads(html.unescape(raw))
        except Exception:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and (item.get("name") or item.get("author")):
                return item
    return {}


def hrefs(body: str, base: str) -> list[str]:
    out: list[str] = []
    for raw in re.findall(r'<a\b[^>]+href=["\']([^"\']+)', body, re.IGNORECASE):
        url = canonical_url(urljoin(base, html.unescape(raw)))
        if url and url not in out:
            out.append(url)
    return out


def paper_pdf_url(value: str, base: str, *, explicitly_paper: bool = False) -> str:
    """Accept paper links, excluding presentation PDFs and incomplete identities."""
    url = canonical_url(urljoin(base, html.unescape(value)))
    if not official_url(url):
        return ""
    p = urlsplit(url)
    if re.search(r"/(?:slides?|posters?|supplement(?:ary|al)?)/", p.path, re.I):
        return ""
    if p.hostname == "openreview.net":
        params = parse_qs(p.query)
        if len(params.get("id", [])) != 1 or not params["id"][0].strip():
            return ""
        if p.path == "/pdf" or (p.path == "/attachment" and params.get("name") == ["pdf"]):
            return url
        return ""
    return url if explicitly_paper and p.path.lower().endswith(".pdf") else ""


def discover_paper_pdf(body: str, base: str) -> str:
    cited = meta(body, "citation_pdf_url")
    if cited:
        pdf = paper_pdf_url(cited, base, explicitly_paper=True)
        if pdf:
            return pdf
    for attrs, raw, label_html in re.findall(
        r'<a\b([^>]*?\bhref=["\']([^"\']+)["\'][^>]*)>(.*?)</a>',
        body, re.I | re.S,
    ):
        label = clean(label_html)
        if re.search(r"\b(?:slides?|posters?|supplement(?:ary|al)?)\b", label, re.I):
            continue
        explicit = bool(re.fullmatch(r"(?:download\s+)?(?:paper(?:\s+pdf)?|pdf|full\s+(?:paper|text))", label, re.I))
        pdf = paper_pdf_url(raw, base, explicitly_paper=explicit)
        if pdf:
            return pdf
    return ""


def parse_page(body: str, item: dict) -> dict:
    url = canonical_url(str(item["landing_url"]))
    schema = jsonld_record(body)
    title = meta(body, "citation_title") or clean(str(schema.get("name") or "")) or class_text(body, "event-title")
    if not title:
        m = re.search(r"<h1\b[^>]*>(.*?)</h1>", body, re.IGNORECASE | re.DOTALL)
        title = clean(m.group(1)) if m else ""
    authors = metas(body, "citation_author")
    if not authors:
        raw_authors = schema.get("author")
        if isinstance(raw_authors, list):
            for author in raw_authors:
                name = author.get("name") if isinstance(author, dict) else author
                if clean(str(name or "")):
                    authors.append(clean(str(name)))
    if not authors:
        authors_text = class_text(body, "event-organizers")
        authors = [clean(x) for x in re.split(r"\s*[⋅·•]\s*", authors_text) if clean(x)]
    abstract = meta(body, "citation_abstract") or class_text(body, "abstract-text-inner")
    if not abstract:
        abstract = class_text(body, "abstract")
    publication_date = meta(body, "citation_publication_date") or clean(str(schema.get("datePublished") or "")) or jsonld_date(body)
    pdf = discover_paper_pdf(body, url)
    if not title or not authors:
        raise ValueError("required visible title/authors marker missing")
    expected_title = clean(str(item.get("title") or "")).casefold()
    if title.casefold() != expected_title:
        raise ValueError(f"title conflict: {title!r} != {item.get('title')!r}")
    return {
        "title": title,
        "authors": authors,
        "abstract": abstract or None,
        "publication_date": publication_date or None,
        "pdf_url": pdf or None,
    }


def fetch(item: dict, timeout: float, retries: int, delay: float) -> dict:
    url = canonical_url(str(item["landing_url"]))
    if not official_url(url):
        return {"kind": "pending", "item": item, "error_code": "non_official_detail_url", "observed_at": now()}
    if delay:
        time.sleep(delay)
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(
                [
                    "curl", "--fail", "--silent", "--show-error", "--location",
                    "--max-time", str(max(5, int(timeout))), "-A", "literature-db-iclr-virtual/1.0", url,
                ],
                capture_output=True,
                timeout=timeout + 5,
            )
            body = proc.stdout.decode("utf-8", "replace")
            if proc.returncode != 0 or not body:
                last_error = proc.stderr.decode("utf-8", "replace").strip() or f"curl_exit_{proc.returncode}"
                if attempt < retries:
                    time.sleep(min(8.0, 1.5**attempt))
                    continue
                return {"kind": "pending", "item": item, "error_code": "official_detail_fetch_failed", "error": last_error, "observed_at": now()}
            lower = body.lower()
            if any(term in lower for term in ("captcha", "verify you are human", "checking your browser", "access denied")) and not re.search(r"event-title|abstract-text-inner", lower):
                return {"kind": "pending", "item": item, "error_code": "official_detail_access_challenge", "observed_at": now(), "page_sha256": hashlib.sha256(body.encode()).hexdigest()}
            parsed = parse_page(body, item)
            return {
                "kind": "staged", "item": item, "parsed": parsed,
                "observed_at": now(), "http_status": 200,
                "final_url": url, "page_sha256": hashlib.sha256(body.encode()).hexdigest(),
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(min(8.0, 1.5**attempt))
    return {"kind": "pending", "item": item, "error_code": "official_detail_fetch_failed", "error": last_error, "observed_at": now()}


def provenance(field: str, value, source_url: str, observed_at: str, status: str = "present", reason: str | None = None) -> dict:
    out = {"source_url": canonical_url(source_url), "observed_at": observed_at, "method": "official_iclr_virtual_visible_html", "status": status}
    if reason:
        out["missing_reason"] = reason
    return out


def make_row(result: dict) -> dict:
    item = result["item"]
    parsed = result["parsed"]
    url = canonical_url(str(item["landing_url"]))
    observed = str(result["observed_at"])
    doi = None
    publication_date = parsed.get("publication_date")
    pdf = parsed.get("pdf_url")
    missing = {"doi": {"reason_code": "not_present_on_official_page"}}
    if not parsed.get("abstract"):
        missing["abstract"] = {"reason_code": "not_present_on_official_page"}
    if not publication_date:
        missing["publication_date"] = {"reason_code": "not_present_on_official_page"}
    if not pdf:
        missing["pdf_url"] = {"reason_code": "not_visible"}
    prov = {
        "source_native_id": provenance("source_native_id", item["source_native_id"], url, observed),
        "title": provenance("title", parsed["title"], url, observed),
        "authors": provenance("authors", parsed["authors"], url, observed),
        "abstract": provenance("abstract", parsed.get("abstract"), url, observed, "present" if parsed.get("abstract") else "checked_missing", None if parsed.get("abstract") else "not_present_on_official_page"),
        "year": provenance("year", item["year"], url, observed),
        "document_type": provenance("document_type", "research-paper", url, observed),
        "landing_url": provenance("landing_url", url, url, observed),
        "publication_date": provenance("publication_date", publication_date, url, observed, "present" if publication_date else "checked_missing", None if publication_date else "not_present_on_official_page"),
        "doi": provenance("doi", doi, url, observed, "checked_missing", "not_present_on_official_page"),
        "pdf_url": provenance("pdf_url", pdf, url, observed, "present" if pdf else "checked_missing", None if pdf else "not_visible"),
        "pdf_discovery_status": provenance("pdf_discovery_status", "visible_url" if pdf else "not_visible", url, observed, "present" if pdf else "checked_missing", None if pdf else "not_visible"),
    }
    return {
        "schema_version": SCHEMA,
        "artifact_kind": "validated_detail_staging_row",
        "venue_id": VENUE,
        "year": int(item["year"]),
        "source_native_id": item["source_native_id"],
        "native_id": item.get("native_id"),
        "title": parsed["title"],
        "authors": parsed["authors"],
        "abstract": parsed["abstract"],
        "document_type": "research-paper",
        "track": item.get("track") or "main",
        "landing_url": url,
        "source_url": url,
        "source_page_url": item.get("source_page_url") or item.get("source_url") or url,
        "publication_date": publication_date,
        "doi": doi,
        "doi_status": "not_present_on_official_page",
        "pdf_url": pdf,
        "pdf_discovery_status": "visible_url" if pdf else "not_visible",
        "observed_at": observed,
        "inclusion_decision": "include",
        "missing_fields": missing,
        "field_provenance": prov,
        "detail_fetch": {"http_status": result.get("http_status"), "final_url": result.get("final_url"), "page_sha256": result.get("page_sha256")},
        "source_reuse_policy": {"expected_identity_reused": True, "listing_not_promoted_to_detail": True, "detail_page_revalidated": True},
    }


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--batch-size", type=int, default=100)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--timeout", type=float, default=35)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args()
    root = args.root.expanduser().resolve()
    run = args.run.expanduser().resolve()
    manifest = read_jsonl(root / "official_listing_manifest.jsonl")
    expected = [
        item for item in manifest
        if item.get("venue_id") == VENUE
        and item.get("include_decision") == "include_candidate"
        and 2020 <= int(item.get("year", 0)) <= 2026
        and str(item.get("landing_url", "")).startswith("https://iclr.cc/virtual/")
    ]
    expected.sort(key=lambda x: (int(x.get("year", 0)), str(x.get("source_native_id"))))
    stage_path = run / "metadata_staging.jsonl"
    evidence_path = run / "browser_evidence.jsonl"
    errors_path = run / "virtual_detail_errors.jsonl"
    checkpoint_path = run / "virtual_detail_checkpoint.json"
    summary_path = run / "virtual_detail_summary.json"
    staging = {str(x.get("source_native_id")): x for x in read_jsonl(stage_path) if x.get("source_native_id")}
    evidence = {str(x.get("source_native_id")): x for x in read_jsonl(evidence_path) if x.get("source_native_id")}
    errors = {str(x.get("source_native_id")): x for x in read_jsonl(errors_path) if x.get("source_native_id")}
    order = {str(x["source_native_id"]): i for i, x in enumerate(expected)}
    previous = -1
    pass_no = 0
    while True:
        pending = [x for x in expected if str(x["source_native_id"]) not in staging]
        if not pending:
            status = "COMPLETE_VIRTUAL_SURFACE"
            write_json(summary_path, {"schema_version": "iclr-virtual-detail-summary-v1", "status": status, "expected_virtual": len(expected), "staged_virtual": len(expected), "pending_virtual": 0, "older_openreview_pending": True, "generated_at": now()})
            write_json(checkpoint_path, {"schema_version": "iclr-virtual-detail-checkpoint-v1", "status": status, "expected_virtual": len(expected), "staged_virtual": len(expected), "pending_virtual": 0, "older_openreview_pending": True, "catalog_write": False, "generated_at": now()})
            return 0
        pass_no += 1
        batch = pending[: max(1, args.batch_size)]
        print(json.dumps({"event": "batch_start", "pass": pass_no, "batch": len(batch), "staged_virtual": len(expected) - len(pending), "pending_virtual": len(pending)}), flush=True)
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=max(1, min(8, args.workers))) as pool:
            futures = {pool.submit(fetch, item, args.timeout, args.retries, args.delay): str(item["source_native_id"]) for item in batch}
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()
        for item in batch:
            sid = str(item["source_native_id"])
            result = results[sid]
            if result.get("kind") == "staged":
                row = make_row(result)
                staging[sid] = row
                evidence[sid] = {"schema_version": "iclr-virtual-detail-evidence-v1", "venue_id": VENUE, "source_native_id": sid, "year": item["year"], "source_url": canonical_url(item["landing_url"]), "observed_at": result["observed_at"], "http_status": result.get("http_status"), "page_sha256": result.get("page_sha256"), "detail_status": "PASS", "fields_present": {"title": True, "authors": True, "abstract": True, "publication_date": bool(row.get("publication_date")), "doi": False, "pdf_url": bool(row.get("pdf_url"))}}
                errors.pop(sid, None)
            else:
                errors[sid] = {"schema_version": "iclr-virtual-detail-error-v1", "venue_id": VENUE, "source_native_id": sid, "year": item["year"], "source_url": canonical_url(item["landing_url"]), "observed_at": result.get("observed_at"), "status": "PENDING", "error_code": result.get("error_code"), "error": result.get("error"), "page_sha256": result.get("page_sha256")}
        atomic_jsonl(stage_path, [staging[sid] for sid in sorted(staging, key=lambda x: order.get(x, 10**9))])
        atomic_jsonl(evidence_path, [evidence[sid] for sid in sorted(evidence, key=lambda x: order.get(x, 10**9))])
        atomic_jsonl(errors_path, [errors[sid] for sid in sorted(errors, key=lambda x: order.get(x, 10**9))])
        current = len([sid for sid in staging if sid in order])
        pending_count = len(expected) - current
        write_json(checkpoint_path, {"schema_version": "iclr-virtual-detail-checkpoint-v1", "status": "RUNNING_VIRTUAL_DETAIL", "expected_virtual": len(expected), "staged_virtual": current, "pending_virtual": pending_count, "older_openreview_pending": True, "last_batch": [str(x["source_native_id"]) for x in batch], "error_count": len(errors), "next_source_native_id": next((str(x["source_native_id"]) for x in expected if str(x["source_native_id"]) not in staging), None), "catalog_write": False, "generated_at": now()})
        write_json(summary_path, {"schema_version": "iclr-virtual-detail-summary-v1", "status": "PARTIAL", "expected_virtual": len(expected), "staged_virtual": current, "pending_virtual": pending_count, "older_openreview_pending": True, "error_count": len(errors), "generated_at": now()})
        print(json.dumps({"event": "batch_done", "pass": pass_no, "staged_virtual": current, "pending_virtual": pending_count, "errors": len(errors)}), flush=True)
        if not args.loop or current <= previous:
            return 0 if not pending_count else 3
        previous = current
        time.sleep(max(0.0, args.pause))


if __name__ == "__main__":
    raise SystemExit(main())

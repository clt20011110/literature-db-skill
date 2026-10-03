#!/usr/bin/env python3
"""Build a controller-only CoRL shadow replay from public PMLR batch surfaces.

This is an audit aid while the required fresh Luna replay is unavailable.  It
never substitutes for, ingests as, or advances the fresh-thread replay gate.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

try:
    from litdb.replay import verify_replay_output
except ModuleNotFoundError:  # imported as tools.build_corl_shadow_replay in tests
    from tools.litdb.replay import verify_replay_output


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_RELATIVE = (
    Path("runs")
    / "bootstrap-20260820T081741.316646Z"
    / "venues"
    / "corl"
    / "e95b5632-cea7-4f7f-9ee8-80373f5b7d23"
)
VOLUMES = {2017: 78, 2020: 155, 2021: 164, 2025: 305}
CONTROLLER_THREAD = "01a01e34-e35f-7aa1-bc1a-41a8b3d52776"
REQUEST_ID = "e95b5632-cea7-4f7f-9ee8-80373f5b7d23"
TLS_CONTEXT = ssl.create_default_context()


def resolve_artifact_paths(
    home: Path | None = None,
    run_root: Path | None = None,
    pilot_manifest: Path | None = None,
    pilot_samples: Path | None = None,
    output_root: Path | None = None,
) -> tuple[Path, Path, Path, Path, Path]:
    selected_home = home or os.environ.get("LITDB_HOME")
    data_home = (
        Path(selected_home).expanduser().resolve()
        if selected_home
        else (PROJECT_ROOT / "data" / "literature-db").resolve()
    )
    selected_run_root = (
        Path(run_root).expanduser().resolve() if run_root else data_home / DEFAULT_RUN_RELATIVE
    )
    selected_manifest = (
        Path(pilot_manifest).expanduser().resolve()
        if pilot_manifest
        else data_home / "recipes" / "corl" / "pilot_manifest.jsonl"
    )
    selected_samples = (
        Path(pilot_samples).expanduser().resolve()
        if pilot_samples
        else data_home / "recipes" / "corl" / "pilot_sample_metadata.jsonl"
    )
    selected_output = (
        Path(output_root).expanduser().resolve()
        if output_root
        else selected_run_root / "controller_shadow"
    )
    return data_home, selected_run_root, selected_manifest, selected_samples, selected_output


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_text(value: str) -> str:
    parser = TextExtractor()
    parser.feed(value)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def fetch(url: str, delay: float, last_request: list[float]) -> tuple[bytes, dict[str, str]]:
    if last_request:
        wait = delay - (time.monotonic() - last_request[0])
        if wait > 0:
            time.sleep(wait)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "literature-db-controller-shadow/1.0 (+public HTML audit; no PDF download)",
            "Accept": "text/html,application/x-bibtex,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30, context=TLS_CONTEXT) as response:
        body = response.read()
        headers = {key.lower(): value for key, value in response.headers.items()}
    last_request[:] = [time.monotonic()]
    return body, headers


def first_group(pattern: str, value: str) -> str | None:
    match = re.search(pattern, value, flags=re.I | re.S)
    return match.group(1) if match else None


def publication_date_from_volume(value: str) -> str | None:
    raw = first_group(r"Published as Volume\s+\d+.*?on\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})", value)
    if not raw:
        return None
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def normalize_url(url: str, base: str) -> str:
    return urllib.parse.urljoin(base, html.unescape(url)).replace("http://proceedings.mlr.press/", "https://proceedings.mlr.press/")


def parse_volume(year: int, volume: int, value: str, observed_at: str) -> tuple[list[dict], dict]:
    base = f"https://proceedings.mlr.press/v{volume}/"
    release_date = publication_date_from_volume(value)
    token_re = re.compile(
        r"<h3\b[^>]*>(?P<section>.*?)</h3>|<div\s+class=[\"']paper[\"'][^>]*>(?P<paper>.*?)</div>",
        flags=re.I | re.S,
    )
    section: str | None = None
    rows: list[dict] = []
    for token in token_re.finditer(value):
        if token.group("section") is not None:
            section = clean_text(token.group("section")) or None
            continue
        block = token.group("paper") or ""
        title = clean_text(first_group(r"<p\s+class=[\"']title[\"'][^>]*>(.*?)</p>", block) or "")
        authors_text = clean_text(first_group(r"<span\s+class=[\"']authors[\"'][^>]*>(.*?)</span>", block) or "")
        authors = [item.strip() for item in authors_text.split(",") if item.strip()]
        info = clean_text(first_group(r"<span\s+class=[\"']info[\"'][^>]*>(.*?)</span>", block) or "")
        landing_url = None
        pdf_url = None
        for href, anchor_text in re.findall(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", block, flags=re.I | re.S):
            label = clean_text(anchor_text).casefold()
            target = normalize_url(href, base)
            if label == "abs":
                landing_url = target
            elif "download pdf" in label:
                pdf_url = target
        if not landing_url:
            continue
        slug = Path(urllib.parse.urlsplit(landing_url).path).stem
        page_match = re.search(r"PMLR\s+\d+:([0-9]+(?:[-–—]+[0-9]+)?)", info)
        page_range = page_match.group(1).replace("–", "-").replace("—", "-") if page_match else None
        document_type = "blue-sky-paper" if (section or "").casefold() == "blue sky papers" else "research-paper"
        rows.append(
            {
                "venue_id": "corl",
                "selected_unit_id": f"corl:{year}:pmlr-v{volume}",
                "year": year,
                "conference_year": year,
                "listing_position": len(rows) + 1,
                "source_item_id": f"pmlr-v{volume}-{slug}",
                "landing_url": landing_url,
                "source_page_url": base,
                "source_url": base,
                "title": title,
                "authors": authors,
                "document_type": document_type,
                "include_decision": "include",
                "inclusion_rule_id": "corl-formal-pmlr-main-volume",
                "observed_at": observed_at,
                "section": section,
                "pmlr_volume": volume,
                "pmlr_page_range": page_range,
                "pmlr_release_date": release_date,
                "pdf_url_observed": pdf_url,
                "thread_id": CONTROLLER_THREAD,
                "actual_thread_id": CONTROLLER_THREAD,
                "request_id": REQUEST_ID,
                "capture_role": "controller-shadow-replay-not-fresh-thread-gate",
            }
        )
    evidence = {
        "venue_id": "corl",
        "year": year,
        "pmlr_volume": volume,
        "url": base,
        "observed_at": observed_at,
        "article_count": len(rows),
        "unique_landing_count": len({row["landing_url"] for row in rows}),
        "publication_date": release_date,
        "body_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "first_landing_url": rows[0]["landing_url"] if rows else None,
        "last_landing_url": rows[-1]["landing_url"] if rows else None,
        "section_counts": {
            str(name): sum(1 for row in rows if row.get("section") == name)
            for name in sorted({row.get("section") for row in rows}, key=lambda item: str(item))
        },
        "capture_role": "controller-shadow-replay-not-fresh-thread-gate",
    }
    return rows, evidence


def parse_bibtex(value: str) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    cursor = 0
    while True:
        match = re.search(r"@InProceedings\s*\{\s*([^,\s]+)\s*,", value[cursor:], flags=re.I)
        if not match:
            break
        key = match.group(1)
        start = cursor + match.end()
        depth = 1
        index = start
        while index < len(value) and depth:
            if value[index] == "{":
                depth += 1
            elif value[index] == "}":
                depth -= 1
            index += 1
        body = value[start:index - 1]
        fields: dict[str, str] = {}
        pos = 0
        while pos < len(body):
            field = re.search(r"([A-Za-z][A-Za-z0-9_-]*)\s*=\s*", body[pos:])
            if not field:
                break
            name = field.group(1).casefold()
            pos += field.end()
            if pos >= len(body):
                break
            if body[pos] == "{":
                depth = 1
                end = pos + 1
                while end < len(body) and depth:
                    if body[end] == "{":
                        depth += 1
                    elif body[end] == "}":
                        depth -= 1
                    end += 1
                fields[name] = body[pos + 1:end - 1].strip()
                pos = end
            elif body[pos] == '"':
                end = pos + 1
                while end < len(body) and body[end] != '"':
                    end += 1
                fields[name] = body[pos + 1:end].strip()
                pos = end + 1
            else:
                end = body.find(",", pos)
                if end < 0:
                    end = len(body)
                fields[name] = body[pos:end].strip()
                pos = end + 1
        entries[key] = fields
        cursor = index
    return entries


def pdf_location(url: str | None) -> dict:
    if not url:
        return {
            "action_visible": False,
            "url": None,
            "location_type": "missing",
            "validated": False,
            "followed": False,
            "external_target_not_stored": False,
            "missing_reason": "official_page_has_no_pdf_action",
        }
    host = urllib.parse.urlsplit(url).hostname or ""
    allowed = host == "proceedings.mlr.press"
    return {
        "action_visible": True,
        "url": url if allowed else None,
        "location_type": "direct_public_pdf_action" if allowed else "resolution_recipe",
        "validated": True,
        "followed": False,
        "external_target_not_stored": not allowed,
        "missing_reason": None if allowed else "external_pdf_link_not_in_allowlist_or_not_stable",
    }


def build_sample(row: dict, bib: dict[str, str], observed_at: str) -> dict:
    location = pdf_location(row.get("pdf_url_observed"))
    return {
        "venue_id": "corl",
        "source_item_id": row["source_item_id"],
        "landing_url": row["landing_url"],
        "source_url": row["landing_url"],
        "source_page_url": row["source_page_url"],
        "title": row["title"],
        "authors": row["authors"],
        "document_type": row["document_type"],
        "doi": bib.get("doi") or None,
        "abstract": bib.get("abstract"),
        "publication_date": row.get("pmlr_release_date"),
        "date": row.get("pmlr_release_date"),
        "pdf_location": location,
        "pdf_url": location.get("url"),
        "year": row["year"],
        "conference_year": row["year"],
        "listing_position": row["listing_position"],
        "section": row.get("section"),
        "pmlr_volume": row["pmlr_volume"],
        "observed_at": observed_at,
        "thread_id": CONTROLLER_THREAD,
        "actual_thread_id": CONTROLLER_THREAD,
        "request_id": REQUEST_ID,
        "capture_role": "controller-shadow-replay-not-fresh-thread-gate",
        "field_provenance": {
            "title_authors_landing_pdf": row["source_page_url"],
            "abstract_doi": f"{row['source_page_url']}assets/bib/bibliography.bib",
            "publication_date": row["source_page_url"],
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay-seconds", type=float, default=10.5)
    parser.add_argument(
        "--home",
        type=Path,
        help="LitDB data directory (defaults to LITDB_HOME or the skill data directory)",
    )
    parser.add_argument("--run-root", type=Path, help="Override the CoRL shadow run directory")
    parser.add_argument(
        "--pilot-manifest", type=Path, help="Override recipes/corl/pilot_manifest.jsonl"
    )
    parser.add_argument(
        "--pilot-samples", type=Path, help="Override recipes/corl/pilot_sample_metadata.jsonl"
    )
    parser.add_argument("--output-root", type=Path, help="Override the controller shadow output directory")
    args = parser.parse_args(argv)
    _, run_root, pilot_manifest, pilot_samples, output_root = resolve_artifact_paths(
        home=args.home,
        run_root=args.run_root,
        pilot_manifest=args.pilot_manifest,
        pilot_samples=args.pilot_samples,
        output_root=args.output_root,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    observed_at = utc_now()
    last_request: list[float] = []
    manifest: list[dict] = []
    listing_evidence: list[dict] = []
    bib_by_volume: dict[int, dict[str, dict[str, str]]] = {}
    fetch_evidence: list[dict] = []

    for year, volume in VOLUMES.items():
        url = f"https://proceedings.mlr.press/v{volume}/"
        body, headers = fetch(url, args.delay_seconds, last_request)
        value = body.decode("utf-8")
        rows, evidence = parse_volume(year, volume, value, observed_at)
        manifest.extend(rows)
        listing_evidence.append(evidence)
        fetch_evidence.append({"url": url, "bytes": len(body), "sha256": sha256_bytes(body), "content_type": headers.get("content-type")})

        bib_url = f"{url}assets/bib/bibliography.bib"
        bib_body, bib_headers = fetch(bib_url, args.delay_seconds, last_request)
        bib_by_volume[volume] = parse_bibtex(bib_body.decode("utf-8"))
        fetch_evidence.append({"url": bib_url, "bytes": len(bib_body), "sha256": sha256_bytes(bib_body), "content_type": bib_headers.get("content-type")})

    current_evidence = []
    for url in ("https://www.corl.org/", "https://proceedings.mlr.press/"):
        body, headers = fetch(url, args.delay_seconds, last_request)
        text = clean_text(body.decode("utf-8", errors="replace"))
        current_evidence.append(
            {
                "url": url,
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                "content_type": headers.get("content-type"),
                "corl_2026_occurrences": len(re.findall(r"CoRL\s+2026", text, flags=re.I)),
                "pmlr_v2026_corl_occurrences": len(re.findall(r"Proceedings of CoRL\s+2026", text, flags=re.I)),
                "observed_at": observed_at,
            }
        )

    pilot_samples_rows = load_jsonl(pilot_samples)
    manifest_by_url = {row["landing_url"]: row for row in manifest}
    samples = []
    for expected in pilot_samples_rows:
        row = manifest_by_url.get(expected["landing_url"])
        if row is None:
            continue
        samples.append(build_sample(row, bib_by_volume[row["pmlr_volume"]].get(row["source_item_id"], {}), observed_at))

    write_jsonl(output_root / "replay_manifest.jsonl", manifest)
    write_jsonl(output_root / "replay_sample_metadata.jsonl", samples)
    write_json(output_root / "replay_report.json", {"venue_id": "corl", "status": "PASS", "shadow_only": True})
    verifier = verify_replay_output(
        output_root,
        "corl",
        pilot_manifest,
        pilot_samples,
        {"corl.org", "proceedings.mlr.press"},
    )
    report = {
        "schema_version": "controller-shadow-replay-v1",
        "venue_id": "corl",
        "request_id": REQUEST_ID,
        "controller_thread_id": CONTROLLER_THREAD,
        "generated_at": utc_now(),
        "status": verifier["status"],
        "shadow_only": True,
        "fresh_thread_gate_satisfied": False,
        "must_not_ingest_as_venue_replay": True,
        "reuse_policy": {
            "pilot_expected_identities_reused": True,
            "listing_and_bibliography_surfaces_refetched": True,
            "current_waterline_refetched": True,
            "catalog_ready": False,
        },
        "listing_evidence": listing_evidence,
        "current_evidence": current_evidence,
        "fetch_evidence": fetch_evidence,
        "verifier": verifier,
    }
    write_json(output_root / "shadow_replay_report.json", report)
    artifacts = []
    for path in sorted(output_root.iterdir()):
        if path.name == "output_manifest.json" or not path.is_file():
            continue
        artifacts.append(
            {
                "relative_path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "row_count": sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) if path.suffix == ".jsonl" else None,
            }
        )
    write_json(
        output_root / "output_manifest.json",
        {
            "schema_version": "output-manifest-v1",
            "manifest_type": "controller-shadow-replay-output",
            "generated_at": utc_now(),
            "venue_id": "corl",
            "request_id": REQUEST_ID,
            "status": verifier["status"],
            "shadow_only": True,
            "self_hash_excluded": True,
            "artifacts": artifacts,
        },
    )
    print(json.dumps({"status": verifier["status"], "manifest": len(manifest), "samples": len(samples), "set_agreement": verifier["set_agreement"], "field_agreement": verifier["field_agreement"], "errors": verifier["errors"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

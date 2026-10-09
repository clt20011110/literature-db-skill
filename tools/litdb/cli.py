from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .browser_preflight import check as preflight_check
from .constants import CONFIG_ROOT
from .count_baseline import verify_count_output
from .db import initialize as initialize_db
from .io import atomic_json, load_json, sha256_file, utc_now
from .metadata_pipeline import bootstrap_plan, merge_venue, reconcile_venue, validate_staging
from .paths import LitDBPaths
from .pilot import verify_pilot_output
from .recipe_models import validate as validate_recipe
from .replay import verify_replay_output
from .registry import install_registry, validate_runtime
from .security_scan import scan_path
from .state import create_controller_lock, initial_state, transition
from .thread_contracts import build_request, validate_receipt
from .verify import verify as verify_all


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def add_home(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", default=None, help="Data directory (overrides LITDB_HOME)")


def check_search_runtime(search_module) -> None:
    """Give CLI users an actionable error before starting the Node runtime."""
    node = shutil.which("node")
    if not node:
        raise search_module.SearchError(
            "本地检索需要 Node.js 22 或更高版本。请先安装 Node.js 22+，再安装 tools/search_runtime 的依赖。"
        )
    try:
        version = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise search_module.SearchError(f"无法读取 Node.js 版本：{exc}") from exc
    match = re.fullmatch(r"v?(\d+)\.\d+\.\d+", version)
    if not match:
        raise search_module.SearchError(f"无法识别 Node.js 版本：{version or '(empty output)'}")
    if int(match.group(1)) < 22:
        raise search_module.SearchError(
            f"本地检索需要 Node.js 22 或更高版本，当前版本为 {version}。"
        )
    dependency_marker = (
        search_module.RUNTIME / "node_modules" / "@zvec" / "zvec-grep" / "package.json"
    )
    if not dependency_marker.is_file():
        raise search_module.SearchError(
            "本地检索依赖尚未安装。请在 skill 的 tools/search_runtime 目录运行 `npm ci`。"
        )


def discovery_options(args: argparse.Namespace) -> dict:
    """Validate discovery bounds before starting a runtime or making HTTP requests."""
    if not args.query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= args.limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    if not 1 <= args.candidate_limit <= 500:
        raise ValueError("candidate-limit must be between 1 and 500")
    if args.candidate_limit < args.limit:
        raise ValueError("candidate-limit must be at least limit")
    if not math.isfinite(args.kev_timeout) or not 1 <= args.kev_timeout <= 3600:
        raise ValueError("kev-timeout must be between 1 and 3600 seconds")
    if not math.isfinite(args.unrelated_threshold) or not 0.5 <= args.unrelated_threshold <= 1:
        raise ValueError("unrelated-threshold must be between 0.5 and 1")
    if args.year_from is not None and args.year_to is not None and args.year_from > args.year_to:
        raise ValueError("year-from must not exceed year-to")
    if any(not value.strip() for value in args.retrieval_query):
        raise ValueError("retrieval-query must not be empty")
    if any(not value.strip() for value in args.keyword):
        raise ValueError("keyword must not be empty")
    if args.retriever in {"combined", "keyword"} and not args.keyword:
        raise ValueError("combined and keyword retrievers require explicit --keyword phrases")
    return dict(query=args.query, retrieval_queries=args.retrieval_query, keywords=args.keyword,
                candidate_limit=args.candidate_limit, limit=args.limit, venues=args.venue,
                year_from=args.year_from, year_to=args.year_to, retriever=args.retriever,
                kev_url=args.kev_url, kev_timeout=args.kev_timeout,
                unrelated_threshold=args.unrelated_threshold)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="litdb")
    sub = root.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    add_home(init)
    init.add_argument("--root-thread-id", default=os.environ.get("CODEX_THREAD_ID"))

    registry = sub.add_parser("registry")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    registry_validate = registry_sub.add_parser("validate")
    add_home(registry_validate)
    registry_validate.add_argument("--strict", action="store_true")

    preflight = sub.add_parser("preflight")
    add_home(preflight)
    preflight.add_argument("--browser-first", action="store_true")
    preflight.add_argument("--controller", default="sol-high")
    preflight.add_argument("--worker", default="luna-max")
    preflight.add_argument("--max-active-venues", type=int, default=1)
    preflight.add_argument("--strict", action="store_true")

    campaign = sub.add_parser("campaign")
    campaign_sub = campaign.add_subparsers(dest="campaign_command", required=True)
    campaign_start = campaign_sub.add_parser("start")
    add_home(campaign_start)
    campaign_start.add_argument("--controller", default="sol-high")
    campaign_start.add_argument("--worker", default="luna-max")
    campaign_start.add_argument("--browser-first", action="store_true")
    campaign_start.add_argument("--max-active-venues", type=int, default=1)
    campaign_start.add_argument("--root-thread-id", default=os.environ.get("CODEX_THREAD_ID"))

    state = sub.add_parser("state")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    state_transition = state_sub.add_parser("transition")
    add_home(state_transition)
    state_transition.add_argument("--venue", required=True)
    state_transition.add_argument("--from", dest="expected", required=True)
    state_transition.add_argument("--to", dest="target", required=True)
    state_transition.add_argument("--receipt", type=Path, required=True)

    thread = sub.add_parser("thread")
    thread_sub = thread.add_subparsers(dest="thread_command", required=True)
    thread_request = thread_sub.add_parser("request")
    add_home(thread_request)
    thread_request.add_argument("--venue", required=True)
    thread_request.add_argument("--role", required=True)
    thread_request.add_argument("--run", required=True)
    thread_request.add_argument("--parent-thread-id", default=os.environ.get("CODEX_THREAD_ID"))
    thread_ingest = thread_sub.add_parser("ingest")
    add_home(thread_ingest)
    thread_ingest.add_argument("--receipt", type=Path, required=True)

    recipe = sub.add_parser("recipe")
    recipe_sub = recipe.add_subparsers(dest="recipe_command", required=True)
    recipe_validate = recipe_sub.add_parser("validate")
    add_home(recipe_validate)
    recipe_validate.add_argument("--venue", required=True)
    recipe_validate.add_argument("--candidate", type=Path, required=True)
    recipe_lock = recipe_sub.add_parser("lock")
    add_home(recipe_lock)
    recipe_lock.add_argument("--venue", required=True)
    recipe_lock.add_argument("--review", type=Path, required=True)
    recipe_lock.add_argument("--replay-report", type=Path, required=True)

    count = sub.add_parser("count")
    count_sub = count.add_subparsers(dest="count_command", required=True)
    count_verify = count_sub.add_parser("verify")
    add_home(count_verify)
    count_verify.add_argument("--venue", required=True)
    count_verify.add_argument("--output-root", type=Path, required=True)
    count_verify.add_argument("--strict", action="store_true")
    count_publish = count_sub.add_parser("publish")
    add_home(count_publish)
    count_publish.add_argument("--venue", required=True)
    count_publish.add_argument("--output-root", type=Path, required=True)

    bootstrap = sub.add_parser("bootstrap")
    bootstrap_sub = bootstrap.add_subparsers(dest="bootstrap_command", required=True)
    bootstrap_plan_parser = bootstrap_sub.add_parser("plan")
    add_home(bootstrap_plan_parser)
    bootstrap_plan_parser.add_argument("--venue", required=True)

    staging = sub.add_parser("staging")
    staging_sub = staging.add_subparsers(dest="staging_command", required=True)
    staging_validate = staging_sub.add_parser("validate")
    add_home(staging_validate)
    staging_validate.add_argument("--venue", required=True)
    staging_validate.add_argument("--run", type=Path, required=True)
    staging_validate.add_argument("--input", type=Path)
    staging_validate.add_argument("--exclusions", type=Path)
    staging_validate.add_argument("--expected-root", type=Path)
    staging_validate.add_argument("--strict", action="store_true")

    merge = sub.add_parser("merge")
    merge_sub = merge.add_subparsers(dest="merge_command", required=True)
    merge_venue_parser = merge_sub.add_parser("venue")
    add_home(merge_venue_parser)
    merge_venue_parser.add_argument("--venue", required=True)
    merge_venue_parser.add_argument("--run", type=Path, required=True)
    merge_venue_parser.add_argument("--input", type=Path)
    merge_venue_parser.add_argument("--exclusions", type=Path)
    merge_venue_parser.add_argument("--expected-root", type=Path)
    merge_venue_parser.add_argument("--single-writer", action="store_true")
    merge_venue_parser.add_argument("--verify-before-commit", action="store_true")

    reconcile = sub.add_parser("reconcile")
    reconcile_sub = reconcile.add_subparsers(dest="reconcile_command", required=True)
    reconcile_venue_parser = reconcile_sub.add_parser("venue")
    add_home(reconcile_venue_parser)
    reconcile_venue_parser.add_argument("--venue", required=True)
    reconcile_venue_parser.add_argument("--run", type=Path)
    reconcile_venue_parser.add_argument("--expected-root", type=Path)
    reconcile_venue_parser.add_argument("--strict", action="store_true")

    security = sub.add_parser("security")
    security_sub = security.add_subparsers(dest="security_command", required=True)
    security_scan = security_sub.add_parser("scan")
    add_home(security_scan)
    security_scan.add_argument("--run")
    security_scan.add_argument("--path", type=Path)
    security_scan.add_argument("--strict", action="store_true")

    verify = sub.add_parser("verify")
    add_home(verify)
    verify.add_argument("--acceptance", default="browser-v2")
    verify.add_argument("--strict", action="store_true")

    search_parser = sub.add_parser("search", help="Search papers by research topic or idea")
    search_sub = search_parser.add_subparsers(dest="search_command", required=True)
    search_index = search_sub.add_parser("index", help="Build or incrementally refresh the local index")
    add_home(search_index)
    search_index.add_argument("--model", default="local/potion-multilingual-128m")
    search_index.add_argument("--rebuild", action="store_true")
    search_status = search_sub.add_parser("status")
    add_home(search_status)
    search_query = search_sub.add_parser("query")
    add_home(search_query)
    search_query.add_argument("query")
    search_query.add_argument("--limit", type=int, default=10)
    search_query.add_argument("--venue", action="append", default=[])
    search_query.add_argument("--year-from", type=int)
    search_query.add_argument("--year-to", type=int)
    search_query.add_argument("--mode", choices=["hybrid","semantic","keyword"], default="hybrid")
    search_query.add_argument("--format", choices=["json","markdown"], default="markdown")
    search_discover = search_sub.add_parser(
        "discover", help="Recall bounded candidates with explicit phrases, then rerank with local Kev"
    )
    add_home(search_discover)
    search_discover.add_argument("query", metavar="TOPIC")
    search_discover.add_argument("--retrieval-query", action="append", default=[],
                                 help="Additional topic-preserving retrieval query (repeatable)")
    search_discover.add_argument("--keyword", action="append", default=[],
                                 help="Literal technical phrase or acronym (repeatable; required for combined/keyword)")
    search_discover.add_argument("--candidate-limit", type=int, default=50,
                                 help="Maximum deduplicated candidates sent to Kev (1..500, at least --limit)")
    search_discover.add_argument("--limit", type=int, default=10, help="Maximum returned papers (1..50)")
    search_discover.add_argument("--venue", action="append", default=[])
    search_discover.add_argument("--year-from", type=int)
    search_discover.add_argument("--year-to", type=int)
    search_discover.add_argument("--retriever", choices=["combined", "zvec", "keyword"], default="combined")
    search_discover.add_argument("--kev-url", default=os.environ.get("LITDB_KEV_URL") or "http://127.0.0.1:8019",
                                 help="Local loopback Kev endpoint (default: LITDB_KEV_URL or port 8019)")
    search_discover.add_argument("--kev-timeout", type=float, default=180,
                                 help="Kev total reranking deadline in seconds (1..3600)")
    search_discover.add_argument("--unrelated-threshold", type=float, default=0.60,
                                 help="Remove only when unrelated wins and reaches this confidence (0.5..1)")
    search_discover.add_argument("--format", choices=["json", "markdown"], default="markdown")
    for action in ("serve","start"):
        serve_parser=search_sub.add_parser(action)
        add_home(serve_parser)
        serve_parser.add_argument("--port",type=int,default=8765)
        serve_parser.add_argument("--open",action="store_true")
    stop_parser=search_sub.add_parser("stop")
    add_home(stop_parser)
    return root


def handle(args: argparse.Namespace) -> int:
    paths = LitDBPaths.from_value(getattr(args, "home", None))
    if args.command == "search":
        from . import search
        try:
            if args.search_command == "index":
                check_search_runtime(search)
                emit(search.build_index(paths,model=args.model,rebuild=args.rebuild))
            elif args.search_command == "status":
                emit(search.status(paths))
            elif args.search_command == "query":
                if search.status(paths).get("ready"):
                    check_search_runtime(search)
                result=search.search(paths,dict(query=args.query,limit=args.limit,venues=args.venue,
                    year_from=args.year_from,year_to=args.year_to,mode=args.mode))
                if args.format=="json":emit(result)
                else:print(search.markdown_results(result))
            elif args.search_command == "discover":
                options = discovery_options(args)
                if args.retriever in {"combined", "zvec"}:
                    check_search_runtime(search)
                from .discovery import discover
                result = discover(paths, options)
                if args.format == "json":
                    emit(result)
                else:
                    print(search.markdown_results(result))
            else:
                if args.search_command in {"serve", "start"}:
                    check_search_runtime(search)
                from . import search_server
                if args.search_command == "serve":search_server.serve(paths,args.port,args.open)
                elif args.search_command == "start":emit(search_server.start(paths,args.port,args.open))
                elif args.search_command == "stop":emit(search_server.stop(paths))
            return 0
        except (search.SearchError,ValueError,OSError) as exc:
            emit({"status":"ERROR","error":str(exc)})
            return 2
    if args.command == "init":
        paths.ensure_tree()
        registry = install_registry(paths)
        initialize_db(paths.catalog)
        if not paths.state.exists():
            atomic_json(paths.state, initial_state(registry["venue_ids"], args.root_thread_id))
        for config in ("defaults.yml", "agents.yml", "browser_policy.yml", "document_types.yml", "publisher_families.yml"):
            shutil.copyfile(CONFIG_ROOT / config, paths.registry / config)
        browser_evidence = CONFIG_ROOT / "browser_capabilities.json"
        if browser_evidence.is_file():
            shutil.copyfile(browser_evidence, paths.preflight / "browser_capabilities.json")
        emit({"status": "PASS", "home": str(paths.home), **registry})
        return 0
    if args.command == "registry" and args.registry_command == "validate":
        result = validate_runtime(paths, strict=args.strict)
        emit(result)
        return 0 if result["status"] == "PASS" else 2
    if args.command == "preflight":
        if args.controller != "sol-high" or args.worker != "luna-max" or args.max_active_venues != 1:
            emit({"status": "CONFIG_ERROR", "error": "required controller/worker/concurrency contract violated"})
            return 2
        code, result = preflight_check(paths, args.browser_first, args.strict)
        emit(result)
        return code
    if args.command == "campaign" and args.campaign_command == "start":
        if args.controller != "sol-high" or args.worker != "luna-max" or not args.browser_first or args.max_active_venues != 1:
            emit({"status": "CONFIG_ERROR", "error": "campaign contract violated"})
            return 2
        owner = {"controller": args.controller, "root_thread_id": args.root_thread_id, "created_at": utc_now()}
        try:
            create_controller_lock(paths, owner)
        except RuntimeError as exc:
            emit({"status": "CONTROLLER_LOCKED", "error": str(exc)})
            return 2
        state = load_json(paths.state)
        state["phase"] = "PASS_A_DISCOVERY"
        state["updated_at"] = utc_now()
        atomic_json(paths.state, state)
        emit({"status": "PASS", "owner": owner, "campaign_id": state["campaign_id"]})
        return 0
    if args.command == "state" and args.state_command == "transition":
        try:
            emit(transition(paths, args.venue, args.expected, args.target, args.receipt))
            return 0
        except ValueError as exc:
            emit({"status": "FAIL", "error": str(exc)})
            return 2
    if args.command == "thread" and args.thread_command == "request":
        venue_path = paths.venues / f"{args.venue}.yml"
        if not venue_path.is_file():
            emit({"status": "FAIL", "error": f"unknown venue: {args.venue}"})
            return 2
        try:
            request, output = build_request(paths, load_json(venue_path), args.role, args.run, args.parent_thread_id)
        except ValueError as exc:
            emit({"status": "FAIL", "error": str(exc)})
            return 2
        emit({"status": "PASS", "request": request, "output_root": str(output)})
        return 0
    if args.command == "thread" and args.thread_command == "ingest":
        receipt_path = args.receipt.resolve()
        output_root = receipt_path.parent
        request_path = output_root / "thread_request.json"
        if not request_path.is_file():
            emit({"status": "FAIL", "errors": ["thread_request.json missing"]})
            return 2
        receipt = load_json(receipt_path)
        if "thread_id" not in receipt and receipt.get("actual_thread_id"):
            receipt["thread_id"] = receipt["actual_thread_id"]
        request = load_json(request_path)
        errors = validate_receipt(receipt, request)
        for name in request.get("required_artifacts", []):
            if not (output_root / name).is_file():
                errors.append(f"required artifact missing: {name}")
        manifest_path = output_root / "output_manifest.json"
        if manifest_path.is_file():
            manifest = load_json(manifest_path)
            manifest_thread_id = manifest.get("thread_id") or manifest.get("actual_thread_id")
            if manifest.get("venue_id") != request.get("venue_id") or manifest_thread_id != receipt.get("thread_id"):
                errors.append("output manifest scope/thread mismatch")
            manifest_items = manifest.get("artifacts") or manifest.get("files") or []
            if not manifest_items:
                errors.append("output manifest has no artifact entries")
            if isinstance(manifest_items, dict):
                manifest_items = [
                    {"path": path, **(metadata if isinstance(metadata, dict) else {})}
                    for path, metadata in manifest_items.items()
                ]
            for item in manifest_items:
                if not isinstance(item, dict):
                    errors.append("output manifest artifact entry is not an object")
                    continue
                relative = item.get("relative_path") or item.get("path", "")
                artifact = (output_root / relative).resolve()
                if not artifact.is_relative_to(output_root.resolve()):
                    errors.append(f"manifest path escapes output root: {relative}")
                    continue
                expected_hash = item.get("sha256")
                required = item.get("required", True)
                if required and not artifact.is_file():
                    errors.append(f"manifest required artifact missing: {relative}")
                elif expected_hash and artifact.is_file() and sha256_file(artifact) != expected_hash:
                    errors.append(f"artifact hash mismatch: {relative}")
        security_code, security_report = scan_path(paths, output_root)
        if security_code:
            errors.append("security scan failed")
        venue_id = request["venue_id"]
        for candidate in output_root.rglob("*.jsonl"):
            for line_no, line in enumerate(candidate.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(f"invalid JSONL: {candidate}:{line_no}")
                    continue
                if isinstance(value, dict) and value.get("venue_id") not in {None, venue_id}:
                    errors.append(f"cross-venue record: {candidate}:{line_no}")
        if receipt.get("status") == "PASS" and request["role"] in {"venue-discovery", "venue-rediscovery"}:
            candidate_path = output_root / "recipe_candidate.yml"
            if not candidate_path.is_file():
                errors.append("recipe_candidate.yml missing")
            else:
                recipe_result = validate_recipe(candidate_path, venue_id)
                errors.extend(recipe_result["errors"])
        if receipt.get("status") == "PASS" and request["role"] == "venue-count":
            venue_config = load_json(paths.venues / f"{venue_id}.yml")
            count_result = verify_count_output(
                output_root,
                venue_id,
                allowed_domains=set(venue_config.get("allowed_domains", [])),
            )
            errors.extend(count_result["errors"])
        if receipt.get("status") == "PASS" and request["role"] == "venue-pilot":
            venue_config = load_json(paths.venues / f"{venue_id}.yml")
            pilot_result = verify_pilot_output(
                output_root,
                venue_id,
                set(venue_config.get("allowed_domains", [])),
            )
            errors.extend(pilot_result["errors"])
        if receipt.get("status") == "PASS" and request["role"] == "venue-replay":
            venue_config = load_json(paths.venues / f"{venue_id}.yml")
            replay_result = verify_replay_output(
                output_root,
                venue_id,
                paths.home / "recipes" / venue_id / "pilot_manifest.jsonl",
                paths.home / "recipes" / venue_id / "pilot_sample_metadata.jsonl",
                set(venue_config.get("allowed_domains", [])),
            )
            errors.extend(replay_result["errors"])
        if errors:
            emit({"status": "FAIL", "errors": errors, "security": security_report})
            return 4 if security_code else 2
        status_to_state = {
            "BLOCKED_AUTH": "AUTH_REQUIRED", "BLOCKED_SOURCE": "SOURCE_BLOCKED",
            "POLICY_BLOCKED": "POLICY_BLOCKED", "PARTIAL": "PARTIAL", "FAIL": "FAILED",
        }
        pass_states = {
            "venue-discovery": "RECIPE_CANDIDATE", "venue-rediscovery": "RECIPE_CANDIDATE",
            "venue-pilot": "PILOT_PASSED", "venue-count": "COUNT_BASELINED",
            "venue-bootstrap": "BOOTSTRAP_STAGED", "venue-reconcile": "ACTIVE",
        }
        target = pass_states.get(request["role"]) if receipt["status"] == "PASS" else status_to_state.get(receipt["status"])
        state = load_json(paths.state)
        current = state["venues"][venue_id]["state"]
        target_already_satisfied = current == target or (
            target == "PILOT_PASSED" and current in {
                "REPLAY_RUNNING", "RECIPE_LOCKED", "COUNT_RUNNING", "COUNT_BASELINED",
                "BOOTSTRAP_RUNNING", "BOOTSTRAP_STAGED", "RECONCILING", "ACTIVE",
            }
        )
        if target and not target_already_satisfied:
            try:
                transition(paths, venue_id, current, target, receipt_path)
            except ValueError as exc:
                emit({"status": "FAIL", "errors": [str(exc)]})
                return 2
        if request["role"] in {"venue-discovery", "venue-rediscovery"} and receipt["status"] in {"PASS", "PARTIAL"}:
            recipe_dir = paths.home / "recipes" / venue_id
            recipe_dir.mkdir(parents=True, exist_ok=True)
            history_dir = recipe_dir / "history" / receipt["thread_id"]
            history_dir.mkdir(parents=True, exist_ok=True)
            for name in ("recipe_candidate.yml", "discovery_report.md", "discovery_evidence.jsonl", "candidate_method_comparison.json", "login_requirements.json", "source_policy_notes.md", "thread_receipt.json", "output_manifest.json"):
                source = output_root / name
                if source.is_file():
                    shutil.copy2(source, history_dir / name)
                    shutil.copy2(source, recipe_dir / name)
        if request["role"] == "venue-pilot" and receipt["status"] == "PASS":
            recipe_dir = paths.home / "recipes" / venue_id
            history_dir = recipe_dir / "history" / receipt["thread_id"]
            history_dir.mkdir(parents=True, exist_ok=True)
            for name in ("pilot_manifest.jsonl", "pilot_report.json", "pilot_sample_metadata.jsonl", "browser_evidence.jsonl", "errors.jsonl", "thread_receipt.json", "output_manifest.json"):
                source = output_root / name
                if source.is_file():
                    shutil.copy2(source, history_dir / name)
                    shutil.copy2(source, recipe_dir / name)
        if request["role"] == "venue-replay" and receipt["status"] == "PASS":
            recipe_dir = paths.home / "recipes" / venue_id
            history_dir = recipe_dir / "history" / receipt["thread_id"]
            history_dir.mkdir(parents=True, exist_ok=True)
            for name in ("replay_manifest.jsonl", "replay_sample_metadata.jsonl", "replay_report.json", "browser_evidence.jsonl", "errors.jsonl", "thread_receipt.json", "output_manifest.json"):
                source = output_root / name
                if source.is_file():
                    shutil.copy2(source, history_dir / name)
                    shutil.copy2(source, recipe_dir / name)
        emit({"status": "PASS", "venue_id": venue_id, "role": request["role"], "transitioned_to": target, "security": security_report})
        return 0
    if args.command == "recipe" and args.recipe_command == "validate":
        try:
            result = validate_recipe(args.candidate, args.venue)
        except (OSError, json.JSONDecodeError) as exc:
            emit({"status": "FAIL", "errors": [str(exc)]})
            return 2
        emit(result)
        return 0 if result["status"] == "PASS" else 2
    if args.command == "recipe" and args.recipe_command == "lock":
        review = load_json(args.review)
        replay = load_json(args.replay_report)
        recipe_dir = paths.home / "recipes" / args.venue
        candidate = recipe_dir / "recipe_candidate.yml"
        errors: list[str] = []
        if review.get("venue_id") != args.venue or review.get("status") != "APPROVE_FOR_LOCK":
            errors.append("Sol review is not APPROVE_FOR_LOCK for this venue")
        if replay.get("venue_id") != args.venue:
            errors.append("replay venue mismatch")
        set_agreement = replay.get("set_agreement")
        if set_agreement is None:
            set_agreement = replay.get("overall", {}).get("set_agreement", {}).get("url", {}).get("jaccard", 0)
        field_agreement = replay.get("field_agreement")
        if field_agreement is None:
            field_agreement = replay.get("sample_metadata", {}).get("overall", {}).get("agreement", 0)
        if float(set_agreement) < 0.999:
            errors.append("replay set agreement below 99.9%")
        if float(field_agreement) < 0.995:
            errors.append("replay field agreement below 99.5%")
        if not candidate.is_file():
            errors.append("recipe candidate missing")
        elif validate_recipe(candidate, args.venue)["status"] != "PASS":
            errors.append("recipe candidate validation failed")
        if errors:
            emit({"status": "FAIL", "errors": errors})
            return 2
        shutil.copy2(candidate, recipe_dir / "recipe.yml")
        lock = {
            "venue_id": args.venue, "recipe_version": load_json(candidate)["recipe_version"],
            "approved_by_model": "sol-high", "review": str(args.review.resolve()),
            "replay_report": str(args.replay_report.resolve()),
            "set_agreement": set_agreement, "field_agreement": field_agreement,
            "locked_at": utc_now(),
        }
        atomic_json(recipe_dir / "recipe_lock.json", lock)
        transition(paths, args.venue, "REPLAY_RUNNING", "RECIPE_LOCKED", recipe_dir / "recipe_lock.json")
        emit({"status": "PASS", "lock": lock})
        return 0
    if args.command == "count" and args.count_command == "verify":
        venue_config = load_json(paths.venues / f"{args.venue}.yml")
        result = verify_count_output(
            args.output_root.resolve(),
            args.venue,
            allowed_domains=set(venue_config.get("allowed_domains", [])),
        )
        emit(result)
        return 0 if result["status"] == "PASS" else 1
    if args.command == "count" and args.count_command == "publish":
        output_root = args.output_root.resolve()
        venue_config = load_json(paths.venues / f"{args.venue}.yml")
        result = verify_count_output(
            output_root,
            args.venue,
            allowed_domains=set(venue_config.get("allowed_domains", [])),
        )
        if result["status"] != "PASS":
            emit(result)
            return 1
        expected_dir = paths.home / "manifests" / "expected" / args.venue
        count_dir = paths.home / "reports" / "count" / args.venue
        expected_dir.mkdir(parents=True, exist_ok=True)
        count_dir.mkdir(parents=True, exist_ok=True)
        for year in range(result["year_from"], result["year_through"] + 1):
            shutil.copy2(output_root / "expected" / f"{year}.jsonl.gz", expected_dir / f"{year}.jsonl.gz")
            shutil.copy2(output_root / "count" / f"{year}.json", count_dir / f"{year}.json")
        shutil.copy2(output_root / "venue_count_report.json", count_dir / "venue_count_report.json")
        result["published_at"] = utc_now()
        result["source_output_root"] = str(output_root)
        result["expected_dir"] = str(expected_dir)
        atomic_json(count_dir / "count_verification.json", result)
        emit(result)
        return 0
    if args.command == "bootstrap" and args.bootstrap_command == "plan":
        result = bootstrap_plan(paths, args.venue)
        emit(result)
        return 0 if result["status"] == "PASS" else 2
    if args.command == "staging" and args.staging_command == "validate":
        result = validate_staging(
            paths,
            args.venue,
            args.run,
            staging_file=args.input,
            exclusion_file=args.exclusions,
            expected_root=args.expected_root,
            strict=args.strict,
        )
        emit(result)
        return 0 if result["status"] == "PASS" else 2
    if args.command == "merge" and args.merge_command == "venue":
        if not args.single_writer:
            emit({"status": "CONFIG_ERROR", "error": "--single-writer is required"})
            return 2
        code, result = merge_venue(
            paths,
            args.venue,
            args.run,
            staging_file=args.input,
            exclusion_file=args.exclusions,
            expected_root=args.expected_root,
            verify_before_commit=args.verify_before_commit,
        )
        emit(result)
        return code
    if args.command == "reconcile" and args.reconcile_command == "venue":
        code, result = reconcile_venue(
            paths,
            args.venue,
            run_root=args.run,
            expected_root=args.expected_root,
            strict=args.strict,
        )
        emit(result)
        return code
    if args.command == "security" and args.security_command == "scan":
        target = args.path or (paths.home / "runs" / args.run if args.run else paths.home)
        code, result = scan_path(paths, target)
        emit(result)
        return code
    if args.command == "verify":
        code, result = verify_all(paths, args.acceptance, args.strict)
        emit(result)
        return code
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return handle(args)
    except (OSError, ValueError, KeyError) as exc:
        emit({"status": "CONFIG_ERROR", "error": str(exc)})
        return 2

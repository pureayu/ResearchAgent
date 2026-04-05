#!/usr/bin/env python3
"""Run the deep research benchmark over a JSONL gold set."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
SRC_DIR = BACKEND_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from benchmarking import (
    aggregate_todo_items,
    available_features,
    load_cases,
    serialize_todo_item,
)
from config import Configuration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        default=str(BACKEND_ROOT / "benchmarks" / "v1" / "cases.jsonl"),
        help="Path to the benchmark JSONL file.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional explicit output JSON path.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only the selected case id. Can be provided multiple times.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of selected cases.",
    )
    parser.add_argument(
        "--allow-missing-features",
        action="store_true",
        help="Run cases even when required runtime features are unavailable.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_cases(args.cases)
    selected = _select_cases(cases, case_ids=args.case_id, limit=args.limit)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = Path(args.output) if args.output else (
        BACKEND_ROOT / "benchmarks" / "v1" / "results" / f"run_{timestamp}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    notes_workspace = output_path.parent / f"notes_{timestamp}"
    config = Configuration.from_env().model_copy(
        update={
            "memory_database_url": None,
            "notes_workspace": str(notes_workspace),
        }
    )
    features = available_features(config)

    results: list[dict[str, object]] = []
    print(f"Running {len(selected)} benchmark case(s)...")
    for index, case in enumerate(selected, start=1):
        case_id = str(case.get("id") or f"case_{index}")
        print(f"[{index}/{len(selected)}] {case_id}: {case.get('query')}")
        missing_features = sorted(
            set(str(item) for item in case.get("required_features") or [])
            - features
        )
        if missing_features and not args.allow_missing_features:
            results.append(
                {
                    "id": case_id,
                    "status": "skipped_missing_features",
                    "missing_features": missing_features,
                    "query": case.get("query"),
                }
            )
            print(f"  skipped: missing features {missing_features}")
            continue

        started = time.perf_counter()
        session_id = f"benchmark-{case_id}-{uuid4().hex[:8]}"
        try:
            from agent import DeepResearchAgent
        except ModuleNotFoundError as exc:
            if exc.name == "hello_agents":
                raise RuntimeError(
                    "Missing runtime dependency `hello_agents`. "
                    "Run the benchmark with the backend environment activated."
                ) from exc
            raise

        try:
            agent = DeepResearchAgent(config=config)
            response = agent.run(str(case.get("query") or ""), session_id=session_id)
        except Exception as exc:
            elapsed = time.perf_counter() - started
            results.append(
                {
                    "id": case_id,
                    "status": "error",
                    "query": case.get("query"),
                    "session_id": session_id,
                    "runtime_seconds": round(elapsed, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"  error: {type(exc).__name__}: {exc}")
            continue

        elapsed = time.perf_counter() - started
        todo_items = [serialize_todo_item(item) for item in response.todo_items]
        aggregated = aggregate_todo_items(todo_items)
        results.append(
            {
                "id": case_id,
                "status": "completed",
                "query": case.get("query"),
                "session_id": response.session_id,
                "runtime_seconds": round(elapsed, 3),
                "response": {
                    "report_markdown": response.report_markdown or response.running_summary or "",
                    "todo_items": todo_items,
                },
                "aggregated": aggregated,
            }
        )
        print(
            "  completed: tasks={tasks} capabilities={caps} sources={sources}".format(
                tasks=aggregated["task_count"],
                caps=aggregated["planned_capabilities"],
                sources=aggregated["source_types"],
            )
        )

    payload = {
        "benchmark_version": "v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(Path(args.cases).resolve()),
        "output_path": str(output_path.resolve()),
        "config": {
            "llm_provider": config.llm_provider,
            "model": config.resolved_model(),
            "search_api": getattr(config.search_api, "value", str(config.search_api)),
            "academic_search_provider": getattr(
                config.academic_search_provider,
                "value",
                str(config.academic_search_provider),
            ),
            "enable_github_mcp": config.enable_github_mcp,
            "notes_workspace": config.notes_workspace,
            "memory_database_url": None,
        },
        "available_features": sorted(features),
        "selected_case_ids": [str(case.get("id") or "") for case in selected],
        "results": results,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved benchmark run to {output_path}")
    return 0


def _select_cases(
    cases: list[dict[str, object]],
    *,
    case_ids: list[str],
    limit: int | None,
) -> list[dict[str, object]]:
    if case_ids:
        wanted = {item.strip() for item in case_ids if item.strip()}
        selected = [case for case in cases if str(case.get("id") or "") in wanted]
    else:
        selected = list(cases)
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    return selected


if __name__ == "__main__":
    raise SystemExit(main())

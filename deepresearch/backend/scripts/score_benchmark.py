#!/usr/bin/env python3
"""Score a benchmark run against the gold-set expectations."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
SRC_DIR = BACKEND_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from benchmarking import load_cases, score_case, summarize_scores


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results",
        required=True,
        help="Path to the JSON file produced by run_benchmark.py.",
    )
    parser.add_argument(
        "--cases",
        default=str(BACKEND_ROOT / "benchmarks" / "v1" / "cases.jsonl"),
        help="Path to the benchmark JSONL file.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional explicit path for scored JSON output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = {str(case.get("id") or ""): case for case in load_cases(args.cases)}
    run_payload = json.loads(Path(args.results).read_text(encoding="utf-8"))

    scored_cases: list[dict[str, object]] = []
    for run_case in run_payload.get("results") or []:
        case_id = str(run_case.get("id") or "")
        case = cases.get(case_id)
        if case is None:
            scored_cases.append(
                {
                    "id": case_id,
                    "status": str(run_case.get("status") or ""),
                    "passed": False,
                    "error": "case_not_found",
                }
            )
            continue
        scored_cases.append(score_case(case, run_case))

    summary = summarize_scores(scored_cases)
    output_path = Path(args.output) if args.output else Path(args.results).with_name(
        Path(args.results).stem + "_scored.json"
    )
    payload = {
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "results_path": str(Path(args.results).resolve()),
        "cases_path": str(Path(args.cases).resolve()),
        "summary": summary,
        "cases": scored_cases,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Benchmark summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved scored benchmark to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

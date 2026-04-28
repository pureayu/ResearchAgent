"""Helpers for capability-level benchmark execution and scoring."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from config import Configuration
from models import TodoItem

SOURCE_LABEL_TO_TYPE = {
    "学术论文": "academic",
    "GitHub 仓库": "github",
    "联网网页": "web_search",
}


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    """Load JSONL benchmark cases."""

    resolved = Path(path)
    cases: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        resolved.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive guardrail
            raise ValueError(
                f"Invalid JSON on line {line_number} in {resolved}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Case on line {line_number} must be a JSON object.")
        cases.append(payload)
    return cases


def available_features(config: Configuration) -> set[str]:
    """Return runtime features available for benchmark execution."""

    features: set[str] = set()
    if (
        config.enable_github_mcp
        and (os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_PAT"))
    ):
        features.add("github_mcp")
    return features


def serialize_todo_item(task: TodoItem) -> dict[str, Any]:
    """Convert a task dataclass into a benchmark-friendly JSON payload."""

    return {
        "id": task.id,
        "title": task.title,
        "intent": task.intent,
        "query": task.query,
        "queries": list(task.queries or [task.query]),
        "round_id": task.round_id,
        "origin": task.origin,
        "parent_task_id": task.parent_task_id,
        "status": task.status,
        "summary": task.summary,
        "sources_summary": task.sources_summary,
        "notices": list(task.notices),
        "attempt_count": task.attempt_count,
        "search_backend": task.search_backend,
        "evidence_count": task.evidence_count,
        "top_score": task.top_score,
        "needs_followup": task.needs_followup,
        "latest_query": task.latest_query,
        "evidence_gap_reason": task.evidence_gap_reason,
        "planned_capabilities": list(task.planned_capabilities),
        "current_capability": task.current_capability,
        "route_intent_label": task.route_intent_label,
        "route_confidence": task.route_confidence,
        "route_reason": task.route_reason,
    }


def extract_source_types_from_summary(summary: str | None) -> list[str]:
    """Parse source types from the deterministic sources summary block."""

    if not summary:
        return []

    source_types: list[str] = []
    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        label = line[2:].split("：", 1)[0].strip()
        source_type = SOURCE_LABEL_TO_TYPE.get(label)
        if source_type and source_type not in source_types:
            source_types.append(source_type)
    return source_types


def aggregate_todo_items(todo_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate task-level benchmark signals into case-level facts."""

    planned_capabilities: list[str] = []
    current_capabilities: list[str] = []
    search_backends: list[str] = []
    source_types: list[str] = []
    gap_reasons: list[str] = []
    task_status_counts: Counter[str] = Counter()
    total_evidence = 0
    max_top_score = 0.0

    for item in todo_items:
        task_status_counts[str(item.get("status") or "unknown")] += 1
        total_evidence += int(item.get("evidence_count") or 0)
        try:
            max_top_score = max(max_top_score, float(item.get("top_score") or 0.0))
        except (TypeError, ValueError):
            pass

        for capability_id in item.get("planned_capabilities") or []:
            capability = str(capability_id or "").strip()
            if capability and capability not in planned_capabilities:
                planned_capabilities.append(capability)

        current_capability = str(item.get("current_capability") or "").strip()
        if current_capability and current_capability not in current_capabilities:
            current_capabilities.append(current_capability)

        backend = str(item.get("search_backend") or "").strip()
        if backend and backend not in search_backends:
            search_backends.append(backend)

        gap_reason = str(item.get("evidence_gap_reason") or "").strip()
        if gap_reason and gap_reason not in gap_reasons:
            gap_reasons.append(gap_reason)

        for source_type in extract_source_types_from_summary(item.get("sources_summary")):
            if source_type not in source_types:
                source_types.append(source_type)

    return {
        "task_count": len(todo_items),
        "planned_capabilities": planned_capabilities,
        "current_capabilities": current_capabilities,
        "search_backends": search_backends,
        "source_types": source_types,
        "task_status_counts": dict(task_status_counts),
        "total_evidence_count": total_evidence,
        "max_top_score": max_top_score,
        "gap_reasons": gap_reasons,
    }


def score_case(case: dict[str, Any], run_case: dict[str, Any]) -> dict[str, Any]:
    """Compute V1 benchmark signals for one executed case."""

    aggregated = run_case.get("aggregated") or {}
    report_text = str((run_case.get("response") or {}).get("report_markdown") or "")
    task_summaries = "\n\n".join(
        str(item.get("summary") or "")
        for item in (run_case.get("response") or {}).get("todo_items") or []
    )
    combined_text = _normalize_text("\n".join([report_text, task_summaries]))

    expected_route = [str(item).strip() for item in case.get("expected_route_contains") or [] if str(item).strip()]
    actual_route = [str(item).strip() for item in aggregated.get("planned_capabilities") or [] if str(item).strip()]
    route_missing = [item for item in expected_route if item not in actual_route]

    expected_sources = [str(item).strip() for item in case.get("expected_source_types") or [] if str(item).strip()]
    actual_sources = [str(item).strip() for item in aggregated.get("source_types") or [] if str(item).strip()]
    source_missing = [item for item in expected_sources if item not in actual_sources]

    keywords = [str(item).strip() for item in case.get("must_have_keywords") or [] if str(item).strip()]
    keyword_hits = [kw for kw in keywords if kw.casefold() in combined_text]

    forbidden_patterns = [
        str(item).strip()
        for item in case.get("forbidden_patterns") or []
        if str(item).strip()
    ]
    forbidden_hits = [pattern for pattern in forbidden_patterns if pattern.casefold() in combined_text]

    expected_gap_reason = case.get("expected_gap_reason")
    gap_reasons = [str(item).strip() for item in aggregated.get("gap_reasons") or [] if str(item).strip()]
    gap_reason_match: bool | None
    if expected_gap_reason is None:
        gap_reason_match = None
    else:
        gap_reason_match = str(expected_gap_reason).strip() in gap_reasons

    status = str(run_case.get("status") or "")
    passed = (
        status == "completed"
        and not route_missing
        and not source_missing
        and len(keyword_hits) == len(keywords)
        and not forbidden_hits
        and (gap_reason_match is not False)
    )

    return {
        "id": case.get("id"),
        "status": status,
        "route_match": not route_missing,
        "route_missing": route_missing,
        "source_coverage": not source_missing,
        "source_missing": source_missing,
        "keyword_hits": keyword_hits,
        "keyword_total": len(keywords),
        "keyword_coverage": (len(keyword_hits) / len(keywords)) if keywords else 1.0,
        "forbidden_hits": forbidden_hits,
        "expected_gap_reason": expected_gap_reason,
        "gap_reasons": gap_reasons,
        "gap_reason_match": gap_reason_match,
        "must_have_facts": list(case.get("must_have_facts") or []),
        "passed": passed,
    }


def summarize_scores(scored_cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact benchmark summary from per-case scores."""

    total = len(scored_cases)
    runnable = [item for item in scored_cases if item.get("status") != "skipped_missing_features"]
    gap_cases = [item for item in runnable if item.get("gap_reason_match") is not None]

    def _rate(items: list[dict[str, Any]], key: str) -> float:
        if not items:
            return 0.0
        hits = sum(1 for item in items if item.get(key))
        return hits / len(items)

    return {
        "total_cases": total,
        "runnable_cases": len(runnable),
        "passed_cases": sum(1 for item in runnable if item.get("passed")),
        "pass_rate": _rate(runnable, "passed"),
        "route_match_rate": _rate(runnable, "route_match"),
        "source_coverage_rate": _rate(runnable, "source_coverage"),
        "full_keyword_coverage_rate": (
            sum(
                1
                for item in runnable
                if float(item.get("keyword_coverage") or 0.0) >= 0.999
            )
            / len(runnable)
            if runnable
            else 0.0
        ),
        "forbidden_clean_rate": (
            sum(1 for item in runnable if not item.get("forbidden_hits"))
            / len(runnable)
            if runnable
            else 0.0
        ),
        "expected_gap_match_rate": _rate(gap_cases, "gap_reason_match"),
        "skipped_cases": sum(
            1 for item in scored_cases if item.get("status") == "skipped_missing_features"
        ),
    }


def _normalize_text(text: str) -> str:
    """Lowercase and compact whitespace for simple lexical matching."""

    return re.sub(r"\s+", " ", text or "").strip().casefold()

"""Search dispatch helpers over capability executors."""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from config import Configuration
from models import TodoItem
from services.capabilities import CapabilityExecutor, CapabilityRegistry
from utils import deduplicate_and_format_sources, format_sources

logger = logging.getLogger(__name__)

MAX_TOKENS_PER_SOURCE = 2000


def dispatch_capability_search(
    capability_id: str,
    query: str,
    config: Configuration,
    loop_count: int,
    *,
    max_results: int = 5,
    task: TodoItem | None = None,
) -> Tuple[dict[str, Any] | None, list[str], Optional[str], str]:
    """Execute one capability and normalize its payload."""

    registry = CapabilityRegistry(config)
    executor = CapabilityExecutor(registry)
    return executor.execute(
        capability_id,
        query,
        config,
        loop_count,
        max_results=max_results,
        task=task,
    )


def prepare_research_context(
    search_result: dict[str, Any] | None,
    answer_text: Optional[str],
    config: Configuration,
) -> tuple[str, str]:
    """Build structured context and source summary for downstream agents."""

    sources_summary = format_sources(search_result)
    source_type_summary = _format_source_type_summary(search_result)
    context = deduplicate_and_format_sources(
        search_result or {"results": []},
        max_tokens_per_source=MAX_TOKENS_PER_SOURCE,
        fetch_full_page=config.fetch_full_page,
    )

    if source_type_summary:
        sources_summary = f"{source_type_summary}\n\n{sources_summary}".strip()
        context = f"{source_type_summary}\n\n{context}".strip()

    if answer_text:
        context = f"AI直接答案：\n{answer_text}\n\n{context}"

    return sources_summary, context


def _format_source_type_summary(search_result: dict[str, Any] | None) -> str:
    """Return a short, human-readable source type summary."""

    results = (search_result or {}).get("results") or []
    counts: dict[str, int] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "web_search")
        counts[source_type] = counts.get(source_type, 0) + 1

    if not counts:
        return ""

    label_map = {
        "academic": "学术论文",
        "web_search": "联网网页",
    }

    ordered = []
    for key in ("academic", "web_search"):
        if key in counts:
            ordered.append(f"- {label_map.get(key, key)}：{counts[key]}")

    for key, value in counts.items():
        if key in {"academic", "web_search"}:
            continue
        ordered.append(f"- {label_map.get(key, key)}：{value}")

    return "来源类型统计：\n" + "\n".join(ordered)

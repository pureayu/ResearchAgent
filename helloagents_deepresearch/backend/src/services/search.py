"""Search dispatch helpers leveraging HelloAgents SearchTool."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional, Tuple

from hello_agents.tools import SearchTool

from config import Configuration
from utils import (
    deduplicate_and_format_sources,
    format_sources,
    get_config_value,
)

logger = logging.getLogger(__name__)

MAX_TOKENS_PER_SOURCE = 2000
_GLOBAL_SEARCH_TOOL = SearchTool(backend="hybrid")
_LOCAL_LIBRARY_SEARCH_TOOL = None


def _get_local_library_search_tool():
    """Lazily import the sibling paper_assistant tool wrapper."""

    global _LOCAL_LIBRARY_SEARCH_TOOL
    if _LOCAL_LIBRARY_SEARCH_TOOL is not None:
        return _LOCAL_LIBRARY_SEARCH_TOOL

    repo_root = Path(__file__).resolve().parents[4]
    paper_assistant_root = repo_root / "paper_assistant"
    if str(paper_assistant_root) not in sys.path:
        sys.path.insert(0, str(paper_assistant_root))

    from app.local_library_tools import LocalLibrarySearchTool

    _LOCAL_LIBRARY_SEARCH_TOOL = LocalLibrarySearchTool()
    return _LOCAL_LIBRARY_SEARCH_TOOL


def _dispatch_local_library_search(query: str) -> dict[str, Any]:
    """Run the local paper library search and normalize to search-service schema."""

    tool = _get_local_library_search_tool()
    response = tool.run(
        {
            "query": query,
            "top_k": 5,
            "retrieval_mode": "hybrid",
        }
    )

    results = []
    for item in response.results:
        results.append(
            {
                "title": item.title,
                "url": item.filepath,
                "content": item.snippet,
                "raw_content": item.content,
                "score": item.score,
                "page": item.page,
                "source_type": "local_library",
            }
        )

    return {
        "results": results,
        "backend": "local_library",
        "answer": None,
        "notices": [],
    }


def dispatch_search(
    query: str,
    config: Configuration,
    loop_count: int,
    backend_override: str | None = None,
) -> Tuple[dict[str, Any] | None, list[str], Optional[str], str]:
    """Execute configured search backend and normalise response payload."""

    search_api = backend_override or get_config_value(config.search_api)

    try:
        if search_api == "local_library":
            raw_response = _dispatch_local_library_search(query)
        else:
            raw_response = _GLOBAL_SEARCH_TOOL.run(
                {
                    "input": query,
                    "backend": search_api,
                    "mode": "structured",
                    "fetch_full_page": config.fetch_full_page,
                    "max_results": 5,
                    "max_tokens_per_source": MAX_TOKENS_PER_SOURCE,
                    "loop_count": loop_count,
                }
            )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Search backend %s failed: %s", search_api, exc)
        raise
    
    #如果传入的是字符串则结构化成字典
    if isinstance(raw_response, str):
        notices = [raw_response]
        logger.warning("Search backend %s returned text notice: %s", search_api, raw_response)
        payload: dict[str, Any] = {
            "results": [],
            "backend": search_api,
            "answer": None,
            "notices": notices,
        }
    else:
        payload = raw_response
        notices = list(payload.get("notices") or [])

    _normalize_result_source_types(payload)

    backend_label = str(payload.get("backend") or search_api)
    answer_text = payload.get("answer")
    results = payload.get("results", [])

    if notices:
        for notice in notices:
            logger.info("Search notice (%s): %s", backend_label, notice)

    logger.info(
        "Search backend=%s resolved_backend=%s answer=%s results=%s",
        search_api,
        backend_label,
        bool(answer_text),
        len(results),
    )

    return payload, notices, answer_text, backend_label


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


def _normalize_result_source_types(payload: dict[str, Any] | None) -> None:
    """Ensure every search result carries a stable source_type for downstream consumers."""

    if not payload:
        return

    backend = str(payload.get("backend") or "")
    default_type = "local_library" if backend == "local_library" else "web_search"

    results = payload.get("results") or []
    for item in results:
        if not isinstance(item, dict):
            continue
        item.setdefault("source_type", default_type)


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
        "local_library": "本地文献",
        "web_search": "联网网页",
    }

    ordered = []
    for key in ("local_library", "web_search"):
        if key in counts:
            ordered.append(f"- {label_map.get(key, key)}：{counts[key]}")

    for key, value in counts.items():
        if key in {"local_library", "web_search"}:
            continue
        ordered.append(f"- {label_map.get(key, key)}：{value}")

    return "来源类型统计：\n" + "\n".join(ordered)

"""Evidence sufficiency and follow-up query strategy."""

from __future__ import annotations

import re
from typing import Any

from config import Configuration
from models import TodoItem

LOCAL_LIBRARY_BACKEND = "local_library"
DEFAULT_WEB_BACKEND = "advanced"
LOCAL_QUERY_HINTS = {
    "rag",
    "retrieval",
    "generation",
    "citation",
    "survey",
    "self-rag",
    "crag",
    "multihop-rag",
    "rag-fusion",
    "adaptive-rag",
    "ragchecker",
    "检索",
    "文献",
    "论文",
    "综述",
    "引用",
    "生成",
    "知识库",
}


class EvidencePolicy:
    """Encapsulate search-backend choice and evidence-gap heuristics."""

    def __init__(self, config: Configuration) -> None:
        self._config = config

    def resolve_initial_backend(self, query: str) -> str:
        """Choose the first backend based on the query shape."""

        if self.looks_like_local_research_query(query):
            return LOCAL_LIBRARY_BACKEND
        return self.resolve_web_backend()

    def resolve_web_backend(self) -> str:
        """Resolve the configured web backend, avoiding the local-only backend."""

        configured_backend = self._config.search_api.value
        if configured_backend == LOCAL_LIBRARY_BACKEND:
            return DEFAULT_WEB_BACKEND
        return configured_backend

    def looks_like_local_research_query(self, query: str) -> bool:
        """Return whether the query appears to target research literature."""

        lowered = (query or "").lower()
        return any(term in lowered for term in LOCAL_QUERY_HINTS)

    def assess_evidence_gap(
        self,
        query: str,
        search_result: dict[str, Any] | None,
        backend: str,
    ) -> str | None:
        """Return a stable gap reason when current evidence is insufficient."""

        if not search_result:
            return "no_results"

        results = search_result.get("results") or []
        top_score = self._extract_top_score(search_result)
        source_breakdown = self.summarize_search_result(search_result).get("source_breakdown", {})

        if not results:
            return "no_results"

        if backend == LOCAL_LIBRARY_BACKEND:
            if not self.looks_like_local_research_query(query):
                return "query_needs_web"
            if len(results) < 3:
                return "insufficient_local_coverage"
            if top_score < 0.65:
                return "low_local_confidence"
            return None

        if len(results) < 3:
            return "insufficient_web_coverage"
        if top_score < 0.45:
            return "low_web_confidence"
        if (
            backend != LOCAL_LIBRARY_BACKEND
            and source_breakdown.get("local_library", 0) == 0
            and self.looks_like_local_research_query(query)
        ):
            return "missing_local_grounding"
        return None

    def build_followup_query(
        self,
        task: TodoItem,
        *,
        base_query: str,
        gap_reason: str,
        attempt_index: int,
    ) -> str:
        """Construct a follow-up query tailored to the current evidence gap."""

        compact_intent = re.sub(r"\s+", " ", f"{task.title} {task.intent}".strip())
        compact_intent = compact_intent[:120].strip()
        use_chinese = self._contains_cjk(base_query) or self._contains_cjk(compact_intent)

        if gap_reason == "query_needs_web":
            return base_query

        if use_chinese:
            suffixes = {
                1: " 论文 综述 方法 挑战 代表工作",
                2: " 最新 进展 对比 评测 最佳实践",
                3: " benchmark evaluation survey",
            }
        else:
            suffixes = {
                1: " paper survey methods challenges representative work",
                2: " latest advances comparison evaluation best practices",
                3: " benchmark survey review",
            }

        suffix = suffixes.get(attempt_index, suffixes[max(suffixes)])
        if gap_reason == "no_results":
            seed = compact_intent or base_query
        elif gap_reason.startswith("insufficient"):
            seed = f"{base_query} {compact_intent}".strip()
        elif gap_reason.startswith("low_"):
            seed = f"{base_query} {compact_intent}".strip()
        elif gap_reason == "missing_local_grounding":
            seed = f"{task.query} {compact_intent}".strip()
        else:
            seed = base_query

        return re.sub(r"\s+", " ", f"{seed}{suffix}".strip())

    @staticmethod
    def summarize_search_result(search_result: dict[str, Any] | None) -> dict[str, Any]:
        """Build lightweight source metadata for search-result visualization."""

        results = (search_result or {}).get("results") or []
        source_breakdown: dict[str, int] = {}
        titles_preview: list[str] = []

        for item in results:
            if not isinstance(item, dict):
                continue

            source_type = str(item.get("source_type") or "web")
            source_breakdown[source_type] = source_breakdown.get(source_type, 0) + 1

            title = str(item.get("title") or "").strip()
            if title and len(titles_preview) < 3:
                titles_preview.append(title)

        return {
            "source_breakdown": source_breakdown,
            "titles_preview": titles_preview,
        }

    @staticmethod
    def _extract_top_score(search_result: dict[str, Any] | None) -> float:
        if not search_result:
            return 0.0
        results = search_result.get("results") or []
        if not results:
            return 0.0
        try:
            return float(results[0].get("score") or 0.0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

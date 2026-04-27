"""Evidence sufficiency and follow-up query strategy."""

from __future__ import annotations

import re
from typing import Any

from capability_types import (
    INSPECT_GITHUB_REPO_CAPABILITY,
    SEARCH_ACADEMIC_PAPERS_CAPABILITY,
    SEARCH_WEB_PAGES_CAPABILITY,
)
from config import Configuration
from models import TodoItem

class EvidencePolicy:
    """Encapsulate evidence-gap heuristics across multiple sources."""

    def __init__(self, config: Configuration) -> None:
        self._config = config

    def assess_evidence_gap(
        self,
        query: str,
        search_result: dict[str, Any] | None,
        current_capability: str,
    ) -> str | None:
        """Return a stable gap reason when current evidence is insufficient."""

        del query

        if not search_result:
            return "no_results"

        results = search_result.get("results") or []
        if not results:
            return "no_results"

        top_score = self._extract_top_score(search_result)
        source_breakdown = self.summarize_search_result(search_result).get("source_breakdown", {})

        if current_capability == SEARCH_ACADEMIC_PAPERS_CAPABILITY:
            academic_results = [
                item
                for item in results
                if str(item.get("source_type") or "") == "academic"
            ]
            if len(academic_results) < 3:
                return "insufficient_academic_coverage"

            rich_metadata_count = 0
            for item in academic_results:
                title = str(item.get("title") or "").strip()
                content = str(item.get("content") or item.get("raw_content") or "").strip()
                url = str(item.get("url") or item.get("pdf_url") or "").strip()
                if title and content and url:
                    rich_metadata_count += 1

            if rich_metadata_count < 2:
                return "insufficient_academic_metadata"
            return None

        if current_capability == INSPECT_GITHUB_REPO_CAPABILITY:
            github_results = [
                item
                for item in results
                if str(item.get("source_type") or "") == "github"
            ]
            if not github_results:
                return "no_results"
            if len(github_results) < 2:
                return "insufficient_github_coverage"

            kinds = {str(item.get("result_kind") or "").strip() for item in github_results}
            if not {"repo", "readme"} & kinds:
                return "missing_repo_context"
            if not {"code", "file"} & kinds:
                return "missing_code_context"
            return None

        if current_capability == SEARCH_WEB_PAGES_CAPABILITY:
            if source_breakdown.get("web_search", 0) == 0 and source_breakdown.get("academic", 0) >= 3:
                return None
            if len(results) < 3:
                return "insufficient_web_coverage"
            if top_score < 0.45:
                return "low_web_confidence"
            if source_breakdown.get("web_search", 0) == 0:
                return "insufficient_web_coverage"
            return None

        return None

    @staticmethod
    def finalize_gap_reason(
        gap_reason: str | None,
        *,
        has_next_source: bool,
    ) -> str | None:
        """Normalize the final gap reason for terminal source exhaustion."""

        if gap_reason is None:
            return None
        if has_next_source:
            return gap_reason
        return "terminal_insufficient_evidence"

    def build_followup_query(
        self,
        task: TodoItem,
        *,
        base_query: str,
        gap_reason: str,
        target_capability: str,
    ) -> str:
        """Construct a follow-up query tailored to the next source."""

        compact_intent = re.sub(r"\s+", " ", f"{task.title} {task.intent}".strip())
        compact_intent = compact_intent[:120].strip()

        if gap_reason == "no_results":
            seed = self._select_seed_query(
                base_query=base_query,
                compact_intent=compact_intent,
                target_capability=target_capability,
            )
        else:
            if (
                target_capability == SEARCH_ACADEMIC_PAPERS_CAPABILITY
                and self._contains_latin(base_query)
                and self._contains_cjk(compact_intent)
            ):
                # Keep arXiv queries in the planner-provided language instead of
                # mixing Chinese intent text into English keyword queries.
                seed = base_query
            else:
                seed = f"{base_query} {compact_intent}".strip()

        use_chinese = self._contains_cjk(seed) and not self._contains_latin(seed)

        if target_capability == SEARCH_ACADEMIC_PAPERS_CAPABILITY:
            suffix = (
                " 论文 综述 benchmark 方法 代表工作"
                if use_chinese
                else " paper survey benchmark methods representative work"
            )
        elif target_capability == INSPECT_GITHUB_REPO_CAPABILITY:
            suffix = (
                " 仓库 实现 架构 代码 关键文件 README"
                if use_chinese
                else " repository implementation architecture code key files README"
            )
        elif target_capability == SEARCH_WEB_PAGES_CAPABILITY:
            suffix = (
                " 最新 官方 文档 博客 最佳实践"
                if use_chinese
                else " latest official docs blog best practices"
            )
        else:
            suffix = ""

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

            source_type = str(item.get("source_type") or "web_search")
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

    @staticmethod
    def _contains_latin(text: str) -> bool:
        return bool(re.search(r"[A-Za-z]", text or ""))

    def _select_seed_query(
        self,
        *,
        base_query: str,
        compact_intent: str,
        target_capability: str,
    ) -> str:
        """Choose the most retrieval-friendly seed when previous source had no results."""

        normalized_base = re.sub(r"\s+", " ", (base_query or "").strip())
        normalized_intent = re.sub(r"\s+", " ", (compact_intent or "").strip())

        if target_capability == SEARCH_ACADEMIC_PAPERS_CAPABILITY:
            if self._contains_latin(normalized_base):
                return normalized_base
            if self._contains_latin(normalized_intent):
                return normalized_intent
            return normalized_intent or normalized_base

        if self._contains_latin(normalized_base) and not self._contains_latin(
            normalized_intent
        ):
            return normalized_base

        return normalized_intent or normalized_base

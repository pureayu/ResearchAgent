"""Thin capability registry and executor built on top of source adapters."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Tuple

from capability_types import (
    SEARCH_ACADEMIC_PAPERS_CAPABILITY,
    INSPECT_GITHUB_REPO_CAPABILITY,
    SEARCH_WEB_PAGES_CAPABILITY,
)
from config import Configuration
from models import TodoItem
from services.github_mcp import GitHubRepoCapabilityHandler
from services.source_adapters import get_source_adapters
from source_types import (
    ACADEMIC_SEARCH_SOURCE,
    ACADEMIC_SOURCE_TYPE,
    GITHUB_MCP_BACKEND,
    GITHUB_SOURCE_TYPE,
    WEB_SEARCH_SOURCE,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapabilitySpec:
    """Static metadata for one executable capability."""

    id: str
    description: str
    source_type: str
    backing_source_id: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = True
    default_priority: int = 0


class CapabilityRegistry:
    """Static in-process registry for the currently supported capabilities."""

    def __init__(self, config: Configuration | None = None) -> None:
        github_enabled = bool(config.enable_github_mcp) if config is not None else False
        self._specs = {
            SEARCH_ACADEMIC_PAPERS_CAPABILITY: CapabilitySpec(
                id=SEARCH_ACADEMIC_PAPERS_CAPABILITY,
                description="Search academic-paper metadata such as papers, surveys, and benchmarks.",
                source_type=ACADEMIC_SOURCE_TYPE,
                backing_source_id=ACADEMIC_SEARCH_SOURCE,
                tags=("academic", "papers", "survey", "benchmark"),
                default_priority=20,
            ),
            SEARCH_WEB_PAGES_CAPABILITY: CapabilitySpec(
                id=SEARCH_WEB_PAGES_CAPABILITY,
                description="Search generic webpages such as official docs, blogs, and news.",
                source_type=WEB_SEARCH_SOURCE,
                backing_source_id=WEB_SEARCH_SOURCE,
                tags=("web", "official", "docs", "news"),
                default_priority=30,
            ),
            INSPECT_GITHUB_REPO_CAPABILITY: CapabilitySpec(
                id=INSPECT_GITHUB_REPO_CAPABILITY,
                description="Inspect a GitHub repository via MCP to understand implementation and key files.",
                source_type=GITHUB_SOURCE_TYPE,
                backing_source_id=GITHUB_MCP_BACKEND,
                tags=("github", "repo", "code", "readme"),
                enabled=github_enabled,
                default_priority=25,
            ),
        }

    def get(self, capability_id: str) -> CapabilitySpec | None:
        return self._specs.get(capability_id)

    def require(self, capability_id: str) -> CapabilitySpec:
        spec = self.get(capability_id)
        if spec is None:
            raise ValueError(f"Unsupported capability_id: {capability_id}")
        if not spec.enabled:
            raise ValueError(f"Disabled capability_id: {capability_id}")
        return spec

    def list_enabled(self) -> list[CapabilitySpec]:
        return [spec for spec in self._specs.values() if spec.enabled]


class CapabilityHandler(Protocol):
    """Stable interface for one executable capability handler."""

    def execute(
        self,
        query: str,
        config: Configuration,
        *,
        loop_count: int,
        max_results: int = 5,
        task: TodoItem | None = None,
    ) -> dict[str, Any] | str:
        """Execute the capability and return a normalized payload."""


class SourceBackedCapabilityHandler:
    """Capability handler that delegates to one thin source adapter."""

    def __init__(self, backing_source_id: str) -> None:
        self._backing_source_id = backing_source_id

    def execute(
        self,
        query: str,
        config: Configuration,
        *,
        loop_count: int,
        max_results: int = 5,
        task: TodoItem | None = None,
    ) -> dict[str, Any] | str:
        del task

        adapters = get_source_adapters()
        adapter = adapters.get(self._backing_source_id)
        if adapter is None:
            raise ValueError(f"Unsupported backing_source_id: {self._backing_source_id}")

        return adapter.search(
            query,
            config,
            loop_count=loop_count,
            max_results=max_results,
        )


class CapabilityExecutor:
    """Execute one capability through its registered handler."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry
        self._handlers: dict[str, CapabilityHandler] = {
            SEARCH_ACADEMIC_PAPERS_CAPABILITY: SourceBackedCapabilityHandler(ACADEMIC_SEARCH_SOURCE),
            SEARCH_WEB_PAGES_CAPABILITY: SourceBackedCapabilityHandler(WEB_SEARCH_SOURCE),
            INSPECT_GITHUB_REPO_CAPABILITY: GitHubRepoCapabilityHandler(),
        }

    def execute(
        self,
        capability_id: str,
        query: str,
        config: Configuration,
        loop_count: int,
        *,
        max_results: int = 5,
        task: TodoItem | None = None,
    ) -> Tuple[dict[str, Any] | None, list[str], Optional[str], str]:
        spec = self._registry.require(capability_id)
        handler = self._handlers.get(capability_id)
        if handler is None:
            raise ValueError(f"Unsupported capability handler: {capability_id}")

        try:
            raw_response = handler.execute(
                query,
                config,
                loop_count=loop_count,
                max_results=max_results,
                task=task,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            message = str(exc)
            if "No results found" in message or "搜索失败" in message:
                logger.warning("Capability %s returned no results: %s", capability_id, exc)
                raw_response = {
                    "results": [],
                    "backend": spec.backing_source_id,
                    "answer": None,
                    "notices": [message],
                }
            else:
                logger.exception("Capability %s failed: %s", capability_id, exc)
                raise

        if isinstance(raw_response, str):
            notices = [raw_response]
            logger.warning("Capability %s returned text notice: %s", capability_id, raw_response)
            payload: dict[str, Any] = {
                "results": [],
                "backend": spec.backing_source_id,
                "answer": None,
                "notices": notices,
            }
        else:
            payload = raw_response
            notices = list(payload.get("notices") or [])

        _normalize_result_source_types(payload, source_id=spec.backing_source_id)

        backend_label = str(payload.get("backend") or spec.backing_source_id)
        answer_text = payload.get("answer")
        results = payload.get("results", [])

        if notices:
            for notice in notices:
                logger.info("Capability notice (%s): %s", backend_label, notice)

        logger.info(
            "Capability=%s source=%s backend=%s answer=%s results=%s",
            capability_id,
            spec.backing_source_id,
            backend_label,
            bool(answer_text),
            len(results),
        )

        return payload, notices, answer_text, backend_label


def _normalize_result_source_types(
    payload: dict[str, Any] | None,
    *,
    source_id: str,
) -> None:
    """Ensure every search result carries a stable source_type."""

    if not payload:
        return

    default_type_map = {
        ACADEMIC_SEARCH_SOURCE: ACADEMIC_SOURCE_TYPE,
        WEB_SEARCH_SOURCE: WEB_SEARCH_SOURCE,
        GITHUB_MCP_BACKEND: GITHUB_SOURCE_TYPE,
    }
    default_type = default_type_map.get(source_id, WEB_SEARCH_SOURCE)

    results = payload.get("results") or []
    for item in results:
        if not isinstance(item, dict):
            continue
        item.setdefault("source_type", default_type)

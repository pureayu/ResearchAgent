"""Thin source adapters for local, academic, and web retrieval."""

from __future__ import annotations

import logging
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Protocol

import requests

from config import AcademicSearchProvider, Configuration
from utils import get_config_value

logger = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


class SourceAdapter(Protocol):
    """Stable interface for a search source."""

    source_id: str

    def search(
        self,
        query: str,
        config: Configuration,
        *,
        loop_count: int,
        max_results: int = 5,
    ) -> dict[str, Any] | str:
        """Run a source-specific search and return normalized payload."""


class LocalLibrarySourceAdapter:
    """Adapter for the embedded local paper/library tool."""

    source_id = "local_library"

    def __init__(self) -> None:
        self._tool = None

    def search(
        self,
        query: str,
        config: Configuration,
        *,
        loop_count: int,
        max_results: int = 5,
    ) -> dict[str, Any]:
        del config, loop_count

        tool = self._get_tool()
        response = tool.run(
            {
                "query": query,
                "top_k": max_results,
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
            "backend": self.source_id,
            "answer": None,
            "notices": [],
        }

    def _get_tool(self):
        if self._tool is not None:
            return self._tool

        repo_root = Path(__file__).resolve().parents[4]
        paper_assistant_root = repo_root / "deepresearch" / "backend" / "paper_assistant"
        if not paper_assistant_root.exists():
            raise RuntimeError("Unable to locate deepresearch/backend/paper_assistant.")

        if str(paper_assistant_root) not in sys.path:
            sys.path.insert(0, str(paper_assistant_root))

        from app.local_library_tools import LocalLibrarySearchTool

        self._tool = LocalLibrarySearchTool()
        return self._tool


class WebSearchSourceAdapter:
    """Adapter around HelloAgents web search."""

    source_id = "web_search"

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def search(
        self,
        query: str,
        config: Configuration,
        *,
        loop_count: int,
        max_results: int = 5,
    ) -> dict[str, Any] | str:
        backend = self._resolve_backend(config)
        tool = self._tools.get(backend)
        if tool is None:
            from agent_runtime.search_adapter import SearchToolAdapter

            tool = SearchToolAdapter(backend=backend)
            self._tools[backend] = tool

        return tool.run(
            {
                "input": query,
                "backend": backend,
                "mode": "structured",
                "fetch_full_page": config.fetch_full_page,
                "max_results": max_results,
                "max_tokens_per_source": 2000,
                "loop_count": loop_count,
            }
        )

    @staticmethod
    def _resolve_backend(config: Configuration) -> str:
        backend = get_config_value(config.search_api)
        if backend == "local_library":
            return "advanced"
        return backend


class ArxivSourceAdapter:
    """Metadata-level academic retrieval using arXiv."""

    source_id = "academic_search"

    def search(
        self,
        query: str,
        config: Configuration,
        *,
        loop_count: int,
        max_results: int = 5,
    ) -> dict[str, Any]:
        del loop_count

        provider = (
            config.academic_search_provider.value
            if isinstance(config.academic_search_provider, AcademicSearchProvider)
            else str(config.academic_search_provider)
        )
        if provider != "arxiv":
            raise ValueError(f"Unsupported academic search provider: {provider}")

        try:
            response = requests.get(
                ARXIV_API_URL,
                params={
                    "search_query": f"all:{query}",
                    "start": 0,
                    "max_results": max_results,
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
                timeout=20,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("arXiv search failed: %s", exc)
            return {
                "results": [],
                "backend": "arxiv",
                "answer": None,
                "notices": [str(exc)],
            }

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse arXiv response: %s", exc)
            return {
                "results": [],
                "backend": "arxiv",
                "answer": None,
                "notices": ["Invalid arXiv response payload"],
            }

        results: list[dict[str, Any]] = []
        for index, entry in enumerate(root.findall("atom:entry", ATOM_NS), start=1):
            title = self._clean_text(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
            abstract = self._clean_text(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
            entry_url = self._clean_text(entry.findtext("atom:id", default="", namespaces=ATOM_NS))
            published_at = self._clean_text(
                entry.findtext("atom:published", default="", namespaces=ATOM_NS)
            )
            pdf_url = ""
            for link in entry.findall("atom:link", ATOM_NS):
                href = str(link.attrib.get("href") or "").strip()
                title_attr = str(link.attrib.get("title") or "").strip().lower()
                if title_attr == "pdf" or href.endswith(".pdf"):
                    pdf_url = href
                    break

            authors = [
                self._clean_text(author.findtext("atom:name", default="", namespaces=ATOM_NS))
                for author in entry.findall("atom:author", ATOM_NS)
            ]
            authors = [author for author in authors if author]
            score = max(0.1, 1.0 - (index - 1) * 0.05)
            year = published_at[:4] if published_at[:4].isdigit() else ""

            results.append(
                {
                    "title": title or entry_url,
                    "url": entry_url or pdf_url,
                    "content": abstract,
                    "raw_content": abstract,
                    "score": score,
                    "source_type": "academic",
                    "authors": authors,
                    "published_at": published_at,
                    "year": year,
                    "pdf_url": pdf_url,
                }
            )

        return {
            "results": results,
            "backend": "arxiv",
            "answer": None,
            "notices": [],
        }

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join((value or "").split())


_LOCAL_ADAPTER = LocalLibrarySourceAdapter()
_ACADEMIC_ADAPTER = ArxivSourceAdapter()
_WEB_ADAPTER = WebSearchSourceAdapter()


def get_source_adapters() -> dict[str, SourceAdapter]:
    """Return the fixed v1 adapter map."""

    return {
        _LOCAL_ADAPTER.source_id: _LOCAL_ADAPTER,
        _ACADEMIC_ADAPTER.source_id: _ACADEMIC_ADAPTER,
        _WEB_ADAPTER.source_id: _WEB_ADAPTER,
    }

"""Thin source adapters for academic and web retrieval."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Protocol

import requests

from config import AcademicSearchProvider, Configuration
from utils import get_config_value

logger = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_TOP_K = 10
_NEW_STYLE_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_OLD_STYLE_ARXIV_ID_RE = re.compile(r"^[A-Za-z.-]+/\d{7}(v\d+)?$")


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
        return get_config_value(config.search_api)


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

        timeout_seconds = max(1.0, float(config.academic_search_timeout_seconds or 6.0))
        requested_results = max(1, min(int(max_results or ARXIV_TOP_K), ARXIV_TOP_K))
        search_query = self._as_arxiv_query(query)

        try:
            response = requests.get(
                ARXIV_API_URL,
                params=self._build_request_params(search_query, requested_results),
                timeout=timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("arXiv search failed for query %r: %s", search_query, exc)
            return {
                "results": [],
                "backend": "arxiv",
                "answer": None,
                "notices": [self._build_notice(exc)],
                "query": search_query,
            }

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse arXiv response for query %r: %s", search_query, exc)
            return {
                "results": [],
                "backend": "arxiv",
                "answer": None,
                "notices": ["Invalid arXiv response payload"],
                "query": search_query,
            }

        results = [
            self._entry_to_result(entry, index=index, matched_query=search_query)
            for index, entry in enumerate(
                root.findall("atom:entry", ATOM_NS)[:requested_results],
                start=1,
            )
        ]

        return {
            "results": results,
            "backend": "arxiv",
            "answer": None,
            "notices": [],
            "query": search_query,
        }

    def _entry_to_result(
        self,
        entry: ET.Element,
        *,
        index: int,
        matched_query: str,
    ) -> dict[str, Any]:
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
        # arXiv returns entries sorted by relevance. Keep a rank-derived score
        # because downstream evidence heuristics expect one.
        score = max(0.2, 1.0 - (index - 1) * 0.08)
        year = published_at[:4] if published_at[:4].isdigit() else ""

        return {
            "id": self._extract_arxiv_id(entry_url),
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
            "matched_query": matched_query,
        }

    @staticmethod
    def _as_arxiv_query(query: str) -> str:
        if not query:
            return "all:LLM"
        if re.search(r"\b(all|ti|abs|au|cat):", query):
            return query
        normalized_id = ArxivSourceAdapter._normalize_arxiv_id(query)
        if ArxivSourceAdapter._looks_like_arxiv_id(normalized_id):
            return normalized_id
        return f"all:{query}"

    @staticmethod
    def _build_request_params(search_query: str, requested_results: int) -> dict[str, Any]:
        if ArxivSourceAdapter._looks_like_arxiv_id(search_query):
            return {"id_list": ArxivSourceAdapter._normalize_arxiv_id(search_query)}
        return {
            "search_query": search_query,
            "start": 0,
            "max_results": requested_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

    @staticmethod
    def _normalize_arxiv_id(value: str) -> str:
        normalized = (value or "").strip()
        if "/abs/" in normalized:
            normalized = normalized.split("/abs/", 1)[1]
        if "/pdf/" in normalized:
            normalized = normalized.split("/pdf/", 1)[1]
        if normalized.startswith("id:"):
            normalized = normalized[3:]
        normalized = normalized.removesuffix(".pdf")
        if "v" in normalized.split(".")[-1]:
            normalized = normalized.rsplit("v", 1)[0]
        return normalized

    @staticmethod
    def _looks_like_arxiv_id(value: str) -> bool:
        cleaned = (value or "").strip()
        return bool(_NEW_STYLE_ARXIV_ID_RE.match(cleaned) or _OLD_STYLE_ARXIV_ID_RE.match(cleaned))

    @classmethod
    def _extract_arxiv_id(cls, url: str) -> str:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", url or "")
        if not match:
            return ""
        return cls._normalize_arxiv_id(match.group(1))

    @staticmethod
    def _append_unique(items: list[str], value: str) -> None:
        if value and value not in items:
            items.append(value)

    @staticmethod
    def _build_notice(exc: Exception) -> str:
        """Return a short user-facing notice for arXiv failures."""

        if isinstance(exc, requests.Timeout):
            return "arXiv 学术检索超时，已自动继续其他来源。"
        return "arXiv 学术检索暂时不可用，已自动继续其他来源。"

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join((value or "").split())


_ACADEMIC_ADAPTER = ArxivSourceAdapter()
_WEB_ADAPTER = WebSearchSourceAdapter()


def get_source_adapters() -> dict[str, SourceAdapter]:
    """Return the fixed v1 adapter map."""

    return {
        _ACADEMIC_ADAPTER.source_id: _ACADEMIC_ADAPTER,
        _WEB_ADAPTER.source_id: _WEB_ADAPTER,
    }

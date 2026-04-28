"""Shared capability identifiers for deep research execution."""

from __future__ import annotations

SEARCH_ACADEMIC_PAPERS_CAPABILITY = "search_academic_papers"
SEARCH_WEB_PAGES_CAPABILITY = "search_web_pages"

DEFAULT_CAPABILITY_CHAIN = [
    SEARCH_ACADEMIC_PAPERS_CAPABILITY,
    SEARCH_WEB_PAGES_CAPABILITY,
]

VALID_CAPABILITY_IDS = set(DEFAULT_CAPABILITY_CHAIN)

"""Shared source identifiers for deep research retrieval."""

from __future__ import annotations

ACADEMIC_SEARCH_SOURCE = "academic_search"
WEB_SEARCH_SOURCE = "web_search"

ACADEMIC_SOURCE_TYPE = "academic"
WEB_SOURCE_TYPE = "web_search"

DEFAULT_SOURCE_CHAIN = [
    ACADEMIC_SEARCH_SOURCE,
    WEB_SEARCH_SOURCE,
]

VALID_SOURCE_IDS = set(DEFAULT_SOURCE_CHAIN)

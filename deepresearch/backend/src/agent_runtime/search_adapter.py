"""HelloAgents-backed search adapter isolated from business services."""

from __future__ import annotations

from typing import Any

from hello_agents.tools import SearchTool


class SearchToolAdapter:
    """Thin wrapper around HelloAgents SearchTool."""

    def __init__(self, backend: str) -> None:
        self._tool = SearchTool(backend=backend)

    def run(self, payload: dict[str, Any]) -> dict[str, Any] | str:
        """Execute the configured search tool."""

        return self._tool.run(payload)

"""Stable agent interface used by services and orchestrator layers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol


class AgentLike(Protocol):
    """Minimal cross-runtime agent capability surface."""

    def run(self, input_text: str, **kwargs: Any) -> str:
        """Run the agent synchronously and return the full response."""

    def stream_run(self, input_text: str, **kwargs: Any) -> Iterator[str]:
        """Run the agent in streaming mode and yield partial response chunks."""

    def clear_history(self) -> None:
        """Clear any retained conversation history."""

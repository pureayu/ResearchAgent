"""Factory that isolates LangChain runtime construction details."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_runtime.interfaces import AgentLike
from agent_runtime.langchain_agent import LangChainSimpleAgent
from agent_runtime.note_tool import NoteTool
from agent_runtime.roles import AgentSpec, SUMMARIZER_ROLE, get_agent_spec
from config import Configuration
from llm.models import build_chat_model
from services.tool_events import ToolCallTracker


class AgentRuntimeFactory:
    """Create role-specific agents while hiding LangChain wiring details."""

    def __init__(self, config: Configuration) -> None:
        self._config = config
        self._tool_tracker = ToolCallTracker(
            config.notes_workspace if config.enable_notes else None
        )
        self._llm = self._build_llm()
        self._note_tool = self._build_note_tool()

    @property
    def tool_tracker(self) -> ToolCallTracker:
        """Expose the shared tracker used by tool-capable agents."""

        return self._tool_tracker

    @property
    def note_tool(self) -> NoteTool | None:
        """Expose the configured note tool when notes are enabled."""

        return self._note_tool

    def create_agent(self, role_id: str) -> AgentLike:
        """Create an agent instance for the given role."""

        spec = get_agent_spec(role_id)
        llm = self._llm if not spec.llm_overrides else self._build_llm(spec.llm_overrides)
        return LangChainSimpleAgent(
            name=spec.display_name,
            llm=llm,
            system_prompt=spec.system_prompt,
            note_tool=self._note_tool if spec.use_tools else None,
            tool_call_listener=self._tool_tracker.record if spec.use_tools else None,
            keep_history=spec.keep_history,
        )

    def create_agent_from_spec(self, spec: AgentSpec) -> AgentLike:
        """Create an agent instance directly from a supplied role spec."""

        llm = self._llm if not spec.llm_overrides else self._build_llm(spec.llm_overrides)
        return LangChainSimpleAgent(
            name=spec.display_name,
            llm=llm,
            system_prompt=spec.system_prompt,
            note_tool=self._note_tool if spec.use_tools else None,
            tool_call_listener=self._tool_tracker.record if spec.use_tools else None,
            keep_history=spec.keep_history,
        )

    def create_summarizer_factory(self) -> Callable[[], AgentLike]:
        """Return a factory producing fresh summarizer agents."""

        return lambda: self.create_agent(SUMMARIZER_ROLE)

    def _build_llm(self, overrides: dict[str, Any] | None = None) -> Any:
        return build_chat_model(self._config, overrides=overrides)

    def _build_note_tool(self) -> NoteTool | None:
        if not self._config.enable_notes:
            return None
        return NoteTool(workspace=self._config.notes_workspace)

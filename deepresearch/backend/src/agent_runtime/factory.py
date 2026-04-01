"""Factory that isolates HelloAgents runtime construction details."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hello_agents import HelloAgentsLLM, ToolAwareSimpleAgent
from hello_agents.tools import ToolRegistry
from hello_agents.tools.builtin.note_tool import NoteTool

from agent_runtime.interfaces import AgentLike
from agent_runtime.roles import AgentSpec, SUMMARIZER_ROLE, get_agent_spec
from config import Configuration
from services.tool_events import ToolCallTracker


class AgentRuntimeFactory:
    """Create role-specific agents while hiding HelloAgents wiring details."""

    def __init__(self, config: Configuration) -> None:
        self._config = config
        self._tool_tracker = ToolCallTracker(
            config.notes_workspace if config.enable_notes else None
        )
        self._llm = self._build_llm()
        self._note_tool = self._build_note_tool()
        self._tool_registry = self._build_tool_registry(self._note_tool)

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
        return ToolAwareSimpleAgent(
            name=spec.display_name,
            llm=llm,
            system_prompt=spec.system_prompt,
            enable_tool_calling=spec.use_tools and self._tool_registry is not None,
            tool_registry=self._tool_registry if spec.use_tools else None,
            tool_call_listener=self._tool_tracker.record if spec.use_tools else None,
        )

    def create_agent_from_spec(self, spec: AgentSpec) -> AgentLike:
        """Create an agent instance directly from a supplied role spec."""

        llm = self._llm if not spec.llm_overrides else self._build_llm(spec.llm_overrides)
        return ToolAwareSimpleAgent(
            name=spec.display_name,
            llm=llm,
            system_prompt=spec.system_prompt,
            enable_tool_calling=spec.use_tools and self._tool_registry is not None,
            tool_registry=self._tool_registry if spec.use_tools else None,
            tool_call_listener=self._tool_tracker.record if spec.use_tools else None,
        )

    def create_summarizer_factory(self) -> Callable[[], AgentLike]:
        """Return a factory producing fresh summarizer agents."""

        return lambda: self.create_agent(SUMMARIZER_ROLE)

    def _build_llm(self, overrides: dict[str, Any] | None = None) -> HelloAgentsLLM:
        llm_kwargs: dict[str, Any] = {"temperature": 0.0}

        model_id = self._config.llm_model_id or self._config.local_llm
        if model_id:
            llm_kwargs["model"] = model_id

        provider = (self._config.llm_provider or "").strip()
        if provider:
            llm_kwargs["provider"] = provider

        if provider == "ollama":
            llm_kwargs["base_url"] = self._config.sanitized_ollama_url()
            llm_kwargs["api_key"] = self._config.llm_api_key or "ollama"
        elif provider == "lmstudio":
            llm_kwargs["base_url"] = self._config.lmstudio_base_url
            if self._config.llm_api_key:
                llm_kwargs["api_key"] = self._config.llm_api_key
        else:
            if self._config.llm_base_url:
                llm_kwargs["base_url"] = self._config.llm_base_url
            if self._config.llm_api_key:
                llm_kwargs["api_key"] = self._config.llm_api_key

        if overrides:
            llm_kwargs.update(overrides)

        return HelloAgentsLLM(**llm_kwargs)

    def _build_note_tool(self) -> NoteTool | None:
        if not self._config.enable_notes:
            return None
        return NoteTool(workspace=self._config.notes_workspace)

    @staticmethod
    def _build_tool_registry(note_tool: NoteTool | None) -> ToolRegistry | None:
        if note_tool is None:
            return None

        registry = ToolRegistry()
        registry.register_tool(note_tool)
        return registry

"""Model-first capability routing for deep-research tasks."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from agent_runtime.interfaces import AgentLike
from capability_types import (
    DEFAULT_CAPABILITY_CHAIN,
    INSPECT_GITHUB_REPO_CAPABILITY,
    VALID_CAPABILITY_IDS,
)
from config import Configuration
from models import TodoItem
from prompts import get_current_date, source_route_planner_instructions
from utils import strip_thinking_tokens

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceRoutePlan:
    """Structured capability routing plan for one research task."""

    intent_label: str
    preferred_capabilities: list[str]
    confidence: float
    reason: str


class SourceRoutingService:
    """Ask a lightweight classifier to pick a capability order for a task."""

    def __init__(self, routing_agent: AgentLike, config: Configuration) -> None:
        self._agent = routing_agent
        self._config = config
        self._allowed_capabilities = set(DEFAULT_CAPABILITY_CHAIN)
        if config.enable_github_mcp:
            self._allowed_capabilities.add(INSPECT_GITHUB_REPO_CAPABILITY)

    def plan_capabilities(self, research_topic: str, task: TodoItem) -> SourceRoutePlan:
        """Return a capability order with a stable fallback on parse failures."""

        prompt = source_route_planner_instructions.format(
            current_date=get_current_date(),
            research_topic=research_topic,
            task_title=task.title,
            task_intent=task.intent,
            task_query=task.query,
        )
        try:
            response = self._agent.run(prompt)
        except Exception:
            return self._default_plan(reason="route_agent_failed")
        finally:
            try:
                self._agent.clear_history()
            except Exception:
                pass

        logger.info("Source router raw output (truncated): %s", response[:500])
        parsed = self._parse_route_plan(response)
        if parsed is None:
            return self._default_plan(reason="route_parse_failed")
        return parsed

    def _parse_route_plan(self, raw_response: str) -> SourceRoutePlan | None:
        text = raw_response.strip()
        if self._config.strip_thinking_tokens:
            text = strip_thinking_tokens(text)

        payload = self._extract_json_payload(text)
        if not isinstance(payload, dict):
            return None

        preferred_capabilities_raw = payload.get("preferred_capabilities")
        if not isinstance(preferred_capabilities_raw, list):
            return None

        preferred_capabilities: list[str] = []
        for item in preferred_capabilities_raw:
            capability_id = str(item or "").strip()
            if (
                capability_id not in VALID_CAPABILITY_IDS
                or capability_id not in self._allowed_capabilities
                or capability_id in preferred_capabilities
            ):
                continue
            preferred_capabilities.append(capability_id)

        if not preferred_capabilities:
            return None

        confidence = self._clamp_confidence(payload.get("confidence"))
        intent_label = str(payload.get("intent_label") or "other").strip() or "other"
        reason = str(payload.get("reason") or "").strip()

        return SourceRoutePlan(
            intent_label=intent_label,
            preferred_capabilities=preferred_capabilities,
            confidence=confidence,
            reason=reason,
        )

    @staticmethod
    def _extract_json_payload(text: str) -> dict | list | None:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _clamp_confidence(value: object) -> float:
        if isinstance(value, (int, float)):
            return min(max(float(value), 0.0), 1.0)
        if isinstance(value, str):
            try:
                return min(max(float(value), 0.0), 1.0)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _default_plan(*, reason: str) -> SourceRoutePlan:
        return SourceRoutePlan(
            intent_label="general_research",
            preferred_capabilities=list(DEFAULT_CAPABILITY_CHAIN),
            confidence=0.0,
            reason=reason,
        )

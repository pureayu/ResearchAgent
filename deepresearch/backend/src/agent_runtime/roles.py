"""Role specifications for the deep research workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prompts import (
    direct_answer_system_prompt,
    memory_recall_selector_instructions,
    research_reviewer_system_prompt,
    report_writer_instructions,
    response_mode_classifier_instructions,
    task_summarizer_instructions,
    todo_planner_system_prompt,
)


@dataclass(frozen=True)
class AgentSpec:
    """Runtime-independent role definition."""

    role_id: str
    display_name: str
    system_prompt: str
    use_tools: bool = True
    use_notes: bool = True
    keep_history: bool = False
    llm_overrides: dict[str, Any] = field(default_factory=dict)


PLANNER_ROLE = "planner"
REPORTER_ROLE = "reporter"
REVIEWER_ROLE = "reviewer"
SUMMARIZER_ROLE = "summarizer"
DIRECT_ANSWER_ROLE = "direct_answer"
RESPONSE_MODE_CLASSIFIER_ROLE = "response_mode_classifier"
MEMORY_RECALL_SELECTOR_ROLE = "memory_recall_selector"


ROLE_SPECS: dict[str, AgentSpec] = {
    PLANNER_ROLE: AgentSpec(
        role_id=PLANNER_ROLE,
        display_name="研究规划专家",
        system_prompt=todo_planner_system_prompt.strip(),
    ),
    REPORTER_ROLE: AgentSpec(
        role_id=REPORTER_ROLE,
        display_name="报告撰写专家",
        system_prompt=report_writer_instructions.strip(),
    ),
    REVIEWER_ROLE: AgentSpec(
        role_id=REVIEWER_ROLE,
        display_name="研究评审专家",
        system_prompt=research_reviewer_system_prompt.strip(),
    ),
    SUMMARIZER_ROLE: AgentSpec(
        role_id=SUMMARIZER_ROLE,
        display_name="任务总结专家",
        system_prompt=task_summarizer_instructions.strip(),
    ),
    DIRECT_ANSWER_ROLE: AgentSpec(
        role_id=DIRECT_ANSWER_ROLE,
        display_name="个性化直接回答专家",
        system_prompt=direct_answer_system_prompt.strip(),
        use_tools=False,
        use_notes=False,
    ),
    RESPONSE_MODE_CLASSIFIER_ROLE: AgentSpec(
        role_id=RESPONSE_MODE_CLASSIFIER_ROLE,
        display_name="模式分流分类器",
        system_prompt=response_mode_classifier_instructions.strip(),
        use_tools=False,
        use_notes=False,
    ),
    MEMORY_RECALL_SELECTOR_ROLE: AgentSpec(
        role_id=MEMORY_RECALL_SELECTOR_ROLE,
        display_name="会话记忆选择器",
        system_prompt=memory_recall_selector_instructions.strip(),
        use_tools=False,
        use_notes=False,
    ),
}


def get_agent_spec(role_id: str) -> AgentSpec:
    """Return the role specification for the given role identifier."""

    try:
        return ROLE_SPECS[role_id]
    except KeyError as exc:  # pragma: no cover - defensive configuration guard
        raise ValueError(f"Unsupported role_id: {role_id}") from exc

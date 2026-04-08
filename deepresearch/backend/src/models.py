"""State models used by the deep research workflow."""

import operator
from dataclasses import dataclass, field
from typing import List, Optional

from typing_extensions import Annotated


@dataclass(kw_only=True)
class TodoItem:
    """单个待办任务项。"""

    id: int
    title: str
    intent: str
    query: str
    round_id: int = field(default=1)
    origin: str = field(default="planner")
    parent_task_id: Optional[int] = field(default=None)
    status: str = field(default="pending")
    summary: Optional[str] = field(default=None)
    sources_summary: Optional[str] = field(default=None)
    notices: list[str] = field(default_factory=list)
    note_id: Optional[str] = field(default=None)
    note_path: Optional[str] = field(default=None)
    stream_token: Optional[str] = field(default=None)
    attempt_count: int = field(default=0)
    search_backend: Optional[str] = field(default=None)
    evidence_count: int = field(default=0)
    top_score: float = field(default=0.0)
    needs_followup: bool = field(default=False)
    latest_query: Optional[str] = field(default=None)
    evidence_gap_reason: Optional[str] = field(default=None)
    planned_capabilities: list[str] = field(default_factory=list)
    current_capability: Optional[str] = field(default=None)
    route_intent_label: Optional[str] = field(default=None)
    route_confidence: float = field(default=0.0)
    route_reason: Optional[str] = field(default=None)


@dataclass(kw_only=True)
class SummaryState:
    recalled_context: dict | None = field(default=None)  # Default prompt-injected context; includes working memory and long-term memory, excludes task logs.
    session_id : Optional[str] = field(default=None)
    #记录每轮的id号， 一次完整研究流程的编号
    run_id: Optional[str] = field(default=None)
    response_mode: str = field(default="deep_research")
    research_topic: str = field(default=None)  # Report topic
    search_query: str = field(default=None)  # Deprecated placeholder
    web_research_results: Annotated[list, operator.add] = field(default_factory=list)
    sources_gathered: Annotated[list, operator.add] = field(default_factory=list)
    research_loop_count: int = field(default=0)  # Research loop count
    running_summary: str = field(default=None)  # Legacy summary field
    todo_items: Annotated[list, operator.add] = field(default_factory=list)
    structured_report: Optional[str] = field(default=None)
    report_note_id: Optional[str] = field(default=None)
    report_note_path: Optional[str] = field(default=None)


@dataclass(kw_only=True)
class SummaryStateInput:
    research_topic: str = field(default=None)  # Report topic


@dataclass(kw_only=True)
class SummaryStateOutput:
    session_id: Optional[str] = field(default=None)
    running_summary: str = field(default=None)  # Backward-compatible文本
    report_markdown: Optional[str] = field(default=None)
    todo_items: List[TodoItem] = field(default_factory=list)

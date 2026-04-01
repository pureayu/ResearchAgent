"""Shared execution-layer models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models import TodoItem


@dataclass(kw_only=True)
class ExecutionEvent:
    """A task-local event emitted by an executor."""

    payload: dict[str, Any]


@dataclass(kw_only=True)
class TaskPatch:
    """Structured task state patch applied by the orchestrator."""

    status: str | None = None
    summary: str | None = None
    sources_summary: str | None = None
    notices: list[str] = field(default_factory=list)
    note_id: str | None = None
    note_path: str | None = None
    attempt_count: int = 0
    search_backend: str | None = None
    evidence_count: int = 0
    top_score: float = 0.0
    needs_followup: bool = False
    latest_query: str | None = None
    evidence_gap_reason: str | None = None

    @classmethod
    def from_task(cls, task: TodoItem) -> "TaskPatch":
        """Create a patch from a task snapshot."""

        return cls(
            status=task.status,
            summary=task.summary,
            sources_summary=task.sources_summary,
            notices=list(task.notices),
            note_id=task.note_id,
            note_path=task.note_path,
            attempt_count=task.attempt_count,
            search_backend=task.search_backend,
            evidence_count=task.evidence_count,
            top_score=task.top_score,
            needs_followup=task.needs_followup,
            latest_query=task.latest_query,
            evidence_gap_reason=task.evidence_gap_reason,
        )


@dataclass(kw_only=True)
class TaskExecutionResult:
    """Final structured outcome of one executor run."""

    status: str
    task_patch: TaskPatch
    events: list[ExecutionEvent] = field(default_factory=list)
    search_result: dict[str, Any] | None = None
    answer_text: str | None = None
    followup_triggered: bool = False
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    context_to_append: str | None = None
    sources_to_append: str | None = None
    research_loop_increment: int = 0
    error: str | None = None

"""Pydantic schemas for LangChain structured-output calls."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlannerTaskItem(BaseModel):
    """One planned research task."""

    title: str = Field(default="", description="任务名称")
    intent: str = Field(default="", description="任务目标")
    query: str = Field(default="", description="Primary search query for backward compatibility")
    queries: list[str] = Field(default_factory=list, description="Multiple complementary search queries")


class PlannerTasksOutput(BaseModel):
    """Structured planner response."""

    tasks: list[PlannerTaskItem] = Field(default_factory=list)


class ReviewerFollowupTask(BaseModel):
    """One reviewer-proposed follow-up task."""

    title: str = Field(default="", description="追加任务名称")
    intent: str = Field(default="", description="任务要补足的缺口")
    query: str = Field(default="", description="Primary search query for backward compatibility")
    queries: list[str] = Field(default_factory=list, description="Multiple complementary search queries")
    parent_task_id: int | None = Field(default=None, description="关联的父任务 ID")


class ReviewerOutput(BaseModel):
    """Structured reviewer verdict."""

    is_sufficient: bool = Field(default=True)
    overall_gap: str = Field(default="")
    confidence: float = Field(default=0.0)
    followup_tasks: list[ReviewerFollowupTask] = Field(default_factory=list)


class SourceRouteOutput(BaseModel):
    """Structured capability routing decision."""

    intent_label: str = Field(default="general_research")
    preferred_capabilities: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0)
    reason: str = Field(default="")


class ResponseModeSelectionOutput(BaseModel):
    """Structured response-mode classification."""

    response_mode: Literal["memory_recall", "direct_answer", "deep_research"]
    confidence: float = Field(default=0.0)
    reason: str = Field(default="")


class MemoryRecallSelectionOutput(BaseModel):
    """Structured memory-selection result."""

    task_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)

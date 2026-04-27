"""Models for durable research project workspaces."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Return an ISO timestamp suitable for persisted state files."""

    return datetime.now(timezone.utc).isoformat()


class ProjectStage(str, Enum):
    """High-level stages aligned with the ARIS research lifecycle."""

    INTAKE = "intake"
    IDEA_DISCOVERY = "idea_discovery"
    HUMAN_GATE = "human_gate"
    REFINE_PLAN = "refine_plan"
    EXPERIMENT_BRIDGE = "experiment_bridge"
    RUN_EXPERIMENT = "run_experiment"
    MONITOR_EXPERIMENT = "monitor_experiment"
    AUTO_REVIEW = "auto_review"
    PAPER_WRITE = "paper_write"
    DONE = "done"


class ProjectStatus(BaseModel):
    """Canonical machine-readable state for a research project."""

    project_id: str
    topic: str
    stage: ProjectStage = ProjectStage.INTAKE
    selected_idea: str = ""
    contract_path: str = "docs/research_contract.md"
    experiment_plan_path: str = "refine-logs/EXPERIMENT_PLAN.md"
    experiment_tracker_path: str = "refine-logs/EXPERIMENT_TRACKER.md"
    baseline: str = ""
    current_branch: str = ""
    training_status: str = "not_started"
    active_tasks: list[str] = Field(default_factory=list)
    next_action: str = "Run idea discovery or attach an existing research direction."
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    def merged(self, patch: dict[str, Any]) -> "ProjectStatus":
        """Return a new status with allowed user-provided fields patched."""

        allowed = {
            "stage",
            "selected_idea",
            "baseline",
            "current_branch",
            "training_status",
            "active_tasks",
            "next_action",
        }
        data = self.model_dump()
        for key, value in patch.items():
            if key in allowed and value is not None:
                data[key] = value
        data["updated_at"] = utc_now_iso()
        return ProjectStatus(**data)


class ProjectSnapshot(BaseModel):
    """Summary returned by the workspace service and HTTP API."""

    project_id: str
    root_path: str
    status: ProjectStatus
    files: dict[str, str]


class IdeaCandidate(BaseModel):
    """One structured research idea candidate extracted from a discovery report."""

    title: str = Field(default="")
    problem: str = Field(default="")
    hypothesis: str = Field(default="")
    minimum_viable_experiment: str = Field(default="")
    expected_outcome: str = Field(default="")
    method_sketch: str = Field(default="")
    expected_signal: str = Field(default="")
    novelty_risk: str = Field(default="")
    feasibility: str = Field(default="")
    impact: str = Field(default="")
    risk_level: Literal["low", "medium", "high", "unclear"] = Field(default="unclear")
    contribution_type: Literal[
        "empirical",
        "method",
        "system",
        "theory",
        "diagnostic",
        "unclear",
    ] = Field(default="unclear")
    ranking_rationale: str = Field(default="")
    estimated_effort: str = Field(default="")
    reviewer_objection: str = Field(default="")
    why_do_this: str = Field(default="")
    pilot_signal: Literal["not_run", "positive", "weak_positive", "negative", "skipped"] = Field(
        default="not_run"
    )
    required_experiments: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0)
    closest_related_work: list[str] = Field(default_factory=list)
    overlap_analysis: str = Field(default="")
    novelty_claim: str = Field(default="")
    novelty_verdict: Literal["novel", "incremental", "overlapping", "unclear"] = Field(
        default="unclear"
    )
    novelty_confidence: float = Field(default=0.0)


class IdeaDiscoveryResult(BaseModel):
    """Result of the minimal project-level idea discovery step."""

    project_id: str
    report_markdown: str = ""
    selected_idea: IdeaCandidate | None = None
    candidates: list[IdeaCandidate] = Field(default_factory=list)
    snapshot: ProjectSnapshot


class DirectionRefinementResult(BaseModel):
    """Result of refining a selected broad direction into a concrete idea."""

    project_id: str
    original_idea: IdeaCandidate
    refined_idea: IdeaCandidate
    snapshot: ProjectSnapshot


class IdeaCandidatesOutput(BaseModel):
    """Structured LLM output for idea candidate extraction."""

    candidates: list[IdeaCandidate] = Field(default_factory=list)


class NoveltyCheckOutput(BaseModel):
    """Structured output for one idea novelty check."""

    closest_related_work: list[str] = Field(default_factory=list)
    overlap_analysis: str = Field(default="")
    novelty_claim: str = Field(default="")
    novelty_verdict: Literal["novel", "incremental", "overlapping", "unclear"] = Field(
        default="unclear"
    )
    novelty_confidence: float = Field(default=0.0)


class ExternalReviewOutput(BaseModel):
    """One external review verdict for the active project idea."""

    verdict: Literal["positive", "needs_revision", "reject", "unclear"] = Field(
        default="unclear"
    )
    summary: str = Field(default="")
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    raw_review: str = Field(default="")


class ExternalReviewResult(BaseModel):
    """Persisted result of one external review loop round."""

    project_id: str
    round: int
    status: str
    review: ExternalReviewOutput
    snapshot: ProjectSnapshot


class ExperimentTask(BaseModel):
    """One experiment task generated by the experiment bridge."""

    id: str
    title: str
    goal: str = Field(default="")
    command: str = Field(default="TBD")
    expected_signal: str = Field(default="")
    status: str = Field(default="pending")


class ExperimentBridgeResult(BaseModel):
    """Result of translating the selected idea into experiment tasks."""

    project_id: str
    tasks: list[ExperimentTask] = Field(default_factory=list)
    snapshot: ProjectSnapshot

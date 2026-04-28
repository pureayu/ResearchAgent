"""External review loop persistence for project workspaces."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from project_workspace.models import (
    ExperimentTask,
    ExternalReviewOutput,
    ExternalReviewResult,
    IdeaCandidate,
    ProjectStage,
    ProjectStatus,
)
from project_workspace.service import ProjectWorkspaceService


ExternalReviewer = Callable[[ProjectStatus, IdeaCandidate | None], ExternalReviewOutput]


class ExternalReviewService:
    """Run and persist one external review round."""

    def __init__(
        self,
        workspace: ProjectWorkspaceService,
        *,
        reviewer: ExternalReviewer | None = None,
    ) -> None:
        self._workspace = workspace
        self._reviewer = reviewer

    def run(
        self,
        project_id: str,
        *,
        review_text: str | None = None,
        verdict: str | None = None,
        max_rounds: int = 10,
    ) -> ExternalReviewResult:
        """Append one review round and update REVIEW_STATE.json."""

        status = self._workspace.load_status(project_id)
        candidate = self._load_selected_candidate(project_id, status)
        state = self._load_review_state(project_id, max_rounds=max_rounds)
        round_id = int(state.get("round") or 0) + 1

        if review_text:
            review = _review_from_text(review_text, verdict=verdict)
        elif self._reviewer is not None:
            review = self._reviewer(status, candidate)
        else:
            review = ExternalReviewOutput(
                verdict="unclear",
                summary="No external reviewer is configured for this project.",
                weaknesses=["External review has not been run yet."],
                action_items=["Configure an external reviewer or provide review_text."],
                raw_review="External review unavailable.",
            )

        state = {
            "round": round_id,
            "max_rounds": max_rounds,
            "status": _status_from_verdict(review.verdict, round_id, max_rounds),
            "latest_thread_id": state.get("latest_thread_id"),
            "latest_verdict": review.verdict,
            "open_actions": list(review.action_items),
        }
        self._workspace.write_text(
            project_id,
            "REVIEW_STATE.json",
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        )
        self._append_auto_review(project_id, round_id, status, candidate, review)
        if review.verdict != "positive":
            self._write_revision_artifacts(project_id, round_id, status, candidate, review)

        snapshot = self._workspace.update_status(
            project_id,
            {
                "stage": ProjectStage.AUTO_REVIEW,
                "training_status": (
                    "review_accepted"
                    if review.verdict == "positive"
                    else "revision_required"
                ),
                "active_tasks": list(review.action_items),
                "next_action": _next_action_from_review(review),
            },
            refresh_contract=False,
        )
        return ExternalReviewResult(
            project_id=snapshot.project_id,
            round=round_id,
            status=str(state["status"]),
            review=review,
            snapshot=snapshot,
        )

    def _load_selected_candidate(
        self,
        project_id: str,
        status: ProjectStatus,
    ) -> IdeaCandidate | None:
        snapshot = self._workspace.snapshot(project_id)
        path = Path(snapshot.files["idea_candidates_json"])
        if not path.exists():
            return None
        candidates = [
            IdeaCandidate.model_validate(item)
            for item in json.loads(path.read_text(encoding="utf-8") or "[]")
            if isinstance(item, dict)
        ]
        for candidate in candidates:
            if candidate.title == status.selected_idea:
                return candidate
        return candidates[0] if candidates else None

    def _load_review_state(self, project_id: str, *, max_rounds: int) -> dict:
        try:
            return json.loads(self._workspace.read_text(project_id, "REVIEW_STATE.json"))
        except Exception:
            return {
                "round": 0,
                "max_rounds": max_rounds,
                "status": "not_started",
                "latest_thread_id": None,
                "latest_verdict": None,
                "open_actions": [],
            }

    def _append_auto_review(
        self,
        project_id: str,
        round_id: int,
        status: ProjectStatus,
        candidate: IdeaCandidate | None,
        review: ExternalReviewOutput,
    ) -> None:
        existing = self._workspace.read_text(project_id, "AUTO_REVIEW.md")
        selected = candidate.title if candidate else status.selected_idea or "TBD"
        content = f"""{existing.rstrip()}

## Round {round_id}

- selected_idea: {selected}
- verdict: {review.verdict}
- summary: {review.summary or "TBD"}

### Strengths

{_render_list(review.strengths)}

### Weaknesses

{_render_list(review.weaknesses)}

### Action Items

{_render_list(review.action_items)}

### Raw Review

{review.raw_review or review.summary or "TBD"}
"""
        self._workspace.write_text(project_id, "AUTO_REVIEW.md", content)

    def _write_revision_artifacts(
        self,
        project_id: str,
        round_id: int,
        status: ProjectStatus,
        candidate: IdeaCandidate | None,
        review: ExternalReviewOutput,
    ) -> None:
        self._workspace.write_text(
            project_id,
            "refine-logs/REVISION_PLAN.md",
            render_revision_plan(round_id, status, candidate, review),
        )
        self._workspace.write_text(
            project_id,
            "refine-logs/DRAFT_EXPERIMENT_TRACKER.md",
            render_draft_experiment_tracker(candidate, review),
        )


def _review_from_text(review_text: str, *, verdict: str | None) -> ExternalReviewOutput:
    normalized_verdict = verdict if verdict in {"positive", "needs_revision", "reject", "unclear"} else "unclear"
    return ExternalReviewOutput(
        verdict=normalized_verdict,
        summary=review_text.strip().splitlines()[0][:500] if review_text.strip() else "",
        weaknesses=[] if normalized_verdict == "positive" else ["Review text requires follow-up triage."],
        action_items=[] if normalized_verdict == "positive" else ["Address external review feedback."],
        raw_review=review_text.strip(),
    )


def _status_from_verdict(verdict: str, round_id: int, max_rounds: int) -> str:
    if verdict == "positive":
        return "accepted"
    if round_id >= max_rounds:
        return "max_rounds_reached"
    if verdict in {"needs_revision", "reject"}:
        return "needs_revision"
    return "review_pending"


def _next_action_from_review(review: ExternalReviewOutput) -> str:
    if review.verdict == "positive":
        return "Proceed to experiment bridge."
    if review.action_items:
        return "Revise using REVISION_PLAN.md, then run another review round."
    return "Configure an external reviewer or provide review_text."


def _render_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item.strip()) or "- None"


def render_revision_plan(
    round_id: int,
    status: ProjectStatus,
    candidate: IdeaCandidate | None,
    review: ExternalReviewOutput,
) -> str:
    """Render reviewer feedback into an explicit revision plan."""

    selected = candidate.title if candidate else status.selected_idea or "TBD"
    return f"""# Revision Plan

This project has not passed external review yet. Do not treat any experiment
tracker as final until a later review returns `positive`.

## Review Round

- round: {round_id}
- verdict: {review.verdict}
- selected_idea: {selected}

## Reviewer Summary

{review.summary or "TBD"}

## Required Revisions

{_render_list(review.action_items)}

## Reviewer Concerns

{_render_list(review.weaknesses)}

## Revision Checklist

- Clarify the novelty claim against closest related work.
- Remove or weaken over-strong first-system claims.
- Add feasibility constraints and fallback paths for risky components.
- Add missing baselines and statistical protocol details.
- Add quality, overhead, and cost metrics before experiment execution.

## Next Step

Revise the research contract and selected idea, then run another external review
round. Generate the formal experiment tracker only after a positive review.
"""


def render_draft_experiment_tracker(
    candidate: IdeaCandidate | None,
    review: ExternalReviewOutput,
) -> str:
    """Render a non-executable draft tracker for revision-only states."""

    rows = "\n".join(
        f"| {task.id} | {task.status} | {task.title} | {task.expected_signal or 'TBD'} |"
        for task in _draft_tasks(candidate, review)
    )
    return f"""# Draft Experiment Tracker

This is a draft generated under reviewer verdict `{review.verdict}`. It is for
planning only and should not be executed as the formal experiment plan until the
revision passes review.

| ID | Status | Task | Expected Signal |
| --- | --- | --- | --- |
{rows}
"""


def _draft_tasks(
    candidate: IdeaCandidate | None,
    review: ExternalReviewOutput,
) -> list[ExperimentTask]:
    tasks = [
        ExperimentTask(
            id="R0",
            title="Revise novelty and feasibility claims",
            goal="Address reviewer concerns before running experiments.",
            expected_signal="Reviewer concerns are explicitly resolved in the revised plan.",
            status="draft",
        )
    ]
    for index, action in enumerate(review.action_items[:6], start=1):
        tasks.append(
            ExperimentTask(
                id=f"R{index}",
                title=_task_title(action),
                goal=action,
                expected_signal="Revision item is reflected in the contract, baselines, or metrics.",
                status="draft",
            )
        )
    if candidate and not review.action_items:
        for index, experiment in enumerate(candidate.required_experiments[:4], start=1):
            tasks.append(
                ExperimentTask(
                    id=f"R{index}",
                    title=_task_title(experiment),
                    goal=experiment,
                    expected_signal=candidate.expected_signal,
                    status="draft",
                )
            )
    return tasks


def _task_title(text: str) -> str:
    first = text.split(":", 1)[0].strip()
    return first or text.strip() or "Revision task"

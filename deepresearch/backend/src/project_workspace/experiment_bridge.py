"""Experiment bridge from selected idea to trackable experiment tasks."""

from __future__ import annotations

import json
from pathlib import Path

from project_workspace.models import (
    ExperimentBridgeResult,
    ExperimentTask,
    IdeaCandidate,
    ProjectStage,
    ProjectStatus,
)
from project_workspace.service import ProjectWorkspaceService


class ExperimentBridgeService:
    """Generate experiment tracker/log entries from the selected project idea."""

    def __init__(self, workspace: ProjectWorkspaceService) -> None:
        self._workspace = workspace

    def run(
        self,
        project_id: str,
        *,
        sanity_first: bool = True,
    ) -> ExperimentBridgeResult:
        """Write experiment tasks and update project status."""

        status = self._workspace.load_status(project_id)
        candidate = self._load_selected_candidate(project_id, status)
        if candidate is None:
            raise ValueError("No selected idea is available for experiment bridge")
        self._ensure_review_passed(project_id)

        tasks = build_experiment_tasks(candidate, sanity_first=sanity_first)
        self._workspace.write_text(
            project_id,
            status.experiment_tracker_path,
            render_experiment_tracker(tasks),
        )
        self._append_experiment_log(project_id, status, candidate, tasks)
        snapshot = self._workspace.update_status(
            project_id,
            {
                "stage": ProjectStage.EXPERIMENT_BRIDGE,
                "training_status": "planned",
                "active_tasks": [f"{task.id}: {task.title}" for task in tasks],
                "next_action": "Review experiment tracker, fill commands, then run sanity check.",
            },
            refresh_contract=False,
        )
        return ExperimentBridgeResult(
            project_id=snapshot.project_id,
            tasks=tasks,
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

    def _ensure_review_passed(self, project_id: str) -> None:
        try:
            state = json.loads(self._workspace.read_text(project_id, "REVIEW_STATE.json"))
        except Exception:
            return
        if int(state.get("round") or 0) <= 0:
            return
        if state.get("latest_verdict") == "positive" and state.get("status") == "accepted":
            return
        if state.get("latest_verdict") == "needs_revision":
            return
        raise ValueError(
            "External review rejected or could not judge this idea; revise first using REVISION_PLAN.md"
        )

    def _append_experiment_log(
        self,
        project_id: str,
        status: ProjectStatus,
        candidate: IdeaCandidate,
        tasks: list[ExperimentTask],
    ) -> None:
        existing = self._workspace.read_text(project_id, "EXPERIMENT_LOG.md")
        task_lines = "\n".join(
            f"- {task.id}: {task.title} | status={task.status} | command={task.command}"
            for task in tasks
        )
        content = f"""{existing.rstrip()}

## Experiment Bridge

- selected_idea: {candidate.title}
- project_stage: {status.stage.value}

### Generated Tasks

{task_lines}
"""
        self._workspace.write_text(project_id, "EXPERIMENT_LOG.md", content)


def build_experiment_tasks(
    candidate: IdeaCandidate,
    *,
    sanity_first: bool = True,
) -> list[ExperimentTask]:
    """Build a minimal experiment task list from one selected idea."""

    tasks: list[ExperimentTask] = []
    if sanity_first:
        tasks.append(
            ExperimentTask(
                id="E0",
                title="Sanity check",
                goal="Verify the code/data/evaluation path on a tiny setting before full experiments.",
                expected_signal="Pipeline completes and produces interpretable metrics.",
            )
        )

    experiments = candidate.required_experiments or [
        "Compare the selected method against a strong baseline.",
        "Run one ablation to isolate the claimed mechanism.",
    ]
    start_index = 1 if sanity_first else 0
    for offset, experiment in enumerate(experiments, start=start_index):
        tasks.append(
            ExperimentTask(
                id=f"E{offset}",
                title=experiment[:120],
                goal=experiment,
                expected_signal=candidate.expected_signal or "Evidence supports or falsifies the core hypothesis.",
            )
        )
    return tasks


def render_experiment_tracker(tasks: list[ExperimentTask]) -> str:
    """Render experiment tasks as the durable tracker Markdown."""

    rows = "\n".join(
        f"| {task.id} | {task.status} | {task.title} | {task.command} | {task.expected_signal} |"
        for task in tasks
    )
    return f"""# Experiment Tracker

| ID | Status | Task | Command | Expected Signal |
| --- | --- | --- | --- | --- |
{rows}
"""

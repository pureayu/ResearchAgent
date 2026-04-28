"""Markdown and JSON templates for project-level research state."""

from __future__ import annotations

import json

from project_workspace.models import IdeaCandidate, ProjectStatus


def render_project_card(status: ProjectStatus) -> str:
    """Render the searchable per-project card."""

    name = status.name or status.topic
    description = status.description or status.next_action or status.topic
    return f"""# Project Card

Name: {name}
Description: {description}

## Metadata

- project_id: {status.project_id}
- topic: {status.topic}
- stage: {status.stage.value}
- selected_idea: {status.selected_idea or "TBD"}
- updated_at: {status.updated_at}

## Key Files

- PROJECT_STATUS.json
- IDEA_REPORT.md
- IDEA_CANDIDATES.md
- docs/research_contract.md
- REVIEW_STATE.json
- AUTO_REVIEW.md
- refine-logs/REVISION_PLAN.md
- refine-logs/EXPERIMENT_TRACKER.md
- EXPERIMENT_LOG.md
"""


def render_workspace_index(statuses: list[ProjectStatus]) -> str:
    """Render the root-level index over all research projects."""

    if not statuses:
        return "# Project Index\n\nNo research projects yet.\n"

    lines = [
        "# Project Index",
        "",
        "This file is the workspace-level index for local research project recall.",
        "",
    ]
    for status in statuses:
        name = status.name or status.topic
        description = status.description or status.next_action or status.topic
        lines.extend(
            [
                f"## {name}",
                "",
                f"- project_id: {status.project_id}",
                f"- description: {description}",
                f"- topic: {status.topic}",
                f"- stage: {status.stage.value}",
                f"- selected_idea: {status.selected_idea or 'TBD'}",
                f"- updated_at: {status.updated_at}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_claude_md(status: ProjectStatus) -> str:
    """Render a human-readable project status file compatible with ARIS habits."""

    active_tasks = "\n".join(f"- {task}" for task in status.active_tasks) or "- None"
    return f"""# Research Project

Name: {status.name or status.topic}
Description: {status.description or status.next_action}

## Pipeline Status

- project_id: {status.project_id}
- stage: {status.stage.value}
- topic: {status.topic}
- selected_idea: {status.selected_idea or "TBD"}
- contract: {status.contract_path}
- experiment_plan: {status.experiment_plan_path}
- current_branch: {status.current_branch or "TBD"}
- baseline: {status.baseline or "TBD"}
- training_status: {status.training_status}
- next: {status.next_action}
- updated_at: {status.updated_at}

## Active Tasks

{active_tasks}
"""


def render_research_contract(
    status: ProjectStatus,
    candidate: IdeaCandidate | None = None,
) -> str:
    """Render the active-idea contract used to keep later sessions focused."""

    selected = candidate.title if candidate else status.selected_idea or "TBD"
    problem = candidate.problem if candidate else "TBD"
    method = candidate.method_sketch if candidate else "TBD"
    signal = candidate.expected_signal if candidate else "TBD"
    experiments = candidate.required_experiments if candidate else []
    experiment_lines = "\n".join(f"- {item}" for item in experiments) or "TBD"
    return f"""# Research Contract

## Selected Idea

{selected}

## Problem

{problem}

## Core Claims

- Hypothesis: {candidate.hypothesis if candidate else "TBD"}
- Expected signal: {signal}

## Method Summary

{method}

## Experiment Design

{experiment_lines}

## Baselines

{status.baseline or "TBD"}

## Current Results

TBD

## Key Decisions

- Created from topic: {status.topic}

## Status

- stage: {status.stage.value}
- next: {status.next_action}
"""


def render_experiment_plan(
    status: ProjectStatus,
    candidate: IdeaCandidate | None = None,
) -> str:
    """Render a claim-driven experiment plan skeleton."""

    thesis = candidate.hypothesis if candidate else "TBD"
    problem = candidate.problem if candidate else status.topic
    method = candidate.method_sketch if candidate else "TBD"
    expected_signal = candidate.expected_signal if candidate else "TBD"
    experiments = candidate.required_experiments if candidate else []
    experiment_blocks = "\n\n".join(
        f"### E{index}: {_experiment_title(item)}\n\n"
        f"- Claim tested: {item}\n"
        f"- Evidence needed: {expected_signal}\n"
        "- Command: TODO after selecting the concrete device, model, runtime, workload config, and output path."
        for index, item in enumerate(experiments, start=1)
    ) or "### E1: TBD\n\n- Claim tested: TBD\n- Evidence needed: TBD\n- Command: TBD"
    run_order = "\n".join(
        [f"{index}. E{index}: {_experiment_title(item)}" for index, item in enumerate(experiments, start=1)]
    ) or "1. E1: TBD"
    return f"""# Experiment Plan

## Problem

{problem}

## Method Thesis

{thesis}

## Method Sketch

{method}

## Claim Map

{experiment_blocks}

## Experiment Blocks

### E0: Sanity Check

- Goal: Verify the pipeline runs end-to-end on a tiny setting.
- Command: TODO after selecting the concrete device, model, runtime, workload config, and output path.
- Expected signal: Pipeline completes and produces interpretable metrics.

## Run Order

0. E0: sanity check
{run_order}

## Compute Budget

TBD

## Risks

- Novelty risk: {candidate.novelty_risk if candidate else "TBD"}
- Feasibility: {candidate.feasibility if candidate else "TBD"}
"""


def render_idea_candidates(candidates: list[IdeaCandidate]) -> str:
    """Render structured idea candidates as Markdown."""

    if not candidates:
        return "# Idea Candidates\n\nNo candidates yet.\n"

    blocks = ["# Idea Candidates\n"]
    for index, candidate in enumerate(candidates, start=1):
        experiments = "\n".join(
            f"- {item}" for item in candidate.required_experiments
        ) or "- TBD"
        blocks.append(
            f"""## {index}. {candidate.title}

- score: {candidate.score:.2f}
- problem: {candidate.problem}
- hypothesis: {candidate.hypothesis}
- minimum_viable_experiment: {candidate.minimum_viable_experiment or "TBD"}
- expected_outcome: {candidate.expected_outcome or "TBD"}
- method_sketch: {candidate.method_sketch}
- expected_signal: {candidate.expected_signal}
- impact: {candidate.impact or "TBD"}
- novelty_risk: {candidate.novelty_risk}
- novelty_verdict: {candidate.novelty_verdict}
- novelty_confidence: {candidate.novelty_confidence:.2f}
- novelty_claim: {candidate.novelty_claim or "TBD"}
- overlap_analysis: {candidate.overlap_analysis or "TBD"}
- feasibility: {candidate.feasibility}
- risk_level: {candidate.risk_level}
- contribution_type: {candidate.contribution_type}
- estimated_effort: {candidate.estimated_effort or "TBD"}
- reviewer_objection: {candidate.reviewer_objection or "TBD"}
- why_do_this: {candidate.why_do_this or "TBD"}
- pilot_signal: {candidate.pilot_signal}
- ranking_rationale: {candidate.ranking_rationale or "TBD"}

### Closest Related Work

{_render_list(candidate.closest_related_work) or "- TBD"}

### Required Experiments

{experiments}
"""
        )
    return "\n".join(blocks)


def _render_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item.strip())


def _experiment_title(text: str) -> str:
    first = text.split(":", 1)[0].strip()
    return first or text.strip() or "Experiment"


def render_experiment_tracker(_: ProjectStatus) -> str:
    """Render the durable experiment progress tracker."""

    return """# Experiment Tracker

| ID | Status | Command | Output | Verdict |
| --- | --- | --- | --- | --- |
| E0 | pending | TBD | TBD | TBD |
"""


def render_experiment_log(status: ProjectStatus) -> str:
    """Render the append-only experiment log."""

    return f"""# Experiment Log

## Project

- project_id: {status.project_id}
- topic: {status.topic}

## Entries

No experiment entries yet.
"""


def render_review_state() -> str:
    """Render the initial auto-review state JSON."""

    return json.dumps(
        {
            "round": 0,
            "max_rounds": 10,
            "status": "not_started",
            "latest_thread_id": None,
            "latest_verdict": None,
            "open_actions": [],
        },
        indent=2,
        ensure_ascii=False,
    ) + "\n"


STATIC_TEMPLATES = {
    "IDEA_REPORT.md": "# Idea Report\n\nNo idea discovery run has been recorded yet.\n",
    "IDEA_CANDIDATES.md": "# Idea Candidates\n\nNo candidates yet.\n",
    "AUTO_REVIEW.md": "# Auto Review\n\nNo external review rounds yet.\n",
    "findings.md": "# Findings\n\nNo stable findings yet.\n",
}

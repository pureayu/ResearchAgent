"""LangChain-backed external reviewer for active research ideas."""

from __future__ import annotations

from config import Configuration
from project_workspace.models import (
    ExternalReviewOutput,
    IdeaCandidate,
    ProjectStatus,
)


def build_structured_external_reviewer(config: Configuration):
    """Build a real-model reviewer that returns a validated review verdict."""

    from llm import StructuredOutputRunner, build_chat_model

    model = build_chat_model(config)
    runner: StructuredOutputRunner[ExternalReviewOutput] = StructuredOutputRunner(
        model,
        system_prompt=(
            "You are an external senior research reviewer. Be skeptical and concrete. "
            "Review the active idea for novelty, feasibility, evaluation quality, and "
            "paper-worthiness. Return actionable feedback. Use verdict=positive only "
            "when the idea is ready to proceed to experiments; use needs_revision for "
            "fixable gaps; use reject for severe flaws; use unclear when evidence is insufficient."
        ),
        schema=ExternalReviewOutput,
        agent_name="ExternalResearchReviewer",
    )

    def review(
        status: ProjectStatus,
        candidate: IdeaCandidate | None,
    ) -> ExternalReviewOutput:
        prompt = f"""Project:
- project_id: {status.project_id}
- topic: {status.topic}
- stage: {status.stage.value}
- selected_idea: {status.selected_idea or "TBD"}

Candidate:
{_format_candidate(candidate)}

Review this research idea as an external reviewer. Include strengths, weaknesses, action_items, and a conservative verdict."""
        output = runner.invoke(prompt)
        if not output.raw_review:
            output.raw_review = output.summary
        if not output.summary:
            output.summary = _fallback_summary(output)
        return output

    return review


def _format_candidate(candidate: IdeaCandidate | None) -> str:
    if candidate is None:
        return "No structured candidate is available."
    experiments = "\n".join(f"- {item}" for item in candidate.required_experiments) or "- TBD"
    related = "\n".join(f"- {item}" for item in candidate.closest_related_work) or "- TBD"
    return f"""- title: {candidate.title}
- problem: {candidate.problem}
- hypothesis: {candidate.hypothesis}
- method_sketch: {candidate.method_sketch}
- expected_signal: {candidate.expected_signal}
- impact: {candidate.impact}
- risk_level: {candidate.risk_level}
- contribution_type: {candidate.contribution_type}
- ranking_rationale: {candidate.ranking_rationale}
- pilot_signal: {candidate.pilot_signal}
- novelty_verdict: {candidate.novelty_verdict}
- novelty_confidence: {candidate.novelty_confidence}
- novelty_claim: {candidate.novelty_claim}
- overlap_analysis: {candidate.overlap_analysis}
- feasibility: {candidate.feasibility}
- score: {candidate.score}

Required experiments:
{experiments}

Closest related work:
{related}
"""


def _fallback_summary(output: ExternalReviewOutput) -> str:
    if output.action_items:
        return f"Reviewer verdict: {output.verdict}; first action: {output.action_items[0]}"
    if output.weaknesses:
        return f"Reviewer verdict: {output.verdict}; main weakness: {output.weaknesses[0]}"
    return f"Reviewer verdict: {output.verdict}."

"""Refine a selected broad direction into a concrete research idea."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from project_workspace.models import (
    DirectionRefinementResult,
    IdeaCandidate,
)
from project_workspace.service import ProjectWorkspaceService


DirectionRefiner = Callable[[IdeaCandidate, str, str], IdeaCandidate]
logger = logging.getLogger(__name__)


class DirectionRefinementService:
    """Narrow one selected direction before review and experiment planning."""

    def __init__(
        self,
        workspace: ProjectWorkspaceService,
        *,
        refiner: DirectionRefiner | None = None,
    ) -> None:
        self._workspace = workspace
        self._refiner = refiner

    def run(self, project_id: str) -> DirectionRefinementResult:
        """Refine the selected candidate and persist the updated contract."""

        status = self._workspace.load_status(project_id)
        selected = self._load_selected_candidate(project_id, status.selected_idea)
        if selected is None:
            raise ValueError("No selected idea is available for direction refinement")

        report = ""
        try:
            report = self._workspace.read_text(project_id, "IDEA_REPORT.md")
        except FileNotFoundError:
            report = ""
        revision_context = self._revision_context(project_id)
        if revision_context:
            report = f"{report.rstrip()}\n\n# Latest Reviewer Feedback For Revision\n\n{revision_context}"

        if self._refiner is not None:
            refined = self._refiner(selected, status.topic, report)
        else:
            refined = fallback_refine_direction(
                selected,
                topic=status.topic,
                revision_context=revision_context,
            )

        snapshot = self._workspace.update_selected_idea_candidate(project_id, refined)
        return DirectionRefinementResult(
            project_id=snapshot.project_id,
            original_idea=selected,
            refined_idea=refined,
            snapshot=snapshot,
        )

    def _load_selected_candidate(
        self,
        project_id: str,
        selected_title: str,
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
            if candidate.title == selected_title:
                return candidate
        return candidates[0] if candidates else None

    def _revision_context(self, project_id: str) -> str:
        """Return the latest reviewer feedback if this is a revision round."""

        fragments: list[str] = []
        for relative_path in ("refine-logs/REVISION_PLAN.md", "AUTO_REVIEW.md"):
            try:
                content = self._workspace.read_text(project_id, relative_path).strip()
            except FileNotFoundError:
                continue
            if content:
                fragments.append(f"## {relative_path}\n\n{content[-6000:]}")
        return "\n\n".join(fragments)


def build_structured_direction_refiner(config):
    """Build a LangChain-backed selected-direction refiner."""

    from llm import StructuredOutputRunner, build_chat_model

    model = build_chat_model(config)
    runner: StructuredOutputRunner[IdeaCandidate] = StructuredOutputRunner(
        model,
        system_prompt=(
            "You refine one broad research direction into one concrete, reviewable "
            "research idea. Do not merely restate a trend. Produce a specific problem, "
            "testable hypothesis, method sketch, expected signal, novelty risk, "
            "feasibility, impact, and 2-4 concrete required_experiments. Treat named "
            "systems or papers in the input as related work, not as the idea itself. "
            "The refined title should be a focused research problem, not a survey title. "
            "If reviewer feedback is supplied, revise the idea directly against every "
            "major concern: novelty overclaim, feasibility, baselines, statistical protocol, "
            "metrics, overhead, and fallback paths. Keep what is strong; do not restart "
            "from an unrelated idea. All list fields must be real arrays/lists of strings, "
            "not serialized JSON strings. For example, required_experiments must be "
            "[\"Compare against baseline\", \"Run ablation\"], never "
            "\"[\\\"Compare against baseline\\\", \\\"Run ablation\\\"]\". "
            "Return all user-visible prose fields in Simplified Chinese, including title, "
            "problem, hypothesis, method_sketch, expected_signal, novelty_risk, feasibility, "
            "impact, ranking_rationale, reviewer_objection, why_do_this, and required_experiments. "
            "Keep standard technical terms, acronyms, system names, and metrics in English "
            "when that is clearer."
        ),
        schema=IdeaCandidate,
        agent_name="DirectionRefiner",
    )

    def refine(candidate: IdeaCandidate, topic: str, report: str) -> IdeaCandidate:
        prompt = f"""Topic:
{topic}

Selected broad direction:
- title: {candidate.title}
- problem: {candidate.problem}
- hypothesis: {candidate.hypothesis}
- method_sketch: {candidate.method_sketch}
- expected_signal: {candidate.expected_signal}
- novelty_risk: {candidate.novelty_risk}
- feasibility: {candidate.feasibility}

Research landscape report:
{report[:12000]}

Return one refined, concrete research idea. It should be specific enough for reviewer critique and experiment planning. If the report contains reviewer feedback, explicitly address it in hypothesis, method_sketch, expected_signal, feasibility, reviewer_objection, and required_experiments.
Write the returned candidate content in Simplified Chinese for the web UI."""
        try:
            output = runner.invoke(prompt)
        except Exception:
            logger.exception("Structured direction refinement failed; using fallback refiner")
            return fallback_refine_direction(
                candidate,
                topic=topic,
                revision_context=report,
            )
        return _complete_refined_candidate(output, fallback=candidate)

    return refine


def fallback_refine_direction(
    candidate: IdeaCandidate,
    *,
    topic: str,
    revision_context: str = "",
) -> IdeaCandidate:
    """Conservative fallback when no model refiner is available."""

    title = candidate.title.strip()
    refined_title = title
    if len(refined_title) > 80:
        refined_title = f"Focused validation of {topic}"
    reviewer_driven_updates = _fallback_revision_updates(candidate, revision_context)
    base_updates = {
        "title": refined_title,
        "problem": candidate.problem
        or f"Identify a concrete, testable bottleneck within {topic}.",
        "hypothesis": candidate.hypothesis
        if candidate.hypothesis and "focused method change" not in candidate.hypothesis.lower()
        else "A narrower mechanism within this direction can improve a measurable cost-quality tradeoff under matched baselines.",
        "method_sketch": candidate.method_sketch
        or "Compare a focused mechanism against matched baselines and isolate the effect with ablations.",
        "expected_signal": candidate.expected_signal
        or "Improved latency, memory, energy, accuracy, or robustness under controlled conditions.",
        "novelty_risk": candidate.novelty_risk
        or "May overlap with existing systems; requires closest-work comparison before claiming novelty.",
        "feasibility": candidate.feasibility
        or "Feasible if public baselines, representative tasks, and measurable device or simulator metrics are available.",
        "impact": candidate.impact
        or "Useful if it clarifies a practical bottleneck or design tradeoff.",
        "required_experiments": candidate.required_experiments
        if candidate.required_experiments
        and candidate.required_experiments
        != ["Run a small sanity check, a main baseline comparison, and one ablation."]
        else [
            "Define one closest baseline and one target metric for the narrowed direction.",
            "Run a tiny sanity check to verify the evaluation path.",
            "Compare the focused method against the baseline under matched settings.",
            "Run one ablation to isolate the claimed mechanism.",
        ],
        "ranking_rationale": "Refined from broad selected direction before review.",
    }
    base_updates.update(reviewer_driven_updates)
    return candidate.model_copy(update=base_updates)


def _fallback_revision_updates(
    candidate: IdeaCandidate,
    revision_context: str,
) -> dict:
    """Conservative deterministic revisions when no LLM refiner is available."""

    if not revision_context.strip():
        return {}

    lowered = revision_context.lower()
    updates: dict = {
        "ranking_rationale": "Revised using latest external review feedback.",
    }
    experiments = list(candidate.required_experiments)
    additions: list[str] = []
    if "fixed window" in lowered or "strawman" in lowered or "baseline" in lowered:
        additions.append(
            "Compare against the empirically best fixed policy, selected from a sweep of fixed settings under the same thermal protocol."
        )
    if "sensor" in lowered or "/sys/class/thermal" in lowered or "root" in lowered:
        additions.append(
            "Validate sensor access on the target device and include fallback proxies such as CPU frequency, throttling state, and battery temperature."
        )
    if "polling" in lowered or "100ms" in lowered or "overhead" in lowered:
        additions.append(
            "Measure controller polling overhead and compare 100ms, 500ms, and 1s polling intervals."
        )
    if "statistical" in lowered or "confidence" in lowered or "ambient" in lowered:
        additions.append(
            "Run at least three repeated trials per configuration under controlled ambient temperature and report mean, standard deviation, and confidence intervals."
        )
    if "calibration" in lowered or "cross-device" in lowered:
        additions.append(
            "Add a short per-device calibration run to estimate thermal response before applying policy parameters."
        )
    if "draft model" in lowered:
        additions.append(
            "Isolate the draft model's heat contribution with a draft-only and target-only ablation."
        )

    if additions:
        updates["required_experiments"] = _dedupe_preserve_order(experiments + additions)[:8]
        updates["method_sketch"] = (
            f"{candidate.method_sketch} Revision adds reviewer-requested controls: "
            + " ".join(additions[:3])
        ).strip()
        updates["expected_signal"] = (
            f"{candidate.expected_signal} Report improvement over the best fixed baseline, "
            "controller overhead, thermal trajectory, and repeated-trial variance."
        ).strip()
        updates["feasibility"] = (
            f"{candidate.feasibility} Revision requires explicit device sensor validation "
            "and fallback thermal proxies before formal experiments."
        ).strip()
        updates["reviewer_objection"] = (
            "Main residual risk: reviewer may still consider the policy heuristic unless "
            "the best-fixed baseline, overhead, sensor access, and calibration ablations are strong."
        )
    return updates


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = " ".join(item.split()).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _complete_refined_candidate(
    candidate: IdeaCandidate,
    *,
    fallback: IdeaCandidate,
) -> IdeaCandidate:
    """Fill sparse structured outputs without hiding that refinement occurred."""

    return fallback.model_copy(
        update={
            "title": candidate.title or fallback.title,
            "problem": candidate.problem or fallback.problem,
            "hypothesis": candidate.hypothesis or fallback.hypothesis,
            "minimum_viable_experiment": candidate.minimum_viable_experiment
            or fallback.minimum_viable_experiment,
            "expected_outcome": candidate.expected_outcome or fallback.expected_outcome,
            "method_sketch": candidate.method_sketch or fallback.method_sketch,
            "expected_signal": candidate.expected_signal or fallback.expected_signal,
            "novelty_risk": candidate.novelty_risk or fallback.novelty_risk,
            "feasibility": candidate.feasibility or fallback.feasibility,
            "impact": candidate.impact or fallback.impact,
            "risk_level": candidate.risk_level or fallback.risk_level,
            "contribution_type": candidate.contribution_type or fallback.contribution_type,
            "ranking_rationale": candidate.ranking_rationale
            or "Refined from broad selected direction before review.",
            "estimated_effort": candidate.estimated_effort or fallback.estimated_effort,
            "reviewer_objection": candidate.reviewer_objection
            or fallback.reviewer_objection,
            "why_do_this": candidate.why_do_this or fallback.why_do_this,
            "pilot_signal": candidate.pilot_signal or fallback.pilot_signal,
            "required_experiments": candidate.required_experiments
            or fallback.required_experiments,
            "score": candidate.score or fallback.score,
            "closest_related_work": candidate.closest_related_work
            or fallback.closest_related_work,
            "overlap_analysis": candidate.overlap_analysis or fallback.overlap_analysis,
            "novelty_claim": candidate.novelty_claim or fallback.novelty_claim,
            "novelty_verdict": candidate.novelty_verdict or fallback.novelty_verdict,
            "novelty_confidence": candidate.novelty_confidence
            or fallback.novelty_confidence,
        }
    )

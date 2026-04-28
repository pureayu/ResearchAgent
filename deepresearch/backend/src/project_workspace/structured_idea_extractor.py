"""LangChain structured-output extractor for research idea candidates."""

from __future__ import annotations

from collections.abc import Callable

from config import Configuration
from project_workspace.models import IdeaCandidate, IdeaCandidatesOutput


def build_structured_idea_extractor(
    config: Configuration,
) -> Callable[[str, str], list[IdeaCandidate]]:
    """Build an ARIS-style LLM idea creator that returns validated candidates."""

    from llm import StructuredOutputRunner, build_chat_model

    model = build_chat_model(config)
    runner: StructuredOutputRunner[IdeaCandidatesOutput] = StructuredOutputRunner(
        model,
        system_prompt=(
            "You are the ARIS idea-creator stage. The supplied text is a landscape report, "
            "not an idea list. First infer the main subareas and gaps, then generate 8 to 12 "
            "concrete research ideas internally, filter them, and return only the top 3 to 5. "
            "Each returned candidate must be a publishable, falsifiable proposal with a "
            "mechanism, target setting, minimum viable experiment, and measurable signal. "
            "Do not return taxonomy labels, report sections, trend claims, cited papers, "
            "benchmarks, bibliography entries, or copied paragraphs from the report. "
            "Reject broad labels such as a compression category, an inference-engine category, "
            "a KV-cache category, a multimodal-agent category, or a privacy category unless "
            "you convert them into a specific testable proposal. "
            "Use the schema fields as follows: title=short concrete idea name containing "
            "mechanism plus target; problem=specific gap and why it matters; "
            "hypothesis=testable claim with expected direction; "
            "minimum_viable_experiment=cheapest experiment to falsify the idea; "
            "expected_outcome=what success or failure would mean; "
            "method_sketch=the actual method/system/design to try; "
            "expected_signal=measurable evidence such as latency, memory, energy, quality, "
            "robustness, privacy, or reviewer preference; "
            "novelty_risk=why this direction may already be crowded; "
            "feasibility=what makes it feasible or hard; "
            "impact=why the result would matter to researchers or builders; "
            "risk_level=low, medium, high, or unclear; "
            "contribution_type=empirical, method, system, theory, diagnostic, or unclear; "
            "ranking_rationale=one concise reason this candidate should rank where it does; "
            "estimated_effort=days, weeks, or months with a short reason; "
            "reviewer_objection=the strongest objection a reviewer would raise; "
            "why_do_this=why this is worth doing even before experiments; "
            "pilot_signal=not_run unless an actual pilot result is supplied in the report; "
            "score=paper-only ranking score from 0 to 1 based on novelty, feasibility, "
            "impact, and risk; do not assume experiments were run. "
            "required_experiments=concrete validation steps, not generic engineering advice. "
            "Do not copy raw report JSON or note-tool fragments. "
            "Do not output titles containing arXiv IDs, DOI strings, paper citations, "
            "or representative-source bullets. "
            "Ground candidates in the supplied report, but synthesize ideas instead of "
            "extracting report headings. "
            "Return all user-visible prose fields in Simplified Chinese, including title, "
            "problem, hypothesis, method_sketch, expected_signal, novelty_risk, feasibility, "
            "impact, ranking_rationale, reviewer_objection, why_do_this, and required_experiments. "
            "Keep standard technical terms, model names, paper/system names, acronyms, and metrics "
            "in English when that is clearer, but explain them in Chinese."
        ),
        schema=IdeaCandidatesOutput,
        agent_name="ARISIdeaCreator",
    )

    def extract(report_markdown: str, topic: str) -> list[IdeaCandidate]:
        prompt = f"""Topic:
{topic}

Research report:
{report_markdown}

Generate ARIS-style concrete research ideas from this landscape.

Process:
1. Build a landscape map silently.
2. Brainstorm 8 to 12 concrete ideas silently.
3. Filter by feasibility, novelty risk, impact, and whether the minimum experiment is clear.
4. Return only the top 3 to 5 candidates.

Bad output examples:
- "内存带宽是唯一真实的速度瓶项" (this is an observation)
- "未来1-2年最可能成立的趋势" (this is a report section)
- "AWQ: Activation-aware Weight Quantization..." (this is a source)
- "模型轻量化" (this is a taxonomy label)
- "推理引擎与硬件加速" (this is a taxonomy label)

Good output examples:
- "Flash-backed quantized KV cache placement for long-context mobile agents"
- "Static NPU lowering for dynamic MoE routing operators on mobile SoCs"
- "Thermal-budget-aware speculative decoding for sustained phone inference"

Language:
- Return the candidate content in Simplified Chinese for the web UI.
- Keep technical names such as KV cache, NPU, speculative decoding, MLC-LLM, llama.cpp, and p95 latency in English when useful.
"""
        first = runner.invoke(prompt).candidates
        if _has_enough_valid_candidates(first):
            return first

        repair_prompt = f"""{prompt}

Your previous output was rejected by the candidate quality gate.

Rejected or weak candidates:
{_format_candidate_quality_feedback(first)}

Regenerate 3 to 5 candidates. Requirements:
1. Titles must be concrete research ideas, not report conclusions or taxonomy labels.
2. problem must describe the research gap.
3. hypothesis must be falsifiable.
4. method_sketch must describe the mechanism to implement or test.
5. minimum_viable_experiment and required_experiments must be concrete validation steps.
6. expected_signal must name measurable evidence such as latency, memory, energy, quality, robustness, or privacy.
7. Return all user-visible prose fields in Simplified Chinese.
"""
        repaired = runner.invoke(repair_prompt).candidates
        if _has_enough_valid_candidates(repaired):
            return repaired
        raise ValueError(
            "structured idea extraction returned too few valid research directions after repair"
        )

    return extract


def _has_enough_valid_candidates(candidates: list[IdeaCandidate]) -> bool:
    from project_workspace.idea_discovery import candidate_rejection_reasons

    valid = [
        candidate
        for candidate in candidates
        if not candidate_rejection_reasons(candidate)
    ]
    return len(valid) >= 3


def _format_candidate_quality_feedback(candidates: list[IdeaCandidate]) -> str:
    from project_workspace.idea_discovery import candidate_rejection_reasons

    if not candidates:
        return "- no candidates returned"
    lines: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        reasons = candidate_rejection_reasons(candidate) or ["accepted but batch had too few valid candidates"]
        lines.append(
            f"{index}. title={candidate.title!r}; reasons={'; '.join(reasons)}"
        )
    return "\n".join(lines)

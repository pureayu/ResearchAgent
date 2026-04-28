"""Minimal project-level idea discovery workflow."""

from __future__ import annotations

import re
from collections.abc import Callable
from logging import getLogger

from project_workspace.models import IdeaCandidate, IdeaDiscoveryResult
from project_workspace.novelty import NoveltyChecker, NoveltyCheckService
from project_workspace.service import ProjectWorkspaceService


ResearchRunner = Callable[[str], str]
CandidateExtractor = Callable[[str, str], list[IdeaCandidate]]
logger = getLogger(__name__)


class ProjectIdeaDiscoveryService:
    """Run idea discovery and persist ARIS-style project outputs."""

    def __init__(
        self,
        workspace: ProjectWorkspaceService,
        *,
        research_runner: ResearchRunner | None = None,
        candidate_extractor: CandidateExtractor | None = None,
        novelty_checker: NoveltyChecker | None = None,
    ) -> None:
        self._workspace = workspace
        self._research_runner = research_runner
        self._candidate_extractor = candidate_extractor
        self._novelty_checker = novelty_checker

    def run(
        self,
        project_id: str,
        *,
        report_markdown: str | None = None,
        auto_select_top: bool = True,
        enable_novelty_check: bool = False,
        selected_candidate_title: str | None = None,
        selected_candidate_index: int | None = None,
    ) -> IdeaDiscoveryResult:
        """Create idea report/candidates and update project contract files."""

        status = self._workspace.load_status(project_id)
        report = (report_markdown or "").strip()
        if not report:
            if self._research_runner is None:
                raise ValueError("report_markdown is required when no research_runner is configured")
            report = self._research_runner(status.topic).strip()
        if not report:
            raise ValueError("idea discovery report must not be empty")

        candidates = self._extract_candidates(report, status.topic)
        if enable_novelty_check:
            candidates = NoveltyCheckService(
                novelty_checker=self._novelty_checker
            ).check(candidates, topic=status.topic)
        candidates = rank_idea_candidates(candidates)
        selected = select_idea_candidate(
            candidates,
            selected_candidate_title=selected_candidate_title,
            selected_candidate_index=selected_candidate_index,
            auto_select_top=auto_select_top,
        )
        snapshot = self._workspace.write_idea_discovery_outputs(
            project_id,
            report_markdown=report,
            candidates=candidates,
            auto_select_top=auto_select_top,
            selected_candidate=selected,
        )
        return IdeaDiscoveryResult(
            project_id=snapshot.project_id,
            report_markdown=report,
            selected_idea=selected,
            candidates=candidates,
            snapshot=snapshot,
        )

    def _extract_candidates(self, report_markdown: str, topic: str) -> list[IdeaCandidate]:
        if self._candidate_extractor is not None:
            try:
                candidates = self._candidate_extractor(report_markdown, topic)
            except Exception:
                logger.exception("Structured idea extraction failed; falling back to rules")
            else:
                normalized = _normalize_candidates(candidates)
                if len(normalized) >= 3:
                    return normalized
                fallback = extract_idea_candidates(report_markdown, topic=topic)
                if len(fallback) >= 3 or not normalized:
                    logger.warning(
                        "Structured idea extraction returned only %d valid candidates; using ARIS fallback",
                        len(normalized),
                    )
                    return fallback
                return normalized

        return extract_idea_candidates(report_markdown, topic=topic)


def extract_idea_candidates(report_markdown: str, *, topic: str) -> list[IdeaCandidate]:
    """Extract a small candidate set from a report using deterministic fallback rules."""

    sections = _mainline_direction_sections(report_markdown)
    if not sections:
        sections = _markdown_signal_candidates(report_markdown)
    if not sections:
        sections = _candidate_sections(report_markdown)
    if not sections:
        sections = _bullet_candidates(report_markdown)
    if not sections:
        sections = [(f"Research direction for {topic}", report_markdown)]

    candidates: list[IdeaCandidate] = []
    for index, (title, body) in enumerate(sections[:5], start=1):
        proposal_title = _synthesize_aris_title(title, body, topic)
        minimum_experiment = (
            _find_labeled_value(body, ["minimum viable experiment", "minimum experiment"])
            or _find_labeled_value(body, ["experiment", "experiments", "evaluation"])
            or _default_minimum_experiment(proposal_title, body)
        )
        candidates.append(
            IdeaCandidate(
                title=proposal_title or _clean_title(title) or f"Idea {index}: {topic}",
                problem=_find_labeled_value(body, ["problem", "gap"])
                or _default_problem(proposal_title, title, body, topic),
                hypothesis=_find_labeled_value(body, ["hypothesis", "claim", "thesis"])
                or _default_hypothesis(proposal_title, body),
                minimum_viable_experiment=minimum_experiment,
                expected_outcome=_find_labeled_value(body, ["expected outcome", "outcome"])
                or _default_expected_outcome(proposal_title, body),
                method_sketch=_find_labeled_value(body, ["method", "approach", "solution"])
                or _default_method_sketch(proposal_title, body),
                expected_signal=_find_labeled_value(body, ["expected signal", "metric", "result"])
                or _default_expected_signal(body),
                novelty_risk=_find_labeled_value(body, ["novelty risk", "risk"])
                or "Needs dedicated related-work and closest-baseline checks.",
                feasibility=_find_labeled_value(body, ["feasibility"])
                or _default_feasibility(proposal_title, body),
                impact=_find_labeled_value(body, ["impact", "why it matters", "so what"])
                or _default_impact(proposal_title, body),
                risk_level=_infer_risk_level(body),
                contribution_type=_infer_contribution_type(body),
                estimated_effort=_infer_estimated_effort(body),
                reviewer_objection=_default_reviewer_objection(proposal_title, body),
                why_do_this=_default_why_do_this(proposal_title, body),
                required_experiments=_extract_experiments(body)
                if _find_labeled_value(body, ["experiment", "experiments", "evaluation"])
                else [minimum_experiment, "Compare against at least two strong baselines and one ablation."],
                score=max(0.1, round(1.0 - (index - 1) * 0.12, 2)),
            )
        )
    return _normalize_candidates(candidates)


def _normalize_candidates(candidates: list[IdeaCandidate]) -> list[IdeaCandidate]:
    normalized: list[IdeaCandidate] = []
    for index, candidate in enumerate(candidates[:5], start=1):
        title = _clean_title(candidate.title)
        if not title:
            continue
        if candidate_rejection_reasons(candidate):
            continue
        score = candidate.score
        if score <= 0:
            score = max(0.1, round(1.0 - (index - 1) * 0.12, 2))
        normalized.append(
            IdeaCandidate(
                title=title,
                problem=_shorten(candidate.problem or "TBD", 500),
                hypothesis=_shorten(candidate.hypothesis or "TBD", 500),
                minimum_viable_experiment=_shorten(
                    candidate.minimum_viable_experiment or "", 500
                ),
                expected_outcome=_shorten(candidate.expected_outcome or "", 500),
                method_sketch=_shorten(candidate.method_sketch or "TBD", 800),
                expected_signal=_shorten(candidate.expected_signal or "TBD", 500),
                novelty_risk=_shorten(candidate.novelty_risk or "TBD", 500),
                feasibility=_shorten(candidate.feasibility or "TBD", 500),
                impact=_shorten(candidate.impact or "TBD", 500),
                risk_level=_normalize_risk_level(candidate.risk_level),
                contribution_type=_normalize_contribution_type(candidate.contribution_type),
                ranking_rationale=_shorten(candidate.ranking_rationale or "TBD", 500),
                estimated_effort=_shorten(candidate.estimated_effort or "", 120),
                reviewer_objection=_shorten(candidate.reviewer_objection or "", 500),
                why_do_this=_shorten(candidate.why_do_this or "", 500),
                pilot_signal=candidate.pilot_signal,
                required_experiments=[
                    _shorten(item, 220)
                    for item in candidate.required_experiments[:6]
                    if item.strip()
                ]
                or ["Run a sanity check and one baseline comparison."],
                score=max(0.0, min(1.0, float(score))),
                closest_related_work=[
                    _shorten(item, 260)
                    for item in candidate.closest_related_work[:8]
                    if item.strip()
                ],
                overlap_analysis=_shorten(candidate.overlap_analysis, 800),
                novelty_claim=_shorten(candidate.novelty_claim, 500),
                novelty_verdict=candidate.novelty_verdict,
                novelty_confidence=max(
                    0.0,
                    min(1.0, float(candidate.novelty_confidence)),
                ),
            )
        )
    return normalized


def candidate_rejection_reasons(candidate: IdeaCandidate) -> list[str]:
    """Return quality problems that make a candidate unsuitable for user choice."""

    reasons: list[str] = []
    if _looks_like_reference_candidate(candidate):
        reasons.append("looks like a cited paper/source or a report observation")
    if _is_low_information_candidate(candidate):
        reasons.append("repeats the same conclusion in title/problem/method with generic fields")
    if _looks_like_observation_text(candidate.title):
        reasons.append("title is an observation/trend statement, not a research direction")
    if len(_compact(candidate.title)) > 90:
        reasons.append("title is too long for a selectable direction")
    if _looks_like_taxonomy_label(candidate):
        reasons.append("candidate is a taxonomy label, not a concrete ARIS idea")
    if _idea_specificity_score(candidate) < 2:
        reasons.append("candidate lacks enough mechanism, target, or measurable validation detail")
    if candidate.problem and _selection_key(candidate.problem) == _selection_key(candidate.title):
        reasons.append("problem only restates the title")
    if candidate.method_sketch and _selection_key(candidate.method_sketch) == _selection_key(candidate.title):
        reasons.append("method only restates the title")
    return reasons


def rank_idea_candidates(candidates: list[IdeaCandidate]) -> list[IdeaCandidate]:
    """Rank candidates with ARIS-style paper-only criteria.

    This deliberately excludes automatic pilot experiments. It uses available
    novelty annotations plus generic feasibility/impact/risk signals, then
    writes the resulting score and rationale back onto each candidate.
    """

    ranked: list[IdeaCandidate] = []
    for candidate in candidates:
        novelty = _novelty_score(candidate)
        feasibility = _feasibility_score(candidate)
        impact = _impact_score(candidate)
        risk = _risk_score(candidate)
        prior = max(0.0, min(1.0, float(candidate.score or 0.0)))
        score = round(
            (0.25 * novelty)
            + (0.22 * feasibility)
            + (0.23 * impact)
            + (0.15 * risk)
            + (0.15 * prior),
            3,
        )
        if candidate.novelty_verdict == "overlapping":
            score = min(score, 0.35)

        risk_level = _normalize_risk_level(candidate.risk_level)
        if risk_level == "unclear":
            risk_level = _infer_risk_level(
                " ".join([candidate.novelty_risk, candidate.feasibility])
            )
        contribution_type = _normalize_contribution_type(candidate.contribution_type)
        if contribution_type == "unclear":
            contribution_type = _infer_contribution_type(
                " ".join([candidate.method_sketch, candidate.expected_signal])
            )

        rationale = candidate.ranking_rationale.strip()
        if not rationale or rationale == "TBD":
            rationale = (
                f"paper-only rank: novelty={novelty:.2f}, feasibility={feasibility:.2f}, "
                f"impact={impact:.2f}, risk={risk:.2f}, prior={prior:.2f}; pilot not run."
            )

        ranked.append(
            candidate.model_copy(
                update={
                    "score": score,
                    "risk_level": risk_level,
                    "contribution_type": contribution_type,
                    "ranking_rationale": rationale,
                    "pilot_signal": candidate.pilot_signal or "not_run",
                }
            )
        )

    return sorted(ranked, key=_candidate_rank_key, reverse=True)


def select_idea_candidate(
    candidates: list[IdeaCandidate],
    *,
    selected_candidate_title: str | None = None,
    selected_candidate_index: int | None = None,
    auto_select_top: bool = True,
) -> IdeaCandidate | None:
    """Select one candidate explicitly or by ranking."""

    if not candidates:
        return None

    if selected_candidate_index is not None:
        if selected_candidate_index < 1 or selected_candidate_index > len(candidates):
            raise ValueError("selected_candidate_index is 1-based and out of range")
        return candidates[selected_candidate_index - 1]

    if selected_candidate_title:
        wanted = _selection_key(selected_candidate_title)
        for candidate in candidates:
            if _selection_key(candidate.title) == wanted:
                return candidate
        raise ValueError(f"selected candidate not found: {selected_candidate_title}")

    if not auto_select_top:
        return None

    return max(candidates, key=_candidate_rank_key)


def _candidate_rank_key(candidate: IdeaCandidate) -> tuple[float, float, float, float]:
    verdict_weight = {
        "novel": 3.0,
        "incremental": 2.0,
        "unclear": 1.0,
        "overlapping": 0.0,
    }.get(candidate.novelty_verdict, 1.0)
    return (
        max(0.0, min(1.0, float(candidate.score))),
        _pilot_score(candidate.pilot_signal),
        verdict_weight,
        max(0.0, min(1.0, float(candidate.novelty_confidence))),
    )


def _selection_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _novelty_score(candidate: IdeaCandidate) -> float:
    base = {
        "novel": 1.0,
        "incremental": 0.72,
        "unclear": 0.45,
        "overlapping": 0.12,
    }.get(candidate.novelty_verdict, 0.45)
    confidence = max(0.0, min(1.0, float(candidate.novelty_confidence or 0.0)))
    if confidence <= 0:
        return base
    return max(0.0, min(1.0, (base * 0.7) + (confidence * 0.3)))


def _feasibility_score(candidate: IdeaCandidate) -> float:
    text = " ".join([candidate.feasibility, candidate.required_experiments[0] if candidate.required_experiments else ""])
    lowered = text.lower()
    positive = [
        "feasible",
        "existing",
        "available",
        "small-scale",
        "sanity",
        "baseline",
        "cheap",
        "simple",
        "可行",
        "已有",
        "小规模",
        "基线",
    ]
    negative = [
        "hard",
        "difficult",
        "unavailable",
        "expensive",
        "large-scale",
        "months",
        "unclear",
        "困难",
        "缺乏",
        "昂贵",
        "大规模",
    ]
    return _keyword_score(lowered, positive=positive, negative=negative, default=0.55)


def _impact_score(candidate: IdeaCandidate) -> float:
    text = " ".join([candidate.impact, candidate.problem, candidate.expected_signal]).lower()
    positive = [
        "important",
        "bottleneck",
        "gap",
        "tradeoff",
        "cost",
        "latency",
        "accuracy",
        "robust",
        "safety",
        "privacy",
        "benchmark",
        "reviewer",
        "practical",
        "重要",
        "瓶颈",
        "空白",
        "权衡",
        "成本",
        "延迟",
        "准确",
        "鲁棒",
        "隐私",
        "基准",
    ]
    negative = [
        "minor",
        "narrow",
        "unclear",
        "generic",
        "incremental only",
        "小",
        "窄",
        "不清楚",
        "泛",
    ]
    return _keyword_score(text, positive=positive, negative=negative, default=0.55)


def _risk_score(candidate: IdeaCandidate) -> float:
    risk_level = _normalize_risk_level(candidate.risk_level)
    if risk_level == "low":
        return 0.9
    if risk_level == "medium":
        return 0.6
    if risk_level == "high":
        return 0.25
    inferred = _infer_risk_level(" ".join([candidate.novelty_risk, candidate.feasibility]))
    if inferred == "low":
        return 0.85
    if inferred == "medium":
        return 0.6
    if inferred == "high":
        return 0.3
    return 0.5


def _pilot_score(pilot_signal: str) -> float:
    return {
        "positive": 1.0,
        "weak_positive": 0.7,
        "not_run": 0.45,
        "skipped": 0.35,
        "negative": 0.0,
    }.get(pilot_signal, 0.45)


def _keyword_score(
    text: str,
    *,
    positive: list[str],
    negative: list[str],
    default: float,
) -> float:
    pos_hits = sum(1 for keyword in positive if keyword in text)
    neg_hits = sum(1 for keyword in negative if keyword in text)
    return max(0.05, min(1.0, default + 0.12 * pos_hits - 0.18 * neg_hits))


def _normalize_risk_level(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if lowered in {"low", "medium", "high"}:
        return lowered
    return "unclear"


def _infer_risk_level(text: str) -> str:
    lowered = text.lower()
    high_markers = [
        "high risk",
        "hard",
        "difficult",
        "unavailable",
        "expensive",
        "crowded",
        "already",
        "overlap",
        "unclear",
        "高风险",
        "困难",
        "缺乏",
        "拥挤",
        "已有",
        "重合",
    ]
    low_markers = [
        "low risk",
        "feasible",
        "available",
        "existing",
        "small-scale",
        "baseline",
        "低风险",
        "可行",
        "已有基线",
        "小规模",
    ]
    if any(marker in lowered for marker in high_markers):
        return "high"
    if any(marker in lowered for marker in low_markers):
        return "low"
    return "medium" if lowered.strip() else "unclear"


def _normalize_contribution_type(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if lowered in {"empirical", "method", "system", "theory", "diagnostic"}:
        return lowered
    return "unclear"


def _infer_contribution_type(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in ["benchmark", "measure", "evaluation", "评测", "测量"]):
        return "empirical"
    if any(marker in lowered for marker in ["method", "algorithm", "optimization", "方法", "算法", "优化"]):
        return "method"
    if any(marker in lowered for marker in ["system", "engine", "pipeline", "framework", "系统", "框架"]):
        return "system"
    if any(marker in lowered for marker in ["theory", "bound", "proof", "理论", "证明"]):
        return "theory"
    if any(marker in lowered for marker in ["diagnostic", "analysis", "ablation", "分析", "诊断"]):
        return "diagnostic"
    return "unclear"


def _synthesize_aris_title(title: str, body: str, topic: str) -> str:
    """Convert a report mainline into a concrete ARIS-style idea title.

    This is a deterministic fallback only. The LLM-backed idea creator should
    normally perform richer synthesis, but offline tests still need candidates
    that are closer to ARIS ideas than raw report headings.
    """

    if _is_explicit_idea_title(title):
        return _clean_title(title)

    title_text = title.lower()
    text = f"{title} {body}".lower()
    topic_hint = "mobile LLM" if any(token in f"{topic} {title}" for token in ["手机", "端侧", "mobile"]) else topic
    if any(token in title for token in ["模型轻量化", "模型压缩", "量化", "剪枝", "蒸馏"]) or any(
        token in title_text for token in ["compression", "quantization", "pruning", "distillation"]
    ):
        return "Activation-outlier-aware mixed-precision quantization for mobile LLMs"
    if any(token in title for token in ["隐私", "个性化"]) or any(
        token in title_text for token in ["privacy", "personalization"]
    ):
        return "Adaptive privacy-budget LoRA fine-tuning for on-device personalization"
    if any(token in title for token in ["安全", "对齐"]) or any(
        token in title_text for token in ["safety", "alignment"]
    ):
        return "Runtime safety defense for offline mobile LLM agents"
    if any(token in title for token in ["评估", "评测", "基准"]) or any(
        token in title_text for token in ["benchmark", "evaluation"]
    ):
        return "Cross-device benchmark for mobile LLM latency-energy-quality tradeoffs"
    if any(token in title for token in ["推理引擎", "硬件", "NPU"]) or any(
        token in title_text for token in ["engine", "hardware", "npu"]
    ):
        return "Static NPU lowering for dynamic LLM operators on mobile SoCs"
    if any(token in text for token in ["kv", "cache", "缓存", "long-context", "上下文"]):
        return "Flash-backed quantized KV cache placement for long-context mobile LLMs"
    if any(token in text for token in ["热", "thermal", "功耗", "能耗", "throttl"]):
        return "Thermal-aware scheduling for sustained on-device LLM inference"
    if any(token in text for token in ["隐私", "privacy", "federated", "联邦", "lora", "个性化"]):
        return "Adaptive privacy-budget LoRA fine-tuning for on-device personalization"
    if any(token in text for token in ["npu", "hexagon", "apu", "算子", "卸载"]):
        return "Static NPU lowering for dynamic LLM operators on mobile SoCs"
    if any(token in text for token in ["量化", "quant", "mixed precision", "混合精度", "剪枝", "蒸馏"]):
        return "Activation-outlier-aware mixed-precision quantization for mobile LLMs"
    if any(token in text for token in ["架构", "architecture", "small model", "小模型", "mobilellm"]):
        return "Depth-width architecture search for sub-billion mobile LLMs"
    if any(token in text for token in ["多模态", "视觉", "ui"]) or (
        "agent" in text and any(token in text for token in ["mobile", "端侧", "手机", "ui", "visual"])
    ):
        return "Plan-reuse and visual-grounding cache for low-latency mobile agents"
    if any(token in text for token in ["benchmark", "评测", "baseline", "基准", "对比"]):
        return "Cross-device benchmark for mobile LLM latency-energy-quality tradeoffs"
    cleaned = _direction_title_from_heading(title) or topic_hint
    if _looks_like_taxonomy_text(cleaned):
        return f"Benchmark-grounded method study for {topic_hint}"
    return _clean_title(cleaned)


def _is_explicit_idea_title(title: str) -> bool:
    lowered = title.strip().lower()
    if re.match(r"^(idea|candidate|proposal)\s*\d+\s*[:：-]", lowered):
        return True
    if any(marker in lowered for marker in ["retrieval", "planner", "verifier", "budget-aware"]):
        return True
    if any(marker in title for marker in ["检索", "规划", "验证器", "预算"]):
        return True
    return False


def _default_hypothesis(title: str, body: str) -> str:
    if "thermal" in title.lower() or "热" in body:
        return "A controller that adapts inference parameters to thermal headroom can improve sustained throughput without degrading output quality."
    if "kv cache" in title.lower():
        return "A hardware-aware KV cache placement policy can reduce memory pressure while preserving long-context quality."
    if "npu" in title.lower():
        return "Static lowering and fusion of dynamic LLM operators can increase NPU utilization relative to CPU/GPU fallback."
    if "privacy" in title.lower() or "隐私" in body:
        return "Layer-adaptive privacy budgets can improve personalization utility at the same privacy guarantee."
    return "A focused mechanism change can produce a measurable improvement over strong baselines under matched conditions."


def _default_problem(title: str, section_title: str, body: str, topic: str) -> str:
    lowered = title.lower()
    if "kv cache" in lowered:
        return "Long-context mobile LLMs are constrained by KV-cache memory growth, but existing compression and eviction policies rarely optimize hot/cold placement for phone memory and storage hierarchies."
    if "quantization" in lowered:
        return "Mobile LLM compression is limited by activation outliers and layer sensitivity, making uniform low-bit quantization fragile under phone latency and memory constraints."
    if "npu" in lowered:
        return "Mobile NPUs are efficient for static dense kernels, but dynamic LLM operators still fall back to CPU/GPU paths and lose much of the available energy advantage."
    if "benchmark" in lowered:
        return "Mobile LLM papers report latency, energy, memory, and quality under incompatible devices and workloads, making system choices hard to compare."
    if "privacy" in lowered:
        return "On-device personalization must trade off privacy, update utility, and heterogeneous phone resources, but fixed privacy or training budgets leave avoidable accuracy loss."
    if "agent" in lowered:
        return "Mobile agents repeatedly re-plan and re-ground UI actions even when similar tasks have appeared before, causing avoidable latency and reliability failures."
    if "thermal" in lowered:
        return "Sustained phone inference is limited by thermal throttling, while current decoding policies optimize short-run throughput instead of long-run stable performance."
    return (
        f"The report section '{section_title}' identifies a broad area in {topic}, "
        "but it needs a narrower mechanism and measurable validation target."
    )


def _default_minimum_experiment(title: str, body: str) -> str:
    del body
    lowered = title.lower()
    if "thermal" in lowered:
        return "Run 10-minute decoding sessions comparing fixed decoding, naive speculative decoding, and the thermal-aware controller."
    if "kv cache" in lowered:
        return "Implement the cache policy on one open model and compare memory, latency, and quality against full KV and quantized-KV baselines."
    if "npu" in lowered:
        return "Lower one dynamic operator path to a static/fused kernel and compare latency and fallback rate against the default runtime."
    if "privacy" in lowered:
        return "Fine-tune a small LoRA adapter with fixed vs adaptive privacy budgets and compare utility at the same epsilon."
    if "benchmark" in lowered:
        return "Run a small cross-device benchmark matrix with matched models, prompts, latency, energy, memory, and quality metrics."
    return "Run a small sanity check, one strong-baseline comparison, and one ablation on the proposed mechanism."


def _default_expected_outcome(title: str, body: str) -> str:
    return (
        "Success should show a clear Pareto improvement or a well-controlled negative result "
        f"that explains whether {title or _first_sentence(body)} is worth scaling."
    )


def _default_method_sketch(title: str, body: str) -> str:
    lowered = title.lower()
    if "thermal" in lowered:
        return "Profile thermal curves, then use a lightweight controller to adjust speculation length, model choice, or device placement."
    if "kv cache" in lowered:
        return "Combine low-bit KV compression with hot/cold placement and explicit quality-preserving eviction or merge rules."
    if "npu" in lowered:
        return "Define static shapes for irregular operators, fuse small kernels, and lower them through a target-specific mobile NPU path."
    if "privacy" in lowered:
        return "Estimate per-layer update sensitivity, allocate privacy budget adaptively, and validate with a privacy accountant."
    if "quantization" in lowered:
        return "Profile activation outliers, assign mixed precision to sensitive layers or channels, and compare against uniform INT4 and activation-aware quantization baselines."
    if "benchmark" in lowered:
        return "Build a small matched-device benchmark matrix that reports latency, energy, memory, and quality for the same models and prompts across devices."
    if "agent" in lowered:
        return "Cache reusable plans or visual grounding traces, retrieve them by task intent, and fall back to fresh planning when confidence is low."
    return "Implement one focused mechanism and compare it against matched baselines with a sanity check and one ablation."


def _default_feasibility(title: str, body: str) -> str:
    lowered = title.lower()
    if "npu" in lowered:
        return "Medium risk: feasible only if the target NPU runtime exposes enough profiling or lowering hooks; otherwise use CPU/GPU fallback as an ablation."
    if "benchmark" in lowered:
        return "Feasible with public models, fixed prompt sets, and a small number of representative phones or simulator traces."
    if "kv cache" in lowered:
        return "Feasible with open LLM runtimes if cache metadata, eviction, and quantized storage can be instrumented."
    if "thermal" in lowered:
        return "Feasible on a rooted or instrumented Android device, with fallback thermal proxies for production-like constraints."
    return "Feasible if public baselines, representative tasks, and measurable device or simulator metrics are available."


def _default_impact(title: str, body: str) -> str:
    lowered = title.lower()
    if "benchmark" in lowered:
        return "A reproducible benchmark would make device/runtime tradeoffs comparable and reduce engineering guesswork."
    if "kv cache" in lowered:
        return "Useful if it extends mobile context length or agent memory without unacceptable quality loss."
    if "npu" in lowered:
        return "Useful if it converts currently wasted NPU capability into measurable latency or energy gains."
    if "quantization" in lowered:
        return "Useful if it preserves quality at lower memory and latency budgets than uniform low-bit quantization."
    return "Useful if it clarifies an important bottleneck, baseline gap, or design tradeoff."


def _default_expected_signal(body: str) -> str:
    text = body.lower()
    signals = []
    if any(token in text for token in ["latency", "延迟", "throughput", "tokens/s", "速度"]):
        signals.append("lower latency or higher sustained throughput")
    if any(token in text for token in ["memory", "内存", "cache", "缓存"]):
        signals.append("lower peak memory")
    if any(token in text for token in ["energy", "功耗", "能耗", "thermal", "热"]):
        signals.append("lower energy or fewer thermal throttling events")
    if any(token in text for token in ["quality", "accuracy", "精度", "perplexity"]):
        signals.append("matched quality or accuracy")
    if not signals:
        signals.append("improved target metric under matched conditions")
    return "; ".join(signals) + "."


def _infer_estimated_effort(body: str) -> str:
    lowered = body.lower()
    if any(token in lowered for token in ["npu", "compiler", "编译", "sdk", "kernel"]):
        return "weeks to months; requires runtime or kernel integration"
    if any(token in lowered for token in ["benchmark", "评测", "profile", "测量"]):
        return "days to weeks; mostly benchmarking and analysis"
    return "weeks; requires one prototype plus baseline evaluation"


def _default_reviewer_objection(title: str, body: str) -> str:
    if "npu" in title.lower():
        return "Reviewer may question whether mobile NPU APIs expose enough control to implement the proposal."
    if "thermal" in title.lower():
        return "Reviewer may argue this is thermal scheduling rather than a distinct LLM inference contribution."
    if "kv cache" in title.lower():
        return "Reviewer may ask whether quality loss is hidden by narrow prompt choices or weak baselines."
    if "privacy" in title.lower() or "隐私" in body:
        return "Reviewer may question whether privacy guarantees are rigorous enough and whether overhead is practical."
    return "Reviewer may ask whether the proposal is sufficiently novel over existing systems and baselines."


def _default_why_do_this(title: str, body: str) -> str:
    signal = _default_expected_signal(body)
    return f"It targets a concrete deployment bottleneck and can be validated with measurable evidence: {signal}"


def _candidate_sections(markdown: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"(?m)^\s*(#{2,4})\s+(.+?)\s*$")
    matches = list(pattern.finditer(markdown))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        title = match.group(2).strip()
        if _looks_like_reference_title(title):
            continue
        if not _looks_like_idea_title(title):
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        sections.append((title, body))
    return sections


def _mainline_direction_sections(markdown: str) -> list[tuple[str, str]]:
    """Use report subsection structure to form broad directions.

    This fallback intentionally avoids taking standalone conclusions such as
    "memory bandwidth is the bottleneck" as candidates. It prefers 2.x technical
    mainlines and 4.x recommendation subareas, because those sections usually
    contain enough context for a user-selectable direction.
    """

    text = _strip_representative_sources(markdown)
    pattern = re.compile(r"(?m)^\s*(?:#{1,4}\s*)?(\d+)(?:\.(\d+))?\.?\s+(.+?)\s*$")
    matches = list(pattern.finditer(text))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        major = match.group(1)
        minor = match.group(2)
        raw_title = match.group(3).strip()
        if minor is None:
            continue
        if major not in {"2", "4"}:
            continue
        if _looks_like_reference_title(raw_title):
            continue
        title = _direction_title_from_heading(raw_title)
        if not title or _looks_like_observation_text(title):
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if len(_compact(body)) < 20:
            continue
        sections.append((title, body))
    return _dedupe_sections(sections)[:5]


def _direction_title_from_heading(title: str) -> str:
    cleaned = re.sub(r"^\d+(?:\.\d+)*\s*", "", title).strip()
    if "：" in cleaned:
        head, tail = cleaned.split("：", 1)
        cleaned = head.strip()
        if len(cleaned) < 4 and tail.strip():
            cleaned = tail.strip()
    elif ":" in cleaned:
        head, tail = cleaned.split(":", 1)
        cleaned = head.strip()
        if len(cleaned) < 4 and tail.strip():
            cleaned = tail.strip()
    return _clean_title(cleaned)


def _bullet_candidates(markdown: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("-", "*")):
            continue
        title = stripped.lstrip("-* ").strip()
        if len(title) < 12:
            continue
        if _looks_like_reference_title(title):
            continue
        if _looks_like_idea_title(title):
            candidates.append((title, title))
    return candidates


def _looks_like_idea_title(title: str) -> bool:
    lowered = title.lower()
    markers = [
        "idea",
        "direction",
        "opportunity",
        "research",
        "hypothesis",
        "method",
        "approach",
        "方向",
        "选题",
        "趋势",
        "建议",
        "机会",
        "瓶颈",
        "挑战",
        "突破口",
        "trend",
        "recommendation",
        "challenge",
        "gap",
    ]
    return any(marker in lowered for marker in markers)


def _markdown_signal_candidates(markdown: str) -> list[tuple[str, str]]:
    """Extract candidate-like items from generic Markdown structure."""

    text = _strip_representative_sources(markdown)
    candidates: list[tuple[str, str]] = []
    for match in re.finditer(r"\*\*([^*\n]{6,100})\*\*[：:]?\s*([^。\n]{0,220})", text):
        title = _clean_title(match.group(1))
        if len(title) < 8 or _looks_like_reference_title(title):
            continue
        body = f"{title}。{match.group(2).strip()}"
        candidates.append((title, body))

    for line in text.splitlines():
        stripped = line.strip()
        if not re.match(r"^(?:[-*]|\d+[\).、])\s+", stripped):
            continue
        item = re.sub(r"^(?:[-*]|\d+[\).、])\s+", "", stripped).strip()
        title = _clean_title(item)
        if len(title) < 8 or _looks_like_reference_title(title):
            continue
        if _looks_like_idea_title(title):
            candidates.append((title, item))

    return _dedupe_sections(candidates)[:5]


def _strip_representative_sources(markdown: str) -> str:
    match = re.search(r"(?m)^\s*(?:#{1,4}\s*)?5\.\s*代表性来源\b", markdown)
    if match:
        return markdown[: match.start()]
    match = re.search(r"(?m)^\s*(?:#{1,4}\s*)?代表性来源\b", markdown)
    if match:
        return markdown[: match.start()]
    return markdown


def _dedupe_sections(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for title, body in sections:
        key = _selection_key(title)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((title, body))
    return deduped


def _looks_like_reference_candidate(candidate: IdeaCandidate) -> bool:
    fields = [
        candidate.title,
        candidate.problem,
        candidate.method_sketch,
        *candidate.closest_related_work,
    ]
    if any(_looks_like_reference_title(field) for field in fields if field):
        return True
    return _looks_like_observation_not_direction(candidate)


def _looks_like_taxonomy_label(candidate: IdeaCandidate) -> bool:
    title = _compact(candidate.title)
    if not _looks_like_taxonomy_text(title):
        return False
    supporting_text = _compact(
        " ".join(
            [
                candidate.problem,
                candidate.hypothesis,
                candidate.method_sketch,
                candidate.minimum_viable_experiment,
                candidate.expected_signal,
            ]
        )
    )
    return _idea_specificity_score(candidate) < 3 or supporting_text.startswith(title)


def _looks_like_taxonomy_text(text: str) -> bool:
    compact = _compact(text)
    lowered = compact.lower()
    if len(compact) <= 18 and any(
        token in compact
        for token in ["轻量化", "硬件加速", "缓存优化", "多模态", "隐私", "个性化", "模型压缩"]
    ):
        return True
    if len(compact.split()) <= 4 and any(
        token in lowered
        for token in ["compression", "hardware acceleration", "privacy", "personalization", "kv cache"]
    ):
        return True
    if "与" in compact and len(compact) <= 24:
        return True
    return False


def _idea_specificity_score(candidate: IdeaCandidate) -> int:
    text = _compact(
        " ".join(
            [
                candidate.title,
                candidate.problem,
                candidate.hypothesis,
                candidate.method_sketch,
                candidate.minimum_viable_experiment,
                candidate.expected_signal,
                " ".join(candidate.required_experiments),
            ]
        )
    ).lower()
    score = 0
    mechanism_markers = [
        "controller",
        "scheduler",
        "quantization",
        "mixed-precision",
        "placement",
        "lowering",
        "compilation",
        "cache",
        "lora",
        "benchmark",
        "architecture",
        "search",
        "routing",
        "distillation",
        "pruning",
        "speculative",
        "控制",
        "调度",
        "量化",
        "混合精度",
        "放置",
        "编译",
        "缓存",
        "路由",
        "蒸馏",
        "剪枝",
        "投机",
        "架构",
        "搜索",
    ]
    target_markers = [
        "mobile",
        "phone",
        "on-device",
        "npu",
        "gpu",
        "soc",
        "llm",
        "agent",
        "端侧",
        "手机",
        "设备",
    ]
    validation_markers = [
        "latency",
        "energy",
        "memory",
        "quality",
        "accuracy",
        "throughput",
        "baseline",
        "ablation",
        "experiment",
        "tokens/s",
        "延迟",
        "能耗",
        "内存",
        "质量",
        "精度",
        "基线",
        "消融",
        "实验",
    ]
    if any(marker in text for marker in mechanism_markers):
        score += 1
    if any(marker in text for marker in target_markers):
        score += 1
    if any(marker in text for marker in validation_markers):
        score += 1
    if candidate.minimum_viable_experiment.strip() or len(candidate.required_experiments) >= 2:
        score += 1
    if candidate.hypothesis.strip() and not candidate.hypothesis.strip().upper() == "TBD":
        score += 1
    return score


def _is_low_information_candidate(candidate: IdeaCandidate) -> bool:
    title = _compact(candidate.title)
    problem = _compact(candidate.problem)
    method = _compact(candidate.method_sketch)
    generic_hypothesis = "focused method change can produce" in candidate.hypothesis.lower()
    generic_signal = "improved target metrics" in candidate.expected_signal.lower()
    repeats_title = (
        bool(title)
        and _selection_key(problem).startswith(_selection_key(title))
        and _selection_key(method).startswith(_selection_key(title))
    )
    return repeats_title and generic_hypothesis and generic_signal


def _looks_like_observation_not_direction(candidate: IdeaCandidate) -> bool:
    """Reject report observations, named systems, and result snippets as candidates."""

    title = _compact(candidate.title)
    if _looks_like_observation_text(title):
        return True
    if re.search(r"\b[A-Z][A-Za-z0-9-]{2,}\b", title) and any(
        marker in title
        for marker in [
            "是标志性进展",
            "首次实现",
            "快",
            "节省",
            "提升",
            "CIFAR",
            "ImageNet",
            "%",
            "倍",
        ]
    ):
        return True
    return False


def _looks_like_observation_text(text: str) -> bool:
    compact = _compact(text)
    lowered = compact.lower()
    observation_markers = [
        "已取代",
        "成为新范式",
        "是标志性进展",
        "首次实现",
        "证明",
        "表明",
        "不再受限",
        "唯一真实",
        "严重不足",
        "构成双重",
        "构成了",
        "最可能成立",
        "未来1–2年",
        "未来1-2年",
        "当前",
    ]
    if any(marker in compact for marker in observation_markers):
        return True
    if lowered.startswith(("current ", "future ", "trend ", "trends ")):
        return True
    return False


def _looks_like_reference_title(title: str) -> bool:
    text = _compact(title)
    lowered = text.lower()
    if re.search(r"\barxiv\s*[:：]?\s*\d{4}\.\d{4,5}", lowered):
        return True
    if re.search(r"\bdoi\s*[:：/]", lowered):
        return True
    if "——" in text and re.search(r"\b(arxiv|doi|survey|benchmark|tutorial|paper)\b", lowered):
        return True
    if lowered.startswith(("tutorial proposal:", "survey:", "paper:", "benchmark:")):
        return True
    if re.match(r"^\*.+\*\s*[（(]\s*arxiv", text, flags=re.I):
        return True
    return False


def _find_labeled_value(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = re.compile(rf"(?im)^\s*[-*]?\s*{re.escape(label)}\s*[:：]\s*(.+)$")
        match = pattern.search(text)
        if match:
            return _shorten(match.group(1).strip(), 300)
    return ""


def _extract_experiments(text: str) -> list[str]:
    labeled = _find_labeled_value(text, ["experiment", "experiments", "evaluation"])
    if labeled:
        return [labeled]
    experiments = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("-*0123456789. ").strip()
        lowered = stripped.lower()
        if any(token in lowered for token in ["experiment", "ablation", "baseline", "benchmark"]):
            experiments.append(_shorten(stripped, 180))
    return experiments[:4] or ["Run a small sanity check, a main baseline comparison, and one ablation."]


def _clean_title(title: str) -> str:
    title = re.sub(r"^\d+[\).\s-]+", "", title.strip())
    return _shorten(title, 120)


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _first_sentence(text: str) -> str:
    match = re.search(r"(.+?[.!?。！？])(?:\s|$)", text)
    if match:
        return _shorten(match.group(1), 260)
    return _shorten(text, 260)


def _shorten(text: str, limit: int) -> str:
    text = _compact(text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."

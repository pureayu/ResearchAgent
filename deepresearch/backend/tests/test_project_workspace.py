import json
import sys
import tempfile
import unittest
from pathlib import Path
from pydantic import BaseModel


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from project_workspace import (
    DirectionRefinementService,
    ExperimentBridgeService,
    IdeaCandidate,
    ExternalReviewService,
    ProjectIdeaDiscoveryService,
    ProjectWorkspaceService,
)
from project_workspace.models import ProjectStage
from llm.structured import _normalize_schema_payload


class _StructuredPayloadFixture(BaseModel):
    required_experiments: list[str]
    closest_related_work: list[str] = []


class ProjectWorkspaceServiceTests(unittest.TestCase):
    def test_structured_payload_normalizes_json_encoded_list_strings(self) -> None:
        payload = _normalize_schema_payload(
            {
                "required_experiments": '["Compare against baseline", "Run ablation"]',
                "closest_related_work": "[]",
            }
        )

        parsed = _StructuredPayloadFixture.model_validate(payload)

        self.assertEqual(
            parsed.required_experiments,
            ["Compare against baseline", "Run ablation"],
        )
        self.assertEqual(parsed.closest_related_work, [])

    def test_create_project_writes_minimal_aris_protocol_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = ProjectWorkspaceService(tmpdir)

            snapshot = service.create_project(
                project_id="demo-project",
                topic="Efficient RAG agents",
                selected_idea="Use verifier-guided retrieval budgets",
            )

            root = Path(snapshot.root_path)
            self.assertEqual(snapshot.project_id, "demo-project")
            self.assertTrue((root / "PROJECT_STATUS.json").exists())
            self.assertTrue((root / "CLAUDE.md").exists())
            self.assertTrue((root / "docs" / "research_contract.md").exists())
            self.assertTrue((root / "refine-logs" / "EXPERIMENT_PLAN.md").exists())
            self.assertTrue((root / "REVIEW_STATE.json").exists())

            status = json.loads((root / "PROJECT_STATUS.json").read_text())
            self.assertEqual(status["topic"], "Efficient RAG agents")
            self.assertEqual(status["stage"], "intake")

            contract = (root / "docs" / "research_contract.md").read_text()
            self.assertIn("Use verifier-guided retrieval budgets", contract)

    def test_update_status_refreshes_canonical_and_human_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = ProjectWorkspaceService(tmpdir)
            service.create_project(project_id="demo", topic="Topic")

            snapshot = service.update_status(
                "demo",
                {
                    "stage": "refine_plan",
                    "active_tasks": ["Draft claim map"],
                    "next_action": "Write EXPERIMENT_PLAN.md",
                },
            )

            self.assertEqual(snapshot.status.stage.value, "refine_plan")
            human_status = Path(snapshot.files["human_status"]).read_text()
            self.assertIn("stage: refine_plan", human_status)
            self.assertIn("Draft claim map", human_status)

    def test_project_id_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = ProjectWorkspaceService(tmpdir)
            snapshot = service.create_project(project_id="../bad id", topic="Topic")

            self.assertEqual(snapshot.project_id, "bad-id")
            self.assertTrue(Path(snapshot.root_path).is_relative_to(Path(tmpdir).resolve()))

    def test_idea_discovery_writes_report_candidates_contract_and_plan(self) -> None:
        report = """
        # Report

        ## Idea 1: Verifier-guided retrieval budgets

        Problem: RAG agents waste retrieval budget on low-value queries.
        Hypothesis: A verifier can stop retrieval earlier without hurting answer quality.
        Method: Route uncertain claims to extra search and skip easy claims.
        Expected signal: Same answer quality with lower search cost.
        Experiment: Compare fixed-depth retrieval with verifier-guided retrieval on QA tasks.

        ## Idea 2: Memory-aware planner

        Problem: Agents repeat searches already covered by session memory.
        Method: Inject durable memory before planning.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="ideas", topic="Efficient RAG agents")

            result = ProjectIdeaDiscoveryService(workspace).run(
                "ideas",
                report_markdown=report,
            )

            self.assertEqual(len(result.candidates), 2)
            self.assertEqual(result.snapshot.status.stage.value, "refine_plan")
            self.assertEqual(
                result.snapshot.status.selected_idea,
                "Idea 1: Verifier-guided retrieval budgets",
            )

            root = Path(result.snapshot.root_path)
            self.assertIn("Verifier-guided", (root / "IDEA_CANDIDATES.md").read_text())
            candidates_json = json.loads((root / "IDEA_CANDIDATES.json").read_text())
            self.assertEqual(len(candidates_json), 2)
            self.assertIn("Same answer quality", (root / "refine-logs" / "EXPERIMENT_PLAN.md").read_text())

    def test_idea_discovery_prefers_structured_extractor_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="structured", topic="Efficient agents")

            def fake_extractor(report: str, topic: str) -> list[IdeaCandidate]:
                self.assertIn("raw report", report)
                self.assertEqual(topic, "Efficient agents")
                return [
                    IdeaCandidate(
                        title="Structured idea",
                        problem="Planning is under-constrained.",
                        hypothesis="Schema-constrained planning improves actionability.",
                        minimum_viable_experiment="Compare structured and free-form planning on existing reports.",
                        method_sketch="Extract claims and experiments before writing.",
                        expected_signal="More valid experiment plans.",
                        novelty_risk="May overlap with planning-agent work.",
                        feasibility="Easy to test on existing reports.",
                        required_experiments=["Compare structured vs free-form planning."],
                        score=0.73,
                    )
                ]

            result = ProjectIdeaDiscoveryService(
                workspace,
                candidate_extractor=fake_extractor,
            ).run("structured", report_markdown="raw report")

            self.assertEqual(result.candidates[0].title, "Structured idea")
            self.assertEqual(result.snapshot.status.selected_idea, "Structured idea")

    def test_idea_discovery_rejects_reference_like_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="directions", topic="Efficient research agents")

            report = (
                "## 4. Recommendations\n"
                "**Budget-aware retrieval routing**: study when an agent should stop searching "
                "and start writing under fixed cost budgets.\n"
                "**Verifier-guided evidence selection**: study how a reviewer model can reject "
                "weak sources before synthesis.\n"
                "\n## 5. Representative Sources\n"
                "*Tutorial Proposal: Efficient Agent Planning* (arxiv:2503.00491) "
                "- a cited source, not a direction."
            )

            def fake_extractor(report_markdown: str, topic: str) -> list[IdeaCandidate]:
                del report_markdown, topic
                return [
                    IdeaCandidate(
                        title=(
                            "Tutorial Proposal: Efficient Agent Planning "
                            "(arxiv:2503.00491) - a cited source"
                        ),
                        problem="A cited paper, not a synthesized research direction.",
                        method_sketch="A cited paper, not a synthesized research direction.",
                        expected_signal="TBD",
                        score=0.9,
                    )
                ]

            result = ProjectIdeaDiscoveryService(
                workspace,
                candidate_extractor=fake_extractor,
            ).run("directions", report_markdown=report, auto_select_top=False)

            titles = [candidate.title for candidate in result.candidates]
            self.assertNotIn("Tutorial Proposal", " ".join(titles))
            self.assertIn("Budget-aware retrieval routing", titles)
            self.assertIn("Verifier-guided evidence selection", titles)
            self.assertIsNone(result.selected_idea)

    def test_idea_discovery_rejects_observations_and_named_system_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="observations", topic="Mobile inference")

            def fake_extractor(report_markdown: str, topic: str) -> list[IdeaCandidate]:
                del report_markdown, topic
                return [
                    IdeaCandidate(
                        title="系统级协同设计已取代单一压缩方法成为新范式",
                        problem="系统级协同设计已取代单一压缩方法成为新范式。",
                        method_sketch="系统级协同设计已取代单一压缩方法成为新范式。",
                        score=1.0,
                    ),
                    IdeaCandidate(
                        title="ExampleSystem",
                        problem="ExampleSystem 是标志性进展，首次实现端到端能力并提升 11 倍。",
                        method_sketch="ExampleSystem 是标志性进展。",
                        score=0.9,
                    ),
                    IdeaCandidate(
                        title="Benchmark-grounded inference engine comparison",
                        problem="研究不同推理引擎在真实设备上的延迟、能耗和内存权衡。",
                        method_sketch="Build a benchmark matrix and compare matched baselines.",
                        expected_signal="Clear tradeoffs across engines and devices.",
                        feasibility="Feasible with existing devices and public engines.",
                        score=0.8,
                    ),
                ]

            result = ProjectIdeaDiscoveryService(
                workspace,
                candidate_extractor=fake_extractor,
            ).run("observations", report_markdown="raw report", auto_select_top=False)

            titles = [candidate.title for candidate in result.candidates]
            self.assertEqual(titles, ["Benchmark-grounded inference engine comparison"])

    def test_idea_discovery_falls_back_when_structured_output_has_too_few_valid_candidates(self) -> None:
        report = """
        # Report

        ## 2. Mainlines
        2.1 模型轻量化：量化与蒸馏
        激活异常值和混合精度量化是端侧模型压缩的关键问题。

        2.2 推理引擎与加速：NPU重构
        NPU对动态算子支持不足，需要静态分块和融合。

        2.3 隐私与个性化：联邦LoRA
        联邦LoRA在端侧存在隐私预算和异构客户端约束。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="too-few", topic="Mobile inference")

            def bad_extractor(report_markdown: str, topic: str) -> list[IdeaCandidate]:
                del report_markdown, topic
                return [
                    IdeaCandidate(
                        title="Static NPU lowering for dynamic LLM operators on mobile SoCs",
                        problem="Only one candidate returned.",
                        method_sketch="Define static shapes.",
                        minimum_viable_experiment="Run one kernel benchmark.",
                        expected_signal="Lower latency.",
                        score=0.8,
                    )
                ]

            result = ProjectIdeaDiscoveryService(
                workspace,
                candidate_extractor=bad_extractor,
            ).run("too-few", report_markdown=report, auto_select_top=False)

            titles = [candidate.title for candidate in result.candidates]
            self.assertGreaterEqual(len(titles), 3)
            self.assertIn("Activation-outlier-aware mixed-precision quantization for mobile LLMs", titles)
            self.assertIn("Static NPU lowering for dynamic LLM operators on mobile SoCs", titles)
            self.assertIn("Adaptive privacy-budget LoRA fine-tuning for on-device personalization", titles)

    def test_idea_discovery_fallback_uses_report_mainlines_not_conclusion_sentences(self) -> None:
        report = """
        # 当前端侧大模型推理（手机端）研究方向分析报告

        ## 1. 执行摘要
        最重要的判断是：端侧推理不再受限于单一算法的突破，而是进入了以“系统级折衷”为核心的工程阶段。

        ## 2. 技术主线与现状
        2.1 模型压缩与量化：从保护显著通道到混合精度自动分配，多模态蒸馏补齐关键短板
        手机端的模型压缩已经摆脱了“一刀切低比特”的粗放阶段。AWQ、LieQ 和 SPEED-Q 表明混合精度自动分配、多模态量化和蒸馏增强量化是重要方向。

        2.2 推理加速与架构创新：KV缓存压缩、稀疏注意力、投机解码与异构卸载协同发力
        解码阶段的延迟主要由KV缓存访存和注意力计算决定。HCAttention、BSFA 和 QuantSpec 分别展示了缓存压缩、稀疏注意力和投机解码的潜力。

        2.3 专为移动端设计的模型架构：小模型、多模态一体与参数高效微调
        MobileLLM 和 TinyLLaVA Factory 说明移动端架构不只是大模型缩小版，小模型任务适配和参数高效微调值得继续研究。

        ## 3. 关键瓶颈与工程约束
        内存带宽是唯一真实的速度瓶项。NPU的通用计算力仍严重不足。热功耗墙与隐私合规构成双重天花板。

        ## 4. 趋势判断与建议
        未来1–2年最可能成立的趋势：
        量化与模型架构将走向联合设计。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="mobile-mainlines", topic="手机端大模型推理")

            result = ProjectIdeaDiscoveryService(workspace).run(
                "mobile-mainlines",
                report_markdown=report,
                auto_select_top=False,
            )

            titles = [candidate.title for candidate in result.candidates]
            self.assertIn("Activation-outlier-aware mixed-precision quantization for mobile LLMs", titles)
            self.assertIn("Flash-backed quantized KV cache placement for long-context mobile LLMs", titles)
            self.assertIn("Depth-width architecture search for sub-billion mobile LLMs", titles)
            self.assertNotIn("模型压缩与量化", titles)
            self.assertNotIn("推理加速与架构创新", titles)
            self.assertNotIn("专为移动端设计的模型架构", titles)
            self.assertNotIn("内存带宽是唯一真实的速度瓶项", titles)
            self.assertFalse(any("未来1" in title for title in titles))
            for candidate in result.candidates:
                self.assertNotEqual(candidate.method_sketch, candidate.problem)
                self.assertNotIn("AWQ、LieQ", candidate.method_sketch)
                self.assertGreater(len(candidate.minimum_viable_experiment), 20)

    def test_idea_discovery_can_annotate_novelty_with_checker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="novelty", topic="Efficient agents")

            def fake_novelty_checker(
                candidates: list[IdeaCandidate],
                topic: str,
            ) -> list[IdeaCandidate]:
                self.assertEqual(topic, "Efficient agents")
                return [
                    candidate.model_copy(
                        update={
                            "closest_related_work": ["Prior Work 2025"],
                            "overlap_analysis": "Prior work solves a broader planning problem but not budget-aware retrieval.",
                            "novelty_claim": "Novel in budget-aware verifier routing.",
                            "novelty_verdict": "incremental",
                            "novelty_confidence": 0.64,
                        }
                    )
                    for candidate in candidates
                ]

            result = ProjectIdeaDiscoveryService(
                workspace,
                novelty_checker=fake_novelty_checker,
            ).run(
                "novelty",
                report_markdown="## Idea 1: Budget-aware retrieval\n\nProblem: Search budget is wasted.",
                enable_novelty_check=True,
            )

            candidate = result.candidates[0]
            self.assertEqual(candidate.novelty_verdict, "incremental")
            self.assertEqual(candidate.closest_related_work, ["Prior Work 2025"])
            markdown = Path(result.snapshot.files["idea_candidates"]).read_text()
            self.assertIn("novelty_verdict: incremental", markdown)

    def test_idea_discovery_reranks_candidates_with_aris_style_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="ranked", topic="Efficient agents")

            report = """
            ## Idea 1: Crowded high-risk direction
            Problem: Generic planning agents are underexplored.
            Method: Build a large system from scratch.
            Feasibility: Hard and expensive; requires unavailable data.
            Impact: Unclear.

            ## Idea 2: Budget-aware retrieval evaluation
            Problem: Agents waste retrieval budget and latency on low-value searches.
            Method: Evaluate verifier-guided routing against fixed-depth baselines.
            Feasibility: Feasible with existing baselines and small-scale sanity checks.
            Impact: Clarifies an important cost-quality tradeoff.
            """

            result = ProjectIdeaDiscoveryService(workspace).run(
                "ranked",
                report_markdown=report,
                auto_select_top=True,
            )

            self.assertEqual(result.candidates[0].title, "Idea 2: Budget-aware retrieval evaluation")
            self.assertEqual(result.selected_idea.title, "Idea 2: Budget-aware retrieval evaluation")
            self.assertGreater(result.candidates[0].score, result.candidates[1].score)
            self.assertEqual(result.candidates[0].pilot_signal, "not_run")
            self.assertIn("paper-only rank", result.candidates[0].ranking_rationale)
            markdown = Path(result.snapshot.files["idea_candidates"]).read_text()
            self.assertIn("risk_level:", markdown)
            self.assertIn("contribution_type:", markdown)
            self.assertIn("pilot_signal: not_run", markdown)

    def test_idea_discovery_novelty_fallback_marks_unclear(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="unclear", topic="Efficient agents")

            result = ProjectIdeaDiscoveryService(workspace).run(
                "unclear",
                report_markdown="## Idea 1: Budget-aware retrieval\n\nProblem: Search budget is wasted.",
                enable_novelty_check=True,
            )

            self.assertEqual(result.candidates[0].novelty_verdict, "unclear")
            self.assertIn("Pending search query", result.candidates[0].closest_related_work[0])

    def test_select_idea_gate_can_select_explicit_candidate_index(self) -> None:
        report = """
        ## Idea 1: First direction
        Problem: First problem.

        ## Idea 2: Second direction
        Problem: Second problem.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="select-index", topic="Selection")

            result = ProjectIdeaDiscoveryService(workspace).run(
                "select-index",
                report_markdown=report,
                selected_candidate_index=2,
            )

            self.assertEqual(result.selected_idea.title, "Idea 2: Second direction")
            self.assertEqual(result.snapshot.status.selected_idea, "Idea 2: Second direction")

    def test_select_idea_gate_can_pause_for_human_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="human-gate", topic="Selection")

            result = ProjectIdeaDiscoveryService(workspace).run(
                "human-gate",
                report_markdown="## Idea 1: First direction\n\nProblem: First problem.",
                auto_select_top=False,
            )

            self.assertIsNone(result.selected_idea)
            self.assertEqual(result.snapshot.status.stage.value, "human_gate")

    def test_direction_refinement_replaces_selected_candidate_before_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="refine-direction", topic="Mobile LLM inference")
            ProjectIdeaDiscoveryService(workspace).run(
                "refine-direction",
                report_markdown=(
                    "## Idea 1: Layer-wise mixed precision control\n\n"
                    "Problem: Mixed precision is broad.\n"
                    "Method: Study layer-wise controls."
                ),
            )

            def fake_refiner(candidate: IdeaCandidate, topic: str, report: str) -> IdeaCandidate:
                self.assertEqual(candidate.title, "Idea 1: Layer-wise mixed precision control")
                self.assertEqual(topic, "Mobile LLM inference")
                self.assertIn("Mixed precision", report)
                return IdeaCandidate(
                    title="Activation-drift gated mixed precision for mobile KV cache",
                    problem="Mobile LLM decoding wastes memory bandwidth by refreshing low-drift KV states at full precision.",
                    hypothesis="Gating refresh precision by activation drift reduces memory traffic without hurting answer quality.",
                    method_sketch="Measure per-layer KV drift and assign refresh precision only when drift exceeds a threshold.",
                    expected_signal="Lower decode latency and memory traffic at matched accuracy.",
                    required_experiments=[
                        "Compare against fixed INT4 and FP16 KV cache baselines.",
                        "Ablate drift threshold and layer grouping.",
                    ],
                    novelty_risk="May overlap with KV cache compression and adaptive precision work.",
                    feasibility="Feasible with public small LLMs and simulator/device latency metrics.",
                    score=0.82,
                )

            result = DirectionRefinementService(
                workspace,
                refiner=fake_refiner,
            ).run("refine-direction")

            self.assertEqual(
                result.refined_idea.title,
                "Activation-drift gated mixed precision for mobile KV cache",
            )
            self.assertEqual(result.snapshot.status.stage.value, "refine_plan")
            self.assertEqual(result.snapshot.status.selected_idea, result.refined_idea.title)

            root = Path(result.snapshot.root_path)
            candidates_json = json.loads((root / "IDEA_CANDIDATES.json").read_text())
            self.assertEqual(candidates_json[0]["title"], result.refined_idea.title)
            self.assertIn(
                "activation drift",
                (root / "docs" / "research_contract.md").read_text().lower(),
            )

    def test_direction_refinement_uses_latest_revision_plan_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="review-refine", topic="Mobile speculative decoding")
            ProjectIdeaDiscoveryService(workspace).run(
                "review-refine",
                report_markdown=(
                    "## Idea 1: Thermal adaptive speculative decoding\n\n"
                    "Problem: Fixed speculation windows overheat phones.\n"
                    "Method: Adapt the speculation window to thermal state."
                ),
            )
            ExternalReviewService(workspace).run(
                "review-refine",
                review_text="Needs stronger baseline and sensor fallback.",
                verdict="needs_revision",
            )

            def fake_refiner(candidate: IdeaCandidate, topic: str, report: str) -> IdeaCandidate:
                self.assertEqual(topic, "Mobile speculative decoding")
                self.assertIn("REVISION_PLAN.md", report)
                self.assertIn("stronger baseline", report)
                self.assertIn("sensor fallback", report)
                return candidate.model_copy(
                    update={
                        "hypothesis": "Adaptive speculation improves sustained throughput over the best fixed window after calibration.",
                        "method_sketch": "Sweep fixed windows, validate sensor proxies, then apply a calibrated adaptive policy.",
                        "required_experiments": [
                            "Compare against the best fixed window from a pre-sweep.",
                            "Validate thermal sensor and proxy availability.",
                        ],
                        "ranking_rationale": "Revised using reviewer feedback.",
                    }
                )

            result = DirectionRefinementService(
                workspace,
                refiner=fake_refiner,
            ).run("review-refine")

            self.assertIn("best fixed window", result.refined_idea.hypothesis)
            self.assertIn(
                "best fixed window",
                Path(result.snapshot.files["research_contract"]).read_text(),
            )

    def test_project_graph_runs_when_langgraph_is_available(self) -> None:
        try:
            import langgraph  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("langgraph is not installed in this interpreter")

        from project_workspace.project_graph import ProjectIdeaDiscoveryGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="graph", topic="Graph agents")

            def fake_novelty_checker(
                candidates: list[IdeaCandidate],
                topic: str,
            ) -> list[IdeaCandidate]:
                del topic
                return [
                    candidate.model_copy(
                        update={
                            "novelty_verdict": "novel",
                            "novelty_confidence": 0.8,
                        }
                    )
                    for candidate in candidates
                ]

            result = ProjectIdeaDiscoveryGraph(
                workspace,
                novelty_checker=fake_novelty_checker,
            ).run(
                "graph",
                report_markdown="## Idea 1: Graph-routed research\n\nProblem: Long workflows need explicit state.",
                enable_novelty_check=True,
                selected_candidate_title="Idea 1: Graph-routed research",
            )

            self.assertEqual(result.snapshot.status.stage.value, "refine_plan")
            self.assertEqual(result.candidates[0].title, "Idea 1: Graph-routed research")
            self.assertEqual(result.candidates[0].novelty_verdict, "novel")

    def test_idea_discovery_endpoint_can_use_precomputed_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            try:
                import fastapi  # noqa: F401
            except ModuleNotFoundError:
                self.skipTest("fastapi is not installed in this interpreter")

            os.environ["PROJECT_WORKSPACE_ROOT"] = tmpdir
            from main import (
                IdeaDiscoveryRequest,
                ProjectCreateRequest,
                create_app,
            )

            app = create_app()
            create = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/projects"
                and "POST" in getattr(route, "methods", set())
            )
            discover = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/projects/{project_id}/idea-discovery"
            )

            create(ProjectCreateRequest(project_id="api-ideas", topic="Agent research"))
            result = discover(
                "api-ideas",
                IdeaDiscoveryRequest(
                    report_markdown="## Idea 1: Research planner\n\nProblem: Planning is weak.\nMethod: Better decomposition.",
                    use_project_graph=False,
                    use_structured_extraction=False,
                    enable_novelty_check=True,
                ),
            )

            self.assertEqual(result.project_id, "api-ideas")
            self.assertEqual(result.snapshot.status.stage.value, "refine_plan")
            self.assertEqual(result.candidates[0].novelty_verdict, "unclear")

    def test_external_review_appends_review_and_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="review", topic="Review topic")
            discovery = ProjectIdeaDiscoveryService(workspace).run(
                "review",
                report_markdown="## Idea 1: Reviewable idea\n\nProblem: Needs review.",
            )
            contract_before = Path(discovery.snapshot.files["research_contract"]).read_text()

            result = ExternalReviewService(workspace).run(
                "review",
                review_text="The idea is promising but needs a stronger baseline.",
                verdict="needs_revision",
            )

            self.assertEqual(result.round, 1)
            self.assertEqual(result.status, "needs_revision")
            self.assertEqual(result.snapshot.status.stage.value, "auto_review")
            auto_review = Path(result.snapshot.files["auto_review"]).read_text()
            self.assertIn("Round 1", auto_review)
            self.assertIn("needs_revision", auto_review)
            review_state = json.loads(Path(result.snapshot.files["review_state"]).read_text())
            self.assertEqual(review_state["latest_verdict"], "needs_revision")
            contract_after = Path(result.snapshot.files["research_contract"]).read_text()
            self.assertEqual(contract_before, contract_after)

    def test_external_review_uses_reviewer_callback_when_no_manual_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="model-review", topic="Review topic")
            ProjectIdeaDiscoveryService(workspace).run(
                "model-review",
                report_markdown="## Idea 1: Model-reviewed idea\n\nProblem: Needs review.",
            )

            def fake_reviewer(status, candidate):
                self.assertEqual(status.project_id, "model-review")
                self.assertEqual(candidate.title, "Idea 1: Model-reviewed idea")
                from project_workspace import ExternalReviewOutput

                return ExternalReviewOutput(
                    verdict="positive",
                    summary="Ready for experiment bridge.",
                    strengths=["Clear hypothesis"],
                    weaknesses=[],
                    action_items=[],
                    raw_review="Ready for experiment bridge.",
                )

            result = ExternalReviewService(
                workspace,
                reviewer=fake_reviewer,
            ).run("model-review")

            self.assertEqual(result.status, "accepted")
            self.assertEqual(result.review.verdict, "positive")

    def test_external_review_needs_revision_writes_revision_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="revision", topic="Review topic")
            ProjectIdeaDiscoveryService(workspace).run(
                "revision",
                report_markdown="## Idea 1: Thermal-aware speculation\n\nProblem: Needs review.",
            )

            result = ExternalReviewService(workspace).run(
                "revision",
                review_text="Needs stronger baselines and a clearer novelty claim.",
                verdict="needs_revision",
            )

            self.assertEqual(result.status, "needs_revision")
            self.assertEqual(result.snapshot.status.training_status, "revision_required")
            root = Path(result.snapshot.root_path)
            revision_plan = root / "refine-logs" / "REVISION_PLAN.md"
            draft_tracker = root / "refine-logs" / "DRAFT_EXPERIMENT_TRACKER.md"
            self.assertTrue(revision_plan.exists())
            self.assertTrue(draft_tracker.exists())
            self.assertIn("Needs stronger baselines", revision_plan.read_text())
            self.assertIn("Draft Experiment Tracker", draft_tracker.read_text())

            bridge_result = ExperimentBridgeService(workspace).run("revision")
            self.assertGreaterEqual(len(bridge_result.tasks), 1)
            self.assertEqual(
                bridge_result.snapshot.status.stage,
                ProjectStage.EXPERIMENT_BRIDGE,
            )

    def test_external_review_endpoint_accepts_manual_review_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            os.environ["PROJECT_WORKSPACE_ROOT"] = tmpdir
            from main import (
                ExternalReviewRequest,
                IdeaDiscoveryRequest,
                ProjectCreateRequest,
                create_app,
            )

            app = create_app()
            create = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/projects"
                and "POST" in getattr(route, "methods", set())
            )
            discover = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/projects/{project_id}/idea-discovery"
            )
            review = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/projects/{project_id}/external-review"
            )

            create(ProjectCreateRequest(project_id="api-review", topic="Review topic"))
            discover(
                "api-review",
                IdeaDiscoveryRequest(
                    report_markdown="## Idea 1: Reviewable idea\n\nProblem: Needs review.",
                    use_project_graph=False,
                    use_structured_extraction=False,
                ),
            )
            result = review(
                "api-review",
                ExternalReviewRequest(
                    review_text="Looks acceptable.",
                    verdict="positive",
                    use_external_model=False,
                ),
            )

            self.assertEqual(result.status, "accepted")

    def test_experiment_bridge_generates_tracker_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = ProjectWorkspaceService(tmpdir)
            workspace.create_project(project_id="bridge", topic="Experiment topic")
            discovery = ProjectIdeaDiscoveryService(workspace).run(
                "bridge",
                report_markdown="""
                ## Idea 1: Experiment-ready idea

                Problem: Needs experiments.
                Expected signal: Better metric.
                Experiment: Compare against baseline.
                """,
            )

            result = ExperimentBridgeService(workspace).run("bridge")

            self.assertEqual(result.snapshot.status.stage.value, "experiment_bridge")
            self.assertEqual(result.tasks[0].id, "E0")
            self.assertTrue(any(task.title == "Compare against baseline." for task in result.tasks))
            tracker = Path(result.snapshot.files["experiment_tracker"]).read_text()
            self.assertIn("Sanity check", tracker)
            log = Path(result.snapshot.files["experiment_log"]).read_text()
            self.assertIn("Experiment Bridge", log)
            self.assertIn(discovery.selected_idea.title, log)

    def test_experiment_bridge_endpoint_generates_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            os.environ["PROJECT_WORKSPACE_ROOT"] = tmpdir
            from main import (
                ExperimentBridgeRequest,
                IdeaDiscoveryRequest,
                ProjectCreateRequest,
                create_app,
            )

            app = create_app()
            create = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/projects"
                and "POST" in getattr(route, "methods", set())
            )
            discover = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/projects/{project_id}/idea-discovery"
            )
            bridge = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/projects/{project_id}/experiment-bridge"
            )

            create(ProjectCreateRequest(project_id="api-bridge", topic="Experiment topic"))
            discover(
                "api-bridge",
                IdeaDiscoveryRequest(
                    report_markdown="## Idea 1: Experiment-ready idea\n\nExperiment: Compare against baseline.",
                    use_project_graph=False,
                    use_structured_extraction=False,
                ),
            )
            result = bridge("api-bridge", ExperimentBridgeRequest())

            self.assertEqual(result.snapshot.status.training_status, "planned")
            self.assertTrue(result.tasks)


if __name__ == "__main__":
    unittest.main()

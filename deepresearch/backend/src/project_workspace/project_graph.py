"""LangGraph routing for project-level idea discovery."""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict

from project_workspace.idea_discovery import CandidateExtractor, ResearchRunner
from project_workspace.models import IdeaCandidate, IdeaDiscoveryResult, ProjectStatus
from project_workspace.novelty import NoveltyChecker, NoveltyCheckService
from project_workspace.service import ProjectWorkspaceService


class ProjectIdeaDiscoveryGraphState(TypedDict):
    """State carried by the project-level idea discovery graph."""

    project_id: str
    report_markdown: NotRequired[str | None]
    auto_select_top: bool
    enable_novelty_check: bool
    selected_candidate_title: NotRequired[str | None]
    selected_candidate_index: NotRequired[int | None]
    status: NotRequired[ProjectStatus]
    candidates: NotRequired[list[IdeaCandidate]]
    selected_candidate: NotRequired[IdeaCandidate | None]
    result: NotRequired[IdeaDiscoveryResult]


class ProjectIdeaDiscoveryGraph:
    """Small LangGraph wrapper around the project workspace protocol."""

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
        self._graph = self._build_graph()

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
        """Run the graph and return the final persisted result."""

        final_state = self._graph.invoke(
            {
                "project_id": project_id,
                "report_markdown": report_markdown,
                "auto_select_top": auto_select_top,
                "enable_novelty_check": enable_novelty_check,
                "selected_candidate_title": selected_candidate_title,
                "selected_candidate_index": selected_candidate_index,
            }
        )
        return final_state["result"]

    def _build_graph(self):
        from langgraph.graph import END, START, StateGraph

        builder = StateGraph(ProjectIdeaDiscoveryGraphState)
        builder.add_node("load_project", self._load_project)
        builder.add_node("resolve_report", self._resolve_report)
        builder.add_node("extract_candidates", self._extract_candidates)
        builder.add_node("novelty_check", self._novelty_check)
        builder.add_node("select_idea_gate", self._select_idea_gate)
        builder.add_node("persist_outputs", self._persist_outputs)

        builder.add_edge(START, "load_project")
        builder.add_edge("load_project", "resolve_report")
        builder.add_edge("resolve_report", "extract_candidates")
        builder.add_conditional_edges(
            "extract_candidates",
            self._route_after_extract_candidates,
            {
                "novelty_check": "novelty_check",
                "select_idea_gate": "select_idea_gate",
            },
        )
        builder.add_edge("novelty_check", "select_idea_gate")
        builder.add_edge("select_idea_gate", "persist_outputs")
        builder.add_edge("persist_outputs", END)
        return builder.compile()

    def _load_project(
        self,
        state: ProjectIdeaDiscoveryGraphState,
    ) -> ProjectIdeaDiscoveryGraphState:
        return {
            **state,
            "status": self._workspace.load_status(state["project_id"]),
        }

    def _resolve_report(
        self,
        state: ProjectIdeaDiscoveryGraphState,
    ) -> ProjectIdeaDiscoveryGraphState:
        report = (state.get("report_markdown") or "").strip()
        if report:
            return {**state, "report_markdown": report}

        if self._research_runner is None:
            raise ValueError("report_markdown is required when no research_runner is configured")

        status = state["status"]
        report = self._research_runner(status.topic).strip()
        if not report:
            raise ValueError("idea discovery report must not be empty")
        return {**state, "report_markdown": report}

    def _extract_candidates(
        self,
        state: ProjectIdeaDiscoveryGraphState,
    ) -> ProjectIdeaDiscoveryGraphState:
        from project_workspace.idea_discovery import (
            _normalize_candidates,
            extract_idea_candidates,
        )

        status = state["status"]
        report = state["report_markdown"] or ""
        candidates: list[IdeaCandidate] = []
        if self._candidate_extractor is not None:
            try:
                candidates = _normalize_candidates(
                    self._candidate_extractor(report, status.topic)
                )
            except Exception:
                candidates = []
        if len(candidates) < 3:
            fallback = extract_idea_candidates(report, topic=status.topic)
            if len(fallback) >= 3 or not candidates:
                candidates = fallback
        return {**state, "candidates": candidates}

    @staticmethod
    def _route_after_extract_candidates(state: ProjectIdeaDiscoveryGraphState) -> str:
        if state.get("enable_novelty_check"):
            return "novelty_check"
        return "select_idea_gate"

    def _novelty_check(
        self,
        state: ProjectIdeaDiscoveryGraphState,
    ) -> ProjectIdeaDiscoveryGraphState:
        status = state["status"]
        candidates = NoveltyCheckService(
            novelty_checker=self._novelty_checker
        ).check(
            state.get("candidates", []),
            topic=status.topic,
        )
        return {**state, "candidates": candidates}

    def _select_idea_gate(
        self,
        state: ProjectIdeaDiscoveryGraphState,
    ) -> ProjectIdeaDiscoveryGraphState:
        from project_workspace.idea_discovery import select_idea_candidate

        selected = select_idea_candidate(
            state.get("candidates", []),
            selected_candidate_title=state.get("selected_candidate_title"),
            selected_candidate_index=state.get("selected_candidate_index"),
            auto_select_top=state["auto_select_top"],
        )
        return {**state, "selected_candidate": selected}

    def _persist_outputs(
        self,
        state: ProjectIdeaDiscoveryGraphState,
    ) -> ProjectIdeaDiscoveryGraphState:
        candidates = state.get("candidates", [])
        snapshot = self._workspace.write_idea_discovery_outputs(
            state["project_id"],
            report_markdown=state["report_markdown"] or "",
            candidates=candidates,
            auto_select_top=state["auto_select_top"],
            selected_candidate=state.get("selected_candidate"),
        )
        selected = state.get("selected_candidate")
        return {
            **state,
            "result": IdeaDiscoveryResult(
                project_id=snapshot.project_id,
                report_markdown=state["report_markdown"] or "",
                selected_idea=selected,
                candidates=candidates,
                snapshot=snapshot,
            ),
        }

"""FastAPI entrypoint exposing the DeepResearchAgent via HTTP."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from config import Configuration, SearchAPI
from project_workspace import (
    DirectionRefinementResult,
    DirectionRefinementService,
    ExperimentBridgeResult,
    ExperimentBridgeService,
    ExternalReviewResult,
    ExternalReviewService,
    IdeaDiscoveryResult,
    ProjectIdeaDiscoveryService,
    ProjectSnapshot,
    ProjectWorkspaceService,
)
from project_workspace.models import ProjectStage

# 添加控制台日志处理程序
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <4}</level> | <cyan>using_function:{function}</cyan> | <cyan>{file}:{line}</cyan> | <level>{message}</level>",
    colorize=True,
)


# 添加错误日志文件处理程序
logger.add(
    sink=sys.stderr,
    level="ERROR",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <4}</level> | <cyan>using_function:{function}</cyan> | <cyan>{file}:{line}</cyan> | <level>{message}</level>",
    colorize=True,
)


class ResearchRequest(BaseModel):
    """Payload for triggering a research run."""
    session_id: str | None = None
    topic: str = Field(..., description="Research topic supplied by the user")
    search_api: SearchAPI | None = Field(
        default=None,
        description="Override the default search backend configured via env",
    )


class ResearchResponse(BaseModel):
    """HTTP response containing the generated report and structured tasks."""
    session_id: str
    report_markdown: str = Field(
        ..., description="Markdown-formatted research report including sections"
    )
    todo_items: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Structured TODO items with summaries and sources",
    )


class ProjectCreateRequest(BaseModel):
    """Payload for creating a durable research project workspace."""

    project_id: str | None = Field(
        default=None,
        description="Optional stable project identifier; generated from topic when omitted",
    )
    topic: str = Field(..., description="Research project topic")
    selected_idea: str | None = Field(
        default=None,
        description="Optional active idea to seed docs/research_contract.md",
    )


class ProjectStatusPatch(BaseModel):
    """Allowed project status updates from the UI or automation layer."""

    stage: ProjectStage | None = None
    selected_idea: str | None = None
    baseline: str | None = None
    current_branch: str | None = None
    training_status: str | None = None
    active_tasks: list[str] | None = None
    next_action: str | None = None


class IdeaDiscoveryRequest(BaseModel):
    """Payload for running project-level idea discovery."""

    report_markdown: str | None = Field(
        default=None,
        description="Optional precomputed discovery report; avoids invoking the research agent",
    )
    run_research: bool = Field(
        default=False,
        description="When true, invoke DeepResearchAgent for the project topic",
    )
    auto_select_top: bool = Field(
        default=True,
        description="Select the top extracted candidate and refresh contract/plan files",
    )
    use_structured_extraction: bool = Field(
        default=True,
        description="Use LangChain structured output for candidate extraction when available",
    )
    use_project_graph: bool = Field(
        default=True,
        description="Route the workflow through the project-level LangGraph when available",
    )
    enable_novelty_check: bool = Field(
        default=False,
        description="Annotate candidates with initial novelty-check fields",
    )
    selected_candidate_title: str | None = Field(
        default=None,
        description="Optional exact candidate title to select at the idea gate",
    )
    selected_candidate_index: int | None = Field(
        default=None,
        description="Optional 1-based candidate index to select at the idea gate",
    )


class ExternalReviewRequest(BaseModel):
    """Payload for appending one external review round."""

    review_text: str | None = Field(
        default=None,
        description="Optional externally produced raw review text",
    )
    verdict: str | None = Field(
        default=None,
        description="Optional verdict: positive, needs_revision, reject, unclear",
    )
    max_rounds: int = Field(
        default=4,
        description="Maximum review rounds before marking max_rounds_reached",
    )
    use_external_model: bool = Field(
        default=True,
        description="When review_text is omitted, use the configured LangChain model as reviewer",
    )


class ExperimentBridgeRequest(BaseModel):
    """Payload for generating experiment bridge tasks."""

    sanity_first: bool = Field(
        default=True,
        description="Prepend a small sanity-check task before full experiments",
    )


def _mask_secret(value: Optional[str], visible: int = 4) -> str:
    """Mask sensitive tokens while keeping leading and trailing characters."""
    if not value:
        return "unset"

    if len(value) <= visible * 2:
        return "*" * len(value)

    return f"{value[:visible]}...{value[-visible:]}"


def _build_config(payload: ResearchRequest) -> Configuration:
    overrides: Dict[str, Any] = {}

    if payload.search_api is not None:
        overrides["search_api"] = payload.search_api

    return Configuration.from_env(overrides=overrides)


def _project_workspace(config: Configuration | None = None) -> ProjectWorkspaceService:
    config = config or Configuration.from_env()
    return ProjectWorkspaceService(config.project_workspace_root)


def _build_agent(config: Configuration):
    """Import the research agent lazily so project APIs do not require LLM deps."""

    from agent import DeepResearchAgent

    return DeepResearchAgent(config=config)


def _build_candidate_extractor(config: Configuration):
    """Best-effort structured idea extractor construction."""

    try:
        from project_workspace.structured_idea_extractor import (
            build_structured_idea_extractor,
        )

        return build_structured_idea_extractor(config)
    except ModuleNotFoundError as exc:
        logger.warning(
            "Structured idea extractor dependency missing ({}); using deterministic fallback",
            exc.name,
        )
        return None
    except Exception as exc:
        logger.warning(
            "Structured idea extractor unavailable ({}); using deterministic fallback",
            exc,
        )
        return None


def _build_novelty_checker(config: Configuration):
    """Best-effort structured novelty checker construction."""

    try:
        from project_workspace.structured_novelty_checker import (
            build_structured_novelty_checker,
        )

        return build_structured_novelty_checker(config)
    except ModuleNotFoundError as exc:
        logger.warning(
            "Structured novelty checker dependency missing ({}); using unclear fallback",
            exc.name,
        )
        return None
    except Exception as exc:
        logger.warning(
            "Structured novelty checker unavailable ({}); using unclear fallback",
            exc,
        )
        return None


def _build_external_reviewer(config: Configuration):
    """Best-effort external reviewer construction."""

    try:
        from project_workspace.structured_external_reviewer import (
            build_structured_external_reviewer,
        )

        return build_structured_external_reviewer(config)
    except ModuleNotFoundError as exc:
        logger.warning(
            "External reviewer dependency missing ({}); using review fallback",
            exc.name,
        )
        return None
    except Exception as exc:
        logger.warning(
            "External reviewer unavailable ({}); using review fallback",
            exc,
        )
    return None


def _build_direction_refiner(config: Configuration):
    """Best-effort selected-direction refiner construction."""

    try:
        from project_workspace.direction_refinement import (
            build_structured_direction_refiner,
        )
    except ImportError as exc:
        logger.warning(
            "Direction refiner dependency missing ({}); using fallback refinement",
            exc,
        )
        return None
    try:
        return build_structured_direction_refiner(config)
    except Exception as exc:
        logger.warning(
            "Direction refiner unavailable ({}); using fallback refinement",
            exc,
        )
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="HelloAgents Deep Researcher")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def log_startup_configuration() -> None:
        config = Configuration.from_env()

        if config.llm_provider == "ollama":
            base_url = config.sanitized_ollama_url()
        elif config.llm_provider == "lmstudio":
            base_url = config.lmstudio_base_url
        else:
            base_url = config.llm_base_url or "unset"

        logger.info(
            "DeepResearch configuration loaded: provider=%s model=%s base_url=%s search_api=%s "
            "project_workspace_root=%s "
            "max_loops=%s fetch_full_page=%s tool_calling=%s strip_thinking=%s api_key=%s",
            config.llm_provider,
            config.resolved_model() or "unset",
            base_url,
            (config.search_api.value if isinstance(config.search_api, SearchAPI) else config.search_api),
            config.project_workspace_root,
            config.max_web_research_loops,
            config.fetch_full_page,
            config.use_tool_calling,
            config.strip_thinking_tokens,
            _mask_secret(config.llm_api_key),
        )

    @app.get("/healthz")
    def health_check() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/projects", response_model=ProjectSnapshot)
    def create_project(payload: ProjectCreateRequest) -> ProjectSnapshot:
        try:
            return _project_workspace().create_project(
                project_id=payload.project_id,
                topic=payload.topic,
                selected_idea=payload.selected_idea,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/projects/{project_id}", response_model=ProjectSnapshot)
    def get_project(project_id: str) -> ProjectSnapshot:
        try:
            return _project_workspace().snapshot(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/projects/{project_id}", response_model=ProjectSnapshot)
    def update_project(project_id: str, payload: ProjectStatusPatch) -> ProjectSnapshot:
        try:
            return _project_workspace().update_status(
                project_id,
                payload.model_dump(exclude_unset=True),
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/idea-discovery", response_model=IdeaDiscoveryResult)
    def run_project_idea_discovery(
        project_id: str,
        payload: IdeaDiscoveryRequest,
    ) -> IdeaDiscoveryResult:
        workspace = _project_workspace()

        try:
            research_runner = None
            candidate_extractor = None
            novelty_checker = None
            config = Configuration.from_env()
            if payload.run_research:
                status = workspace.load_status(project_id)

                def research_runner(topic: str) -> str:
                    result = _build_agent(config).run(topic, session_id=status.project_id)
                    return result.report_markdown or result.running_summary or ""

            if payload.use_structured_extraction:
                candidate_extractor = _build_candidate_extractor(config)
            if payload.enable_novelty_check:
                novelty_checker = _build_novelty_checker(config)

            if payload.use_project_graph:
                try:
                    from project_workspace.project_graph import ProjectIdeaDiscoveryGraph

                    return ProjectIdeaDiscoveryGraph(
                        workspace,
                        research_runner=research_runner,
                        candidate_extractor=candidate_extractor,
                        novelty_checker=novelty_checker,
                    ).run(
                        project_id,
                        report_markdown=payload.report_markdown,
                        auto_select_top=payload.auto_select_top,
                        enable_novelty_check=payload.enable_novelty_check,
                        selected_candidate_title=payload.selected_candidate_title,
                        selected_candidate_index=payload.selected_candidate_index,
                    )
                except ModuleNotFoundError as exc:
                    if exc.name != "langgraph":
                        raise
                    logger.warning("LangGraph unavailable; using service fallback")

            return ProjectIdeaDiscoveryService(
                workspace,
                research_runner=research_runner,
                candidate_extractor=candidate_extractor,
                novelty_checker=novelty_checker,
            ).run(
                project_id,
                report_markdown=payload.report_markdown,
                auto_select_top=payload.auto_select_top,
                enable_novelty_check=payload.enable_novelty_check,
                selected_candidate_title=payload.selected_candidate_title,
                selected_candidate_index=payload.selected_candidate_index,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/external-review", response_model=ExternalReviewResult)
    def run_external_review(
        project_id: str,
        payload: ExternalReviewRequest,
    ) -> ExternalReviewResult:
        try:
            reviewer = None
            if payload.use_external_model and not payload.review_text:
                reviewer = _build_external_reviewer(Configuration.from_env().reviewer_config())
            return ExternalReviewService(
                _project_workspace(),
                reviewer=reviewer,
            ).run(
                project_id,
                review_text=payload.review_text,
                verdict=payload.verdict,
                max_rounds=payload.max_rounds,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/direction-refine", response_model=DirectionRefinementResult)
    def refine_project_direction(project_id: str) -> DirectionRefinementResult:
        try:
            refiner = _build_direction_refiner(Configuration.from_env())
            return DirectionRefinementService(
                _project_workspace(),
                refiner=refiner,
            ).run(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/experiment-bridge", response_model=ExperimentBridgeResult)
    def run_experiment_bridge(
        project_id: str,
        payload: ExperimentBridgeRequest,
    ) -> ExperimentBridgeResult:
        try:
            return ExperimentBridgeService(_project_workspace()).run(
                project_id,
                sanity_first=payload.sanity_first,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/research", response_model=ResearchResponse)
    def run_research(payload: ResearchRequest) -> ResearchResponse:
        try:
            config = _build_config(payload)
            agent = _build_agent(config)
            result = agent.run(payload.topic, session_id = payload.session_id)
        except ValueError as exc:  # Likely due to unsupported configuration
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive guardrail
            raise HTTPException(status_code=500, detail="Research failed") from exc

        todo_payload = [
            {
                "id": item.id,
                "title": item.title,
                "intent": item.intent,
                "query": item.query,
                "queries": list(item.queries or [item.query]),
                "round_id": item.round_id,
                "origin": item.origin,
                "parent_task_id": item.parent_task_id,
                "status": item.status,
                "summary": item.summary,
                "sources_summary": item.sources_summary,
                "note_id": item.note_id,
                "note_path": item.note_path,
                "planned_capabilities": item.planned_capabilities,
                "current_capability": item.current_capability,
                "route_intent_label": item.route_intent_label,
                "route_confidence": item.route_confidence,
                "route_reason": item.route_reason,
            }
            for item in result.todo_items
        ]

        return ResearchResponse(
            session_id=result.session_id,
            report_markdown=(result.report_markdown or result.running_summary or ""),
            todo_items=todo_payload,
        )

    @app.post("/research/stream")
    def stream_research(payload: ResearchRequest) -> StreamingResponse:
        try:
            config = _build_config(payload)
            agent = _build_agent(config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        def event_iterator() -> Iterator[str]:
            try:
                for event in agent.run_stream(payload.topic, session_id = payload.session_id):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:  # pragma: no cover - defensive guardrail
                logger.exception("Streaming research failed")
                error_payload = {"type": "error", "detail": str(exc)}
                yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_iterator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )

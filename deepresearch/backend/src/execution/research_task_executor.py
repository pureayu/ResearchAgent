"""Task executor for deep-research tasks."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

from capability_types import (
    DEFAULT_CAPABILITY_CHAIN,
    INSPECT_GITHUB_REPO_CAPABILITY,
    SEARCH_ACADEMIC_PAPERS_CAPABILITY,
    SEARCH_WEB_PAGES_CAPABILITY,
)
from config import Configuration
from execution.evidence_policy import EvidencePolicy
from execution.models import ExecutionEvent, TaskExecutionResult, TaskPatch
from models import SummaryState, TodoItem
from services.search import dispatch_capability_search, prepare_research_context
from services.source_routing import SourceRoutingService
from services.summarizer import SummarizationService


class ResearchTaskExecutor:
    """Execute one deep-research task without mutating global workflow state."""

    def __init__(
        self,
        config: Configuration,
        summarizer: SummarizationService,
        source_routing: SourceRoutingService,
        evidence_policy: EvidencePolicy,
        drain_tool_events: Callable[[int | None], list[dict[str, Any]]],
    ) -> None:
        self._config = config
        self._summarizer = summarizer
        self._source_routing = source_routing
        self._evidence_policy = evidence_policy
        self._drain_tool_events = drain_tool_events

    def execute(
        self,
        state: SummaryState,
        task: TodoItem,
        *,
        emit_stream: bool,
        step: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Run multi-source retrieval and task summarization."""

        runtime_task = replace(task)
        runtime_task.status = "in_progress"
        runtime_task.latest_query = task.query

        local_events: list[ExecutionEvent] = []
        tool_events: list[dict[str, Any]] = []
        context_to_append: str | None = None
        sources_to_append: str | None = None

        def emit(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
            local_events.append(ExecutionEvent(payload=dict(payload)))
            if emit_stream:
                yield payload

        def emit_many(payloads: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
            for payload in payloads:
                yield from emit(payload)

        def drain_and_capture_tool_events() -> list[dict[str, Any]]:
            drained = self._drain_tool_events(step)
            if not drained:
                return []

            tool_events.extend(drained)
            for payload in drained:
                if payload.get("task_id") != runtime_task.id:
                    continue
                note_id = payload.get("note_id")
                if note_id:
                    runtime_task.note_id = str(note_id)
                    note_path = payload.get("note_path")
                    if isinstance(note_path, str) and note_path:
                        runtime_task.note_path = note_path
            return drained

        route_plan = self._source_routing.plan_capabilities(state.research_topic or task.query, runtime_task)
        runtime_task.planned_capabilities = list(
            route_plan.preferred_capabilities or DEFAULT_CAPABILITY_CHAIN
        )
        runtime_task.route_intent_label = route_plan.intent_label
        runtime_task.route_confidence = route_plan.confidence
        runtime_task.route_reason = route_plan.reason

        yield from emit(
            {
                "type": "route_plan",
                "task_id": runtime_task.id,
                "intent_label": runtime_task.route_intent_label,
                "preferred_capabilities": list(runtime_task.planned_capabilities),
                "confidence": runtime_task.route_confidence,
                "reason": runtime_task.route_reason,
            }
        )

        search_result: dict[str, Any] | None = None
        answer_text: str | None = None
        current_query = task.query
        gap_reason: str | None = None
        deferred_notices: list[str] = []

        for index, capability_id in enumerate(runtime_task.planned_capabilities, start=1):
            has_next_source = index < len(runtime_task.planned_capabilities)
            runtime_task.current_capability = capability_id

            if index > 1:
                runtime_task.needs_followup = True
                next_query = self._evidence_policy.build_followup_query(
                    task,
                    base_query=current_query,
                    gap_reason=gap_reason or "no_results",
                    target_capability=capability_id,
                )
                runtime_task.latest_query = next_query
                yield from emit(
                    {
                        "type": "query_rewrite",
                        "task_id": runtime_task.id,
                        "backend": capability_id,
                        "current_capability": capability_id,
                        "planned_capabilities": list(runtime_task.planned_capabilities),
                        "gap_reason": gap_reason,
                        "previous_query": current_query,
                        "rewritten_query": next_query,
                        "attempt": runtime_task.attempt_count + 1,
                    }
                )
                current_query = next_query
            else:
                runtime_task.latest_query = current_query

            yield from emit(
                self._build_stage_event(
                    task=runtime_task,
                    capability_id=capability_id,
                    query=current_query,
                )
            )

            source_result, source_notices, source_answer, backend_label = dispatch_capability_search(
                capability_id,
                current_query,
                self._config,
                state.research_loop_count,
                task=runtime_task,
            )
            runtime_task.attempt_count += 1

            if source_result and source_result.get("results"):
                deferred_notices = []
                search_result = (
                    source_result
                    if search_result is None
                    else self._merge_search_results(search_result, source_result)
                )
                if source_answer and not answer_text:
                    answer_text = source_answer
            elif search_result is None:
                search_result = source_result
                self._extend_unique_notices(deferred_notices, source_notices)
            else:
                self._extend_unique_notices(deferred_notices, source_notices)

            runtime_task.search_backend = (
                str(search_result.get("backend") or backend_label) if search_result else backend_label
            )
            runtime_task.evidence_count = len((search_result or {}).get("results", []))
            runtime_task.top_score = self._extract_top_score(search_result)

            drained = drain_and_capture_tool_events()
            yield from emit_many(drained)
            yield from emit(
                self._build_search_result_event(
                    task=runtime_task,
                    search_result=search_result,
                    backend=backend_label,
                    query=current_query,
                )
            )

            gap_reason = self._evidence_policy.assess_evidence_gap(
                current_query,
                search_result,
                capability_id,
            )
            gap_reason = self._evidence_policy.finalize_gap_reason(
                gap_reason,
                has_next_source=has_next_source,
            )
            runtime_task.evidence_gap_reason = gap_reason
            if gap_reason is None:
                break

        if not search_result or not search_result.get("results"):
            runtime_task.notices.extend(deferred_notices)
            runtime_task.status = "skipped"
            yield from emit_many(drain_and_capture_tool_events())
            yield from emit_many(self._emit_notices(deferred_notices, runtime_task.id))
            yield from emit(
                self._mark_skipped_or_failed(
                    task=runtime_task,
                    search_result=search_result,
                    status="skipped",
                )
            )
            return TaskExecutionResult(
                status=runtime_task.status,
                task_patch=TaskPatch.from_task(runtime_task),
                events=local_events,
                search_result=search_result,
                answer_text=answer_text,
                followup_triggered=runtime_task.needs_followup,
                tool_events=tool_events,
            )

        sources_summary, context = prepare_research_context(
            search_result,
            answer_text,
            self._config,
        )
        runtime_task.sources_summary = sources_summary
        context_to_append = context
        sources_to_append = sources_summary

        yield from emit_many(drain_and_capture_tool_events())
        yield from emit(
            {
                "type": "sources",
                "task_id": runtime_task.id,
                "latest_sources": sources_summary,
                "raw_context": context,
                "backend": runtime_task.search_backend,
                "attempt_count": runtime_task.attempt_count,
                "evidence_count": runtime_task.evidence_count,
                "top_score": runtime_task.top_score,
                "needs_followup": runtime_task.needs_followup,
                "latest_query": runtime_task.latest_query,
                "evidence_gap_reason": runtime_task.evidence_gap_reason,
                "planned_capabilities": list(runtime_task.planned_capabilities),
                "current_capability": runtime_task.current_capability,
                "route_intent_label": runtime_task.route_intent_label,
                "route_confidence": runtime_task.route_confidence,
                "route_reason": runtime_task.route_reason,
                "note_id": runtime_task.note_id,
                "note_path": runtime_task.note_path,
                **self._evidence_policy.summarize_search_result(search_result),
            }
        )

        summary_text = yield from self._build_task_summary(
            state=state,
            task=runtime_task,
            context=context,
            emit=emit,
            drain_and_capture_tool_events=drain_and_capture_tool_events,
            emit_stream=emit_stream,
        )

        runtime_task.summary = summary_text.strip() if summary_text else "暂无可用信息"
        runtime_task.status = "completed"

        yield from emit_many(drain_and_capture_tool_events())
        yield from emit(
            {
                "type": "task_status",
                "task_id": runtime_task.id,
                "status": "completed",
                "summary": runtime_task.summary,
                "sources_summary": runtime_task.sources_summary,
                "note_id": runtime_task.note_id,
                "note_path": runtime_task.note_path,
                "attempt_count": runtime_task.attempt_count,
                "search_backend": runtime_task.search_backend,
                "evidence_count": runtime_task.evidence_count,
                "top_score": runtime_task.top_score,
                "needs_followup": runtime_task.needs_followup,
                "latest_query": runtime_task.latest_query,
                "evidence_gap_reason": runtime_task.evidence_gap_reason,
                "planned_capabilities": list(runtime_task.planned_capabilities),
                "current_capability": runtime_task.current_capability,
                "route_intent_label": runtime_task.route_intent_label,
                "route_confidence": runtime_task.route_confidence,
                "route_reason": runtime_task.route_reason,
                **self._evidence_policy.summarize_search_result(search_result),
            }
        )

        return TaskExecutionResult(
            status=runtime_task.status,
            task_patch=TaskPatch.from_task(runtime_task),
            events=local_events,
            search_result=search_result,
            answer_text=answer_text,
            followup_triggered=runtime_task.needs_followup,
            tool_events=tool_events,
            context_to_append=context_to_append,
            sources_to_append=sources_to_append,
            research_loop_increment=1,
        )

    def _build_task_summary(
        self,
        *,
        state: SummaryState,
        task: TodoItem,
        context: str,
        emit: Callable[[dict[str, Any]], Iterator[dict[str, Any]]],
        drain_and_capture_tool_events: Callable[[], list[dict[str, Any]]],
        emit_stream: bool,
    ) -> Iterator[dict[str, Any]]:
        summary_text: str | None = None

        if emit_stream:
            summary_stream, summary_getter = self._summarizer.stream_task_summary(
                state,
                task,
                context,
            )
            try:
                yield from self._emit_tool_events(drain_and_capture_tool_events, emit)
                for chunk in summary_stream:
                    if chunk:
                        yield from emit(
                            {
                                "type": "task_summary_chunk",
                                "task_id": task.id,
                                "content": chunk,
                                "note_id": task.note_id,
                            }
                        )
                    yield from self._emit_tool_events(drain_and_capture_tool_events, emit)
            finally:
                summary_text = summary_getter()
        else:
            summary_text = self._summarizer.summarize_task(state, task, context)
            drain_and_capture_tool_events()

        return summary_text or "暂无可用信息"

    def _mark_skipped_or_failed(
        self,
        *,
        task: TodoItem,
        search_result: dict[str, Any] | None,
        status: str,
    ) -> dict[str, Any]:
        return {
            "type": "task_status",
            "task_id": task.id,
            "status": status,
            "title": task.title,
            "intent": task.intent,
            "note_id": task.note_id,
            "note_path": task.note_path,
            "attempt_count": task.attempt_count,
            "search_backend": task.search_backend,
            "evidence_count": task.evidence_count,
            "top_score": task.top_score,
            "needs_followup": task.needs_followup,
            "latest_query": task.latest_query,
            "evidence_gap_reason": task.evidence_gap_reason,
            "planned_capabilities": list(task.planned_capabilities),
            "current_capability": task.current_capability,
            "route_intent_label": task.route_intent_label,
            "route_confidence": task.route_confidence,
            "route_reason": task.route_reason,
            **self._evidence_policy.summarize_search_result(search_result),
        }

    @staticmethod
    def _emit_tool_events(
        drain_and_capture_tool_events: Callable[[], list[dict[str, Any]]],
        emit: Callable[[dict[str, Any]], Iterator[dict[str, Any]]],
    ) -> Iterator[dict[str, Any]]:
        for payload in drain_and_capture_tool_events():
            yield from emit(payload)

    @staticmethod
    def _extract_top_score(search_result: dict[str, Any] | None) -> float:
        if not search_result:
            return 0.0
        results = search_result.get("results") or []
        if not results:
            return 0.0
        try:
            return float(results[0].get("score") or 0.0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def _merge_search_results(
        left: dict[str, Any] | None,
        right: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged_results: dict[tuple[str, str, str, Any], dict[str, Any]] = {}
        merged_backends: list[str] = []
        merged_notices: list[str] = []
        merged_answer: str | None = None

        for payload in (left, right):
            if not payload:
                continue

            backend = str(payload.get("backend") or "")
            if backend and backend not in merged_backends:
                merged_backends.append(backend)

            answer = payload.get("answer")
            if not merged_answer and isinstance(answer, str) and answer.strip():
                merged_answer = answer.strip()

            for notice in payload.get("notices") or []:
                if notice and notice not in merged_notices:
                    merged_notices.append(notice)

            for item in payload.get("results") or []:
                if not isinstance(item, dict):
                    continue
                key = (
                    str(item.get("title") or ""),
                    str(item.get("url") or ""),
                    str(item.get("source_type") or ""),
                    item.get("page"),
                )
                current = merged_results.get(key)
                if current is None or float(item.get("score") or 0.0) > float(
                    current.get("score") or 0.0
                ):
                    merged_results[key] = item

        ordered_results = sorted(
            merged_results.values(),
            key=lambda item: float(item.get("score") or 0.0),
            reverse=True,
        )[:8]

        backend_label = "+".join(merged_backends) if merged_backends else "unknown"
        return {
            "results": ordered_results,
            "backend": backend_label,
            "answer": merged_answer,
            "notices": merged_notices,
        }

    def _build_search_result_event(
        self,
        *,
        task: TodoItem,
        search_result: dict[str, Any] | None,
        backend: str,
        query: str,
    ) -> dict[str, Any]:
        return {
            "type": "search_result",
            "task_id": task.id,
            "backend": backend,
            "query": query,
            "attempt_count": task.attempt_count,
            "evidence_count": task.evidence_count,
            "top_score": task.top_score,
            "needs_followup": task.needs_followup,
            "evidence_gap_reason": task.evidence_gap_reason,
            "planned_capabilities": list(task.planned_capabilities),
            "current_capability": task.current_capability,
            "route_intent_label": task.route_intent_label,
            "route_confidence": task.route_confidence,
            "route_reason": task.route_reason,
            **self._evidence_policy.summarize_search_result(search_result),
        }

    @staticmethod
    def _emit_notices(notices: list[str], task_id: int) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for notice in notices:
            if not notice:
                continue
            payloads.append(
                {
                    "type": "status",
                    "message": notice,
                    "task_id": task_id,
                }
            )
        return payloads

    @staticmethod
    def _extend_unique_notices(target: list[str], notices: list[str]) -> None:
        for notice in notices:
            if notice and notice not in target:
                target.append(notice)

    @staticmethod
    def _build_stage_event(
        *,
        task: TodoItem,
        capability_id: str,
        query: str,
    ) -> dict[str, Any]:
        if capability_id == SEARCH_ACADEMIC_PAPERS_CAPABILITY:
            stage = "retrieving_academic"
        elif capability_id == INSPECT_GITHUB_REPO_CAPABILITY:
            stage = "retrieving_github"
        elif capability_id == SEARCH_WEB_PAGES_CAPABILITY:
            stage = "retrieving_web"
        else:
            stage = "retrieving_source"

        return {
            "type": "task_stage",
            "task_id": task.id,
            "stage": stage,
            "backend": capability_id,
            "query": query,
            "attempt": task.attempt_count + 1,
            "previous_backend": task.search_backend,
            "previous_evidence_count": task.evidence_count,
            "previous_top_score": task.top_score,
            "evidence_gap_reason": task.evidence_gap_reason,
            "planned_capabilities": list(task.planned_capabilities),
            "current_capability": capability_id,
        }

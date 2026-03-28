"""Orchestrator coordinating the deep research workflow."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any, Callable, Iterator

from hello_agents import HelloAgentsLLM, ToolAwareSimpleAgent
from hello_agents.tools import ToolRegistry
from hello_agents.tools.builtin.note_tool import NoteTool

from config import Configuration
from prompts import (
    report_writer_instructions,
    task_summarizer_instructions,
    todo_planner_system_prompt,
)
from models import SummaryState, SummaryStateOutput, TodoItem
from services.planner import PlanningService
from services.reporter import ReportingService
from services.search import dispatch_search, prepare_research_context
from services.summarizer import SummarizationService
from services.tool_events import ToolCallTracker
from services.memory import MemoryService

logger = logging.getLogger(__name__)
LOCAL_LIBRARY_BACKEND = "local_library"
DEFAULT_WEB_BACKEND = "advanced"
LOCAL_QUERY_HINTS = {
    "rag",
    "retrieval",
    "generation",
    "citation",
    "survey",
    "self-rag",
    "crag",
    "multihop-rag",
    "rag-fusion",
    "adaptive-rag",
    "ragchecker",
    "检索",
    "文献",
    "论文",
    "综述",
    "引用",
    "生成",
    "知识库",
}


class DeepResearchAgent:
    """Coordinator orchestrating TODO-based research workflow using HelloAgents."""

    def __init__(self, config: Configuration | None = None) -> None:
        """Initialise the coordinator with configuration and shared tools."""
        self.config = config or Configuration.from_env()
        self.llm = self._init_llm()
        self.memory_service = MemoryService(self.config)

        self.note_tool = (
            NoteTool(workspace=self.config.notes_workspace)
            if self.config.enable_notes
            else None
        )
        self.tools_registry: ToolRegistry | None = None
        if self.note_tool:
            registry = ToolRegistry()
            registry.register_tool(self.note_tool)
            self.tools_registry = registry

        self._tool_tracker = ToolCallTracker(
            self.config.notes_workspace if self.config.enable_notes else None
        )
        self._tool_event_sink_enabled = False
        self._state_lock = Lock()

        self.todo_agent = self._create_tool_aware_agent(
            name="研究规划专家",
            system_prompt=todo_planner_system_prompt.strip(),
        )
        self.report_agent = self._create_tool_aware_agent(
            name="报告撰写专家",
            system_prompt=report_writer_instructions.strip(),
        )

        self._summarizer_factory: Callable[[], ToolAwareSimpleAgent] = lambda: self._create_tool_aware_agent(  # noqa: E501
            name="任务总结专家",
            system_prompt=task_summarizer_instructions.strip(),
        )

        self.planner = PlanningService(self.todo_agent, self.config)
        self.summarizer = SummarizationService(self._summarizer_factory, self.config)
        self.reporting = ReportingService(self.report_agent, self.config)
        self._last_search_notices: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def _init_llm(self) -> HelloAgentsLLM:
        """Instantiate HelloAgentsLLM following configuration preferences."""
        llm_kwargs: dict[str, Any] = {"temperature": 0.0}

        model_id = self.config.llm_model_id or self.config.local_llm
        if model_id:
            llm_kwargs["model"] = model_id

        provider = (self.config.llm_provider or "").strip()
        if provider:
            llm_kwargs["provider"] = provider

        if provider == "ollama":
            llm_kwargs["base_url"] = self.config.sanitized_ollama_url()
            if self.config.llm_api_key:
                llm_kwargs["api_key"] = self.config.llm_api_key
            else:
                llm_kwargs["api_key"] = "ollama"
        elif provider == "lmstudio":
            llm_kwargs["base_url"] = self.config.lmstudio_base_url
            if self.config.llm_api_key:
                llm_kwargs["api_key"] = self.config.llm_api_key
        else:
            if self.config.llm_base_url:
                llm_kwargs["base_url"] = self.config.llm_base_url
            if self.config.llm_api_key:
                llm_kwargs["api_key"] = self.config.llm_api_key

        return HelloAgentsLLM(**llm_kwargs)

    def _create_tool_aware_agent(self, *, name: str, system_prompt: str) -> ToolAwareSimpleAgent:
        """Instantiate a ToolAwareSimpleAgent sharing tool registry and tracker."""
        return ToolAwareSimpleAgent(
            name=name,
            llm=self.llm,
            system_prompt=system_prompt,
            enable_tool_calling=self.tools_registry is not None,
            tool_registry=self.tools_registry,
            tool_call_listener=self._tool_tracker.record,
        )

    def _set_tool_event_sink(self, sink: Callable[[dict[str, Any]], None] | None) -> None:
        """Enable or disable immediate tool event callbacks."""
        self._tool_event_sink_enabled = sink is not None
        self._tool_tracker.set_event_sink(sink)

    #一次研究
    def run(self, topic: str, session_id: str | None = None) -> SummaryStateOutput:
        """Execute the research workflow and return the final report."""
        state = SummaryState(research_topic=topic)
        state.session_id = self.memory_service.get_or_create_session(session_id, topic)
        state.recalled_context = self.memory_service.load_relevant_context(
            state.session_id, topic
        )
        state.run_id = self.memory_service.start_run(state.session_id, topic)
        #分解任务
        state.todo_items = self.planner.plan_todo_list(state)
        self._drain_tool_events(state)

        if not state.todo_items:
            logger.info("No TODO items generated; falling back to single task")
            state.todo_items = [self.planner.create_fallback_task(state)]

        for task in state.todo_items:
            for _ in self._execute_task(state, task, emit_stream=False):
                pass
        #一个完整的Markdown报告字符串
        yield {
            "type": "status",
            "message": "任务执行完成，正在生成最终报告",
        }
        report = self.reporting.generate_report(state)
        self._drain_tool_events(state)
        state.structured_report = report
        state.running_summary = report
        self._persist_final_report(state, report)
        self.memory_service.save_report_memory(state.run_id, state, report)
        self.memory_service.consolidate_semantic_facts(state.run_id, topic, report)
        return SummaryStateOutput(
            session_id=state.session_id,
            running_summary=report,
            report_markdown=report,
            todo_items=state.todo_items,
        )

    def run_stream(self, topic: str, session_id: str | None = None) -> Iterator[dict[str, Any]]:
        """Execute the workflow yielding incremental progress events."""
        state = SummaryState(research_topic=topic)
        state.session_id = self.memory_service.get_or_create_session(session_id, topic)
        state.recalled_context = self.memory_service.load_relevant_context(
            state.session_id, topic
        )
        state.run_id = self.memory_service.start_run(state.session_id, topic)
        yield {
            "type": "session",
            "session_id": state.session_id,
            "run_id": state.run_id,
        }
        logger.debug("Starting streaming research: topic=%s", topic)
        yield {"type": "status", "message": "初始化研究流程"}

        state.todo_items = self.planner.plan_todo_list(state)
        for event in self._drain_tool_events(state, step=0):
            yield event
        if not state.todo_items:
            state.todo_items = [self.planner.create_fallback_task(state)]

        channel_map: dict[int, dict[str, Any]] = {}
        for index, task in enumerate(state.todo_items, start=1):
            token = f"task_{task.id}"
            task.stream_token = token
            channel_map[task.id] = {"step": index, "token": token}

        yield {
            "type": "todo_list",
            "tasks": [self._serialize_task(t) for t in state.todo_items],
            "step": 0,
        }

        event_queue: Queue[dict[str, Any]] = Queue()

        def enqueue(
            event: dict[str, Any],
            *,
            task: TodoItem | None = None,
            step_override: int | None = None,
        ) -> None:
            payload = dict(event)
            target_task_id = payload.get("task_id")
            if task is not None:
                target_task_id = task.id
                payload["task_id"] = task.id

            channel = channel_map.get(target_task_id) if target_task_id is not None else None
            if channel:
                payload.setdefault("step", channel["step"])
                payload["stream_token"] = channel["token"]
            if step_override is not None:
                payload["step"] = step_override
            event_queue.put(payload)

        def tool_event_sink(event: dict[str, Any]) -> None:
            enqueue(event)

        self._set_tool_event_sink(tool_event_sink)

        threads: list[Thread] = []

        def worker(task: TodoItem, step: int) -> None:
            try:
                enqueue(
                    {
                        "type": "task_status",
                        "task_id": task.id,
                        "status": "in_progress",
                        "title": task.title,
                        "intent": task.intent,
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                        "attempt_count": task.attempt_count,
                        "search_backend": task.search_backend,
                        "evidence_count": task.evidence_count,
                        "top_score": task.top_score,
                        "needs_followup": task.needs_followup,
                    },
                    task=task,
                )

                for event in self._execute_task(state, task, emit_stream=True, step=step):
                    enqueue(event, task=task)
            except Exception as exc:  # pragma: no cover - defensive guardrail
                logger.exception("Task execution failed", exc_info=exc)
                enqueue(
                    {
                        "type": "task_status",
                        "task_id": task.id,
                        "status": "failed",
                        "detail": str(exc),
                        "title": task.title,
                        "intent": task.intent,
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                    },
                    task=task,
                )
            finally:
                enqueue({"type": "__task_done__", "task_id": task.id})

        for task in state.todo_items:
            step = channel_map.get(task.id, {}).get("step", 0)
            thread = Thread(target=worker, args=(task, step), daemon=True)
            threads.append(thread)
            thread.start()

        active_workers = len(state.todo_items)
        finished_workers = 0

        try:
            while finished_workers < active_workers:
                event = event_queue.get()
                if event.get("type") == "__task_done__":
                    finished_workers += 1
                    continue
                yield event

            while True:
                try:
                    event = event_queue.get_nowait()
                except Empty:
                    break
                if event.get("type") != "__task_done__":
                    yield event
        finally:
            self._set_tool_event_sink(None)
            for thread in threads:
                thread.join()

        report = self.reporting.generate_report(state)
        final_step = len(state.todo_items) + 1
        for event in self._drain_tool_events(state, step=final_step):
            yield event
        state.structured_report = report
        state.running_summary = report

        note_event = self._persist_final_report(state, report)
        self.memory_service.save_report_memory(state.run_id, state, report)
        yield {
            "type": "status",
            "message": "最终报告已生成，正在沉淀语义记忆",
        }
        self.memory_service.consolidate_semantic_facts(state.run_id, topic, report)
        if note_event:
            yield note_event

        yield {
            "type": "final_report",
            "report": report,
            "note_id": state.report_note_id,
            "note_path": state.report_note_path,
        }
        yield {"type": "done"}

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------
    def _execute_task(
        self,
        state: SummaryState,
        task: TodoItem,
        *,
        emit_stream: bool,
        step: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Run search + summarization for a single task."""
        task.status = "in_progress"
        initial_backend = (
            LOCAL_LIBRARY_BACKEND
            if self._looks_like_local_research_query(task.query)
            else self._resolve_web_backend()
        )
        local_result: dict[str, Any] | None = None
        local_notices: list[str] = []
        local_answer: str | None = None
        local_backend = initial_backend
        task.latest_query = task.query

        #把过程中的状态/结果事件往外推送，用于后续展示。
        for event in self._emit_search_stage(
            task,
            backend=initial_backend,
            query=task.query,
            emit_stream=emit_stream,
            step=step,
        ):
            yield event

        local_result, local_notices, local_answer, local_backend = dispatch_search(
            task.query,
            self.config,
            state.research_loop_count,
            backend_override=initial_backend,
        )
        task.attempt_count += 1
        task.search_backend = local_backend
        task.evidence_count = len((local_result or {}).get("results", []))
        task.top_score = self._extract_top_score(local_result)
        task.notices = list(local_notices)
        self._last_search_notices = local_notices

        if emit_stream:
            for event in self._drain_tool_events(state, step=step):
                yield event
            yield self._build_search_result_event(
                task,
                search_result=local_result,
                backend=local_backend,
                query=task.query,
                step=step,
            )
            for event in self._emit_notices(local_notices, task.id, step):
                yield event
        else:
            self._drain_tool_events(state)

        search_result = local_result
        notices = local_notices
        answer_text = local_answer
        backend = local_backend
        gap_reason = self._assess_evidence_gap(task.query, local_result, local_backend)
        task.evidence_gap_reason = gap_reason

        if gap_reason is not None:
            task.needs_followup = True
            web_backend = self._resolve_web_backend()
            max_followups = max(1, int(self.config.max_web_research_loops))
            current_query = task.query

            for followup_round in range(1, max_followups + 1):
                followup_query = self._build_followup_query(
                    task,
                    base_query=current_query,
                    gap_reason=gap_reason,
                    attempt_index=followup_round,
                )
                task.latest_query = followup_query

                if emit_stream:
                    yield {
                        "type": "query_rewrite",
                        "task_id": task.id,
                        "backend": web_backend,
                        "gap_reason": gap_reason,
                        "previous_query": current_query,
                        "rewritten_query": followup_query,
                        "attempt": task.attempt_count + 1,
                        "step": step,
                    }

                for event in self._emit_search_stage(
                    task,
                    backend=web_backend,
                    query=followup_query,
                    emit_stream=emit_stream,
                    step=step,
                ):
                    yield event

                web_result, web_notices, web_answer, web_backend = dispatch_search(
                    followup_query,
                    self.config,
                    state.research_loop_count,
                    backend_override=web_backend,
                )
                task.attempt_count += 1
                task.notices.extend(web_notices)
                self._last_search_notices = web_notices

                if web_result and web_result.get("results"):
                    search_result = self._merge_search_results(search_result, web_result)
                    notices = list(dict.fromkeys(notices + web_notices))
                    answer_text = web_answer or answer_text
                    backend = str(search_result.get("backend") or web_backend)
                    task.search_backend = backend
                    task.evidence_count = len(search_result.get("results", []))
                    task.top_score = self._extract_top_score(search_result)
                else:
                    task.search_backend = backend
                    task.evidence_count = len((search_result or {}).get("results", []))

                if emit_stream:
                    for event in self._drain_tool_events(state, step=step):
                        yield event
                    yield self._build_search_result_event(
                        task,
                        search_result=search_result,
                        backend=backend,
                        query=followup_query,
                        step=step,
                    )
                    for event in self._emit_notices(web_notices, task.id, step):
                        yield event
                else:
                    self._drain_tool_events(state)

                current_query = followup_query
                gap_reason = self._assess_evidence_gap(followup_query, search_result, backend)
                task.evidence_gap_reason = gap_reason
                if gap_reason is None:
                    break
        else:
            task.needs_followup = False
            task.evidence_gap_reason = None

        if not search_result or not search_result.get("results"):
            task.status = "skipped"
            if emit_stream:
                for event in self._drain_tool_events(state, step=step):
                    yield event
                yield {
                    "type": "task_status",
                    "task_id": task.id,
                    "status": "skipped",
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
                    "step": step,
                    **self._summarize_search_result(search_result),
                }
            else:
                self._drain_tool_events(state)
            self.memory_service.save_task_memory(state.run_id, task)
            return
        if not emit_stream:
            self._drain_tool_events(state)

        sources_summary, context = prepare_research_context(
            search_result,
            answer_text,
            self.config,
        )

        task.sources_summary = sources_summary

        summary_text: str | None = None

        if emit_stream:
            for event in self._drain_tool_events(state, step=step):
                yield event
            yield {
                "type": "sources",
                "task_id": task.id,
                "latest_sources": sources_summary,
                "raw_context": context,
                "step": step,
                "backend": backend,
                "attempt_count": task.attempt_count,
                "evidence_count": task.evidence_count,
                "top_score": task.top_score,
                "needs_followup": task.needs_followup,
                "latest_query": task.latest_query,
                "evidence_gap_reason": task.evidence_gap_reason,
                "note_id": task.note_id,
                "note_path": task.note_path,
                **self._summarize_search_result(search_result),
            }

            summary_stream, summary_getter = self.summarizer.stream_task_summary(state, task, context)
            try:
                for event in self._drain_tool_events(state, step=step):
                    yield event
                for chunk in summary_stream:
                    if chunk:
                        yield {
                            "type": "task_summary_chunk",
                            "task_id": task.id,
                            "content": chunk,
                            "note_id": task.note_id,
                            "step": step,
                        }
                    for event in self._drain_tool_events(state, step=step):
                        yield event
            finally:
                summary_text = summary_getter()
        else:
            summary_text = self.summarizer.summarize_task(state, task, context)
            self._drain_tool_events(state)

        task.summary = summary_text.strip() if summary_text else "暂无可用信息"
        task.status = "completed"
        self.memory_service.save_task_memory(state.run_id, task)
        with self._state_lock:
            state.web_research_results.append(context)
            state.sources_gathered.append(sources_summary)
            state.research_loop_count += 1

        if emit_stream:
            for event in self._drain_tool_events(state, step=step):
                yield event
            yield {
                "type": "task_status",
                "task_id": task.id,
                "status": "completed",
                "summary": task.summary,
                "sources_summary": task.sources_summary,
                "note_id": task.note_id,
                "note_path": task.note_path,
                "attempt_count": task.attempt_count,
                "search_backend": task.search_backend,
                "evidence_count": task.evidence_count,
                "top_score": task.top_score,
                "needs_followup": task.needs_followup,
                "latest_query": task.latest_query,
                "evidence_gap_reason": task.evidence_gap_reason,
                "step": step,
                **self._summarize_search_result(search_result),
            }
        else:
            self._drain_tool_events(state)

    def _drain_tool_events(
        self,
        state: SummaryState,
        *,
        step: int | None = None,
    ) -> list[dict[str, Any]]:
        """Proxy to the shared tool call tracker."""
        events = self._tool_tracker.drain(state, step=step)
        if self._tool_event_sink_enabled:
            return []
        return events

    @property
    def _tool_call_events(self) -> list[dict[str, Any]]:
        """Expose recorded tool events for legacy integrations."""
        return self._tool_tracker.as_dicts()

    def _serialize_task(self, task: TodoItem) -> dict[str, Any]:
        """Convert task dataclass to serializable dict for frontend."""
        return {
            "id": task.id,
            "title": task.title,
            "intent": task.intent,
            "query": task.query,
            "status": task.status,
            "summary": task.summary,
            "sources_summary": task.sources_summary,
            "note_id": task.note_id,
            "note_path": task.note_path,
            "stream_token": task.stream_token,
            "attempt_count": task.attempt_count,
            "search_backend": task.search_backend,
            "evidence_count": task.evidence_count,
            "top_score": task.top_score,
            "needs_followup": task.needs_followup,
            "latest_query": task.latest_query,
            "evidence_gap_reason": task.evidence_gap_reason,
        }

    def _resolve_web_backend(self) -> str:
        configured_backend = self.config.search_api.value
        if configured_backend == LOCAL_LIBRARY_BACKEND:
            return DEFAULT_WEB_BACKEND
        return configured_backend

    def _should_follow_up_with_web(
        self,
        query: str,
        search_result: dict[str, Any] | None,
    ) -> bool:
        return self._assess_evidence_gap(query, search_result, LOCAL_LIBRARY_BACKEND) is not None

    def _assess_evidence_gap(
        self,
        query: str,
        search_result: dict[str, Any] | None,
        backend: str,
    ) -> str | None:
        if not search_result:
            return "no_results"

        results = search_result.get("results") or []
        top_score = self._extract_top_score(search_result)
        source_breakdown = self._summarize_search_result(search_result).get("source_breakdown", {})

        if not results:
            return "no_results"

        if backend == LOCAL_LIBRARY_BACKEND:
            if not self._looks_like_local_research_query(query):
                return "query_needs_web"
            if len(results) < 3:
                return "insufficient_local_coverage"
            if top_score < 0.65:
                return "low_local_confidence"
            return None

        if len(results) < 3:
            return "insufficient_web_coverage"
        if top_score < 0.45:
            return "low_web_confidence"
        if backend != LOCAL_LIBRARY_BACKEND and source_breakdown.get("local_library", 0) == 0 and self._looks_like_local_research_query(query):
            return "missing_local_grounding"
        return None

    def _extract_top_score(self, search_result: dict[str, Any] | None) -> float:
        if not search_result:
            return 0.0
        results = search_result.get("results") or []
        if not results:
            return 0.0
        try:
            return float(results[0].get("score") or 0.0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    def _summarize_search_result(self, search_result: dict[str, Any] | None) -> dict[str, Any]:
        """Build lightweight metadata for search-result visualization and trace."""

        results = (search_result or {}).get("results") or []
        source_breakdown: dict[str, int] = {}
        titles_preview: list[str] = []

        for item in results:
            if not isinstance(item, dict):
                continue

            source_type = str(item.get("source_type") or "web")
            source_breakdown[source_type] = source_breakdown.get(source_type, 0) + 1

            title = str(item.get("title") or "").strip()
            if title and len(titles_preview) < 3:
                titles_preview.append(title)

        return {
            "source_breakdown": source_breakdown,
            "titles_preview": titles_preview,
        }

    def _looks_like_local_research_query(self, query: str) -> bool:
        lowered = query.lower()
        return any(term in lowered for term in LOCAL_QUERY_HINTS)
    
    # 基于当前 base_query
    # 结合 gap_reason
    # 再结合 task.title/task.intent
    # 拼出一个更适合补检索的新 query
    def _build_followup_query(
        self,
        task: TodoItem,
        *,
        base_query: str,
        gap_reason: str,
        attempt_index: int,
    ) -> str:
        compact_intent = re.sub(r"\s+", " ", f"{task.title} {task.intent}".strip())
        compact_intent = compact_intent[:120].strip()
        use_chinese = self._contains_cjk(base_query) or self._contains_cjk(compact_intent)

        if gap_reason == "query_needs_web":
            return base_query

        if use_chinese:
            suffixes = {
                1: " 论文 综述 方法 挑战 代表工作",
                2: " 最新 进展 对比 评测 最佳实践",
                3: " benchmark evaluation survey",
            }
        else:
            suffixes = {
                1: " paper survey methods challenges representative work",
                2: " latest advances comparison evaluation best practices",
                3: " benchmark survey review",
            }

        suffix = suffixes.get(attempt_index, suffixes[max(suffixes)])
        if gap_reason == "no_results":
            seed = compact_intent or base_query
        elif gap_reason.startswith("insufficient"):
            seed = f"{base_query} {compact_intent}".strip()
        elif gap_reason.startswith("low_"):
            seed = f"{base_query} {compact_intent}".strip()
        elif gap_reason == "missing_local_grounding":
            seed = f"{task.query} {compact_intent}".strip()
        else:
            seed = base_query

        return re.sub(r"\s+", " ", f"{seed}{suffix}".strip())

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

    def _merge_search_results(
        self,
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
                if current is None or float(item.get("score") or 0.0) > float(current.get("score") or 0.0):
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

    def _emit_search_stage(
        self,
        task: TodoItem,
        *,
        backend: str,
        query: str,
        emit_stream: bool,
        step: int | None,
    ) -> Iterator[dict[str, Any]]:
        if not emit_stream:
            return

        stage = "retrieving_local" if backend == LOCAL_LIBRARY_BACKEND else "retrieving_web"
        yield {
            "type": "task_stage",
            "task_id": task.id,
            "stage": stage,
            "backend": backend,
            "query": query,
            "attempt": task.attempt_count + 1,
            "previous_backend": task.search_backend,
            "previous_evidence_count": task.evidence_count,
            "previous_top_score": task.top_score,
            "evidence_gap_reason": task.evidence_gap_reason,
            "step": step,
        }

    def _build_search_result_event(
        self,
        task: TodoItem,
        *,
        search_result: dict[str, Any] | None,
        backend: str,
        query: str,
        step: int | None,
    ) -> dict[str, Any]:
        """Emit a structured per-attempt search summary event."""

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
            "step": step,
            **self._summarize_search_result(search_result),
        }

    def _emit_notices(
        self,
        notices: list[str],
        task_id: int,
        step: int | None,
    ) -> Iterator[dict[str, Any]]:
        for notice in notices:
            if not notice:
                continue
            yield {
                "type": "status",
                "message": notice,
                "task_id": task_id,
                "step": step,
            }

    def _persist_final_report(self, state: SummaryState, report: str) -> dict[str, Any] | None:
        if not self.note_tool or not report or not report.strip():
            return None

        note_title = f"研究报告：{state.research_topic}".strip() or "研究报告"
        tags = ["deep_research", "report"]
        content = report.strip()

        note_id = self._find_existing_report_note_id(state)
        response = ""

        if note_id:
            response = self.note_tool.run(
                {
                    "action": "update",
                    "note_id": note_id,
                    "title": note_title,
                    "note_type": "conclusion",
                    "tags": tags,
                    "content": content,
                }
            )
            if response.startswith("❌"):
                note_id = None

        if not note_id:
            response = self.note_tool.run(
                {
                    "action": "create",
                    "title": note_title,
                    "note_type": "conclusion",
                    "tags": tags,
                    "content": content,
                }
            )
            note_id = self._extract_note_id_from_text(response)

        if not note_id:
            return None

        state.report_note_id = note_id
        if self.config.notes_workspace:
            note_path = Path(self.config.notes_workspace) / f"{note_id}.md"
            state.report_note_path = str(note_path)
        else:
            note_path = None

        payload = {
            "type": "report_note",
            "note_id": note_id,
            "title": note_title,
            "content": content,
        }
        if note_path:
            payload["note_path"] = str(note_path)

        return payload

    def _find_existing_report_note_id(self, state: SummaryState) -> str | None:
        if state.report_note_id:
            return state.report_note_id

        for event in reversed(self._tool_tracker.as_dicts()):
            if event.get("tool") != "note":
                continue

            parameters = event.get("parsed_parameters") or {}
            if not isinstance(parameters, dict):
                continue

            action = parameters.get("action")
            if action not in {"create", "update"}:
                continue

            note_type = parameters.get("note_type")
            if note_type != "conclusion":
                title = parameters.get("title")
                if not (isinstance(title, str) and title.startswith("研究报告")):
                    continue

            note_id = parameters.get("note_id")
            if not note_id:
                note_id = self._tool_tracker._extract_note_id(event.get("result", ""))  # type: ignore[attr-defined]

            if note_id:
                return note_id

        return None

    @staticmethod
    def _extract_note_id_from_text(response: str) -> str | None:
        if not response:
            return None

        match = re.search(r"ID:\s*([^\n]+)", response)
        if not match:
            return None

        return match.group(1).strip()


def run_deep_research(topic: str, config: Configuration | None = None) -> SummaryStateOutput:
    """Convenience function mirroring the class-based API."""
    agent = DeepResearchAgent(config=config)
    return agent.run(topic)

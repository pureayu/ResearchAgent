"""Orchestrator coordinating the deep research workflow."""

from __future__ import annotations

import logging
import re
from collections.abc import Generator, Iterator
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from agent_runtime.factory import AgentRuntimeFactory
from agent_runtime.roles import (
    DIRECT_ANSWER_ROLE,
    MEMORY_RECALL_SELECTOR_ROLE,
    PLANNER_ROLE,
    REPORTER_ROLE,
    RESPONSE_MODE_CLASSIFIER_ROLE,
    REVIEWER_ROLE,
    SOURCE_ROUTE_PLANNER_ROLE,
)
from agent_runtime.tool_protocol import extract_note_id_from_text
from config import Configuration
from execution import (
    EvidencePolicy,
    RESPONSE_MODE_DEEP_RESEARCH,
    RESPONSE_MODE_DIRECT_ANSWER,
    RESPONSE_MODE_MEMORY_RECALL,
    ResearchTaskExecutor,
    SpecialModeExecutor,
    TaskExecutionResult,
    TaskPatch,
)
from models import SummaryState, SummaryStateOutput, TodoItem
from services.memory import MemoryService
from services.planner import PlanningService
from services.reporter import ReportingService
from services.reviewer import ReviewerService
from services.source_routing import SourceRoutingService
from services.summarizer import SummarizationService

logger = logging.getLogger(__name__)


class DeepResearchAgent:
    """Coordinator orchestrating the deep research workflow."""

    def __init__(self, config: Configuration | None = None) -> None:
        self.config = config or Configuration.from_env()
        self.memory_service = MemoryService(self.config)
        self.runtime_factory = AgentRuntimeFactory(self.config)

        self.note_tool = self.runtime_factory.note_tool
        self._tool_tracker = self.runtime_factory.tool_tracker
        self._tool_event_sink_enabled = False
        self._state_lock = Lock()

        self.todo_agent = self.runtime_factory.create_agent(PLANNER_ROLE)
        self.report_agent = self.runtime_factory.create_agent(REPORTER_ROLE)
        self.review_agent = self.runtime_factory.create_agent(REVIEWER_ROLE)
        self.direct_answer_agent = self.runtime_factory.create_agent(DIRECT_ANSWER_ROLE)
        self.response_mode_classifier_agent = self.runtime_factory.create_agent(
            RESPONSE_MODE_CLASSIFIER_ROLE
        )
        self.memory_recall_selector_agent = self.runtime_factory.create_agent(
            MEMORY_RECALL_SELECTOR_ROLE
        )
        self.source_route_planner_agent = self.runtime_factory.create_agent(
            SOURCE_ROUTE_PLANNER_ROLE
        )

        self.planner = PlanningService(self.todo_agent, self.config)
        self.summarizer = SummarizationService(
            self.runtime_factory.create_summarizer_factory(),
            self.config,
        )
        self.reporting = ReportingService(self.report_agent, self.config)
        self.reviewer = ReviewerService(self.review_agent, self.config)
        self.source_routing = SourceRoutingService(
            self.source_route_planner_agent,
            self.config,
        )

        self._evidence_policy = EvidencePolicy(self.config)
        self._research_task_executor = ResearchTaskExecutor(
            self.config,
            self.summarizer,
            self.source_routing,
            self._evidence_policy,
            self._drain_tool_events,
        )
        self._special_mode_executor = SpecialModeExecutor(
            self.config,
            self.direct_answer_agent,
            self.response_mode_classifier_agent,
            self.memory_recall_selector_agent,
            task_log_loader=self.memory_service.load_recent_task_logs,
        )

    def run(self, topic: str, session_id: str | None = None) -> SummaryStateOutput:
        """Execute the research workflow and return the final report."""

        state = self._build_state(topic, session_id)
        self._prepare_tasks(state)

        max_rounds = (
            1
            if state.response_mode != RESPONSE_MODE_DEEP_RESEARCH
            else max(1, int(self.config.max_research_rounds))
        )
        current_round = 1

        while current_round <= max_rounds:
            pending_tasks = self._pending_tasks_for_round(state, current_round)
            if not pending_tasks:
                break

            for task in pending_tasks:
                result = self._consume_execution(
                    self._execute_task(state, task, emit_stream=False),
                )
                self._finalize_task_result(state, task, result)

            if current_round >= max_rounds:
                break

            review = self.reviewer.review_progress(state, current_round)
            if review.is_sufficient or not review.followup_tasks:
                break

            appended_tasks = self._append_followup_tasks(
                state,
                review.followup_tasks,
                round_id=current_round + 1,
            )
            if not appended_tasks:
                break

            current_round += 1

        if state.response_mode == RESPONSE_MODE_DEEP_RESEARCH:
            report = self.reporting.generate_report(state)
            report_tool_events = self._drain_tool_events(None)
            self._apply_tool_event_bindings(state.todo_items, report_tool_events)
        else:
            report = self._build_single_task_report(state)

        state.structured_report = report
        state.running_summary = report
        self._persist_final_report(state, report)
        self._capture_profile_memory(state)
        self.memory_service.save_report_memory(state.run_id, state, report)
        if state.response_mode == RESPONSE_MODE_DEEP_RESEARCH:
            self.memory_service.consolidate_semantic_facts(state.run_id, topic, report)

        return SummaryStateOutput(
            session_id=state.session_id,
            running_summary=report,
            report_markdown=report,
            todo_items=state.todo_items,
        )

    def run_stream(self, topic: str, session_id: str | None = None) -> Iterator[dict[str, Any]]:
        """Execute the workflow yielding incremental progress events."""

        state = self._create_state(topic, session_id)
        yield {
            "type": "session",
            "session_id": state.session_id,
            "run_id": state.run_id,
        }
        yield {
            "type": "status",
            "message": "正在召回历史上下文",
        }
        state.recalled_context = self.memory_service.load_relevant_context(
            state.session_id,
            topic,
            exclude_run_id=state.run_id,
        )
        yield {
            "type": "status",
            "message": "正在判断响应模式",
        }
        state.response_mode = self._classify_response_mode(topic, state.recalled_context)
        yield {
            "type": "response_mode",
            "response_mode": state.response_mode,
            "label": self._response_mode_label(state.response_mode),
        }
        logger.debug("Starting streaming research: topic=%s", topic)
        yield {
            "type": "status",
            "message": self._initial_status_message(state.response_mode),
            "response_mode": state.response_mode,
        }

        if state.response_mode == RESPONSE_MODE_DEEP_RESEARCH:
            yield {
                "type": "status",
                "message": "正在规划任务",
                "response_mode": state.response_mode,
            }

        planner_events = self._prepare_tasks(state, stream_mode=True)
        for event in planner_events:
            yield event

        yield {
            "type": "todo_list",
            "tasks": [self._serialize_task(t) for t in state.todo_items],
            "step": 0,
            "response_mode": state.response_mode,
        }

        max_rounds = (
            1
            if state.response_mode != RESPONSE_MODE_DEEP_RESEARCH
            else max(1, int(self.config.max_research_rounds))
        )
        current_round = 1
        step_counter = 1

        while current_round <= max_rounds:
            pending_tasks = self._pending_tasks_for_round(state, current_round)
            if not pending_tasks:
                break

            yield {
                "type": "status",
                "message": (
                    f"开始第 {current_round} 轮研究"
                    if state.response_mode == RESPONSE_MODE_DEEP_RESEARCH
                    else "正在结合已召回上下文生成回答"
                    if state.response_mode == RESPONSE_MODE_DIRECT_ANSWER
                    else "正在整理当前会话中的相关历史记录"
                ),
                "response_mode": state.response_mode,
            }

            for task in pending_tasks:
                step = step_counter
                step_counter += 1
                task.stream_token = f"task_{task.id}"

                yield {
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
                    "step": step,
                    "stream_token": task.stream_token,
                    "round_id": task.round_id,
                    "origin": task.origin,
                    "parent_task_id": task.parent_task_id,
                    "planned_capabilities": list(task.planned_capabilities),
                    "current_capability": task.current_capability,
                    "route_intent_label": task.route_intent_label,
                    "route_confidence": task.route_confidence,
                    "route_reason": task.route_reason,
                    "response_mode": state.response_mode,
                }

                try:
                    result = yield from self._stream_task_execution(
                        state,
                        task,
                        step=step,
                    )
                    self._finalize_task_result(state, task, result)
                except Exception as exc:  # pragma: no cover - defensive guardrail
                    logger.exception("Task execution failed", exc_info=exc)
                    task.status = "failed"
                    self.memory_service.save_task_log(state.run_id, task)
                    yield {
                        "type": "task_status",
                        "task_id": task.id,
                        "status": "failed",
                        "detail": str(exc),
                        "title": task.title,
                        "intent": task.intent,
                        "note_id": task.note_id,
                        "note_path": task.note_path,
                        "step": step,
                        "stream_token": task.stream_token,
                        "planned_capabilities": list(task.planned_capabilities),
                        "current_capability": task.current_capability,
                        "route_intent_label": task.route_intent_label,
                        "route_confidence": task.route_confidence,
                        "route_reason": task.route_reason,
                        "response_mode": state.response_mode,
                    }

            if current_round >= max_rounds:
                break

            yield {
                "type": "status",
                "message": f"第 {current_round} 轮任务完成，正在评估研究覆盖度",
                "response_mode": state.response_mode,
            }
            review = self.reviewer.review_progress(state, current_round)
            if review.is_sufficient or not review.followup_tasks:
                yield {
                    "type": "status",
                    "message": "当前研究已覆盖核心问题，准备生成最终报告",
                    "response_mode": state.response_mode,
                }
                break

            appended_tasks = self._append_followup_tasks(
                state,
                review.followup_tasks,
                round_id=current_round + 1,
            )
            if not appended_tasks:
                yield {
                    "type": "status",
                    "message": "未生成有效追加任务，准备生成最终报告",
                    "response_mode": state.response_mode,
                }
                break

            yield {
                "type": "status",
                "message": f"发现研究缺口：{review.overall_gap or '需要补充证据'}，已追加 {len(appended_tasks)} 个任务",
                "response_mode": state.response_mode,
            }
            yield {
                "type": "todo_list",
                "tasks": [self._serialize_task(t) for t in state.todo_items],
                "step": 0,
                "response_mode": state.response_mode,
            }
            current_round += 1

        yield {
            "type": "status",
            "message": (
                "任务执行完成，正在生成最终报告"
                if state.response_mode == RESPONSE_MODE_DEEP_RESEARCH
                else "回答已生成，正在整理最终输出"
            ),
            "response_mode": state.response_mode,
        }

        if state.response_mode == RESPONSE_MODE_DEEP_RESEARCH:
            report = self.reporting.generate_report(state)
            final_step = step_counter
            report_tool_events = self._drain_tool_events(final_step)
            self._apply_tool_event_bindings(state.todo_items, report_tool_events)
            for event in report_tool_events:
                yield event
        else:
            report = self._build_single_task_report(state)

        state.structured_report = report
        state.running_summary = report

        note_event = self._persist_final_report(state, report)
        self._capture_profile_memory(state)
        self.memory_service.save_report_memory(state.run_id, state, report)
        if state.response_mode == RESPONSE_MODE_DEEP_RESEARCH:
            yield {
                "type": "status",
                "message": "最终报告已生成，正在沉淀语义记忆",
                "response_mode": state.response_mode,
            }
            self.memory_service.consolidate_semantic_facts(state.run_id, topic, report)
        if note_event:
            yield note_event

        yield {
            "type": "final_report",
            "report": report,
            "note_id": state.report_note_id,
            "note_path": state.report_note_path,
            "response_mode": state.response_mode,
        }
        yield {"type": "done"}

    def _create_state(self, topic: str, session_id: str | None) -> SummaryState:
        state = SummaryState(research_topic=topic)
        state.session_id = self.memory_service.get_or_create_session(session_id, topic)
        state.run_id = self.memory_service.start_run(state.session_id, topic)
        return state

    def _build_state(self, topic: str, session_id: str | None) -> SummaryState:
        state = self._create_state(topic, session_id)
        state.recalled_context = self.memory_service.load_relevant_context(
            state.session_id,
            topic,
            exclude_run_id=state.run_id,
        )
        state.response_mode = self._classify_response_mode(topic, state.recalled_context)
        return state

    def _capture_profile_memory(self, state: SummaryState) -> None:
        """Persist query-derived profile memory outside the first-response critical path."""

        if not state.run_id or not state.session_id or not state.research_topic:
            return

        self.memory_service.capture_profile_memory(
            state.run_id,
            state.session_id,
            state.research_topic,
        )

    def _prepare_tasks(
        self,
        state: SummaryState,
        *,
        stream_mode: bool = False,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        if state.response_mode == RESPONSE_MODE_MEMORY_RECALL:
            state.todo_items = [self._build_memory_recall_task(state)]
            self._initialize_round_metadata(state.todo_items, round_id=1, origin="memory")
            return events

        if state.response_mode == RESPONSE_MODE_DIRECT_ANSWER:
            state.todo_items = [self._build_direct_answer_task(state)]
            self._initialize_round_metadata(state.todo_items, round_id=1, origin="direct")
            return events

        state.todo_items = self.planner.plan_todo_list(state)
        planner_events = self._drain_tool_events(0 if stream_mode else None)
        self._apply_tool_event_bindings(state.todo_items, planner_events)
        if stream_mode:
            events.extend(planner_events)

        if not state.todo_items:
            logger.info("No TODO items generated; falling back to single task")
            state.todo_items = [self.planner.create_fallback_task(state)]
        self._initialize_round_metadata(state.todo_items, round_id=1, origin="planner")
        return events

    def _execute_task(
        self,
        state: SummaryState,
        task: TodoItem,
        *,
        emit_stream: bool,
        step: int | None = None,
    ) -> Generator[dict[str, Any], None, TaskExecutionResult]:
        if task.origin == "memory":
            return (yield from self._special_mode_executor.execute_memory_recall(
                state,
                task,
                emit_stream=emit_stream,
            ))
        if task.origin == "direct":
            return (yield from self._special_mode_executor.execute_direct_answer(
                state,
                task,
                emit_stream=emit_stream,
            ))
        return (yield from self._research_task_executor.execute(
            state,
            task,
            emit_stream=emit_stream,
            step=step,
        ))

    def _stream_task_execution(
        self,
        state: SummaryState,
        task: TodoItem,
        *,
        step: int,
    ) -> Generator[dict[str, Any], None, TaskExecutionResult]:
        result = yield from self._decorate_execution_stream(
            self._execute_task(state, task, emit_stream=True, step=step),
            task,
            step=step,
            response_mode=state.response_mode,
        )
        return result

    def _decorate_execution_stream(
        self,
        generator: Generator[dict[str, Any], None, TaskExecutionResult],
        task: TodoItem,
        *,
        step: int,
        response_mode: str,
    ) -> Generator[dict[str, Any], None, TaskExecutionResult]:
        while True:
            try:
                payload = next(generator)
            except StopIteration as stop:
                return stop.value

            decorated = dict(payload)
            if decorated.get("task_id") == task.id:
                decorated.setdefault("step", step)
                decorated["stream_token"] = task.stream_token
                if decorated.get("type") in {"task_status", "sources"}:
                    decorated["response_mode"] = response_mode
            yield decorated

    @staticmethod
    def _consume_execution(
        generator: Generator[dict[str, Any], None, TaskExecutionResult],
    ) -> TaskExecutionResult:
        while True:
            try:
                next(generator)
            except StopIteration as stop:
                return stop.value

    def _finalize_task_result(
        self,
        state: SummaryState,
        task: TodoItem,
        result: TaskExecutionResult,
    ) -> None:
        self._apply_task_patch(task, result.task_patch)
        self._apply_tool_event_bindings(state.todo_items, result.tool_events)

        if result.context_to_append:
            with self._state_lock:
                state.web_research_results.append(result.context_to_append)
                if result.sources_to_append:
                    state.sources_gathered.append(result.sources_to_append)
                state.research_loop_count += result.research_loop_increment

        self.memory_service.save_task_log(state.run_id, task)

    @staticmethod
    def _apply_task_patch(task: TodoItem, patch: TaskPatch) -> None:
        task.status = patch.status
        task.summary = patch.summary
        task.sources_summary = patch.sources_summary
        task.notices = list(patch.notices)
        task.note_id = patch.note_id
        task.note_path = patch.note_path
        task.attempt_count = patch.attempt_count
        task.search_backend = patch.search_backend
        task.evidence_count = patch.evidence_count
        task.top_score = patch.top_score
        task.needs_followup = patch.needs_followup
        task.latest_query = patch.latest_query
        task.evidence_gap_reason = patch.evidence_gap_reason
        task.planned_capabilities = list(patch.planned_capabilities)
        task.current_capability = patch.current_capability
        task.route_intent_label = patch.route_intent_label
        task.route_confidence = patch.route_confidence
        task.route_reason = patch.route_reason

    @staticmethod
    def _apply_tool_event_bindings(
        tasks: list[TodoItem],
        tool_events: list[dict[str, Any]],
    ) -> None:
        task_map = {task.id: task for task in tasks}
        for event in tool_events:
            task_id = event.get("task_id")
            note_id = event.get("note_id")
            if task_id is None or not note_id:
                continue
            task = task_map.get(int(task_id))
            if task is None:
                continue
            task.note_id = str(note_id)
            note_path = event.get("note_path")
            if isinstance(note_path, str) and note_path:
                task.note_path = note_path

    def _set_tool_event_sink(self, sink: Callable[[dict[str, Any]], None] | None) -> None:
        """Enable or disable immediate tool event callbacks."""

        self._tool_event_sink_enabled = sink is not None
        self._tool_tracker.set_event_sink(sink)

    def _drain_tool_events(self, step: int | None = None) -> list[dict[str, Any]]:
        events = self._tool_tracker.drain(step=step)
        if self._tool_event_sink_enabled:
            return []
        return events

    @property
    def _tool_call_events(self) -> list[dict[str, Any]]:
        """Expose recorded tool events for legacy integrations."""

        return self._tool_tracker.as_dicts()

    def _classify_response_mode(
        self,
        topic: str,
        recalled_context: dict[str, Any] | None,
    ) -> str:
        return self._special_mode_executor.classify_response_mode(topic, recalled_context)

    @staticmethod
    def _build_memory_recall_task(state: SummaryState) -> TodoItem:
        return TodoItem(
            id=1,
            title="会话历史回顾",
            intent="基于当前会话历史回答用户是否曾讨论过相关主题，并提炼已有结论",
            query=state.research_topic,
            origin="memory",
        )

    @staticmethod
    def _build_direct_answer_task(state: SummaryState) -> TodoItem:
        return TodoItem(
            id=1,
            title="个性化直接回答",
            intent="结合已召回的长期目标、偏好和会话上下文，给出简洁明确的回答",
            query=state.research_topic,
            origin="direct",
        )

    @staticmethod
    def _build_single_task_report(state: SummaryState) -> str:
        if not state.todo_items:
            return "暂无可用信息"
        return state.todo_items[0].summary or "暂无可用信息"

    @staticmethod
    def _response_mode_label(response_mode: str) -> str:
        if response_mode == RESPONSE_MODE_MEMORY_RECALL:
            return "会话回忆"
        if response_mode == RESPONSE_MODE_DIRECT_ANSWER:
            return "直接回答"
        return "深度研究"

    @staticmethod
    def _initial_status_message(response_mode: str) -> str:
        if response_mode == RESPONSE_MODE_MEMORY_RECALL:
            return "检测到历史回忆型问题，优先检索会话记忆"
        if response_mode == RESPONSE_MODE_DIRECT_ANSWER:
            return "检测到可直接回答的问题，正在结合历史上下文生成回答"
        return "初始化研究流程"

    @staticmethod
    def _initialize_round_metadata(
        tasks: list[TodoItem],
        *,
        round_id: int,
        origin: str,
    ) -> None:
        for task in tasks:
            task.round_id = round_id
            task.origin = origin
            if task.parent_task_id is None:
                task.parent_task_id = None

    @staticmethod
    def _pending_tasks_for_round(state: SummaryState, round_id: int) -> list[TodoItem]:
        return [
            task
            for task in state.todo_items
            if task.round_id == round_id and task.status == "pending"
        ]

    def _append_followup_tasks(
        self,
        state: SummaryState,
        followup_tasks: list[dict[str, Any]],
        *,
        round_id: int,
    ) -> list[TodoItem]:
        appended: list[TodoItem] = []
        next_id = max((task.id for task in state.todo_items), default=0) + 1

        for item in followup_tasks:
            title = str(item.get("title") or "").strip()
            intent = str(item.get("intent") or "").strip()
            query = str(item.get("query") or "").strip()
            raw_parent = item.get("parent_task_id")

            if not (title and intent and query):
                continue
            if self._is_duplicate_task(state.todo_items, title=title, query=query):
                continue

            parent_task_id: int | None = None
            if isinstance(raw_parent, int):
                parent_task_id = raw_parent
            elif isinstance(raw_parent, str) and raw_parent.isdigit():
                parent_task_id = int(raw_parent)

            task = TodoItem(
                id=next_id,
                title=title,
                intent=intent,
                query=query,
                round_id=round_id,
                origin="reviewer",
                parent_task_id=parent_task_id,
            )
            state.todo_items.append(task)
            appended.append(task)
            next_id += 1

        return appended

    @staticmethod
    def _is_duplicate_task(
        existing_tasks: list[TodoItem],
        *,
        title: str,
        query: str,
    ) -> bool:
        normalized_title = re.sub(r"\s+", "", title).lower()
        normalized_query = re.sub(r"\s+", " ", query).strip().lower()

        for task in existing_tasks:
            task_title = re.sub(r"\s+", "", task.title).lower()
            task_query = re.sub(r"\s+", " ", (task.latest_query or task.query)).strip().lower()
            if task_title == normalized_title:
                return True
            if task_query and task_query == normalized_query:
                return True

        return False

    @staticmethod
    def _serialize_task(task: TodoItem) -> dict[str, Any]:
        return {
            "id": task.id,
            "title": task.title,
            "intent": task.intent,
            "query": task.query,
            "round_id": task.round_id,
            "origin": task.origin,
            "parent_task_id": task.parent_task_id,
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
            "planned_capabilities": list(task.planned_capabilities),
            "current_capability": task.current_capability,
            "route_intent_label": task.route_intent_label,
            "route_confidence": task.route_confidence,
            "route_reason": task.route_reason,
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
            note_id = extract_note_id_from_text(response)

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
                note_id = extract_note_id_from_text(str(event.get("result") or ""))

            if note_id:
                return str(note_id)

        return None


def run_deep_research(topic: str, config: Configuration | None = None) -> SummaryStateOutput:
    """Convenience function mirroring the class-based API."""

    agent = DeepResearchAgent(config=config)
    return agent.run(topic)

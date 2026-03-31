"""Orchestrator coordinating the deep research workflow."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterator

from hello_agents import HelloAgentsLLM, ToolAwareSimpleAgent
from hello_agents.tools import ToolRegistry
from hello_agents.tools.builtin.note_tool import NoteTool

from config import Configuration
from prompts import (
    direct_answer_system_prompt,
    research_reviewer_system_prompt,
    report_writer_instructions,
    task_summarizer_instructions,
    todo_planner_system_prompt,
)
from models import SummaryState, SummaryStateOutput, TodoItem
from services.planner import PlanningService
from services.reporter import ReportingService
from services.search import dispatch_search, prepare_research_context
from services.summarizer import SummarizationService
from services.text_processing import dedupe_markdown_blocks, strip_tool_calls
from services.tool_events import ToolCallTracker
from services.memory import MemoryService
from services.reviewer import ReviewerService
from utils import strip_thinking_tokens

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
MEMORY_RECALL_PATTERNS = (
    r"还记得.*(之前|上次|以前)",
    r"(之前|上次|以前).*(问过|聊过|提过|说过)",
    r"(有没有|是否).*(问过|聊过|提过|说过)",
    r"记不记得",
)
MEMORY_TERM_EXPANSIONS = {
    "药品": {"药物", "用药", "服药", "剂量", "副作用", "禁忌", "褪黑素"},
    "药物": {"药品", "用药", "服药", "剂量", "副作用", "禁忌", "褪黑素"},
    "失眠": {"褪黑素", "睡眠", "安眠药"},
    "牙齿": {"智齿", "口腔", "冠周炎", "拔牙"},
    "牙": {"智齿", "口腔", "冠周炎", "拔牙"},
}
MEMORY_QUERY_STOPWORDS = {
    "还记得",
    "记得",
    "记不记得",
    "之前",
    "上次",
    "以前",
    "是否",
    "有没有",
    "关于",
    "事情",
    "情况",
    "问过",
    "聊过",
    "提过",
    "说过",
    "这个",
    "那个",
    "内容",
}
RESPONSE_MODE_MEMORY_RECALL = "memory_recall"
RESPONSE_MODE_DIRECT_ANSWER = "direct_answer"
RESPONSE_MODE_DEEP_RESEARCH = "deep_research"
DIRECT_ANSWER_PATTERNS = (
    r"适合吗",
    r"能不能",
    r"可以吗",
    r"可不可以",
    r"要不要",
    r"该不该",
    r"行不行",
    r"值不值得",
    r"热量(如何|怎么样|高吗|低吗)?",
    r"会不会",
    r"喝.*吗",
    r"吃.*吗",
)
DEEP_RESEARCH_PATTERNS = (
    r"分别",
    r"对比",
    r"比较",
    r"系统",
    r"全面",
    r"详细",
    r"展开",
    r"综述",
    r"研究",
    r"量化",
    r"频率",
    r"预算",
    r"策略",
    r"路线",
    r"并给出",
)


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
        self.review_agent = self._create_tool_aware_agent(
            name="研究评审专家",
            system_prompt=research_reviewer_system_prompt.strip(),
        )
        self.direct_answer_agent = self._create_tool_aware_agent(
            name="个性化直接回答专家",
            system_prompt=direct_answer_system_prompt.strip(),
            use_tools=False,
        )

        self._summarizer_factory: Callable[[], ToolAwareSimpleAgent] = lambda: self._create_tool_aware_agent(  # noqa: E501
            name="任务总结专家",
            system_prompt=task_summarizer_instructions.strip(),
        )

        self.planner = PlanningService(self.todo_agent, self.config)
        self.summarizer = SummarizationService(self._summarizer_factory, self.config)
        self.reporting = ReportingService(self.report_agent, self.config)
        self.reviewer = ReviewerService(self.review_agent, self.config)
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

    def _create_tool_aware_agent(
        self,
        *,
        name: str,
        system_prompt: str,
        use_tools: bool = True,
    ) -> ToolAwareSimpleAgent:
        """Instantiate a ToolAwareSimpleAgent sharing tool registry and tracker."""
        return ToolAwareSimpleAgent(
            name=name,
            llm=self.llm,
            system_prompt=system_prompt,
            enable_tool_calling=use_tools and self.tools_registry is not None,
            tool_registry=self.tools_registry if use_tools else None,
            tool_call_listener=self._tool_tracker.record if use_tools else None,
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
        state.run_id = self.memory_service.start_run(state.session_id, topic)
        self.memory_service.capture_profile_memory(state.run_id, state.session_id, topic)
        state.recalled_context = self.memory_service.load_relevant_context(
            state.session_id,
            topic,
            exclude_run_id=state.run_id,
        )
        state.response_mode = self._classify_response_mode(topic, state.recalled_context)

        if state.response_mode == RESPONSE_MODE_MEMORY_RECALL:
            state.todo_items = [self._build_memory_recall_task(state)]
            self._initialize_round_metadata(state.todo_items, round_id=1, origin="memory")
        elif state.response_mode == RESPONSE_MODE_DIRECT_ANSWER:
            state.todo_items = [self._build_direct_answer_task(state)]
            self._initialize_round_metadata(state.todo_items, round_id=1, origin="direct")
        else:
            #分解任务
            state.todo_items = self.planner.plan_todo_list(state)
            self._drain_tool_events(state)

            if not state.todo_items:
                logger.info("No TODO items generated; falling back to single task")
                state.todo_items = [self.planner.create_fallback_task(state)]
            self._initialize_round_metadata(state.todo_items, round_id=1, origin="planner")

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
                for _ in self._execute_task(state, task, emit_stream=False):
                    pass

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
            self._drain_tool_events(state)
        else:
            report = self._build_single_task_report(state)
        state.structured_report = report
        state.running_summary = report
        self._persist_final_report(state, report)
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
        state = SummaryState(research_topic=topic)
        state.session_id = self.memory_service.get_or_create_session(session_id, topic)
        state.run_id = self.memory_service.start_run(state.session_id, topic)
        self.memory_service.capture_profile_memory(state.run_id, state.session_id, topic)
        state.recalled_context = self.memory_service.load_relevant_context(
            state.session_id,
            topic,
            exclude_run_id=state.run_id,
        )
        state.response_mode = self._classify_response_mode(topic, state.recalled_context)
        yield {
            "type": "session",
            "session_id": state.session_id,
            "run_id": state.run_id,
            "response_mode": state.response_mode,
        }
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

        if state.response_mode == RESPONSE_MODE_MEMORY_RECALL:
            state.todo_items = [self._build_memory_recall_task(state)]
            self._initialize_round_metadata(state.todo_items, round_id=1, origin="memory")
        elif state.response_mode == RESPONSE_MODE_DIRECT_ANSWER:
            state.todo_items = [self._build_direct_answer_task(state)]
            self._initialize_round_metadata(state.todo_items, round_id=1, origin="direct")
        else:
            state.todo_items = self.planner.plan_todo_list(state)
            for event in self._drain_tool_events(state, step=0):
                yield event
            if not state.todo_items:
                state.todo_items = [self.planner.create_fallback_task(state)]
            self._initialize_round_metadata(state.todo_items, round_id=1, origin="planner")

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
                        "response_mode": state.response_mode,
                    }

                try:
                    for event in self._execute_task(state, task, emit_stream=True, step=step):
                        payload = dict(event)
                        if payload.get("task_id") == task.id:
                            payload.setdefault("step", step)
                            payload["stream_token"] = task.stream_token
                        yield payload
                except Exception as exc:  # pragma: no cover - defensive guardrail
                    logger.exception("Task execution failed", exc_info=exc)
                    task.status = "failed"
                    self.memory_service.save_task_memory(state.run_id, task)
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
            for event in self._drain_tool_events(state, step=final_step):
                yield event
        else:
            report = self._build_single_task_report(state)
        state.structured_report = report
        state.running_summary = report

        note_event = self._persist_final_report(state, report)
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
        if task.origin == "memory":
            for event in self._execute_memory_recall_task(
                state,
                task,
                emit_stream=emit_stream,
                step=step,
            ):
                yield event
            return
        if task.origin == "direct":
            for event in self._execute_direct_answer_task(
                state,
                task,
                emit_stream=emit_stream,
                step=step,
            ):
                yield event
            return
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
                    "response_mode": state.response_mode,
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
                "response_mode": state.response_mode,
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
                "response_mode": state.response_mode,
                **self._summarize_search_result(search_result),
            }
        else:
            self._drain_tool_events(state)

    def _execute_memory_recall_task(
        self,
        state: SummaryState,
        task: TodoItem,
        *,
        emit_stream: bool,
        step: int | None,
    ) -> Iterator[dict[str, Any]]:
        """Answer session-history questions directly from recalled memory."""

        task.latest_query = task.query
        task.search_backend = "memory"
        task.attempt_count = 1
        task.needs_followup = False
        task.evidence_gap_reason = None

        summary, sources_summary, evidence_count = self._build_memory_recall_answer(
            state,
            task.query,
        )
        task.summary = summary
        task.sources_summary = sources_summary
        task.evidence_count = evidence_count
        task.top_score = 1.0 if evidence_count else 0.0
        task.notices = ["本任务直接基于当前会话历史与语义记忆生成，未进行联网搜索。"]
        task.status = "completed"

        self.memory_service.save_task_memory(state.run_id, task)
        with self._state_lock:
            state.web_research_results.append(summary)
            state.sources_gathered.append(sources_summary)
            state.research_loop_count += 1

        if emit_stream:
            yield {
                "type": "sources",
                "task_id": task.id,
                "latest_sources": sources_summary,
                "raw_context": summary,
                "step": step,
                "backend": "memory",
                "attempt_count": task.attempt_count,
                "evidence_count": task.evidence_count,
                "top_score": task.top_score,
                "needs_followup": task.needs_followup,
                "latest_query": task.latest_query,
                "evidence_gap_reason": task.evidence_gap_reason,
                "note_id": task.note_id,
                "note_path": task.note_path,
                "response_mode": state.response_mode,
            }
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
                "response_mode": state.response_mode,
            }

    def _looks_like_memory_recall_query(self, topic: str) -> bool:
        """Detect user questions asking about prior conversation history."""

        normalized = (topic or "").strip().lower()
        if not normalized:
            return False
        return any(re.search(pattern, normalized) for pattern in MEMORY_RECALL_PATTERNS)

    def _classify_response_mode(
        self,
        topic: str,
        recalled_context: dict[str, Any] | None,
    ) -> str:
        """Route the current query to memory recall, direct answer, or deep research."""

        if self._looks_like_memory_recall_query(topic):
            return RESPONSE_MODE_MEMORY_RECALL
        if self._looks_like_direct_answer_query(topic, recalled_context):
            return RESPONSE_MODE_DIRECT_ANSWER
        return RESPONSE_MODE_DEEP_RESEARCH

    def _looks_like_direct_answer_query(
        self,
        topic: str,
        recalled_context: dict[str, Any] | None,
    ) -> bool:
        """Detect short, personal, decision-oriented questions suitable for direct answers."""

        normalized = re.sub(r"\s+", " ", (topic or "").strip().lower())
        if not normalized:
            return False
        if len(normalized) > 80:
            return False
        if sum(normalized.count(token) for token in ("，", "、", ";", "；")) > 1:
            return False
        if any(re.search(pattern, normalized) for pattern in DEEP_RESEARCH_PATTERNS):
            return False
        if not any(re.search(pattern, normalized) for pattern in DIRECT_ANSWER_PATTERNS):
            has_personal_frame = any(token in normalized for token in ("我", "现在", "今天", "最近"))
            has_decision_or_advice = any(
                token in normalized
                for token in ("适合", "能不能", "可以", "要不要", "该不该", "热量", "高吗", "低吗")
            )
            if not (has_personal_frame and has_decision_or_advice):
                return False

        if not recalled_context:
            return False
        return bool(
            (recalled_context.get("profile_facts") or [])
            or (recalled_context.get("session_facts") or recalled_context.get("semantic_facts") or [])
            or (recalled_context.get("session_runs") or [])
            or (recalled_context.get("recent_tasks") or [])
        )

    def _has_recallable_history(self, recalled_context: dict[str, Any] | None) -> bool:
        """Return whether current session has enough history to answer from memory."""

        if not recalled_context:
            return False
        return bool(
            (recalled_context.get("session_runs") or [])
            or (recalled_context.get("recent_tasks") or [])
            or (recalled_context.get("session_facts") or recalled_context.get("semantic_facts") or [])
        )

    def _build_memory_recall_task(self, state: SummaryState) -> TodoItem:
        """Create a synthetic task that answers directly from session memory."""

        return TodoItem(
            id=1,
            title="会话历史回顾",
            intent="基于当前会话历史回答用户是否曾讨论过相关主题，并提炼已有结论",
            query=state.research_topic,
            origin="memory",
        )

    def _build_direct_answer_task(self, state: SummaryState) -> TodoItem:
        """Create a synthetic task for short, context-aware direct answers."""

        return TodoItem(
            id=1,
            title="个性化直接回答",
            intent="结合当前问题与已召回的长期目标、偏好和会话上下文，给出简洁明确的回答",
            query=state.research_topic,
            origin="direct",
        )

    def _build_memory_recall_answer(
        self,
        state: SummaryState,
        query: str,
    ) -> tuple[str, str, int]:
        """Summarize relevant session history without hitting external search."""

        recalled = state.recalled_context or {}
        session_runs = [
            run
            for run in (recalled.get("session_runs") or [])
            if str(run.get("topic") or "").strip() != query.strip()
        ]
        recent_tasks = list(recalled.get("recent_tasks") or [])
        semantic_facts = list(recalled.get("session_facts") or recalled.get("semantic_facts") or [])
        terms = self._extract_memory_terms(query)

        task_summaries_by_run: dict[str, list[str]] = {}
        for item in recent_tasks:
            run_id = str(item.get("run_id") or "").strip()
            summary = str(item.get("summary") or "").strip()
            title = str(item.get("title") or "").strip()
            if not run_id:
                continue
            task_summaries_by_run.setdefault(run_id, [])
            if title:
                task_summaries_by_run[run_id].append(title)
            if summary:
                task_summaries_by_run[run_id].append(summary[:300])

        semantic_run_ids = [
            str(item.get("run_id") or "").strip()
            for item in semantic_facts
            if str(item.get("run_id") or "").strip()
        ]

        scored_runs: list[tuple[int, dict[str, Any]]] = []
        for run in session_runs:
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                continue
            text = " ".join(
                [
                    str(run.get("topic") or ""),
                    str(run.get("report_excerpt") or ""),
                    " ".join(task_summaries_by_run.get(run_id, [])),
                ]
            )
            score = sum(1 for term in terms if term and term in text)
            if run_id in semantic_run_ids:
                score += 2
            scored_runs.append((score, run))

        relevant_runs = [
            run for score, run in sorted(scored_runs, key=lambda item: item[0], reverse=True) if score > 0
        ]
        if not relevant_runs:
            relevant_runs = session_runs[:3]

        relevant_run_ids = {
            str(run.get("run_id") or "").strip()
            for run in relevant_runs
            if str(run.get("run_id") or "").strip()
        }
        relevant_facts = [
            item
            for item in semantic_facts
            if not relevant_run_ids
            or str(item.get("run_id") or "").strip() in relevant_run_ids
        ]

        matched_topic_lines: list[str] = []
        for idx, run in enumerate(relevant_runs[:3], start=1):
            topic = str(run.get("topic") or "未知主题").strip()
            finished_at = str(run.get("finished_at") or "").strip()
            matched_topic_lines.append(
                f"{idx}. {topic}" + (f"（完成于 {finished_at[:10]}）" if finished_at else "")
            )

        fact_lines: list[str] = []
        seen_facts: set[str] = set()
        for item in relevant_facts[:5]:
            fact = str(item.get("fact") or "").strip()
            if not fact or fact in seen_facts:
                continue
            seen_facts.add(fact)
            fact_lines.append(f"- {fact}")

        if matched_topic_lines:
            summary_lines = [
                "# 会话历史回顾",
                "",
                "## 结论",
                "是的，在当前会话里我找到了与这次问题相关的历史研究记录。",
                "",
                "## 我能回忆到的相关主题",
                *matched_topic_lines,
            ]
            if fact_lines:
                summary_lines.extend(
                    [
                        "",
                        "## 已沉淀的关键结论",
                        *fact_lines,
                    ]
                )
            summary_lines.extend(
                [
                    "",
                    "## 说明",
                    "这次回答直接基于当前会话中的历史研究记录与语义记忆生成，未额外联网搜索。",
                ]
            )
        else:
            summary_lines = [
                "# 会话历史回顾",
                "",
                "## 结论",
                "当前会话中没有找到足够相关的历史研究记录来直接回答这个回忆型问题。",
                "",
                "## 说明",
                "如果你愿意，可以直接指出你想回顾的具体主题，我再基于已有会话内容为你整理。",
            ]

        source_lines: list[str] = []
        for idx, run in enumerate(relevant_runs[:3], start=1):
            source_lines.extend(
                [
                    f"Source: 会话历史 {idx}",
                    f"信息内容: 主题：{str(run.get('topic') or '').strip()}",
                ]
            )
        for idx, item in enumerate(relevant_facts[:3], start=1):
            fact = str(item.get("fact") or "").strip()
            if not fact:
                continue
            source_lines.extend(
                [
                    f"Source: 语义记忆 {idx}",
                    f"信息内容: {fact}",
                ]
            )

        summary = "\n".join(summary_lines).strip()
        sources_summary = "\n".join(source_lines).strip()
        evidence_count = len(relevant_runs[:3]) + len(relevant_facts[:5])
        return summary, sources_summary, evidence_count

    def _execute_direct_answer_task(
        self,
        state: SummaryState,
        task: TodoItem,
        *,
        emit_stream: bool,
        step: int | None,
    ) -> Iterator[dict[str, Any]]:
        """Answer short personal questions directly from recalled context."""

        task.latest_query = task.query
        task.search_backend = "direct_answer"
        task.attempt_count = 1
        task.needs_followup = False
        task.evidence_gap_reason = None

        summary, sources_summary, evidence_count = self._build_direct_answer_output(
            state,
            task.query,
        )
        task.summary = summary
        task.sources_summary = sources_summary
        task.evidence_count = evidence_count
        task.top_score = 1.0 if evidence_count else 0.0
        task.notices = ["本任务直接基于已召回的长期目标、偏好与会话上下文生成，未进行联网搜索。"]
        task.status = "completed"

        self.memory_service.save_task_memory(state.run_id, task)
        with self._state_lock:
            state.web_research_results.append(summary)
            state.sources_gathered.append(sources_summary)
            state.research_loop_count += 1

        if emit_stream:
            yield {
                "type": "sources",
                "task_id": task.id,
                "latest_sources": sources_summary,
                "raw_context": summary,
                "step": step,
                "backend": task.search_backend,
                "attempt_count": task.attempt_count,
                "evidence_count": task.evidence_count,
                "top_score": task.top_score,
                "needs_followup": task.needs_followup,
                "latest_query": task.latest_query,
                "evidence_gap_reason": task.evidence_gap_reason,
                "note_id": task.note_id,
                "note_path": task.note_path,
                "response_mode": state.response_mode,
            }
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
                "response_mode": state.response_mode,
            }

    def _build_direct_answer_output(
        self,
        state: SummaryState,
        query: str,
    ) -> tuple[str, str, int]:
        """Generate a concise answer grounded in recalled context."""

        prompt = self._build_direct_answer_prompt(state, query)
        try:
            response = self.direct_answer_agent.run(prompt)
        finally:
            self.direct_answer_agent.clear_history()

        answer_text = response.strip()
        if self.config.strip_thinking_tokens:
            answer_text = strip_thinking_tokens(answer_text)
        answer_text = strip_tool_calls(answer_text).strip()
        answer_text = dedupe_markdown_blocks(answer_text)

        if not answer_text:
            answer_text = (
                "基于当前召回到的上下文，我暂时无法给出足够可靠的直接判断。\n\n"
                "- 你可以补充品牌、杯型、糖度或你当前所处的目标阶段，我再给你更具体的建议。"
            )

        sources_summary, evidence_count = self._build_direct_answer_sources(state)
        return answer_text, sources_summary, evidence_count

    def _build_direct_answer_prompt(self, state: SummaryState, query: str) -> str:
        """Build the input for the direct-answer agent."""

        recalled = state.recalled_context or {}

        def format_lines(items: list[Any], formatter: Callable[[Any], str], fallback: str) -> str:
            lines = [formatter(item) for item in items if formatter(item)]
            return "\n".join(lines[:5]) if lines else fallback

        profile_block = format_lines(
            list(recalled.get("profile_facts") or []),
            lambda item: f"- {str(item.get('fact') or '').strip()}",
            "- 暂无命中的长期目标/偏好/约束",
        )
        session_fact_block = format_lines(
            list(recalled.get("session_facts") or recalled.get("semantic_facts") or []),
            lambda item: f"- {str(item.get('fact') or '').strip()}",
            "- 暂无当前会话语义结论",
        )
        recent_task_block = format_lines(
            list(recalled.get("recent_tasks") or []),
            lambda item: (
                f"- {str(item.get('title') or '').strip()}："
                f"{str(item.get('summary') or '').strip()[:160]}"
            ),
            "- 暂无相关历史任务",
        )
        session_run_block = format_lines(
            list(recalled.get("session_runs") or []),
            lambda item: (
                f"- {str(item.get('topic') or '').strip()}："
                f"{str(item.get('report_excerpt') or '').strip()[:180]}"
            ),
            "- 暂无相关历史研究",
        )

        has_context = any(
            recalled.get(key)
            for key in ("profile_facts", "session_facts", "semantic_facts", "recent_tasks", "session_runs")
        )

        return (
            f"当前问题：{query}\n"
            f"是否命中历史上下文：{'是' if has_context else '否'}\n\n"
            "请直接回答用户，不要把自己写成研究报告。\n\n"
            f"长期目标/偏好/约束：\n{profile_block}\n\n"
            f"当前会话语义记忆：\n{session_fact_block}\n\n"
            f"最近相关任务：\n{recent_task_block}\n\n"
            f"最近相关研究：\n{session_run_block}\n"
        )

    def _build_direct_answer_sources(self, state: SummaryState) -> tuple[str, int]:
        """Summarize which recalled memory blocks informed the direct answer."""

        recalled = state.recalled_context or {}
        source_lines: list[str] = []
        evidence_count = 0

        for idx, item in enumerate(recalled.get("profile_facts") or [], start=1):
            fact = str(item.get("fact") or "").strip()
            if not fact:
                continue
            source_lines.extend(
                [
                    f"Source: Profile Memory {idx}",
                    f"信息内容: {fact}",
                ]
            )
            evidence_count += 1

        for idx, item in enumerate(recalled.get("session_facts") or recalled.get("semantic_facts") or [], start=1):
            fact = str(item.get("fact") or "").strip()
            if not fact:
                continue
            source_lines.extend(
                [
                    f"Source: Session Semantic {idx}",
                    f"信息内容: {fact}",
                ]
            )
            evidence_count += 1

        for idx, item in enumerate(recalled.get("recent_tasks") or [], start=1):
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if not (title or summary):
                continue
            source_lines.extend(
                [
                    f"Source: Recent Task {idx}",
                    f"信息内容: {title} {summary[:180]}".strip(),
                ]
            )
            evidence_count += 1

        for idx, item in enumerate(recalled.get("session_runs") or [], start=1):
            topic = str(item.get("topic") or "").strip()
            excerpt = str(item.get("report_excerpt") or "").strip()
            if not (topic or excerpt):
                continue
            source_lines.extend(
                [
                    f"Source: Session Run {idx}",
                    f"信息内容: {topic} {excerpt[:180]}".strip(),
                ]
            )
            evidence_count += 1

        return "\n".join(source_lines).strip(), evidence_count

    def _build_single_task_report(self, state: SummaryState) -> str:
        """Reuse the single synthetic task as the final output for non-research modes."""

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

    def _initial_status_message(self, response_mode: str) -> str:
        if response_mode == RESPONSE_MODE_MEMORY_RECALL:
            return "检测到历史回忆型问题，优先检索会话记忆"
        if response_mode == RESPONSE_MODE_DIRECT_ANSWER:
            return "检测到可直接回答的问题，正在结合历史上下文生成个性化短答"
        return "初始化研究流程"

    def _extract_memory_terms(self, query: str) -> set[str]:
        """Extract coarse-grained semantic terms for memory matching."""

        raw_terms = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9_-]{1,}", query or "")
        terms: set[str] = set()
        for term in raw_terms:
            normalized = term.strip().lower()
            if not normalized or normalized in MEMORY_QUERY_STOPWORDS:
                continue
            terms.add(normalized)
            for extra in MEMORY_TERM_EXPANSIONS.get(normalized, set()):
                terms.add(extra.lower())
        return terms

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

    def _initialize_round_metadata(
        self,
        tasks: list[TodoItem],
        *,
        round_id: int,
        origin: str,
    ) -> None:
        """Initialize round metadata for a batch of tasks."""

        for task in tasks:
            task.round_id = round_id
            task.origin = origin
            if task.parent_task_id is None:
                task.parent_task_id = None

    def _pending_tasks_for_round(self, state: SummaryState, round_id: int) -> list[TodoItem]:
        """Return the tasks still pending for a given research round."""

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
        """Append reviewer-proposed tasks while avoiding exact duplicates."""

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

    def _is_duplicate_task(
        self,
        existing_tasks: list[TodoItem],
        *,
        title: str,
        query: str,
    ) -> bool:
        """Check whether a follow-up task duplicates an existing task."""

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

    def _serialize_task(self, task: TodoItem) -> dict[str, Any]:
        """Convert task dataclass to serializable dict for frontend."""
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

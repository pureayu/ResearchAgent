"""Executors for memory-recall and direct-answer response modes."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime
from typing import Any

from agent_runtime.interfaces import AgentLike
from config import Configuration
from execution.models import ExecutionEvent, TaskExecutionResult, TaskPatch
from models import SummaryState, TodoItem
from services.text_processing import dedupe_markdown_blocks, strip_tool_calls
from utils import strip_thinking_tokens

RESPONSE_MODE_MEMORY_RECALL = "memory_recall"
RESPONSE_MODE_DIRECT_ANSWER = "direct_answer"
RESPONSE_MODE_DEEP_RESEARCH = "deep_research"

MODE_CLASSIFIER_CONFIDENCE_THRESHOLD = 0.55
MEMORY_RECALL_RUN_LIMIT = 3
MEMORY_RECALL_TASK_LOG_LIMIT = 5
MEMORY_RECALL_FACT_LIMIT = 5
MEMORY_RECALL_PROFILE_LIMIT = 3
RESEARCH_INTENT_KEYWORDS = (
    "方向",
    "现状",
    "趋势",
    "综述",
    "梳理",
    "研究",
    "对比",
    "比较",
    "系统",
    "全面",
    "全景",
    "路径",
    "进展",
    "哪些方向",
    "benchmark",
    "survey",
    "overview",
    "landscape",
    "roadmap",
    "state of the art",
    "sota",
)


class SpecialModeExecutor:
    """Handle non-deep-research response modes."""

    def __init__(
        self,
        config: Configuration,
        direct_answer_agent: AgentLike,
        response_mode_classifier_agent: AgentLike,
        memory_recall_selector_agent: AgentLike,
        task_log_loader: Callable[..., list[dict[str, Any]]] | None = None,
    ) -> None:
        self._config = config
        self._direct_answer_agent = direct_answer_agent
        self._response_mode_classifier_agent = response_mode_classifier_agent
        self._memory_recall_selector_agent = memory_recall_selector_agent
        self._task_log_loader = task_log_loader or self._default_task_log_loader

    def execute_memory_recall(
        self,
        state: SummaryState,
        task: TodoItem,
        *,
        emit_stream: bool,
    ) -> Iterator[dict[str, Any]]:
        """Answer session-history questions directly from recalled memory."""

        runtime_task = replace(task)
        local_events: list[ExecutionEvent] = []

        def emit(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
            local_events.append(ExecutionEvent(payload=dict(payload)))
            if emit_stream:
                yield payload

        runtime_task.latest_query = runtime_task.query
        runtime_task.search_backend = "memory"
        runtime_task.attempt_count = 1
        runtime_task.needs_followup = False
        runtime_task.evidence_gap_reason = None

        recalled_context = self._build_memory_recall_context(state)
        summary, sources_summary, evidence_count = self._build_memory_recall_answer(
            state,
            runtime_task.query,
            recalled_context=recalled_context,
        )
        runtime_task.summary = summary
        runtime_task.sources_summary = sources_summary
        runtime_task.evidence_count = evidence_count
        runtime_task.top_score = 1.0 if evidence_count else 0.0
        runtime_task.notices = ["本任务直接基于当前会话历史与语义记忆生成，未进行联网搜索。"]
        runtime_task.status = "completed"

        yield from emit(
            {
                "type": "sources",
                "task_id": runtime_task.id,
                "latest_sources": sources_summary,
                "raw_context": summary,
                "backend": "memory",
                "attempt_count": runtime_task.attempt_count,
                "evidence_count": runtime_task.evidence_count,
                "top_score": runtime_task.top_score,
                "needs_followup": runtime_task.needs_followup,
                "latest_query": runtime_task.latest_query,
                "evidence_gap_reason": runtime_task.evidence_gap_reason,
                "note_id": runtime_task.note_id,
                "note_path": runtime_task.note_path,
            }
        )
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
            }
        )

        return TaskExecutionResult(
            status=runtime_task.status,
            task_patch=TaskPatch.from_task(runtime_task),
            events=local_events,
            followup_triggered=False,
            context_to_append=summary,
            sources_to_append=sources_summary,
            research_loop_increment=1,
        )

    def execute_direct_answer(
        self,
        state: SummaryState,
        task: TodoItem,
        *,
        emit_stream: bool,
    ) -> Iterator[dict[str, Any]]:
        """Answer short questions from recalled context."""

        runtime_task = replace(task)
        local_events: list[ExecutionEvent] = []

        def emit(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
            local_events.append(ExecutionEvent(payload=dict(payload)))
            if emit_stream:
                yield payload

        runtime_task.latest_query = runtime_task.query
        runtime_task.attempt_count = 1
        runtime_task.needs_followup = False
        runtime_task.evidence_gap_reason = None

        runtime_task.search_backend = "memory"
        runtime_task.notices = [
            "本任务基于已召回的长期目标、偏好和会话上下文生成回答，未触发外部检索。"
        ]

        summary, sources_summary, evidence_count = self._build_direct_answer_output(
            state,
            runtime_task.query,
        )
        runtime_task.summary = summary
        runtime_task.sources_summary = sources_summary
        runtime_task.evidence_count = evidence_count
        runtime_task.top_score = 1.0 if runtime_task.evidence_count else 0.0
        runtime_task.status = "completed"

        yield from emit(
            {
                "type": "sources",
                "task_id": runtime_task.id,
                "latest_sources": runtime_task.sources_summary,
                "raw_context": summary,
                "backend": runtime_task.search_backend,
                "attempt_count": runtime_task.attempt_count,
                "evidence_count": runtime_task.evidence_count,
                "top_score": runtime_task.top_score,
                "needs_followup": runtime_task.needs_followup,
                "latest_query": runtime_task.latest_query,
                "evidence_gap_reason": runtime_task.evidence_gap_reason,
                "note_id": runtime_task.note_id,
                "note_path": runtime_task.note_path,
            }
        )
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
            }
        )

        return TaskExecutionResult(
            status=runtime_task.status,
            task_patch=TaskPatch.from_task(runtime_task),
            events=local_events,
            followup_triggered=False,
            context_to_append=summary,
            sources_to_append=sources_summary,
            research_loop_increment=1,
        )

    def classify_response_mode(
        self,
        topic: str,
        recalled_context: dict[str, Any] | None,
    ) -> str:
        """Classify the best response mode with model-first routing."""

        payload = self._run_json_agent(
            self._response_mode_classifier_agent,
            self._build_response_mode_classifier_input(topic, recalled_context),
        )
        selection = self._parse_mode_selection(payload)
        if selection is None:
            return RESPONSE_MODE_DEEP_RESEARCH

        response_mode = selection["response_mode"]
        if selection["confidence"] < MODE_CLASSIFIER_CONFIDENCE_THRESHOLD:
            return RESPONSE_MODE_DEEP_RESEARCH
        if (
            response_mode == RESPONSE_MODE_DIRECT_ANSWER
            and self._is_research_intent_topic(topic)
        ):
            return RESPONSE_MODE_DEEP_RESEARCH
        if (
            response_mode == RESPONSE_MODE_DIRECT_ANSWER
            and not self._has_direct_answer_context(recalled_context)
        ):
            return RESPONSE_MODE_DEEP_RESEARCH
        return response_mode

    @staticmethod
    def has_recallable_history(recalled_context: dict[str, Any] | None) -> bool:
        """Return whether current context has enough memory for recall responses."""

        if not recalled_context:
            return False
        return bool(
            (recalled_context.get("session_runs") or [])
            or str(recalled_context.get("working_memory_summary") or "").strip()
            or (recalled_context.get("recent_turns") or [])
            or (recalled_context.get("profile_facts") or [])
            or (recalled_context.get("global_facts") or [])
        )

    def _build_memory_recall_answer(
        self,
        state: SummaryState,
        query: str,
        *,
        recalled_context: dict[str, Any] | None = None,
    ) -> tuple[str, str, int]:
        """Summarize relevant session/profile memory without external search."""

        selected = self._select_memory_recall_items(
            query,
            recalled_context or state.recalled_context or {},
        )
        session_runs = selected["session_runs"]
        task_logs = selected["task_logs"]
        working_memory_summary = selected["working_memory_summary"]
        recent_turns = selected["recent_turns"]
        profile_facts = selected["profile_facts"]

        if not (session_runs or task_logs or working_memory_summary or recent_turns or profile_facts):
            summary = "\n".join(
                [
                    "# 会话历史回顾",
                    "",
                    "## 结论",
                    "当前没有找到足够相关的历史研究记录或用户记忆来直接回答这个回忆型问题。",
                    "",
                    "## 说明",
                    "如果你愿意，可以直接指出你想回顾的具体主题、时间点或偏好，我再基于已有会话内容为你整理。",
                ]
            ).strip()
            return summary, "", 0

        run_lines: list[str] = []
        for idx, run in enumerate(session_runs[:MEMORY_RECALL_RUN_LIMIT], start=1):
            topic = str(run.get("topic") or "未知主题").strip()
            finished_at = str(run.get("finished_at") or "").strip()
            run_lines.append(
                f"{idx}. {topic}" + (f"（完成于 {finished_at[:10]}）" if finished_at else "")
            )

        task_log_lines: list[str] = []
        for idx, item in enumerate(task_logs[:MEMORY_RECALL_TASK_LOG_LIMIT], start=1):
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if not (title or summary):
                continue
            line = f"{idx}. {title or '任务记录'}"
            if summary:
                line += f"：{summary[:180]}"
            task_log_lines.append(line)

        recent_turn_lines: list[str] = []
        for idx, turn in enumerate(recent_turns[:MEMORY_RECALL_RUN_LIMIT], start=1):
            user_query = str(turn.get("user_query") or "").strip()
            assistant_response = str(turn.get("assistant_response") or "").strip()
            if not (user_query or assistant_response):
                continue
            recent_turn_lines.append(
                f"- 第 {idx} 轮：用户问“{user_query[:80]}”；最终回答“{assistant_response[:120]}”"
            )
        profile_fact_lines = self._build_fact_lines(
            profile_facts[:MEMORY_RECALL_PROFILE_LIMIT],
            prefix="- ",
        )

        has_session_history = bool(run_lines or task_log_lines or working_memory_summary or recent_turn_lines)
        has_profile_memory = bool(profile_fact_lines)
        if has_session_history and has_profile_memory:
            conclusion = "是的，我找到了与你这次问题相关的历史研究记录，也命中了你之前透露过的长期偏好/目标。"
        elif has_session_history:
            conclusion = "是的，在当前会话里我找到了与你这次问题相关的历史研究记录。"
        else:
            conclusion = "是的，我记得你之前提到过与这个问题相关的长期偏好、目标或约束。"

        summary_lines = [
            "# 会话历史回顾",
            "",
            "## 结论",
            conclusion,
        ]
        if run_lines:
            summary_lines.extend(["", "## 相关历史研究", *run_lines])
        if task_log_lines:
            summary_lines.extend(["", "## 相关任务记录", *task_log_lines])
        if working_memory_summary:
            summary_lines.extend(["", "## 会话工作记忆摘要", working_memory_summary])
        if recent_turn_lines:
            summary_lines.extend(["", "## 最近几轮对话", *recent_turn_lines])
        if profile_fact_lines:
            summary_lines.extend(["", "## 我记住的长期偏好/目标", *profile_fact_lines])
        summary_lines.extend(
            [
                "",
                "## 说明",
                "这次回答直接基于当前会话中的历史研究记录与用户记忆生成，未额外联网搜索。",
            ]
        )

        source_lines: list[str] = []
        for idx, run in enumerate(session_runs[:MEMORY_RECALL_RUN_LIMIT], start=1):
            source_lines.extend(
                [
                    f"Source: 会话历史 {idx}",
                    f"信息内容: 主题：{str(run.get('topic') or '').strip()}",
                ]
            )
        for idx, item in enumerate(task_logs[:MEMORY_RECALL_TASK_LOG_LIMIT], start=1):
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if not (title or summary):
                continue
            source_lines.extend(
                [
                    f"Source: 任务记录 {idx}",
                    f"信息内容: {(title + ' ' + summary[:180]).strip()}",
                ]
            )
        if working_memory_summary:
            source_lines.extend(
                [
                    "Source: Working Memory Summary",
                    f"信息内容: {working_memory_summary[:240]}",
                ]
            )
        for idx, turn in enumerate(recent_turns[:MEMORY_RECALL_RUN_LIMIT], start=1):
            user_query = str(turn.get("user_query") or "").strip()
            assistant_response = str(turn.get("assistant_response") or "").strip()
            if not (user_query or assistant_response):
                continue
            source_lines.extend(
                [
                    f"Source: Recent Turn {idx}",
                    f"信息内容: 问题：{user_query[:100]}；回答：{assistant_response[:140]}",
                ]
            )
        for idx, item in enumerate(profile_facts[:MEMORY_RECALL_PROFILE_LIMIT], start=1):
            fact = str(item.get("fact") or "").strip()
            if not fact:
                continue
            source_lines.extend(
                [
                    f"Source: 用户画像记忆 {idx}",
                    f"信息内容: {fact}",
                ]
            )

        evidence_count = (
            len(session_runs[:MEMORY_RECALL_RUN_LIMIT])
            + len(task_log_lines)
            + (1 if working_memory_summary else 0)
            + len(recent_turn_lines)
            + len(profile_fact_lines)
        )
        return "\n".join(summary_lines).strip(), "\n".join(source_lines).strip(), evidence_count

    def _select_memory_recall_items(
        self,
        query: str,
        recalled_context: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        """Select memory-recall materials with LLM selection and recency fallback."""

        session_runs = list(recalled_context.get("session_runs") or [])
        task_logs = list(recalled_context.get("task_logs") or [])
        working_memory_summary = str(recalled_context.get("working_memory_summary") or "").strip()
        recent_turns = list(recalled_context.get("recent_turns") or [])
        profile_facts = list(recalled_context.get("profile_facts") or [])

        if not (session_runs or task_logs or working_memory_summary or recent_turns or profile_facts):
            return {
                "session_runs": [],
                "task_logs": [],
                "working_memory_summary": "",
                "recent_turns": [],
                "profile_facts": [],
            }

        payload = self._run_json_agent(
            self._memory_recall_selector_agent,
            self._build_memory_recall_selector_input(
                query,
                session_runs=session_runs,
                task_logs=task_logs,
                working_memory_summary=working_memory_summary,
                recent_turns=recent_turns,
                profile_facts=profile_facts,
            ),
        )
        selection = self._parse_memory_selection(payload)
        if selection is None or not any(selection.values()):
            return self._memory_recall_fallback(
                session_runs=session_runs,
                task_logs=task_logs,
                working_memory_summary=working_memory_summary,
                recent_turns=recent_turns,
                profile_facts=profile_facts,
            )

        selected_runs = self._select_items_by_ids(
            session_runs,
            selection["run_ids"],
            key="run_id",
            limit=MEMORY_RECALL_RUN_LIMIT,
        )
        selected_tasks = self._select_items_by_ids(
            task_logs,
            selection["task_ids"],
            key="task_id",
            limit=MEMORY_RECALL_TASK_LOG_LIMIT,
        )
        selected_profile_facts = self._select_items_by_ids(
            profile_facts,
            selection["fact_ids"],
            key="fact_id",
            limit=MEMORY_RECALL_PROFILE_LIMIT,
        )

        related_run_ids = {
            str(item.get("run_id") or "").strip()
            for item in selected_tasks
            if str(item.get("run_id") or "").strip()
        }
        selected_run_ids = {
            str(item.get("run_id") or "").strip()
            for item in selected_runs
            if str(item.get("run_id") or "").strip()
        }
        missing_run_ids = related_run_ids - selected_run_ids
        if missing_run_ids:
            for run in session_runs:
                run_id = str(run.get("run_id") or "").strip()
                if run_id and run_id in missing_run_ids:
                    selected_runs.append(run)
                if len(selected_runs) >= MEMORY_RECALL_RUN_LIMIT:
                    break

        if not (selected_runs or selected_tasks or working_memory_summary or recent_turns or selected_profile_facts):
            return self._memory_recall_fallback(
                session_runs=session_runs,
                task_logs=task_logs,
                working_memory_summary=working_memory_summary,
                recent_turns=recent_turns,
                profile_facts=profile_facts,
            )

        return {
            "session_runs": selected_runs[:MEMORY_RECALL_RUN_LIMIT],
            "task_logs": selected_tasks[:MEMORY_RECALL_TASK_LOG_LIMIT],
            "working_memory_summary": working_memory_summary,
            "recent_turns": recent_turns[:MEMORY_RECALL_RUN_LIMIT],
            "profile_facts": selected_profile_facts[:MEMORY_RECALL_PROFILE_LIMIT],
        }

    def _memory_recall_fallback(
        self,
        *,
        session_runs: list[dict[str, Any]],
        task_logs: list[dict[str, Any]],
        working_memory_summary: str,
        recent_turns: list[dict[str, Any]],
        profile_facts: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Fallback to simple recency when selector output is unusable."""

        selected_runs = session_runs[:MEMORY_RECALL_RUN_LIMIT]
        selected_run_ids = {
            str(item.get("run_id") or "").strip()
            for item in selected_runs
            if str(item.get("run_id") or "").strip()
        }
        selected_tasks = [
            item
            for item in task_logs
            if str(item.get("run_id") or "").strip() in selected_run_ids
        ][:MEMORY_RECALL_TASK_LOG_LIMIT]

        return {
            "session_runs": selected_runs,
            "task_logs": selected_tasks,
            "working_memory_summary": working_memory_summary,
            "recent_turns": recent_turns[:MEMORY_RECALL_RUN_LIMIT],
            "profile_facts": profile_facts[:MEMORY_RECALL_PROFILE_LIMIT],
        }

    def _build_direct_answer_output(
        self,
        state: SummaryState,
        query: str,
    ) -> tuple[str, str, int]:
        """Generate a concise answer grounded in recalled context."""

        prompt = self._build_direct_answer_prompt(state, query)
        try:
            response = self._direct_answer_agent.run(prompt)
        finally:
            self._direct_answer_agent.clear_history()

        answer_text = response.strip()
        if self._config.strip_thinking_tokens:
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

    def _build_direct_answer_prompt(
        self,
        state: SummaryState,
        query: str,
    ) -> str:
        """Build the input for the direct-answer agent."""

        recalled = state.recalled_context or {}

        def format_lines(items: list[Any], formatter, fallback: str) -> str:
            lines = [formatter(item) for item in items if formatter(item)]
            return "\n".join(lines[:5]) if lines else fallback

        profile_block = format_lines(
            list(recalled.get("profile_facts") or []),
            lambda item: f"- {str(item.get('fact') or '').strip()}",
            "- 暂无命中的长期目标/偏好/约束",
        )
        session_fact_block = format_lines(
            [
                {"fact": str(recalled.get("working_memory_summary") or "").strip()}
            ]
            if str(recalled.get("working_memory_summary") or "").strip()
            else [],
            lambda item: f"- {str(item.get('fact') or '').strip()}",
            "- 暂无当前会话工作记忆摘要",
        )
        recent_turn_block = format_lines(
            list(recalled.get("recent_turns") or []),
            lambda item: (
                f"- 用户：{str(item.get('user_query') or '').strip()[:80]}；"
                f"回答：{str(item.get('assistant_response') or '').strip()[:120]}"
            ),
            "- 暂无最近对话",
        )
        global_fact_block = format_lines(
            list(recalled.get("global_facts") or []),
            lambda item: f"- {str(item.get('fact') or '').strip()}",
            "- 暂无跨会话稳定知识",
        )
        session_run_block = format_lines(
            list(recalled.get("session_runs") or []),
            lambda item: (
                f"- {str(item.get('topic') or '').strip()}："
                f"{str(item.get('report_excerpt') or '').strip()[:180]}"
            ),
            "- 暂无相关历史研究",
        )

        has_context = self._has_direct_answer_context(recalled)

        return (
            f"当前问题：{query}\n"
            f"是否命中历史上下文：{'是' if has_context else '否'}\n\n"
            "请直接回答用户，不要把自己写成研究报告。\n\n"
            f"长期目标/偏好/约束：\n{profile_block}\n\n"
            f"当前会话工作记忆摘要：\n{session_fact_block}\n\n"
            f"最近几轮对话：\n{recent_turn_block}\n\n"
            f"跨会话稳定知识：\n{global_fact_block}\n\n"
            f"最近相关研究：\n{session_run_block}\n"
        )

    @staticmethod
    def _build_direct_answer_sources(state: SummaryState) -> tuple[str, int]:
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

        working_memory_summary = str(recalled.get("working_memory_summary") or "").strip()
        if working_memory_summary:
            source_lines.extend(
                [
                    "Source: Working Memory Summary",
                    f"信息内容: {working_memory_summary[:240]}",
                ]
            )
            evidence_count += 1

        for idx, item in enumerate(recalled.get("recent_turns") or [], start=1):
            user_query = str(item.get("user_query") or "").strip()
            assistant_response = str(item.get("assistant_response") or "").strip()
            if not (user_query or assistant_response):
                continue
            source_lines.extend(
                [
                    f"Source: Recent Turn {idx}",
                    f"信息内容: 问题：{user_query[:100]}；回答：{assistant_response[:140]}",
                ]
            )
            evidence_count += 1

        for idx, item in enumerate(recalled.get("global_facts") or [], start=1):
            fact = str(item.get("fact") or "").strip()
            if not fact:
                continue
            source_lines.extend(
                [
                    f"Source: Global Memory {idx}",
                    f"信息内容: {fact}",
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

    def _build_response_mode_classifier_input(
        self,
        topic: str,
        recalled_context: dict[str, Any] | None,
    ) -> str:
        """Serialize a compact routing context for the classifier agent."""

        recalled = recalled_context or {}
        payload = {
            "topic": topic,
            "session_runs": [
                {
                    "run_id": str(item.get("run_id") or "").strip(),
                    "topic": self._trim_text(item.get("topic"), 120),
                    "finished_at": self._trim_text(item.get("finished_at"), 40),
                }
                for item in (recalled.get("session_runs") or [])[:3]
            ],
            "working_memory_summary": self._trim_text(
                recalled.get("working_memory_summary"),
                240,
            ),
            "recent_turns": [
                {
                    "run_id": str(item.get("run_id") or "").strip(),
                    "user_query": self._trim_text(item.get("user_query"), 120),
                    "assistant_response": self._trim_text(item.get("assistant_response"), 180),
                }
                for item in (recalled.get("recent_turns") or [])[:3]
            ],
            "profile_facts": [
                {
                    "fact_id": str(item.get("fact_id") or "").strip(),
                    "fact": self._trim_text(item.get("fact"), 160),
                }
                for item in (recalled.get("profile_facts") or [])[:5]
            ],
            "global_facts": [
                {
                    "fact_id": str(item.get("fact_id") or "").strip(),
                    "fact": self._trim_text(item.get("fact"), 160),
                }
                for item in (recalled.get("global_facts") or [])[:5]
            ],
        }
        return json.dumps(payload, ensure_ascii=False, default=self._json_default)

    def _build_memory_recall_selector_input(
        self,
        query: str,
        *,
        session_runs: list[dict[str, Any]],
        task_logs: list[dict[str, Any]],
        working_memory_summary: str,
        recent_turns: list[dict[str, Any]],
        profile_facts: list[dict[str, Any]],
    ) -> str:
        """Serialize session/profile recall candidates for selector inference."""

        payload = {
            "query": query,
            "session_runs": [
                {
                    "run_id": str(item.get("run_id") or "").strip(),
                    "topic": self._trim_text(item.get("topic"), 120),
                    "report_excerpt": self._trim_text(item.get("report_excerpt"), 220),
                    "finished_at": self._trim_text(item.get("finished_at"), 40),
                }
                for item in session_runs[:MEMORY_RECALL_RUN_LIMIT]
            ],
            "task_logs": [
                {
                    "task_id": str(item.get("task_id") or "").strip(),
                    "run_id": str(item.get("run_id") or "").strip(),
                    "title": self._trim_text(item.get("title"), 80),
                    "summary": self._trim_text(item.get("summary"), 180),
                }
                for item in task_logs[:MEMORY_RECALL_TASK_LOG_LIMIT]
            ],
            "working_memory_summary": self._trim_text(working_memory_summary, 240),
            "recent_turns": [
                {
                    "run_id": str(item.get("run_id") or "").strip(),
                    "user_query": self._trim_text(item.get("user_query"), 120),
                    "assistant_response": self._trim_text(item.get("assistant_response"), 180),
                }
                for item in recent_turns[:MEMORY_RECALL_RUN_LIMIT]
            ],
            "profile_facts": [
                {
                    "fact_id": str(item.get("fact_id") or "").strip(),
                    "subject": self._trim_text(item.get("subject"), 60),
                    "fact": self._trim_text(item.get("fact"), 180),
                }
                for item in profile_facts[:MEMORY_RECALL_PROFILE_LIMIT]
            ],
        }
        return json.dumps(payload, ensure_ascii=False, default=self._json_default)

    def _run_json_agent(
        self,
        agent: AgentLike,
        prompt: str,
    ) -> dict[str, Any] | list | None:
        """Run an agent expected to emit one JSON payload."""

        try:
            response = agent.run(prompt)
        except Exception:
            try:
                agent.clear_history()
            except Exception:
                pass
            return None

        try:
            text = response.strip()
            if self._config.strip_thinking_tokens:
                text = strip_thinking_tokens(text)
            text = strip_tool_calls(text).strip()
            return self._extract_json_payload(text)
        finally:
            agent.clear_history()

    def _parse_mode_selection(self, payload: dict[str, Any] | list | None) -> dict[str, Any] | None:
        """Validate classifier JSON and normalize confidence."""

        if not isinstance(payload, dict):
            return None

        response_mode = str(payload.get("response_mode") or "").strip()
        if response_mode not in {
            RESPONSE_MODE_MEMORY_RECALL,
            RESPONSE_MODE_DIRECT_ANSWER,
            RESPONSE_MODE_DEEP_RESEARCH,
        }:
            return None

        confidence = self._clamp_confidence(payload.get("confidence"))
        return {
            "response_mode": response_mode,
            "confidence": confidence,
            "reason": str(payload.get("reason") or "").strip(),
        }

    def _parse_memory_selection(self, payload: dict[str, Any] | list | None) -> dict[str, list[str]] | None:
        """Validate selector JSON output."""

        if not isinstance(payload, dict):
            return None

        def normalize_ids(value: Any, *, limit: int) -> list[str]:
            if not isinstance(value, list):
                return []
            normalized: list[str] = []
            for item in value:
                candidate = str(item or "").strip()
                if candidate and candidate not in normalized:
                    normalized.append(candidate)
                if len(normalized) >= limit:
                    break
            return normalized

        return {
            "run_ids": normalize_ids(payload.get("run_ids"), limit=MEMORY_RECALL_RUN_LIMIT),
            "task_ids": normalize_ids(payload.get("task_ids"), limit=MEMORY_RECALL_TASK_LOG_LIMIT),
            "fact_ids": normalize_ids(payload.get("fact_ids"), limit=MEMORY_RECALL_FACT_LIMIT),
        }

    @staticmethod
    def _select_items_by_ids(
        items: list[dict[str, Any]],
        selected_ids: list[str],
        *,
        key: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Select items in caller-provided priority order."""

        if not items or not selected_ids:
            return []

        by_id = {
            str(item.get(key) or "").strip(): item
            for item in items
            if str(item.get(key) or "").strip()
        }
        selected: list[dict[str, Any]] = []
        for item_id in selected_ids:
            resolved = by_id.get(str(item_id or "").strip())
            if resolved is not None:
                selected.append(resolved)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _build_fact_lines(items: list[dict[str, Any]], *, prefix: str) -> list[str]:
        """Render fact bullets with de-duplication by fact text."""

        seen: set[str] = set()
        lines: list[str] = []
        for item in items:
            fact = str(item.get("fact") or "").strip()
            if not fact or fact in seen:
                continue
            seen.add(fact)
            lines.append(f"{prefix}{fact}")
        return lines

    @staticmethod
    def _trim_text(value: Any, limit: int) -> str:
        """Convert arbitrary values into short display text."""

        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    @staticmethod
    def _json_default(value: Any) -> str:
        """Serialize datetimes when building JSON agent prompts."""

        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _clamp_confidence(value: Any) -> float:
        """Normalize arbitrary numeric values into [0, 1]."""

        try:
            resolved = float(value)
        except (TypeError, ValueError):
            return 0.0
        return min(max(resolved, 0.0), 1.0)

    @staticmethod
    def _extract_json_payload(text: str) -> dict[str, Any] | list | None:
        """Best-effort extraction of one JSON object or array from model output."""

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None

        return None

    @staticmethod
    def _has_direct_answer_context(recalled_context: dict[str, Any] | None) -> bool:
        """Return whether recalled memory is non-empty enough for direct answers."""

        if not recalled_context:
            return False
        return bool(
            (recalled_context.get("profile_facts") or [])
            or str(recalled_context.get("working_memory_summary") or "").strip()
            or (recalled_context.get("recent_turns") or [])
            or (recalled_context.get("global_facts") or [])
            or (recalled_context.get("session_runs") or [])
        )

    def _build_memory_recall_context(self, state: SummaryState) -> dict[str, Any]:
        """Build an explicit recall context without mutating the default state context."""

        recalled_context = dict(state.recalled_context or {})
        task_logs = self._task_log_loader(
            state.session_id,
            exclude_run_id=state.run_id,
            limit=MEMORY_RECALL_TASK_LOG_LIMIT,
        )
        if task_logs:
            recalled_context["task_logs"] = task_logs
        return recalled_context

    @staticmethod
    def _default_task_log_loader(
        _session_id: str | None,
        *,
        exclude_run_id: str | None = None,
        limit: int = MEMORY_RECALL_TASK_LOG_LIMIT,
    ) -> list[dict[str, Any]]:
        """Default no-op task-log loader used when no backend is injected."""

        del exclude_run_id, limit
        return []

    @staticmethod
    def _is_research_intent_topic(topic: str) -> bool:
        normalized = str(topic or "").strip().lower()
        if not normalized:
            return False
        return any(keyword in normalized for keyword in RESEARCH_INTENT_KEYWORDS)

    @staticmethod
    def _merge_source_sections(*sections: str) -> str:
        chunks = [section.strip() for section in sections if section and section.strip()]
        return "\n\n".join(chunks)

    @staticmethod
    def _extend_unique_notices(existing: list[str], additions: list[str]) -> None:
        seen = {item for item in existing if item}
        for notice in additions:
            normalized = str(notice or "").strip()
            if not normalized or normalized in seen:
                continue
            existing.append(normalized)
            seen.add(normalized)

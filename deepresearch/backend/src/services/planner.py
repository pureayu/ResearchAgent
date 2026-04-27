"""Service responsible for converting the research topic into actionable tasks."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

from agent_runtime.interfaces import AgentLike
from agent_runtime.tool_protocol import extract_note_id_from_text
from config import Configuration
from llm.schemas import PlannerTasksOutput
from llm.structured import StructuredOutputRunner
from models import SummaryState, TodoItem
from prompts import (
    get_current_date,
    todo_planner_structured_instructions,
)
from services.tool_events import ToolCallTracker

logger = logging.getLogger(__name__)

class PlanningService:
    """Wraps the planner agent to produce structured TODO items."""

    def __init__(
        self,
        planner_agent: AgentLike | None,
        config: Configuration,
        *,
        structured_planner: StructuredOutputRunner[PlannerTasksOutput] | None = None,
        note_tool: Any | None = None,
        tool_tracker: ToolCallTracker | None = None,
    ) -> None:
        del planner_agent
        self._config = config
        self._structured_planner = structured_planner
        self._note_tool = note_tool
        self._tool_tracker = tool_tracker

    def plan_todo_list(self, state: SummaryState) -> List[TodoItem]:
        """Ask the planner agent to break the topic into actionable tasks."""

        recalled_context_text = self._format_recalled_context(state.recalled_context)
        structured_prompt = todo_planner_structured_instructions.format(
            current_date=get_current_date(),
            research_topic=state.research_topic,
            recalled_context=recalled_context_text,
        )

        tasks_payload = self._invoke_planner(structured_prompt=structured_prompt)
        todo_items: List[TodoItem] = []

        max_items = max(1, int(self._config.max_todo_items))
        for idx, item in enumerate(tasks_payload[:max_items], start=1):
            title = str(item.get("title") or f"任务{idx}").strip()
            intent = str(item.get("intent") or "聚焦主题的关键问题").strip()
            queries = self._normalize_queries(item, fallback=state.research_topic or "")
            query = queries[0] if queries else str(state.research_topic or "").strip()

            if not query:
                query = state.research_topic
            if not queries and query:
                queries = [query]

            task = TodoItem(
                id=idx,
                title=title,
                intent=intent,
                query=query,
                queries=queries,
            )
            todo_items.append(task)

        self._sync_task_notes(todo_items)
        state.todo_items = todo_items

        titles = [task.title for task in todo_items]
        logger.info("Planner produced %d tasks: %s", len(todo_items), titles)
        return todo_items

    def _invoke_planner(
        self,
        *,
        structured_prompt: str,
    ) -> List[dict[str, Any]]:
        """Use structured output and retry once with validation feedback."""

        if self._structured_planner is None:
            logger.warning("Structured planner unavailable; returning no tasks")
            return []

        prompt = structured_prompt
        last_error: Exception | None = None
        for attempt in range(0, 3):
            try:
                payload = self._structured_planner.invoke(prompt)
                tasks = [item.model_dump() for item in payload.tasks]
                logger.info(
                    "Planner structured output produced %d tasks via %s on attempt %d",
                    len(tasks),
                    self._structured_planner.agent_name,
                    attempt,
                )
                return tasks
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Structured planner attempt %d failed; retrying with validation feedback",
                    attempt,
                    exc_info=True,
                )
                prompt = self._build_repair_prompt(structured_prompt, exc)

        logger.error("Structured planner failed after repair retry: %s", last_error)
        return []

    @staticmethod
    def _build_repair_prompt(original_prompt: str, error: Exception) -> str:
        """Ask the model to repair its previous schema violation."""

        error_text = str(error)
        if len(error_text) > 3000:
            error_text = f"{error_text[:3000]}\n...[truncated]"
        return (
            f"{original_prompt}\n\n"
            "<PREVIOUS_ATTEMPT_FAILED>\n"
            "你的上一次输出没有通过 Pydantic schema 校验。错误信息如下，"
            "其中可能包含你上一次返回的非法 JSON。请不要解释错误，只重新输出一个符合 schema 的 JSON 对象。\n"
            f"{error_text}\n"
            "</PREVIOUS_ATTEMPT_FAILED>\n\n"
            "<REPAIR_RULES>\n"
            "1. 顶层必须是 JSON object，不是数组。\n"
            "2. 顶层必须只有 `tasks` 字段。\n"
            "3. `tasks` 中每个元素只能包含 `title`、`intent`、`query`、`queries`。\n"
            "4. `queries` 必须是字符串数组，包含 2~4 条互补英文检索式；`query` 必须等于 `queries[0]`。\n"
            "5. 禁止输出 note/action/task_id/tags/content 字段。\n"
            "6. 禁止输出 Markdown 或解释文字。\n"
            "</REPAIR_RULES>"
        )

    @staticmethod
    def _normalize_queries(item: dict[str, Any], *, fallback: str) -> list[str]:
        """Return deduplicated planner queries while preserving legacy `query`."""

        values: list[str] = []
        raw_queries = item.get("queries")
        if isinstance(raw_queries, list):
            values.extend(str(query or "") for query in raw_queries)
        legacy_query = str(item.get("query") or "").strip()
        if legacy_query:
            values.append(legacy_query)
        if not values and fallback:
            values.append(fallback)

        normalized: list[str] = []
        for value in values:
            for part in str(value or "").split(";"):
                query = " ".join(part.split()).strip()
                if query and query not in normalized:
                    normalized.append(query)
        return normalized[:4]

    def _sync_task_notes(self, todo_items: List[TodoItem]) -> None:
        """Create deterministic task notes when planner output is schema-driven."""

        if not self._note_tool:
            return

        for task in todo_items:
            if task.note_id:
                continue

            title = f"任务 {task.id}: {task.title}".strip()
            payload = {
                "action": "create",
                "task_id": task.id,
                "title": title,
                "note_type": "task_state",
                "tags": ["deep_research", f"task_{task.id}"],
                "content": self._build_initial_note_content(task),
            }
            raw_parameters = json.dumps(payload, ensure_ascii=False)

            try:
                response = str(self._note_tool.run(payload) or "")
            except Exception:
                logger.exception("Failed to create planner note for task %s", task.id)
                continue

            note_id = extract_note_id_from_text(response)
            if note_id:
                task.note_id = note_id
                if self._config.notes_workspace:
                    task.note_path = str(Path(self._config.notes_workspace) / f"{note_id}.md")

            if self._tool_tracker is not None:
                self._tool_tracker.record(
                    {
                        "agent_name": (
                            self._structured_planner.agent_name
                            if self._structured_planner is not None
                            else "研究规划专家"
                        ),
                        "tool_name": "note",
                        "raw_parameters": raw_parameters,
                        "parsed_parameters": payload,
                        "result": response,
                    }
                )

    @staticmethod
    def _build_initial_note_content(task: TodoItem) -> str:
        """Seed a new task note with planner-time metadata."""

        return (
            f"# 任务 {task.id}: {task.title}\n\n"
            "## 任务概览\n"
            f"- 任务目标：{task.intent}\n"
            f"- 检索查询：{task.query}\n\n"
            f"- 多重检索：{'; '.join(task.queries or [task.query])}\n\n"
            "## 来源概览\n"
            "待补充\n\n"
            "## 任务总结\n"
            "待补充"
        )

    def _format_recalled_context(self, recalled_context: dict[str, Any] | None) -> str:
        """Convert structured recalled memory into stable planner-facing text."""

        if not recalled_context:
            return "无"

        working_memory_summary = str(recalled_context.get("working_memory_summary") or "").strip()
        recent_turns = recalled_context.get("recent_turns") or []
        profile_facts = recalled_context.get("profile_facts") or []
        global_facts = recalled_context.get("global_facts") or []

        sections: list[str] = []

        if working_memory_summary:
            sections.append(f"当前会话工作记忆摘要：\n{working_memory_summary}")

        if recent_turns:
            lines = ["最近几轮对话："]
            for idx, turn in enumerate(recent_turns[:3], start=1):
                user_query = str(turn.get("user_query") or "").strip()
                assistant_response = str(turn.get("assistant_response") or "").strip()
                lines.append(f"{idx}. 用户：{user_query[:120]}")
                if assistant_response:
                    lines.append(f"   回答：{assistant_response[:180]}")
            sections.append("\n".join(lines))

        if profile_facts:
            lines = ["用户长期目标/偏好："]
            for idx, fact in enumerate(profile_facts[:5], start=1):
                fact_text = str(fact.get("fact") or "").strip()
                if not fact_text:
                    continue
                lines.append(f"{idx}. {fact_text}")
            if len(lines) > 1:
                sections.append("\n".join(lines))

        if global_facts:
            lines = ["跨会话稳定知识："]
            for idx, fact in enumerate(global_facts[:5], start=1):
                fact_text = str(fact.get("fact") or "").strip()
                if not fact_text:
                    continue
                lines.append(f"{idx}. {fact_text}")
            if len(lines) > 1:
                sections.append("\n".join(lines))

        return "\n\n".join(sections) if sections else "无"

    @staticmethod
    def create_fallback_task(state: SummaryState) -> TodoItem:
        """Create a minimal fallback task when planning failed."""

        return TodoItem(
            id=1,
            title="基础背景梳理",
            intent="收集主题的核心背景与最新动态",
            query=f"{state.research_topic} 最新进展" if state.research_topic else "基础背景梳理",
            queries=[f"{state.research_topic} 最新进展"] if state.research_topic else ["基础背景梳理"],
        )

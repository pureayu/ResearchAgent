"""Service responsible for converting the research topic into actionable tasks."""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

from agent_runtime.interfaces import AgentLike
from agent_runtime.tool_protocol import extract_tool_calls, parse_tool_payload_body
from models import SummaryState, TodoItem
from config import Configuration
from prompts import get_current_date, todo_planner_instructions
from utils import strip_thinking_tokens

logger = logging.getLogger(__name__)

class PlanningService:
    """Wraps the planner agent to produce structured TODO items."""

    def __init__(self, planner_agent: AgentLike, config: Configuration) -> None:
        self._agent = planner_agent
        self._config = config

    def plan_todo_list(self, state: SummaryState) -> List[TodoItem]:
        """Ask the planner agent to break the topic into actionable tasks."""

        recalled_context_text = self._format_recalled_context(state.recalled_context)
        prompt = todo_planner_instructions.format(
            current_date=get_current_date(),
            research_topic=state.research_topic,
            recalled_context=recalled_context_text,
        )

        response = self._agent.run(prompt)
        self._agent.clear_history()

        logger.info("Planner raw output (truncated): %s", response[:500])

        tasks_payload = self._extract_tasks(response)
        todo_items: List[TodoItem] = []

        max_items = max(1, int(self._config.max_todo_items))
        for idx, item in enumerate(tasks_payload[:max_items], start=1):
            title = str(item.get("title") or f"任务{idx}").strip()
            intent = str(item.get("intent") or "聚焦主题的关键问题").strip()
            query = str(item.get("query") or state.research_topic).strip()

            if not query:
                query = state.research_topic

            task = TodoItem(
                id=idx,
                title=title,
                intent=intent,
                query=query,
            )
            todo_items.append(task)

        state.todo_items = todo_items

        titles = [task.title for task in todo_items]
        logger.info("Planner produced %d tasks: %s", len(todo_items), titles)
        return todo_items

    def _format_recalled_context(self, recalled_context: dict[str, Any] | None) -> str:
        """Convert structured recalled memory into stable planner-facing text."""

        if not recalled_context:
            return "无"

        session_runs = recalled_context.get("session_runs") or []
        working_memory_summary = str(recalled_context.get("working_memory_summary") or "").strip()
        recent_turns = recalled_context.get("recent_turns") or []
        profile_facts = recalled_context.get("profile_facts") or []
        global_facts = recalled_context.get("global_facts") or []

        sections: list[str] = []

        if session_runs:
            lines = ["最近研究轮次："]
            for idx, run in enumerate(session_runs[:3], start=1):
                topic = str(run.get("topic") or "未知主题").strip()
                finished_at = str(run.get("finished_at") or "未完成").strip()
                task_count = run.get("task_count")
                excerpt = str(run.get("report_excerpt") or "").strip()
                excerpt = excerpt[:180] + ("..." if len(excerpt) > 180 else "")
                lines.append(
                    f"{idx}. 主题：{topic}；完成时间：{finished_at}；任务数：{task_count}"
                )
                if excerpt:
                    lines.append(f"   报告摘要：{excerpt}")
            sections.append("\n".join(lines))

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
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------
    def _extract_tasks(self, raw_response: str) -> List[dict[str, Any]]:
        """Parse planner output into a list of task dictionaries."""

        text = raw_response.strip()
        if self._config.strip_thinking_tokens:
            text = strip_thinking_tokens(text)

        json_payload = self._extract_json_payload(text)
        tasks: List[dict[str, Any]] = []

        if isinstance(json_payload, dict):
            candidate = json_payload.get("tasks")
            if isinstance(candidate, list):
                for item in candidate:
                    if isinstance(item, dict):
                        tasks.append(item)
        elif isinstance(json_payload, list):
            for item in json_payload:
                if isinstance(item, dict):
                    tasks.append(item)

        if not tasks:
            tool_payload = self._extract_tool_payload(text)
            if tool_payload and isinstance(tool_payload.get("tasks"), list):
                for item in tool_payload["tasks"]:
                    if isinstance(item, dict):
                        tasks.append(item)

        if not tasks:
            tasks.extend(self._extract_tasks_from_note_tool_calls(text))

        if not tasks:
            tasks.extend(self._extract_tasks_from_markdown(text))

        return tasks

    def _extract_json_payload(self, text: str) -> Optional[dict[str, Any] | list]:
        """Try to locate and parse a JSON object or array from the text."""

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None

        return None

    def _extract_tool_payload(self, text: str) -> Optional[dict[str, Any]]:
        """Parse the first TOOL_CALL expression in the output."""
        tool_calls = self._extract_tool_calls(text)
        if not tool_calls:
            return None

        _, body = tool_calls[0]
        return self._parse_tool_payload_body(body)

    def _extract_tool_calls(self, text: str) -> list[tuple[str, str]]:
        """Extract TOOL_CALL entries while tolerating JSON arrays in the payload."""

        return extract_tool_calls(text)

    def _extract_tasks_from_note_tool_calls(self, text: str) -> List[dict[str, Any]]:
        """Recover task definitions from note create calls when final JSON is missing."""

        tasks: list[dict[str, Any]] = []

        for tool_name, body in self._extract_tool_calls(text):
            if tool_name.strip().lower() != "note":
                continue

            payload = self._parse_tool_payload_body(body)

            if not isinstance(payload, dict):
                continue

            if str(payload.get("action") or "").lower() != "create":
                continue

            title = str(payload.get("title") or "").strip()
            content = str(payload.get("content") or "").strip()

            if not title:
                continue

            intent = self._extract_field(
                content,
                prefixes=["任务目标：", "任务目标:", "目标：", "目标:"],
            )
            query = self._extract_field(
                content,
                prefixes=["检索方向：", "检索方向:", "检索关键词：", "检索关键词:"],
            )

            tasks.append(
                {
                    "title": title,
                    "intent": intent or "聚焦主题的关键问题",
                    "query": query or title,
                }
            )

        return tasks

    def _parse_tool_payload_body(self, body: str) -> Optional[dict[str, Any]]:
        """Parse a TOOL_CALL body, tolerating quasi-JSON produced by the LLM."""

        return parse_tool_payload_body(body)

    def _extract_tasks_from_markdown(self, text: str) -> List[dict[str, Any]]:
        """Fallback parser for markdown tables or numbered task lists."""

        tasks: list[dict[str, Any]] = []

        table_row_pattern = re.compile(
            r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
            re.MULTILINE,
        )
        for _, title, intent in table_row_pattern.findall(text):
            clean_title = title.strip()
            if clean_title.startswith("任务名称") or clean_title.startswith("任务ID"):
                continue
            tasks.append(
                {
                    "title": clean_title,
                    "intent": intent.strip(),
                    "query": clean_title,
                }
            )

        if tasks:
            return tasks

        line_pattern = re.compile(
            r"^(?:[-*]|\d+[.)]|任务\s*\d+[:：-])\s*(.+)$",
            re.MULTILINE,
        )
        for match in line_pattern.findall(text):
            line = match.strip()
            if len(line) < 4:
                continue
            title = re.split(r"[：:。；;]", line, maxsplit=1)[0].strip()
            tasks.append(
                {
                    "title": title,
                    "intent": line,
                    "query": title,
                }
            )

        return tasks

    @staticmethod
    def _extract_field(content: str, prefixes: list[str]) -> str:
        """Extract a single-line field value by prefix from note content."""

        for line in content.splitlines():
            stripped = line.strip()
            for prefix in prefixes:
                if stripped.startswith(prefix):
                    return stripped[len(prefix) :].strip()
        return ""

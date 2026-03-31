"""Service responsible for reviewing research coverage and proposing follow-up tasks."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from hello_agents import ToolAwareSimpleAgent

from config import Configuration
from models import SummaryState
from prompts import get_current_date, research_reviewer_instructions
from utils import strip_thinking_tokens

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class ResearchReview:
    """Structured reviewer verdict for one research round."""

    is_sufficient: bool
    overall_gap: str = ""
    confidence: float = 0.0
    followup_tasks: list[dict[str, Any]] = field(default_factory=list)


class ReviewerService:
    """Wraps the reviewer agent to decide whether more research is needed."""

    def __init__(self, reviewer_agent: ToolAwareSimpleAgent, config: Configuration) -> None:
        self._agent = reviewer_agent
        self._config = config

    def review_progress(self, state: SummaryState, current_round: int) -> ResearchReview:
        """Review current task coverage and optionally propose follow-up tasks."""

        prompt = research_reviewer_instructions.format(
            current_date=get_current_date(),
            research_topic=state.research_topic,
            current_round=current_round,
            recalled_context=self._format_recalled_context(state.recalled_context),
            tasks_snapshot=self._format_tasks_snapshot(state),
        )

        response = self._agent.run(prompt)
        self._agent.clear_history()

        logger.info("Reviewer raw output (truncated): %s", response[:500])
        verdict = self._parse_review(response)
        logger.info(
            "Reviewer verdict: sufficient=%s followups=%d confidence=%.2f gap=%s",
            verdict.is_sufficient,
            len(verdict.followup_tasks),
            verdict.confidence,
            verdict.overall_gap,
        )
        return verdict

    def _format_tasks_snapshot(self, state: SummaryState) -> str:
        """Serialize current tasks into stable reviewer-facing text."""

        if not state.todo_items:
            return "暂无任务。"

        blocks: list[str] = []
        for task in state.todo_items:
            summary = (task.summary or "").strip()
            summary = summary[:220] + ("..." if len(summary) > 220 else "")
            sources = (task.sources_summary or "").strip()
            sources = sources[:160] + ("..." if len(sources) > 160 else "")
            blocks.append(
                f"### 任务 {task.id}: {task.title}\n"
                f"- 轮次：{task.round_id}\n"
                f"- 来源：{task.origin}\n"
                f"- 目标：{task.intent}\n"
                f"- 查询：{task.latest_query or task.query}\n"
                f"- 状态：{task.status}\n"
                f"- 后端：{task.search_backend or 'unknown'}\n"
                f"- 尝试次数：{task.attempt_count}\n"
                f"- 证据数量：{task.evidence_count}\n"
                f"- 证据缺口：{task.evidence_gap_reason or '无'}\n"
                f"- 总结：{summary or '暂无可用信息'}\n"
                f"- 来源概览：{sources or '暂无来源'}"
            )

        return "\n\n".join(blocks)

    def _format_recalled_context(self, recalled_context: dict[str, Any] | None) -> str:
        """Render recalled context for reviewer consumption."""

        if not recalled_context:
            return "无"

        sections: list[str] = []

        session_runs = recalled_context.get("session_runs") or []
        if session_runs:
            lines = ["最近研究轮次："]
            for idx, run in enumerate(session_runs[:3], start=1):
                lines.append(
                    f"{idx}. 主题：{run.get('topic') or '未知主题'}；完成时间：{run.get('finished_at') or '未完成'}"
                )
            sections.append("\n".join(lines))

        session_facts = recalled_context.get("session_facts") or recalled_context.get("semantic_facts") or []
        if session_facts:
            lines = ["当前会话已沉淀结论："]
            for idx, fact in enumerate(session_facts[:5], start=1):
                fact_text = str(fact.get("fact") or "").strip()
                if fact_text:
                    lines.append(f"{idx}. {fact_text}")
            if len(lines) > 1:
                sections.append("\n".join(lines))

        profile_facts = recalled_context.get("profile_facts") or []
        if profile_facts:
            lines = ["用户长期目标/偏好："]
            for idx, fact in enumerate(profile_facts[:5], start=1):
                fact_text = str(fact.get("fact") or "").strip()
                if fact_text:
                    lines.append(f"{idx}. {fact_text}")
            if len(lines) > 1:
                sections.append("\n".join(lines))

        global_facts = recalled_context.get("global_facts") or []
        if global_facts:
            lines = ["跨会话稳定知识："]
            for idx, fact in enumerate(global_facts[:5], start=1):
                fact_text = str(fact.get("fact") or "").strip()
                if fact_text:
                    lines.append(f"{idx}. {fact_text}")
            if len(lines) > 1:
                sections.append("\n".join(lines))

        return "\n\n".join(sections) if sections else "无"

    def _parse_review(self, raw_response: str) -> ResearchReview:
        """Parse reviewer output into a structured verdict."""

        text = raw_response.strip()
        if self._config.strip_thinking_tokens:
            text = strip_thinking_tokens(text)

        payload = self._extract_json_payload(text)
        if not isinstance(payload, dict):
            return ResearchReview(
                is_sufficient=True,
                overall_gap="review_parse_failed",
                confidence=0.0,
                followup_tasks=[],
            )

        followup_items = payload.get("followup_tasks")
        parsed_followups: list[dict[str, Any]] = []
        if isinstance(followup_items, list):
            for item in followup_items[:3]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                intent = str(item.get("intent") or "").strip()
                query = str(item.get("query") or "").strip()
                if not (title and intent and query):
                    continue
                parent_task_id: int | None = None
                raw_parent = item.get("parent_task_id")
                if isinstance(raw_parent, int):
                    parent_task_id = raw_parent
                elif isinstance(raw_parent, str) and raw_parent.isdigit():
                    parent_task_id = int(raw_parent)
                parsed_followups.append(
                    {
                        "title": title,
                        "intent": intent,
                        "query": query,
                        "parent_task_id": parent_task_id,
                    }
                )

        is_sufficient = bool(payload.get("is_sufficient"))
        overall_gap = str(payload.get("overall_gap") or "").strip()

        confidence = 0.0
        raw_confidence = payload.get("confidence")
        if isinstance(raw_confidence, (int, float)):
            confidence = float(raw_confidence)
        elif isinstance(raw_confidence, str):
            try:
                confidence = float(raw_confidence)
            except ValueError:
                confidence = 0.0
        confidence = min(max(confidence, 0.0), 1.0)

        if not parsed_followups and not is_sufficient:
            is_sufficient = True
            overall_gap = overall_gap or "review_no_followup_tasks"

        return ResearchReview(
            is_sufficient=is_sufficient,
            overall_gap=overall_gap,
            confidence=confidence,
            followup_tasks=parsed_followups,
        )

    def _extract_json_payload(self, text: str) -> dict[str, Any] | list | None:
        """Best-effort extraction of a JSON object or array from model output."""

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

"""Service responsible for reviewing research coverage and proposing follow-up tasks."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from agent_runtime.interfaces import AgentLike
from config import Configuration
from llm.schemas import ReviewerOutput
from llm.structured import StructuredOutputRunner
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

    def __init__(
        self,
        reviewer_agent: AgentLike | None,
        config: Configuration,
        *,
        structured_reviewer: StructuredOutputRunner[ReviewerOutput] | None = None,
    ) -> None:
        self._agent = reviewer_agent
        self._config = config
        self._structured_reviewer = structured_reviewer

    def review_progress(self, state: SummaryState, current_round: int) -> ResearchReview:
        """Review current task coverage and optionally propose follow-up tasks."""

        prompt = research_reviewer_instructions.format(
            current_date=get_current_date(),
            research_topic=state.research_topic,
            current_round=current_round,
            recalled_context=self._format_recalled_context(state.recalled_context),
            tasks_snapshot=self._format_tasks_snapshot(state),
        )

        if self._structured_reviewer is not None:
            try:
                payload = self._structured_reviewer.invoke(prompt)
                verdict = self._from_structured_output(payload)
                logger.info(
                    "Reviewer structured verdict: sufficient=%s followups=%d confidence=%.2f gap=%s",
                    verdict.is_sufficient,
                    len(verdict.followup_tasks),
                    verdict.confidence,
                    verdict.overall_gap,
                )
                return verdict
            except Exception:
                logger.exception("Structured reviewer failed; falling back to legacy agent")

        if self._agent is None:
            return ResearchReview(
                is_sufficient=True,
                overall_gap="review_agent_unavailable",
                confidence=0.0,
                followup_tasks=[],
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

    @classmethod
    def _from_structured_output(cls, payload: ReviewerOutput) -> ResearchReview:
        """Convert a Pydantic schema into the legacy reviewer dataclass."""

        parsed_followups: list[dict[str, Any]] = []
        for item in payload.followup_tasks[:3]:
            title = str(item.title or "").strip()
            intent = str(item.intent or "").strip()
            queries = cls._normalize_queries(item.queries, item.query)
            query = queries[0] if queries else ""
            if not (title and intent and query):
                continue
            parsed_followups.append(
                {
                    "title": title,
                    "intent": intent,
                    "query": query,
                    "queries": queries,
                    "parent_task_id": item.parent_task_id,
                }
            )

        return ResearchReview(
            is_sufficient=bool(payload.is_sufficient),
            overall_gap=str(payload.overall_gap or "").strip(),
            confidence=min(max(float(payload.confidence or 0.0), 0.0), 1.0),
            followup_tasks=parsed_followups,
        )

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
                f"- 多重查询：{'; '.join(task.queries or [task.query])}\n"
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

        working_memory_summary = str(recalled_context.get("working_memory_summary") or "").strip()
        if working_memory_summary:
            sections.append(f"当前会话工作记忆摘要：\n{working_memory_summary}")

        recent_turns = recalled_context.get("recent_turns") or []
        if recent_turns:
            lines = ["最近几轮对话："]
            for idx, turn in enumerate(recent_turns[:3], start=1):
                user_query = str(turn.get("user_query") or "").strip()
                assistant_response = str(turn.get("assistant_response") or "").strip()
                if user_query:
                    lines.append(f"{idx}. 用户：{user_query[:120]}")
                if assistant_response:
                    lines.append(f"   回答：{assistant_response[:180]}")
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
                queries = self._normalize_queries(item.get("queries"), item.get("query"))
                query = queries[0] if queries else ""
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
                        "queries": queries,
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

    @staticmethod
    def _normalize_queries(raw_queries: Any, raw_query: Any) -> list[str]:
        values: list[str] = []
        if isinstance(raw_queries, list):
            values.extend(str(query or "") for query in raw_queries)
        legacy_query = str(raw_query or "").strip()
        if legacy_query:
            values.append(legacy_query)

        normalized: list[str] = []
        for value in values:
            for part in str(value or "").split(";"):
                query = " ".join(part.split()).strip()
                if query and query not in normalized:
                    normalized.append(query)
        return normalized[:4]

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

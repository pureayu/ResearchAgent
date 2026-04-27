"""Service that consolidates task results into the final report."""

from __future__ import annotations

import json

from agent_runtime.interfaces import AgentLike
from models import SummaryState
from config import Configuration
from utils import strip_thinking_tokens
from services.text_processing import clean_task_summary, dedupe_markdown_blocks, strip_tool_calls


class ReportingService:
    """Generates the final structured report."""

    def __init__(self, report_agent: AgentLike, config: Configuration) -> None:
        self._agent = report_agent
        self._config = config

    def generate_report(self, state: SummaryState) -> str:
        """Generate a structured report based on completed tasks."""

        tasks_block = []
        for task in state.todo_items:
            summary_block = clean_task_summary(task.summary or "") or "暂无可用信息"
            sources_block = task.sources_summary or "暂无来源"
            tasks_block.append(
                f"### 任务 {task.id}: {task.title}\n"
                f"- 任务目标：{task.intent}\n"
                f"- 检索查询：{task.query}\n"
                f"- 多重检索：{'; '.join(task.queries or [task.query])}\n"
                f"- 执行状态：{task.status}\n"
                f"- 检索后端：{task.search_backend or 'unknown'}\n"
                f"- 检索轮次：{task.attempt_count}\n"
                f"- 证据数量：{task.evidence_count}\n"
                f"- 最高分：{task.top_score:.4f}\n"
                f"- 是否二次补检索：{'是' if task.needs_followup else '否'}\n"
                f"- 任务总结：\n{summary_block}\n"
                f"- 来源概览：\n{sources_block}\n"
            )

        authoritative_status_section = self._build_authoritative_status_section(state)
        authoritative_sources_section = self._build_authoritative_sources_section(state)

        note_references = []
        for task in state.todo_items:
            if task.note_id:
                note_references.append(
                    f"- 任务 {task.id}《{task.title}》：note_id={task.note_id}"
                )

        notes_section = "\n".join(note_references) if note_references else "- 暂无可用任务笔记"

        read_template = json.dumps({"action": "read", "note_id": "<note_id>"}, ensure_ascii=False)
        create_conclusion_template = json.dumps(
            {
                "action": "create",
                "title": f"研究报告：{state.research_topic}",
                "note_type": "conclusion",
                "tags": ["deep_research", "report"],
                "content": "请在此沉淀最终报告要点",
            },
            ensure_ascii=False,
        )

        prompt = (
            f"研究主题：{state.research_topic}\n"
            "以下“任务事实表”和“来源事实表”是权威输入，优先级高于任务笔记，不得与其矛盾。\n"
            "写作时请把它们当作事实校验材料，不要把这些表格原样搬进最终报告。\n"
            "最终报告应以分析性正文为主，优先用长段落综合论证，而不是把信息拆成许多零碎短点。\n"
            f"任务事实表：\n{authoritative_status_section}\n\n"
            f"来源事实表：\n{authoritative_sources_section}\n\n"
            f"任务概览：\n{''.join(tasks_block)}\n"
            f"可用任务笔记：\n{notes_section}\n"
            f"请针对每条任务笔记使用格式：[TOOL_CALL:note:{read_template}] 读取内容，整合所有信息后撰写报告。\n"
            f"如需输出汇总结论，可追加调用：[TOOL_CALL:note:{create_conclusion_template}] 保存报告要点。"
        )

        response = self._agent.run(prompt)
        self._agent.clear_history()

        report_text = response.strip()
        if self._config.strip_thinking_tokens:
            report_text = strip_thinking_tokens(report_text)

        report_text = strip_tool_calls(report_text).strip()
        report_text = dedupe_markdown_blocks(report_text)

        return report_text or "报告生成失败，请检查输入。"

    def _build_authoritative_status_section(self, state: SummaryState) -> str:
        """Build a deterministic task-status table from runtime state."""

        header = "| 任务编号 | 任务标题 | 执行状态 | 检索后端 | 检索轮次 | 证据数量 | 最高分 | 是否补检索 |"
        divider = "|----------|----------|----------|----------|----------|----------|--------|------------|"
        rows = [header, divider]

        for task in state.todo_items:
            rows.append(
                "| {id} | {title} | {status} | {backend} | {attempts} | {evidence} | {score:.4f} | {followup} |".format(
                    id=task.id,
                    title=task.title,
                    status=task.status,
                    backend=task.search_backend or "unknown",
                    attempts=task.attempt_count,
                    evidence=task.evidence_count,
                    score=task.top_score,
                    followup="是" if task.needs_followup else "否",
                )
            )

        return "\n".join(rows)

    def _build_authoritative_sources_section(self, state: SummaryState) -> str:
        """Build a deterministic per-task source summary block."""

        blocks = []
        for task in state.todo_items:
            blocks.append(
                f"### 任务 {task.id}: {task.title}\n"
                f"- 执行状态：{task.status}\n"
                f"- 来源概览：\n{task.sources_summary or '暂无来源'}\n"
            )
        return "\n".join(blocks).strip() or "暂无来源"

    def _append_authoritative_appendix(
        self,
        report_text: str,
        *,
        authoritative_status_section: str,
        authoritative_sources_section: str,
    ) -> str:
        """Append deterministic facts so the final report remains grounded."""

        appendix = (
            "\n\n---\n\n"
            "## 任务执行事实附录\n\n"
            "以下内容由系统运行状态直接生成，用于校验报告中的任务状态与来源信息。\n\n"
            f"{authoritative_status_section}\n\n"
            "## 来源事实附录\n\n"
            f"{authoritative_sources_section}\n"
        )
        return f"{report_text}{appendix}".strip()

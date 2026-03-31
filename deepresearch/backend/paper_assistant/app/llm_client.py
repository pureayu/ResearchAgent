import json
import re
from collections.abc import Callable

import httpx
from openai import OpenAI

from app.config import Settings
from app.models import Citation


class LiteratureLLM:
    def __init__(self, settings: Settings):
        settings.require_llm()
        self.settings = settings
        http_client = httpx.Client(timeout=settings.llm_timeout, trust_env=False)
        self.client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
            http_client=http_client,
        )

    def answer_question(
        self,
        question: str,
        citations: list[Citation],
        stream: bool = False,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        evidence = _format_citations(citations)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是文献检索助手。只能基于给定材料回答。"
                    "如果材料不足，就明确说明不足。"
                    "回答中尽量使用 [1] [2] 这种引用标记。"
                ),
            },
            {
                "role": "user",
                "content": f"问题：{question}\n\n材料：\n{evidence}",
            },
        ]
        return self._complete(messages, stream=stream, on_chunk=on_chunk)

    def rewrite_question(
        self,
        question: str,
        history_text: str,
        research_notes_text: str = "",
    ) -> str:
        if not history_text.strip() and not research_notes_text.strip():
            return question
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个研究问答系统中的问题补全器。"
                    "你的任务是根据历史对话和已有研究结论，将当前用户问题改写成一个独立、明确、适合检索的问题。"
                    "如果当前问题已经完整明确，直接原样返回。"
                    "如果无法从历史对话或研究结论中明确确定指代对象，也直接原样返回。"
                    "禁止引入历史中没有明确出现的新论文名、新方法名或新术语。"
                    "禁止根据常识或猜测扩展问题。"
                    "不要回答问题，只做改写。"
                    "只输出改写后的问题，不要解释，不要添加额外内容。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"历史对话：\n{history_text}\n\n"
                    f"研究结论：\n{research_notes_text}\n\n"
                    f"当前问题：\n{question}\n\n"
                    "请输出改写后的独立问题："
                ),
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,
                temperature=0.0,
                stream=False,
            )
            rewritten = (response.choices[0].message.content or "").strip()
            rewritten = rewritten.strip("`\"' ")
            rewritten = rewritten.splitlines()[0].strip() if rewritten else ""
            return rewritten or question
        except Exception:
            return question

    def summarize_topic(
        self,
        topic: str,
        citations: list[Citation],
        stream: bool = False,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        evidence = _format_citations(citations)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是文献主题总结助手。"
                    "请基于给定材料输出简洁、结构化的中文总结。"
                    "禁止使用材料以外的事实。"
                    "总结中尽量使用 [1] [2] 这种引用标记。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"主题：{topic}\n\n"
                    "请给出 2 到 4 段总结，优先覆盖定义、方法、挑战和差异。\n\n"
                    f"材料：\n{evidence}"
                ),
            },
        ]
        return self._complete(messages, stream=stream, on_chunk=on_chunk)

    def compress_research_note(
        self,
        question: str,
        answer: str,
        citation_titles: list[str],
    ) -> str:
        if not answer.strip():
            return answer

        citation_text = "\n".join(f"- {title}" for title in citation_titles) or "- 无"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是研究结论压缩助手。"
                    "请把给定问答压缩成 1 到 3 句稳定、可复用的研究结论。"
                    "保留关键定义、差异、挑战或方法结论。"
                    "不要输出引用标号，不要写成长段落，不要加入材料中没有的新事实。"
                    "只输出压缩后的结论，不要解释。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"问题：{question}\n\n"
                    f"参考标题：\n{citation_text}\n\n"
                    f"原始回答：\n{answer}\n\n"
                    "请输出压缩后的研究结论："
                ),
            },
        ]
        try:
            compressed = self._complete(messages, stream=False)
            compressed = compressed.strip()
            return compressed or answer
        except Exception:
            return answer

    def judge_answer(
        self,
        question: str,
        answer: str,
        citations: list[Citation],
        expected_titles: list[str],
        expected_points: list[str],
    ) -> dict[str, object]:
        evidence = _format_citations(citations)
        expected_titles_text = "\n".join(f"- {title}" for title in expected_titles) or "- 无"
        expected_points_text = "\n".join(f"- {point}" for point in expected_points) or "- 无"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是严格的文献问答评审器。"
                    "请只基于给定问题、标准要点、检索证据和模型回答进行评分。"
                    "输出必须是一个 JSON 对象，不要输出额外解释。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"问题：{question}\n\n"
                    f"参考文档标题：\n{expected_titles_text}\n\n"
                    f"参考要点：\n{expected_points_text}\n\n"
                    f"检索证据：\n{evidence}\n\n"
                    f"模型回答：\n{answer}\n\n"
                    "请输出 JSON，字段如下：\n"
                    '{'
                    '"correctness": 0到2的整数, '
                    '"groundedness": 0到2的整数, '
                    '"citation_use": 0到2的整数, '
                    '"pass": true或false, '
                    '"reason": "一句中文原因"'
                    '}\n'
                    "评分标准：\n"
                    "- correctness: 是否回答到主要问题并覆盖关键要点。\n"
                    "- groundedness: 是否明显依赖给定证据，没有超出材料乱编。\n"
                    "- citation_use: 是否合理使用或体现引用意识。\n"
                    "- pass: correctness>=1 且 groundedness>=1 时为 true。"
                ),
            },
        ]
        raw = self._complete(messages, stream=False)
        parsed = _parse_json_object(raw)
        if not parsed:
            return {
                "correctness": 0,
                "groundedness": 0,
                "citation_use": 0,
                "pass": False,
                "reason": f"评审解析失败: {raw[:120]}",
            }
        return {
            "correctness": int(parsed.get("correctness", 0)),
            "groundedness": int(parsed.get("groundedness", 0)),
            "citation_use": int(parsed.get("citation_use", 0)),
            "pass": bool(parsed.get("pass", False)),
            "reason": str(parsed.get("reason", "")),
        }

    def _complete(
        self,
        messages: list[dict[str, str]],
        stream: bool = False,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        if stream:
            response = self.client.chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,
                temperature=0.2,
                stream=True,
            )
            parts: list[str] = []
            for chunk in response:
                content = chunk.choices[0].delta.content or ""
                if not content:
                    continue
                parts.append(content)
                if on_chunk is not None:
                    on_chunk(content)
            return "".join(parts).strip()

        response = self.client.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,
            temperature=0.2,
        )
        return (response.choices[0].message.content or "").strip()


def _format_citations(citations: list[Citation]) -> str:
    if not citations:
        return "没有检索到可用材料。"
    lines: list[str] = []
    for index, citation in enumerate(citations, start=1):
        page_text = f", page={citation.page}" if citation.page else ""
        lines.append(
            f"[{index}] title={citation.title}{page_text}\n"
            f"path={citation.filepath}\n"
            f"content={citation.content}"
        )
    return "\n\n".join(lines)


def _parse_json_object(text: str) -> dict[str, object] | None:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None

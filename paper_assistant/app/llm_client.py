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

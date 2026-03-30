"""Utility helpers for normalizing agent generated text."""

from __future__ import annotations

import re


def strip_tool_calls(text: str) -> str:
    """移除文本中的工具调用标记。"""

    if not text:
        return text

    pattern = re.compile(r"\[TOOL_CALL:[^\]]+\]")
    return pattern.sub("", text)


def clean_task_summary(text: str) -> str:
    """Normalize task-summary markdown before persisting or reusing it."""

    if not text:
        return text

    cleaned = strip_tool_calls(text).strip()
    cleaned = re.sub(
        r"^\s*#{1,6}\s*任务(总结|摘要)\s*\n+",
        "",
        cleaned,
        count=1,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^\s*任务(总结|摘要)\s*\n+", "", cleaned, count=1, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def dedupe_markdown_blocks(text: str) -> str:
    """Drop exact duplicate markdown blocks while preserving first occurrence."""

    if not text:
        return text

    blocks = re.split(r"\n{2,}", text.strip())
    deduped: list[str] = []
    seen: set[str] = set()

    for block in blocks:
        normalized = re.sub(r"\s+", " ", block).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(block.strip())

    return "\n\n".join(deduped).strip()

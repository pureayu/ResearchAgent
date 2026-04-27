"""LangChain-backed implementation of the local AgentLike protocol."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent_runtime.interfaces import AgentLike
from agent_runtime.note_tool import NoteTool
from agent_runtime.tool_protocol import extract_tool_calls, parse_tool_payload_body, strip_tool_calls


class LangChainSimpleAgent(AgentLike):
    """Minimal agent wrapper that preserves the existing text tool protocol."""

    def __init__(
        self,
        *,
        name: str,
        llm: BaseChatModel,
        system_prompt: str,
        note_tool: NoteTool | None = None,
        tool_call_listener: Callable[[dict[str, Any]], None] | None = None,
        keep_history: bool = False,
        max_tool_rounds: int = 4,
    ) -> None:
        self._name = name
        self._llm = llm
        self._system_prompt = system_prompt.strip()
        self._note_tool = note_tool
        self._tool_call_listener = tool_call_listener
        self._keep_history = keep_history
        self._max_tool_rounds = max_tool_rounds
        self._history: list[Any] = []

    def run(self, input_text: str, **kwargs: Any) -> str:
        """Run the agent synchronously and return the final visible text."""

        del kwargs
        return self._run_internal(input_text)

    def stream_run(self, input_text: str, **kwargs: Any) -> Iterator[str]:
        """Yield the final visible text as one stream chunk."""

        del kwargs
        text = self._run_internal(input_text)
        if text:
            yield text

    def clear_history(self) -> None:
        """Reset retained conversation history."""

        self._history.clear()

    def _run_internal(self, input_text: str) -> str:
        messages = [SystemMessage(content=self._system_prompt)]
        if self._keep_history:
            messages.extend(self._history)
        messages.append(HumanMessage(content=input_text))

        visible_segments: list[str] = []

        for _ in range(self._max_tool_rounds):
            response = self._llm.invoke(messages)
            raw_text = self._coerce_text(response)
            messages.append(AIMessage(content=raw_text))

            tool_calls = extract_tool_calls(raw_text) if self._note_tool else []
            visible_text = strip_tool_calls(raw_text).strip()

            if visible_text:
                visible_segments.append(visible_text)

            if not tool_calls:
                break

            tool_results: list[str] = []
            for tool_name, body in tool_calls:
                tool_results.append(self._execute_tool(tool_name, body))

            messages.append(
                HumanMessage(
                    content=(
                        "以下是工具调用结果，请基于结果继续完成任务。"
                        "不要重复已执行的 [TOOL_CALL:...] 指令。\n\n"
                        + "\n\n".join(tool_results)
                    )
                )
            )

        final_text = "\n\n".join(segment for segment in visible_segments if segment).strip()
        if self._keep_history:
            self._history = messages[1:]
        return final_text

    def _execute_tool(self, tool_name: str, body: str) -> str:
        if tool_name.strip().lower() != "note" or self._note_tool is None:
            return f"❌ Unsupported tool: {tool_name}"

        payload = parse_tool_payload_body(body)
        if not isinstance(payload, dict):
            return f"❌ Invalid note payload: {body}"

        result = self._note_tool.run(payload)
        if self._tool_call_listener is not None:
            self._tool_call_listener(
                {
                    "agent_name": self._name,
                    "tool_name": "note",
                    "raw_parameters": json.dumps(payload, ensure_ascii=False),
                    "parsed_parameters": payload,
                    "result": result,
                }
            )
        return result

    @staticmethod
    def _coerce_text(response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return str(content or "")

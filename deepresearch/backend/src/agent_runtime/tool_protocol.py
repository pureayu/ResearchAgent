"""Shared helpers for the custom [TOOL_CALL:...] text protocol."""

from __future__ import annotations

import json
import re
from typing import Any

TOOL_CALL_MARKER = "[TOOL_CALL:"


def strip_tool_calls(text: str) -> str:
    """Remove tool call markers from a model response."""

    if not text:
        return text

    pattern = re.compile(r"\[TOOL_CALL:[^\]]+\]")
    return pattern.sub("", text)


def find_matching_brace(text: str, start: int) -> int | None:
    """Return the matching closing brace index for a JSON object body."""

    depth = 0
    in_string = False
    escaped = False

    for idx in range(start, len(text)):
        char = text[idx]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return idx

    return None


def extract_tool_calls(text: str) -> list[tuple[str, str]]:
    """Extract tool name and body pairs from a text response."""

    calls: list[tuple[str, str]] = []
    cursor = 0

    while True:
        start = text.find(TOOL_CALL_MARKER, cursor)
        if start == -1:
            break

        tool_start = start + len(TOOL_CALL_MARKER)
        colon = text.find(":", tool_start)
        if colon == -1:
            break

        tool_name = text[tool_start:colon].strip()
        body_start = colon + 1
        if body_start >= len(text):
            break

        if text[body_start] == "{":
            body_end = find_matching_brace(text, body_start)
            if body_end is None:
                cursor = body_start
                continue
            body = text[body_start : body_end + 1]
            closing = text.find("]", body_end + 1)
            cursor = closing + 1 if closing != -1 else body_end + 1
        else:
            closing = text.find("]", body_start)
            if closing == -1:
                break
            body = text[body_start:closing]
            cursor = closing + 1

        calls.append((tool_name, body))

    return calls


def parse_tool_payload_body(body: str) -> dict[str, Any] | None:
    """Parse a TOOL_CALL payload body, tolerating quasi-JSON text."""

    try:
        payload = json.loads(body)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    normalized = re.sub(r"(?<!\\)\n", r"\\n", body)
    try:
        payload = json.loads(normalized)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    payload: dict[str, Any] = {}
    for key in ("action", "title", "note_type", "content", "note_id"):
        match = re.search(rf'"{key}"\s*:\s*"(?P<value>.*?)"', body, re.DOTALL)
        if match:
            payload[key] = match.group("value").replace("\\n", "\n").strip()

    task_id_match = re.search(r'"task_id"\s*:\s*(\d+)', body)
    if task_id_match:
        payload["task_id"] = int(task_id_match.group(1))

    tags_match = re.search(r'"tags"\s*:\s*\[(?P<value>.*?)\]', body, re.DOTALL)
    if tags_match:
        payload["tags"] = re.findall(r'"(.*?)"', tags_match.group("value"))

    if payload:
        return payload

    parts = [segment.strip() for segment in body.split(",") if segment.strip()]
    loose_payload: dict[str, Any] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        loose_payload[key.strip()] = value.strip().strip('"').strip("'")

    return loose_payload or None


def extract_note_id_from_text(text: str) -> str | None:
    """Extract a note identifier from tool output text."""

    if not text:
        return None

    match = re.search(r"ID:\s*([^\n]+)", text)
    if not match:
        return None
    return match.group(1).strip()

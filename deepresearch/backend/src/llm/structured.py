"""Structured-output runner built on top of LangChain chat models."""

from __future__ import annotations

import json
from typing import Generic, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StructuredOutputRunner(Generic[SchemaT]):
    """Invoke one role-specific model with a Pydantic response schema."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        system_prompt: str,
        schema: type[SchemaT],
        agent_name: str,
    ) -> None:
        self._schema = schema
        self._model = model
        self._system_prompt = system_prompt.strip()
        self._agent_name = agent_name
        self._runner = model.with_structured_output(schema)

    @property
    def agent_name(self) -> str:
        """Return the human-readable agent label."""

        return self._agent_name

    def invoke(self, prompt: str) -> SchemaT:
        """Run the model and return one validated schema instance."""

        messages = [
            SystemMessage(
                content=(
                    f"{self._system_prompt}\n\n"
                    "When JSON mode is required by the model provider, return only valid JSON matching the requested schema."
                )
            ),
            HumanMessage(content=prompt),
        ]
        result = self._invoke_with_fallbacks(messages)
        if isinstance(result, self._schema):
            return result
        if isinstance(result, BaseModel):
            return self._schema.model_validate(_normalize_schema_payload(result.model_dump()))
        return self._schema.model_validate(_normalize_schema_payload(result))

    def _invoke_with_fallbacks(self, messages: list[SystemMessage | HumanMessage]):
        """Invoke structured output with fallbacks for OpenAI-compatible APIs."""

        last_error: Exception | None = None
        for runner in self._structured_runners():
            try:
                return runner.invoke(messages)
            except Exception as exc:
                last_error = exc
                if not _should_try_next_method(exc):
                    raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("No structured output runner available")

    def _structured_runners(self):
        yield self._runner
        for method in ("function_calling", "json_mode"):
            try:
                yield self._model.with_structured_output(self._schema, method=method)
            except Exception:
                continue


def _should_try_next_method(exc: Exception) -> bool:
    message = str(exc).lower()
    fallback_markers = [
        "response_format",
        "json_schema",
        "unavailable",
        "unsupported",
        "invalid_request_error",
    ]
    return any(marker in message for marker in fallback_markers)


def _normalize_schema_payload(value):
    """Repair common structured-output provider mistakes before validation.

    Some OpenAI-compatible providers return list fields as serialized JSON strings,
    e.g. ``"[\"exp1\", \"exp2\"]"``. The Pydantic schema is still authoritative;
    this only normalizes values that are already valid JSON arrays/objects.
    """

    if isinstance(value, dict):
        return {key: _normalize_schema_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_schema_payload(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if (stripped.startswith("[") and stripped.endswith("]")) or (
            stripped.startswith("{") and stripped.endswith("}")
        ):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return value
            return _normalize_schema_payload(parsed)
    return value

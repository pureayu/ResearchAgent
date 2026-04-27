"""Chat model factory for LangChain-backed runtime components."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from config import Configuration


def build_chat_model(
    config: Configuration,
    *,
    overrides: dict[str, Any] | None = None,
) -> BaseChatModel:
    """Build a LangChain chat model from the current configuration."""

    provider = (config.llm_provider or "").strip().lower()
    resolved_model = config.resolved_model()
    if not resolved_model:
        raise ValueError("Missing model identifier for LangChain chat model")

    model_kwargs: dict[str, Any] = {
        "temperature": 0.0,
    }
    if overrides:
        model_kwargs.update(overrides)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=resolved_model,
            base_url=config.ollama_base_url,
            **model_kwargs,
        )

    if provider in {"lmstudio", "custom", "openai"}:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - dependency-dependent
            raise RuntimeError(
                "langchain-openai is required for lmstudio/custom/openai providers"
            ) from exc

        base_url = None
        api_key = config.llm_api_key
        if provider == "lmstudio":
            base_url = config.lmstudio_base_url
            api_key = api_key or "lm-studio"
        else:
            base_url = config.llm_base_url

        init_kwargs = {
            "model": resolved_model,
            **model_kwargs,
        }
        if base_url:
            init_kwargs["base_url"] = base_url
        if api_key:
            init_kwargs["api_key"] = api_key

        return ChatOpenAI(**init_kwargs)

    raise ValueError(f"Unsupported LangChain provider: {config.llm_provider}")

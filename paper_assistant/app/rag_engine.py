from collections.abc import Callable
from typing import Any

from app.config import Settings
from app.models import ProcessedDocument

try:
    from lightrag import LightRAG, QueryParam
    from lightrag.llm.openai import openai_complete_if_cache, openai_embed
    from lightrag.utils import wrap_embedding_func_with_attrs
except ImportError:  # pragma: no cover
    LightRAG = None  # type: ignore
    QueryParam = None  # type: ignore
    openai_complete_if_cache = None  # type: ignore
    openai_embed = None  # type: ignore
    wrap_embedding_func_with_attrs = None  # type: ignore


class LightRAGService:
    def __init__(self, settings: Settings):
        if LightRAG is None:
            raise RuntimeError("lightrag-hku is not installed. Run: pip install lightrag-hku")
        settings.require_llm()
        settings.require_embedding()
        self.settings = settings
        self.rag: Any | None = None

    async def __aenter__(self) -> "LightRAGService":
        llm_model_func = self._build_llm_model_func()
        embedding_func = self._build_embedding_func()
        self.rag = LightRAG(
            working_dir=str(self.settings.rag_working_dir),
            llm_model_func=llm_model_func,
            embedding_func=embedding_func,
            addon_params={"language": self.settings.response_language},
        )
        await self.rag.initialize_storages()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.rag is not None:
            await self.rag.finalize_storages()

    async def insert_document(self, document: ProcessedDocument) -> None:
        if self.rag is None:
            raise RuntimeError("LightRAG is not initialized")
        payload = (
            f"Document title: {document.title}\n"
            f"Source path: {document.filepath}\n\n"
            f"{document.text}"
        )
        try:
            await self.rag.ainsert([payload], ids=[document.id], file_paths=[document.filepath])
        except TypeError:
            await self.rag.ainsert([payload], ids=[document.id])

    async def query(
        self,
        question: str,
        mode: str,
        response_type: str = "Multiple Paragraphs",
        stream: bool = False,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        if self.rag is None:
            raise RuntimeError("LightRAG is not initialized")
        result = await self.rag.aquery(
            question,
            param=QueryParam(mode=mode, response_type=response_type, stream=stream),
        )
        if stream and hasattr(result, "__aiter__"):
            parts: list[str] = []
            async for chunk in result:
                text = str(chunk)
                parts.append(text)
                if on_chunk is not None:
                    on_chunk(text)
            return "".join(parts).strip()
        return str(result).strip()

    def _build_llm_model_func(self):
        async def llm_model_func(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, str]] | None = None,
            **kwargs,
        ):
            return await openai_complete_if_cache(
                self.settings.llm_model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url,
                **kwargs,
            )

        return llm_model_func

    def _build_embedding_func(self):
        @wrap_embedding_func_with_attrs(
            embedding_dim=self.settings.embedding_dim,
            max_token_size=self.settings.embedding_max_tokens,
            model_name=self.settings.embedding_model,
        )
        async def embedding_func(texts: list[str]):
            return await openai_embed.func(
                texts,
                model=self.settings.embedding_model,
                api_key=self.settings.effective_embedding_api_key,
                base_url=self.settings.effective_embedding_base_url,
            )

        return embedding_func

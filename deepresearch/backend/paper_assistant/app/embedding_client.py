from collections.abc import Iterable

import httpx
from openai import OpenAI

from app.config import Settings


class EmbeddingClient:
    def __init__(self, settings: Settings):
        settings.require_embedding()
        self.settings = settings
        http_client = httpx.Client(timeout=settings.llm_timeout, trust_env=False)
        self.client = OpenAI(
            api_key=settings.effective_embedding_api_key,
            base_url=settings.effective_embedding_base_url,
            timeout=settings.llm_timeout,
            http_client=http_client,
        )

    def embed_texts(self, texts: list[str], batch_size: int = 10) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = self.client.embeddings.create(
                model=self.settings.embedding_model,
                input=batch,
            )
            embeddings.extend([item.embedding for item in response.data])
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

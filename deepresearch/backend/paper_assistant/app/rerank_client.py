from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings


logger = logging.getLogger(__name__)


class DashScopeRerankClient:
    """Thin DashScope text-rerank client for qwen3-rerank."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.enabled = settings.rerank_enabled
        self._client: httpx.Client | None = None

        if self.enabled:
            self._client = httpx.Client(
                timeout=settings.rerank_timeout,
                trust_env=False,
            )

    def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list[tuple[int, float]]:
        if not self.enabled or self._client is None:
            return []
        if not query.strip() or not documents:
            return []

        resolved_top_n = max(1, min(top_n or len(documents), len(documents)))
        payload: dict[str, Any] = {
            "model": self.settings.rerank_model,
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": {
                "return_documents": False,
                "top_n": resolved_top_n,
            },
        }
        instruct = (self.settings.rerank_instruct or "").strip()
        if instruct:
            payload["input"]["instruct"] = instruct

        response = self._client.post(
            self.settings.rerank_base_url,
            headers={
                "Authorization": f"Bearer {self.settings.effective_rerank_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = self._extract_error_message(exc.response)
            raise RuntimeError(message) from exc

        data = response.json()
        results = (((data or {}).get("output") or {}).get("results") or [])
        reranked: list[tuple[int, float]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            raw_index = item.get("index")
            raw_score = item.get("relevance_score")
            if not isinstance(raw_index, int):
                continue
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                continue
            reranked.append((raw_index, score))
        return reranked

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except Exception:
            payload = None

        if isinstance(payload, dict):
            code = str(payload.get("code") or "").strip()
            message = str(payload.get("message") or "").strip()
            if code or message:
                return f"DashScope rerank failed: {code} {message}".strip()
        return f"DashScope rerank failed with status {response.status_code}"

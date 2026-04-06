import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
PAPER_ASSISTANT_ROOT = Path(__file__).resolve().parents[1] / "paper_assistant"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PAPER_ASSISTANT_ROOT) not in sys.path:
    sys.path.insert(0, str(PAPER_ASSISTANT_ROOT))

from app.config import Settings
from app.rerank_client import DashScopeRerankClient
from app.simple_vector_rag import IndexedChunk, RetrievalCandidate, SimpleVectorRAG


class FakeRerankClient:
    def __init__(self, results, *, enabled=True, should_raise=False):
        self.enabled = enabled
        self._results = results
        self._should_raise = should_raise

    def rerank(self, *, query, documents, top_n=None):
        del query, documents, top_n
        if self._should_raise:
            raise RuntimeError("rerank unavailable")
        return list(self._results)


class PaperAssistantRerankTests(unittest.TestCase):
    def test_dashscope_rerank_client_parses_response(self) -> None:
        settings = Settings(
            rerank_model="qwen3-rerank",
            rerank_api_key="test-key",
        )
        client = DashScopeRerankClient(settings)
        response = httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0.92},
                        {"index": 0, "relevance_score": 0.41},
                    ]
                }
            },
            request=httpx.Request("POST", settings.rerank_base_url),
        )

        with patch.object(client._client, "post", return_value=response):
            results = client.rerank(query="什么是文本排序模型", documents=["a", "b"], top_n=2)

        self.assertEqual(results, [(1, 0.92), (0, 0.41)])

    def test_model_rerank_reorders_heuristic_candidates(self) -> None:
        rag = SimpleVectorRAG.__new__(SimpleVectorRAG)
        rag.settings = SimpleNamespace(rerank_top_n=20, rerank_max_chars_per_doc=200)
        rag.rerank_client = FakeRerankClient([(1, 0.88), (0, 0.62)])

        candidates = [
            RetrievalCandidate(
                record=IndexedChunk(
                    chunk_uid="doc-a:0",
                    doc_id="doc-a",
                    chunk_id="0",
                    title="Doc A",
                    filepath="/tmp/a.pdf",
                    text="alpha",
                ),
                rerank_score=0.80,
            ),
            RetrievalCandidate(
                record=IndexedChunk(
                    chunk_uid="doc-b:0",
                    doc_id="doc-b",
                    chunk_id="0",
                    title="Doc B",
                    filepath="/tmp/b.pdf",
                    text="beta",
                ),
                rerank_score=0.70,
            ),
        ]

        reranked = rag._model_rerank_candidates("query", candidates)

        self.assertEqual([item.record.doc_id for item in reranked], ["doc-b", "doc-a"])
        self.assertAlmostEqual(reranked[0].model_score, 0.88, places=6)
        self.assertAlmostEqual(reranked[1].model_score, 0.62, places=6)

    def test_model_rerank_failure_falls_back_to_heuristic(self) -> None:
        rag = SimpleVectorRAG.__new__(SimpleVectorRAG)
        rag.settings = SimpleNamespace(rerank_top_n=20, rerank_max_chars_per_doc=200)
        rag.rerank_client = FakeRerankClient([], should_raise=True)

        candidates = [
            RetrievalCandidate(
                record=IndexedChunk(
                    chunk_uid="doc-a:0",
                    doc_id="doc-a",
                    chunk_id="0",
                    title="Doc A",
                    filepath="/tmp/a.pdf",
                    text="alpha",
                ),
                fusion_score=0.9,
                vector_score=0.8,
                bm25_score=0.2,
            ),
            RetrievalCandidate(
                record=IndexedChunk(
                    chunk_uid="doc-b:0",
                    doc_id="doc-b",
                    chunk_id="0",
                    title="Doc B",
                    filepath="/tmp/b.pdf",
                    text="beta",
                ),
                fusion_score=0.3,
                vector_score=0.2,
                bm25_score=0.8,
            ),
        ]

        reranked = rag._rerank_candidates("alpha", ["alpha"], candidates)

        self.assertEqual(len(reranked), 2)
        self.assertEqual(reranked[0].record.doc_id, "doc-a")


if __name__ == "__main__":
    unittest.main()

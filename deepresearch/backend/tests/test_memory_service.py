import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import Configuration
from services.memory import HIGH_SENSITIVITY
from services.memory import PROFILE_MEMORY_SCOPE
from services.memory import SESSION_MEMORY_SCOPE
from services.memory import MemoryService


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeCompletionResponse:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]


class FakeChatCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    def create(self, **_: object) -> FakeCompletionResponse:
        return FakeCompletionResponse(self._content)


class FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = FakeChatCompletions(content)


class FakeLLMClient:
    def __init__(self, content: str) -> None:
        self.chat = FakeChat(content)


class FakeResult:
    def __init__(self, *, one=None, rows=None) -> None:
        self._one = one
        self._rows = rows or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class RecordingConnection:
    def __init__(self, *, existing_fact=None) -> None:
        self.existing_fact = existing_fact
        self.statements: list[tuple[str, object]] = []
        self.committed = False

    def execute(self, sql: str, params=None) -> FakeResult:
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        if normalized.startswith("SELECT fact_id FROM semantic_facts"):
            return FakeResult(one=self.existing_fact)
        return FakeResult(rows=[])

    def commit(self) -> None:
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class StubMemoryService(MemoryService):
    def __init__(
        self,
        *,
        llm_content: str | None = None,
        embeddings: list[list[float]] | None = None,
        connection: RecordingConnection | None = None,
    ) -> None:
        self._config = Configuration(memory_database_url="postgresql://example/test")
        self._llm_client = FakeLLMClient(llm_content) if llm_content is not None else None
        self._llm_model = "test-llm" if llm_content is not None else None
        self._embedding_client = None
        self._embedding_model = "test-embedding" if embeddings is not None else None
        self._database_url = "postgresql://example/test"
        self._embeddings = embeddings or []
        self._connection = connection or RecordingConnection()

    def _connect(self) -> RecordingConnection:
        return self._connection

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._embeddings[: len(texts)]


class MemoryServiceTests(unittest.TestCase):
    def test_extract_profile_facts_uses_llm_and_defaults_to_profile_scope(self) -> None:
        service = StubMemoryService(
            llm_content="""
            {
              "facts": [
                {
                  "scope": "preference",
                  "subject": "answer_language",
                  "fact": "用户偏好中文回答",
                  "confidence": 0.9,
                  "stability_score": 0.95,
                  "sensitivity": "low"
                }
              ]
            }
            """,
        )

        facts = service.extract_profile_facts("请用中文回答我")

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["memory_scope"], PROFILE_MEMORY_SCOPE)
        self.assertEqual(facts[0]["fact"], "用户偏好中文回答")

    def test_prepare_semantic_fact_downgrades_unsafe_global_scope(self) -> None:
        service = StubMemoryService()

        prepared = service._prepare_semantic_fact(
            "褪黑素长期服用安全吗",
            {
                "subject": "sleep_supplement",
                "fact": "褪黑素存在副作用风险",
                "memory_scope": "global",
                "confidence": 0.98,
                "stability_score": 0.99,
                "sensitivity": HIGH_SENSITIVITY,
            },
            session_id="session-1",
        )

        self.assertEqual(prepared["memory_scope"], SESSION_MEMORY_SCOPE)

    def test_rerank_fallback_uses_similarity_confidence_and_recency(self) -> None:
        service = StubMemoryService()

        result = service._rerank_recalled_facts(
            "topic",
            session_candidates=[
                {
                    "fact_id": "older",
                    "fact": "old",
                    "similarity": 0.75,
                    "confidence": 0.70,
                    "stability_score": 0.60,
                    "last_verified_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                },
                {
                    "fact_id": "best",
                    "fact": "best",
                    "similarity": 0.91,
                    "confidence": 0.20,
                    "stability_score": 0.10,
                    "last_verified_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
                },
            ],
            profile_candidates=[],
            global_candidates=[],
        )

        self.assertEqual(
            [item["fact_id"] for item in result["session_facts"]],
            ["best", "older"],
        )

    def test_rerank_uses_model_selected_ids_when_available(self) -> None:
        service = StubMemoryService(
            llm_content="""
            {
              "session_fact_ids": ["session_2"],
              "profile_fact_ids": ["profile_1"],
              "global_fact_ids": []
            }
            """,
        )

        result = service._rerank_recalled_facts(
            "topic",
            session_candidates=[
                {"fact_id": "session_1", "fact": "s1", "similarity": 0.8},
                {"fact_id": "session_2", "fact": "s2", "similarity": 0.6},
            ],
            profile_candidates=[
                {"fact_id": "profile_1", "fact": "p1", "similarity": 0.4},
            ],
            global_candidates=[
                {"fact_id": "global_1", "fact": "g1", "similarity": 0.9},
            ],
        )

        self.assertEqual(
            [item["fact_id"] for item in result["session_facts"]],
            ["session_2"],
        )
        self.assertEqual(
            [item["fact_id"] for item in result["profile_facts"]],
            ["profile_1"],
        )
        self.assertEqual(result["global_facts"], [])

    def test_save_semantic_facts_updates_existing_fact_instead_of_inserting(self) -> None:
        connection = RecordingConnection(existing_fact={"fact_id": "existing_fact"})
        service = StubMemoryService(
            embeddings=[[0.1, 0.2, 0.3]],
            connection=connection,
        )

        service.save_semantic_facts(
            "run-1",
            "topic",
            [
                {
                    "subject": "answer_language",
                    "fact": "用户偏好中文回答",
                    "memory_scope": PROFILE_MEMORY_SCOPE,
                    "confidence": 0.88,
                    "stability_score": 0.9,
                    "sensitivity": "low",
                }
            ],
            session_id="session-1",
        )

        executed_sql = [sql for sql, _ in connection.statements]
        self.assertTrue(any(sql.startswith("UPDATE semantic_facts SET") for sql in executed_sql))
        self.assertFalse(any(sql.startswith("INSERT INTO semantic_facts") for sql in executed_sql))
        self.assertTrue(connection.committed)


if __name__ == "__main__":
    unittest.main()

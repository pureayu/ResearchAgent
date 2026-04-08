import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import Configuration
from models import TodoItem
from services.memory import HIGH_SENSITIVITY
from services.memory import PROFILE_MEMORY_SCOPE
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
    def __init__(
        self,
        *,
        existing_fact=None,
        query_rows: dict[str, list[dict[str, object]]] | None = None,
        query_one: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.existing_fact = existing_fact
        self.query_rows = query_rows or {}
        self.query_one = query_one or {}
        self.statements: list[tuple[str, object]] = []
        self.committed = False

    def execute(self, sql: str, params=None) -> FakeResult:
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        if normalized.startswith("SELECT fact_id FROM semantic_facts"):
            return FakeResult(one=self.existing_fact)
        for prefix, row in self.query_one.items():
            if normalized.startswith(prefix):
                return FakeResult(one=row)
        for prefix, rows in self.query_rows.items():
            if normalized.startswith(prefix):
                return FakeResult(rows=rows)
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


class ContextStubMemoryService(StubMemoryService):
    def __init__(
        self,
        *,
        session_candidates: list[dict[str, object]] | None = None,
        profile_candidates: list[dict[str, object]] | None = None,
        global_candidates: list[dict[str, object]] | None = None,
        connection: RecordingConnection | None = None,
    ) -> None:
        super().__init__(connection=connection)
        self._session_candidates = session_candidates or []
        self._profile_candidates = profile_candidates or []
        self._global_candidates = global_candidates or []

    def _search_semantic_facts(
        self,
        connection,
        topic: str,
        *,
        run_ids=None,
        memory_scope=None,
        limit=5,
    ):
        del connection, topic, run_ids, limit
        if memory_scope == PROFILE_MEMORY_SCOPE:
            return list(self._profile_candidates)
        return list(self._global_candidates)

    def _rerank_recalled_facts(
        self,
        topic: str,
        *,
        profile_candidates,
        global_candidates,
        limit=5,
    ):
        del topic, limit
        return {
            "profile_facts": list(profile_candidates),
            "global_facts": list(global_candidates),
        }


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

        self.assertEqual(prepared["memory_scope"], PROFILE_MEMORY_SCOPE)

    def test_rerank_fallback_uses_similarity_confidence_and_recency(self) -> None:
        service = StubMemoryService()

        result = service._rerank_recalled_facts(
            "topic",
            profile_candidates=[
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
            global_candidates=[],
        )

        self.assertEqual(
            [item["fact_id"] for item in result["profile_facts"]],
            ["best", "older"],
        )

    def test_rerank_uses_model_selected_ids_when_available(self) -> None:
        service = StubMemoryService(
            llm_content="""
            {
              "profile_fact_ids": ["profile_1"],
              "global_fact_ids": []
            }
            """,
        )

        result = service._rerank_recalled_facts(
            "topic",
            profile_candidates=[
                {"fact_id": "profile_1", "fact": "p1", "similarity": 0.4},
            ],
            global_candidates=[
                {"fact_id": "global_1", "fact": "g1", "similarity": 0.9},
            ],
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

    def test_load_relevant_context_excludes_recent_tasks_from_default_context(self) -> None:
        connection = RecordingConnection(
            query_one={
                "SELECT working_memory_summary FROM research_sessions": {
                    "working_memory_summary": "历史摘要"
                }
            },
            query_rows={
                "SELECT run_id, session_id, topic, started_at, finished_at, final_report, task_count FROM research_runs": [
                    {
                        "run_id": "run-1",
                        "session_id": "session-1",
                        "topic": "端侧推理调研",
                        "started_at": "2026-04-01T10:00:00+00:00",
                        "finished_at": "2026-04-01T11:00:00+00:00",
                        "final_report": "报告正文",
                        "task_count": 3,
                    }
                ],
                "SELECT run_id, user_query, assistant_response, response_mode, created_at FROM session_turns": [
                    {
                        "run_id": "run-1",
                        "user_query": "之前问过端侧推理的方向",
                        "assistant_response": "回答了主要技术方向。",
                        "response_mode": "deep_research",
                        "created_at": "2026-04-01T12:00:00+00:00",
                    }
                ],
            }
        )
        service = ContextStubMemoryService(
            profile_candidates=[{"fact_id": "pf-1", "fact": "用户偏好中文"}],
            global_candidates=[{"fact_id": "gf-1", "fact": "全局稳定知识"}],
            connection=connection,
        )

        context = service.load_relevant_context("session-1", "端侧推理")

        self.assertNotIn("session_runs", context)
        self.assertEqual(context["working_memory_summary"], "历史摘要")
        self.assertEqual(len(context["recent_turns"]), 1)
        self.assertNotIn("recent_tasks", context)
        executed_sql = [sql for sql, _ in connection.statements]
        self.assertFalse(any("FROM task_memories" in sql for sql in executed_sql))
        self.assertFalse(any("FROM research_runs" in sql for sql in executed_sql))

    def test_save_session_turn_inserts_turn_record(self) -> None:
        connection = RecordingConnection()
        service = StubMemoryService(connection=connection)

        from models import SummaryState

        state = SummaryState(
            session_id="session-1",
            run_id="run-1",
            research_topic="端侧推理方向",
            response_mode="deep_research",
        )

        service.save_session_turn(state, "这里是本轮最终回答")

        executed_sql = [sql for sql, _ in connection.statements]
        self.assertTrue(any(sql.startswith("INSERT INTO session_turns") for sql in executed_sql))
        self.assertTrue(connection.committed)

    def test_load_recent_task_logs_uses_dedicated_query(self) -> None:
        connection = RecordingConnection(
            query_rows={
                "SELECT tm.run_id, tm.task_id, tm.title, tm.status, tm.summary, tm.created_at FROM task_memories tm JOIN research_runs rr ON rr.run_id = tm.run_id WHERE rr.session_id = %s": [
                    {
                        "run_id": "run-1",
                        "task_id": 101,
                        "title": "分析端到端延迟",
                        "status": "completed",
                        "summary": "定位检索链路延迟瓶颈",
                        "created_at": "2026-04-01T11:00:00+00:00",
                    }
                ]
            }
        )
        service = StubMemoryService(connection=connection)

        task_logs = service.load_recent_task_logs(
            "session-1",
            exclude_run_id="run-2",
            limit=5,
        )

        self.assertEqual(len(task_logs), 1)
        self.assertEqual(task_logs[0]["task_id"], 101)
        executed_sql = [sql for sql, _ in connection.statements]
        self.assertTrue(any("FROM task_memories tm JOIN research_runs rr" in sql for sql in executed_sql))

    def test_save_task_log_prunes_task_logs_per_session(self) -> None:
        connection = RecordingConnection(
            query_one={
                "SELECT session_id FROM research_runs WHERE run_id = %s": {
                    "session_id": "session-1"
                }
            }
        )
        service = StubMemoryService(connection=connection)
        task = TodoItem(
            id=1,
            title="基础背景梳理",
            intent="收集端侧大模型背景",
            query="端侧大模型 推理",
            status="completed",
        )

        service.save_task_log("run-1", task)

        executed_sql = [sql for sql, _ in connection.statements]
        self.assertTrue(any(sql.startswith("INSERT INTO task_memories") for sql in executed_sql))
        self.assertTrue(
            any(sql.startswith("DELETE FROM task_memories WHERE id IN") for sql in executed_sql)
        )
        self.assertTrue(connection.committed)


if __name__ == "__main__":
    unittest.main()

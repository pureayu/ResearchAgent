"""Structured memory service skeleton for the deep research workflow."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from openai import OpenAI

from config import Configuration
from models import SummaryState, TodoItem
from prompts import semantic_fact_extraction_instructions


class MemoryService:
    """Facade between the agent runtime and structured long-term memory storage.

    Phase 1 only defines the interface and ownership boundary:
    - start a run and return a stable run_id
    - persist task-level execution records
    - persist the final report for a run
    - later expose recall and semantic consolidation APIs
    """

    def __init__(self, config: Configuration) -> None:
        self._config = config
        self._db_path = Path(config.memory_db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._llm_client, self._llm_model = self._build_llm_client()
        self._embedding_client, self._embedding_model = self._build_embedding_client()
        self._init_db()

    @property
    def db_path(self) -> Path:
        """Return the configured SQLite path for structured memory."""

        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection for memory operations."""

        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _build_llm_client(self) -> tuple[OpenAI | None, str | None]:
        """Create an OpenAI-compatible client for semantic fact extraction."""

        provider = (self._config.llm_provider or "").strip()
        model = self._config.resolved_model()
        base_url = self._config.llm_base_url
        api_key = self._config.llm_api_key

        if provider == "ollama":
            base_url = self._config.sanitized_ollama_url()
            api_key = api_key or "ollama"
        elif provider == "lmstudio":
            base_url = self._config.lmstudio_base_url

        if not model or not base_url:
            return None, model

        http_client = httpx.Client(timeout=120.0, trust_env=False)
        return OpenAI(
            api_key=api_key or "dummy",
            base_url=base_url,
            timeout=120.0,
            http_client=http_client,
        ), model

    def _build_embedding_client(self) -> tuple[OpenAI | None, str | None]:
        """Create an OpenAI-compatible client for embedding-based retrieval."""

        model = self._config.resolved_embedding_model()
        base_url = self._config.embedding_base_url or self._config.llm_base_url
        api_key = self._config.embedding_api_key or self._config.llm_api_key

        if not model or not base_url:
            return None, model

        http_client = httpx.Client(timeout=120.0, trust_env=False)
        return OpenAI(
            api_key=api_key or "dummy",
            base_url=base_url,
            timeout=120.0,
            http_client=http_client,
        ), model

    def _init_db(self) -> None:
        """Create the minimal Phase 1 tables if they do not exist yet."""

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_sessions (
                    session_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_id TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    final_report TEXT,
                    report_note_id TEXT,
                    task_count INTEGER DEFAULT 0
                )
                """
            )
            existing_run_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(research_runs)")
            }
            if "session_id" not in existing_run_columns:
                connection.execute(
                    "ALTER TABLE research_runs ADD COLUMN session_id TEXT"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    task_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    query TEXT NOT NULL,
                    latest_query TEXT,
                    status TEXT NOT NULL,
                    search_backend TEXT,
                    attempt_count INTEGER DEFAULT 0,
                    evidence_count INTEGER DEFAULT 0,
                    top_score REAL DEFAULT 0.0,
                    summary TEXT,
                    sources_summary TEXT,
                    note_id TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_facts (
                    fact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    scope TEXT,
                    subject TEXT,
                    fact TEXT NOT NULL,
                    embedding TEXT,
                    confidence REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    last_verified_at TEXT NOT NULL
                )
                """
            )
            existing_fact_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(semantic_facts)")
            }
            if "embedding" not in existing_fact_columns:
                connection.execute(
                    "ALTER TABLE semantic_facts ADD COLUMN embedding TEXT"
                )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS semantic_facts_fts
                    USING fts5(
                        fact_id UNINDEXED,
                        topic,
                        subject,
                        fact,
                        tokenize='trigram'
                    )
                    """
                )
            except sqlite3.OperationalError:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS semantic_facts_fts
                    USING fts5(
                        fact_id UNINDEXED,
                        topic,
                        subject,
                        fact
                    )
                    """
                )
            connection.commit()

    def start_run(self, session_id: str, topic: str) -> str:
        """Create a structured run record and return its run_id."""

        run_id = uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_runs (
                    run_id,
                    session_id,
                    topic,
                    started_at
                ) VALUES (?, ?, ?, ?)
                """,
                (run_id, session_id, topic, started_at),
            )
            connection.execute(
                """
                UPDATE research_sessions
                SET updated_at = ?,
                    last_run_id = ?
                WHERE session_id = ?
                """,
                (started_at, run_id, session_id),
            )
            connection.commit()

        return run_id

    def save_task_memory(self, run_id: str, task: TodoItem) -> None:
        """Persist a task-level episodic memory record for the current run."""

        created_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO task_memories (
                    run_id,
                    task_id,
                    title,
                    intent,
                    query,
                    latest_query,
                    status,
                    search_backend,
                    attempt_count,
                    evidence_count,
                    top_score,
                    summary,
                    sources_summary,
                    note_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task.id,
                    task.title,
                    task.intent,
                    task.query,
                    task.latest_query,
                    task.status,
                    task.search_backend,
                    task.attempt_count,
                    task.evidence_count,
                    task.top_score,
                    task.summary,
                    task.sources_summary,
                    task.note_id,
                    created_at,
                ),
            )
            connection.commit()

    def save_report_memory(self, run_id: str, state: SummaryState, report: str) -> None:
        """Persist the final report and run-level summary information."""

        finished_at = datetime.now(timezone.utc).isoformat()
        task_count = len(state.todo_items)

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE research_runs
                SET finished_at = ?,
                    final_report = ?,
                    report_note_id = ?,
                    task_count = ?
                WHERE run_id = ?
                """,
                (
                    finished_at,
                    report,
                    state.report_note_id,
                    task_count,
                    run_id,
                ),
            )
            connection.commit()

    def get_or_create_session(self, session_id: str | None, topic: str) -> str:
        """Return an existing session_id or create a new research session."""

        now = datetime.now(timezone.utc).isoformat()
        resolved_session_id = session_id or uuid4().hex

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT session_id
                FROM research_sessions
                WHERE session_id = ?
                """,
                (resolved_session_id,),
            ).fetchone()

            if existing:
                connection.execute(
                    """
                    UPDATE research_sessions
                    SET updated_at = ?
                    WHERE session_id = ?
                    """,
                    (now, resolved_session_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO research_sessions (
                        session_id,
                        topic,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (resolved_session_id, topic, now, now),
                )
            connection.commit()

        return resolved_session_id


    def load_relevant_context(self, session_id: str | None, topic: str) -> dict[str, Any]:
        """Load recalled memory context for a new topic.

        Intended future shape:
        - related_runs
        - related_tasks
        - semantic_facts
        """
        session_runs: list[dict[str, Any]] = []
        recent_tasks: list[dict[str, Any]] = []
        semantic_facts: list[dict[str, Any]] = []

        if not session_id:
            return {
                "session_runs": [],
                "recent_tasks": [],
                "semantic_facts": [],
            }

        with self._connect() as connection:
            run_rows = connection.execute(
                """
                SELECT run_id, session_id, topic, started_at, finished_at, final_report, task_count
                FROM research_runs
                WHERE session_id = ?
                ORDER BY started_at DESC
                LIMIT 3
                """,
                (session_id,),
            ).fetchall()

            for row in run_rows:
                final_report = row["final_report"] or ""
                session_runs.append(
                    {
                        "run_id": row["run_id"],
                        "session_id": row["session_id"],
                        "topic": row["topic"],
                        "started_at": row["started_at"],
                        "finished_at": row["finished_at"],
                        "task_count": row["task_count"],
                        "report_excerpt": final_report[:600],
                    }
                )

            run_ids = [row["run_id"] for row in run_rows]
            if run_ids:
                placeholders = ", ".join("?" for _ in run_ids)
                task_rows = connection.execute(
                    f"""
                    SELECT run_id, task_id, title, status, summary, created_at
                    FROM task_memories
                    WHERE run_id IN ({placeholders})
                    ORDER BY created_at DESC
                    LIMIT 8
                    """,
                    run_ids,
                ).fetchall()
            else:
                task_rows = []

            for row in task_rows:
                recent_tasks.append(
                    {
                        "run_id": row["run_id"],
                        "task_id": row["task_id"],
                        "title": row["title"],
                        "status": row["status"],
                        "summary": row["summary"],
                        "created_at": row["created_at"],
                    }
                )

            semantic_facts = self._search_semantic_facts(connection, topic)

        return {
            "session_runs": session_runs,
            "recent_tasks": recent_tasks,
            "semantic_facts": semantic_facts,
        }

    def _search_semantic_facts(
        self, connection: sqlite3.Connection, topic: str
    ) -> list[dict[str, Any]]:
        """Retrieve the most relevant semantic facts for the current topic."""

        embedding_results = self._search_semantic_facts_by_embedding(connection, topic)
        if embedding_results:
            return embedding_results

        query = self._build_fts_query(topic)
        if not query:
            return []

        try:
            rows = connection.execute(
                """
                SELECT sf.fact_id, sf.topic, sf.scope, sf.subject, sf.fact, sf.confidence,
                       sf.last_verified_at
                FROM semantic_facts_fts fts
                JOIN semantic_facts sf ON sf.fact_id = fts.fact_id
                WHERE semantic_facts_fts MATCH ?
                ORDER BY bm25(semantic_facts_fts), sf.last_verified_at DESC
                LIMIT 5
                """,
                (query,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        return [
            {
                "fact_id": row["fact_id"],
                "topic": row["topic"],
                "scope": row["scope"],
                "subject": row["subject"],
                "fact": row["fact"],
                "confidence": row["confidence"],
                "last_verified_at": row["last_verified_at"],
            }
            for row in rows
        ]

    def _search_semantic_facts_by_embedding(
        self, connection: sqlite3.Connection, topic: str
    ) -> list[dict[str, Any]]:
        """Retrieve semantic facts by vector similarity when embeddings are available."""

        query_embedding = self._embed_query(topic)
        if not query_embedding:
            return []

        rows = connection.execute(
            """
            SELECT fact_id, topic, scope, subject, fact, confidence, last_verified_at, embedding
            FROM semantic_facts
            WHERE embedding IS NOT NULL
            """
        ).fetchall()

        scored_rows: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            embedding_text = row["embedding"]
            if not embedding_text:
                continue
            try:
                candidate_embedding = json.loads(embedding_text)
            except json.JSONDecodeError:
                continue

            score = self._cosine_similarity(query_embedding, candidate_embedding)
            if score <= 0:
                continue
            scored_rows.append((score, row))

        scored_rows.sort(key=lambda item: (item[0], item[1]["confidence"]), reverse=True)

        return [
            {
                "fact_id": row["fact_id"],
                "topic": row["topic"],
                "scope": row["scope"],
                "subject": row["subject"],
                "fact": row["fact"],
                "confidence": row["confidence"],
                "last_verified_at": row["last_verified_at"],
                "similarity": score,
            }
            for score, row in scored_rows[:5]
        ]

    def _build_fts_query(self, text: str) -> str:
        """Build a simple MATCH query from the current topic."""

        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9_-]{1,}", text or "")
        deduped: list[str] = []
        for token in tokens:
            normalized = token.strip()
            if not normalized or normalized in deduped:
                continue
            deduped.append(normalized)
            if len(deduped) >= 6:
                break

        if not deduped:
            return ""

        return " OR ".join(f'"{token}"' for token in deduped)

    def extract_semantic_facts(self, report: str, topic: str) -> list[dict[str, Any]]:
        """Extract stable semantic facts from the final report."""

        if not report.strip() or self._llm_client is None or not self._llm_model:
            return []

        messages = [
            {"role": "system", "content": semantic_fact_extraction_instructions.strip()},
            {
                "role": "user",
                "content": f"研究主题：{topic}\n\n研究报告：\n{report}",
            },
        ]

        try:
            response = self._llm_client.chat.completions.create(
                model=self._llm_model,
                messages=messages,
                temperature=0.0,
            )
        except Exception:
            return []

        content = (response.choices[0].message.content or "").strip()
        payload = self._extract_json_payload(content)
        candidate_facts = payload.get("facts") if isinstance(payload, dict) else None
        if not isinstance(candidate_facts, list):
            return []

        facts: list[dict[str, Any]] = []
        for item in candidate_facts[:8]:
            if not isinstance(item, dict):
                continue
            fact_text = str(item.get("fact") or "").strip()
            if not fact_text:
                continue
            facts.append(
                {
                    "scope": str(item.get("scope") or "").strip() or None,
                    "subject": str(item.get("subject") or "").strip() or topic,
                    "fact": fact_text,
                    "confidence": float(item.get("confidence", 0.0) or 0.0),
                }
            )

        return facts

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts for semantic retrieval."""

        if not texts or self._embedding_client is None or not self._embedding_model:
            return []

        try:
            response = self._embedding_client.embeddings.create(
                model=self._embedding_model,
                input=texts,
            )
        except Exception:
            return []

        return [item.embedding for item in response.data]

    def _embed_query(self, text: str) -> list[float] | None:
        """Embed a single query string."""

        embeddings = self._embed_texts([text])
        return embeddings[0] if embeddings else None

    def _cosine_similarity(
        self, left: list[float], right: list[float]
    ) -> float:
        """Compute cosine similarity between two vectors."""

        if not left or not right or len(left) != len(right):
            return 0.0

        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0

        return numerator / (left_norm * right_norm)

    def save_semantic_facts(
        self, run_id: str, topic: str, facts: list[dict[str, Any]]
    ) -> None:
        """Persist semantic facts and sync the FTS index."""

        if not facts:
            return

        now = datetime.now(timezone.utc).isoformat()
        embedding_inputs = [
            f"{topic}\n{item.get('subject') or ''}\n{item.get('fact') or ''}".strip()
            for item in facts
        ]
        embeddings = self._embed_texts(embedding_inputs)
        with self._connect() as connection:
            for idx, item in enumerate(facts):
                fact_id = uuid4().hex
                embedding_json = None
                if idx < len(embeddings):
                    embedding_json = json.dumps(embeddings[idx])
                connection.execute(
                    """
                    INSERT INTO semantic_facts (
                        fact_id,
                        run_id,
                        topic,
                        scope,
                        subject,
                        fact,
                        embedding,
                        confidence,
                        created_at,
                        last_verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact_id,
                        run_id,
                        topic,
                        item.get("scope"),
                        item.get("subject"),
                        item.get("fact"),
                        embedding_json,
                        float(item.get("confidence", 0.0) or 0.0),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO semantic_facts_fts (
                        fact_id,
                        topic,
                        subject,
                        fact
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        fact_id,
                        topic,
                        item.get("subject") or "",
                        item.get("fact") or "",
                    ),
                )
            connection.commit()

    def consolidate_semantic_facts(
        self, run_id: str, topic: str, report: str
    ) -> list[dict[str, Any]]:
        """Extract and persist semantic facts derived from a final report."""

        facts = self.extract_semantic_facts(report, topic)
        self.save_semantic_facts(run_id, topic, facts)
        return facts

    def _extract_json_payload(self, text: str) -> dict[str, Any] | list | None:
        """Best-effort extraction of a JSON object or array from model output."""

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None

        return None

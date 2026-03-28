"""Structured memory service skeleton for the deep research workflow."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import Configuration
from models import SummaryState, TodoItem


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

        return {
            "session_runs": session_runs,
            "recent_tasks": recent_tasks,
            "semantic_facts": [],
        }

    def consolidate_semantic_facts(
        self,
        run_id: str,
        topic: str,
        report: str,
    ) -> list[dict[str, Any]]:
        """Extract reusable long-term facts from a completed run.

        This belongs to a later phase after episodic memory is stable.
        """

        raise NotImplementedError(
            "Semantic consolidation is deferred and not implemented yet."
        )

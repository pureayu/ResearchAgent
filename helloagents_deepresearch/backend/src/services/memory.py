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
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    final_report TEXT,
                    report_note_id TEXT,
                    task_count INTEGER DEFAULT 0
                )
                """
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

    def start_run(self, topic: str) -> str:
        """Create a structured run record and return its run_id."""

        run_id = uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_runs (
                    run_id,
                    topic,
                    started_at
                ) VALUES (?, ?, ?)
                """,
                (run_id, topic, started_at),
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

    def load_relevant_context(self, topic: str) -> dict[str, Any]:
        """Load recalled memory context for a new topic.

        Intended future shape:
        - related_runs
        - related_tasks
        - semantic_facts
        """

        raise NotImplementedError(
            "Recall is deferred: load_relevant_context is not implemented yet."
        )

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

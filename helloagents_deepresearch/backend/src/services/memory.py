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

SESSION_MEMORY_SCOPE = "session"
GLOBAL_MEMORY_SCOPE = "global"
PROFILE_MEMORY_SCOPE = "profile"
LOW_SENSITIVITY = "low"
MEDIUM_SENSITIVITY = "medium"
HIGH_SENSITIVITY = "high"

GLOBAL_PROMOTION_CONFIDENCE = 0.85
GLOBAL_PROMOTION_STABILITY = 0.80

MEDICAL_MEMORY_KEYWORDS = {
    "药",
    "药品",
    "药物",
    "用药",
    "服药",
    "褪黑素",
    "副作用",
    "禁忌",
    "失眠",
    "睡眠",
    "智齿",
    "拔牙",
    "口腔",
    "症状",
    "疾病",
    "治疗",
    "医疗",
}

PROFILE_RULES = {
    "goal:weight_loss": {
        "patterns": (r"减肥", r"减脂", r"瘦身", r"控卡", r"控制热量", r"卡路里"),
        "fact": "用户近期目标是减肥或控制热量摄入",
        "kind": "goal",
        "sensitivity": LOW_SENSITIVITY,
        "expansions": ("减肥", "减脂", "控卡", "热量", "卡路里", "饮食", "食物"),
    },
    "goal:sleep_improvement": {
        "patterns": (r"失眠", r"睡不好", r"入睡", r"睡眠", r"睡不着"),
        "fact": "用户近期目标是改善睡眠质量",
        "kind": "goal",
        "sensitivity": MEDIUM_SENSITIVITY,
        "expansions": ("睡眠", "失眠", "褪黑素", "助眠", "入睡"),
    },
    "goal:muscle_gain": {
        "patterns": (r"增肌", r"长肌肉", r"练肌肉"),
        "fact": "用户近期目标是增肌或提升训练效果",
        "kind": "goal",
        "sensitivity": LOW_SENSITIVITY,
        "expansions": ("增肌", "蛋白", "训练", "热量", "营养"),
    },
    "goal:exam_preparation": {
        "patterns": (r"备考", r"考试", r"刷题", r"复习"),
        "fact": "用户近期目标是备考或提升学习表现",
        "kind": "goal",
        "sensitivity": LOW_SENSITIVITY,
        "expansions": ("备考", "学习", "记忆", "专注", "复习"),
    },
    "constraint:side_effect_sensitive": {
        "patterns": (r"怕副作用", r"副作用.*敏感", r"不想有副作用", r"担心副作用"),
        "fact": "用户对副作用较敏感，希望避免高风险方案",
        "kind": "constraint",
        "sensitivity": MEDIUM_SENSITIVITY,
        "expansions": ("副作用", "风险", "安全性", "耐受性"),
    },
    "constraint:budget_limited": {
        "patterns": (r"预算有限", r"便宜", r"省钱", r"花费少"),
        "fact": "用户存在预算限制，希望优先考虑成本较低的方案",
        "kind": "constraint",
        "sensitivity": LOW_SENSITIVITY,
        "expansions": ("预算", "成本", "便宜", "花费"),
    },
    "preference:concise_answer": {
        "patterns": (r"简洁", r"直接一点", r"先给结论", r"一句话"),
        "fact": "用户偏好简洁直接、先给结论的回答风格",
        "kind": "preference",
        "sensitivity": LOW_SENSITIVITY,
        "expansions": ("简洁", "结论", "直接"),
    },
    "preference:chinese_answer": {
        "patterns": (r"中文回答", r"用中文", r"中文"),
        "fact": "用户偏好中文回答",
        "kind": "preference",
        "sensitivity": LOW_SENSITIVITY,
        "expansions": ("中文",),
    },
    "interest:drug_safety": {
        "patterns": (r"药品", r"药物", r"用药", r"褪黑素"),
        "fact": "用户近期持续关注药品、用药或睡眠相关问题",
        "kind": "interest",
        "sensitivity": MEDIUM_SENSITIVITY,
        "expansions": ("药品", "药物", "用药", "褪黑素", "副作用", "睡眠"),
    },
    "interest:oral_health": {
        "patterns": (r"智齿", r"牙齿", r"口腔", r"拔牙"),
        "fact": "用户近期持续关注口腔与牙齿健康问题",
        "kind": "interest",
        "sensitivity": MEDIUM_SENSITIVITY,
        "expansions": ("口腔", "牙齿", "智齿", "拔牙"),
    },
    "interest:edge_inference": {
        "patterns": (r"端侧", r"推理", r"NPU", r"部署", r"大模型"),
        "fact": "用户持续关注端侧推理和模型部署相关主题",
        "kind": "interest",
        "sensitivity": LOW_SENSITIVITY,
        "expansions": ("端侧", "推理", "部署", "NPU", "延迟"),
    },
}


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
                    round_id INTEGER DEFAULT 1,
                    origin TEXT DEFAULT 'planner',
                    parent_task_id INTEGER,
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
                    memory_scope TEXT NOT NULL DEFAULT 'session',
                    stability_score REAL DEFAULT 0.0,
                    sensitivity TEXT NOT NULL DEFAULT 'medium',
                    source_session_id TEXT,
                    confidence REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    last_verified_at TEXT NOT NULL
                )
                """
            )
            existing_task_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(task_memories)")
            }
            if "round_id" not in existing_task_columns:
                connection.execute(
                    "ALTER TABLE task_memories ADD COLUMN round_id INTEGER DEFAULT 1"
                )
            if "origin" not in existing_task_columns:
                connection.execute(
                    "ALTER TABLE task_memories ADD COLUMN origin TEXT DEFAULT 'planner'"
                )
            if "parent_task_id" not in existing_task_columns:
                connection.execute(
                    "ALTER TABLE task_memories ADD COLUMN parent_task_id INTEGER"
                )
            existing_fact_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(semantic_facts)")
            }
            if "embedding" not in existing_fact_columns:
                connection.execute(
                    "ALTER TABLE semantic_facts ADD COLUMN embedding TEXT"
                )
            if "memory_scope" not in existing_fact_columns:
                connection.execute(
                    "ALTER TABLE semantic_facts ADD COLUMN memory_scope TEXT NOT NULL DEFAULT 'session'"
                )
            if "stability_score" not in existing_fact_columns:
                connection.execute(
                    "ALTER TABLE semantic_facts ADD COLUMN stability_score REAL DEFAULT 0.0"
                )
            if "sensitivity" not in existing_fact_columns:
                connection.execute(
                    "ALTER TABLE semantic_facts ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'medium'"
                )
            if "source_session_id" not in existing_fact_columns:
                connection.execute(
                    "ALTER TABLE semantic_facts ADD COLUMN source_session_id TEXT"
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
            connection.execute(
                """
                UPDATE semantic_facts
                SET memory_scope = ?
                WHERE memory_scope IS NULL OR memory_scope = ''
                """,
                (SESSION_MEMORY_SCOPE,),
            )
            connection.execute(
                """
                UPDATE semantic_facts
                SET stability_score = COALESCE(stability_score, confidence, 0.0)
                WHERE stability_score IS NULL OR stability_score = 0.0
                """,
            )
            connection.execute(
                """
                UPDATE semantic_facts
                SET sensitivity = ?
                WHERE sensitivity IS NULL OR sensitivity = ''
                """,
                (MEDIUM_SENSITIVITY,),
            )
            connection.execute(
                """
                UPDATE semantic_facts
                SET source_session_id = (
                    SELECT rr.session_id
                    FROM research_runs rr
                    WHERE rr.run_id = semantic_facts.run_id
                )
                WHERE source_session_id IS NULL OR source_session_id = ''
                """,
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
                    round_id,
                    origin,
                    parent_task_id,
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task.id,
                    task.title,
                    task.intent,
                    task.query,
                    task.round_id,
                    task.origin,
                    task.parent_task_id,
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

    def capture_profile_memory(
        self,
        run_id: str,
        session_id: str,
        topic: str,
    ) -> list[dict[str, Any]]:
        """Extract and persist lightweight profile facts from the raw user topic."""

        facts = self.extract_profile_facts(topic)
        self.save_semantic_facts(
            run_id,
            topic,
            facts,
            session_id=session_id,
        )
        return facts

    def extract_profile_facts(self, topic: str) -> list[dict[str, Any]]:
        """Derive lightweight profile/user-goal facts from the user's raw query."""

        if not topic.strip():
            return []

        facts: list[dict[str, Any]] = []
        seen_subjects: set[str] = set()

        for subject, rule in PROFILE_RULES.items():
            if any(re.search(pattern, topic, flags=re.IGNORECASE) for pattern in rule["patterns"]):
                if subject in seen_subjects:
                    continue
                facts.append(
                    {
                        "scope": rule["kind"],
                        "subject": subject,
                        "fact": rule["fact"],
                        "confidence": 0.92,
                        "stability_score": 0.90,
                        "sensitivity": rule["sensitivity"],
                        "memory_scope": PROFILE_MEMORY_SCOPE,
                    }
                )
                seen_subjects.add(subject)
            if len(facts) >= 3:
                break

        return facts


    def load_relevant_context(
        self,
        session_id: str | None,
        topic: str,
        *,
        exclude_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Load recalled memory context for a new topic.

        Current shape:
        - session_runs
        - recent_tasks
        - session_facts
        - profile_facts
        - global_facts
        """
        session_runs: list[dict[str, Any]] = []
        recent_tasks: list[dict[str, Any]] = []
        session_facts: list[dict[str, Any]] = []
        profile_facts: list[dict[str, Any]] = []
        global_facts: list[dict[str, Any]] = []

        with self._connect() as connection:
            profile_candidates = self._load_profile_candidates(connection)
            expanded_topic = self._build_memory_search_text(topic, profile_candidates=profile_candidates)

            run_rows: list[sqlite3.Row] = []
            if session_id:
                sql = """
                    SELECT run_id, session_id, topic, started_at, finished_at, final_report, task_count
                    FROM research_runs
                    WHERE session_id = ?
                """
                params: list[Any] = [session_id]
                if exclude_run_id:
                    sql += " AND run_id != ?"
                    params.append(exclude_run_id)
                sql += " ORDER BY started_at DESC LIMIT 3"
                run_rows = connection.execute(sql, params).fetchall()

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

            if run_ids:
                session_facts = self._search_semantic_facts(
                    connection,
                    expanded_topic,
                    run_ids=run_ids,
                    memory_scope=SESSION_MEMORY_SCOPE,
                )
            else:
                session_facts = []
            profile_facts = self._search_semantic_facts(
                connection,
                expanded_topic,
                memory_scope=PROFILE_MEMORY_SCOPE,
            )
            expanded_with_history = self._build_memory_search_text(
                topic,
                profile_candidates=profile_facts or profile_candidates,
                session_facts=session_facts,
            )
            global_facts = self._search_semantic_facts(
                connection,
                expanded_with_history,
                memory_scope=GLOBAL_MEMORY_SCOPE,
            )

        return {
            "session_runs": session_runs,
            "recent_tasks": recent_tasks,
            "session_facts": session_facts,
            "profile_facts": profile_facts,
            "global_facts": global_facts,
            "semantic_facts": session_facts,
        }

    def _search_semantic_facts(
        self,
        connection: sqlite3.Connection,
        topic: str,
        *,
        run_ids: list[str] | None = None,
        memory_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve the most relevant semantic facts for the current topic."""

        embedding_results = self._search_semantic_facts_by_embedding(
            connection,
            topic,
            run_ids=run_ids,
            memory_scope=memory_scope,
        )
        if embedding_results:
            return embedding_results

        query = self._build_fts_query(topic)
        if not query:
            return []

        try:
            where_clauses = ["semantic_facts_fts MATCH ?"]
            params: list[Any] = [query]
            if memory_scope:
                where_clauses.append("sf.memory_scope = ?")
                params.append(memory_scope)
            if run_ids:
                placeholders = ", ".join("?" for _ in run_ids)
                where_clauses.append(f"sf.run_id IN ({placeholders})")
                params.extend(run_ids)
            rows = connection.execute(
                f"""
                SELECT sf.fact_id, sf.run_id, sf.topic, sf.scope, sf.subject, sf.fact, sf.confidence,
                       sf.stability_score, sf.sensitivity, sf.memory_scope, sf.source_session_id,
                       sf.last_verified_at
                FROM semantic_facts_fts fts
                JOIN semantic_facts sf ON sf.fact_id = fts.fact_id
                WHERE {" AND ".join(where_clauses)}
                ORDER BY bm25(semantic_facts_fts), sf.last_verified_at DESC
                LIMIT 5
                """,
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        return [
            {
                "fact_id": row["fact_id"],
                "run_id": row["run_id"],
                "topic": row["topic"],
                "scope": row["scope"],
                "subject": row["subject"],
                "fact": row["fact"],
                "confidence": row["confidence"],
                "stability_score": row["stability_score"],
                "sensitivity": row["sensitivity"],
                "memory_scope": row["memory_scope"],
                "source_session_id": row["source_session_id"],
                "last_verified_at": row["last_verified_at"],
            }
            for row in rows
        ]

    def _search_semantic_facts_by_embedding(
        self,
        connection: sqlite3.Connection,
        topic: str,
        *,
        run_ids: list[str] | None = None,
        memory_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve semantic facts by vector similarity when embeddings are available."""

        query_embedding = self._embed_query(topic)
        if not query_embedding:
            return []

        where_clauses = ["embedding IS NOT NULL"]
        params: list[Any] = []
        if memory_scope:
            where_clauses.append("memory_scope = ?")
            params.append(memory_scope)
        if run_ids:
            placeholders = ", ".join("?" for _ in run_ids)
            where_clauses.append(f"run_id IN ({placeholders})")
            params.extend(run_ids)

        rows = connection.execute(
            f"""
            SELECT fact_id, run_id, topic, scope, subject, fact, confidence, stability_score,
                   sensitivity, memory_scope, source_session_id, last_verified_at, embedding
            FROM semantic_facts
            WHERE {" AND ".join(where_clauses)}
            """,
            params,
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
                "run_id": row["run_id"],
                "topic": row["topic"],
                "scope": row["scope"],
                "subject": row["subject"],
                "fact": row["fact"],
                "confidence": row["confidence"],
                "stability_score": row["stability_score"],
                "sensitivity": row["sensitivity"],
                "memory_scope": row["memory_scope"],
                "source_session_id": row["source_session_id"],
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

        if not deduped:
            return ""

        ranked = sorted(deduped, key=len, reverse=True)[:8]
        return " OR ".join(f'"{token}"' for token in ranked)

    def _load_profile_candidates(
        self,
        connection: sqlite3.Connection,
        *,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Load recent profile facts for memory query expansion."""

        rows = connection.execute(
            """
            SELECT fact_id, subject, fact, confidence, stability_score, sensitivity, source_session_id
            FROM semantic_facts
            WHERE memory_scope = ?
            ORDER BY last_verified_at DESC
            LIMIT ?
            """,
            (PROFILE_MEMORY_SCOPE, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def _build_memory_search_text(
        self,
        topic: str,
        *,
        profile_candidates: list[dict[str, Any]] | None = None,
        session_facts: list[dict[str, Any]] | None = None,
    ) -> str:
        """Expand recall query with lightweight profile and session-memory hints."""

        pieces: list[str] = [topic.strip()]
        lowered_topic = topic.lower()

        for item in list(profile_candidates or []) + list(session_facts or []):
            subject = str(item.get("subject") or "").lower()
            fact_text = str(item.get("fact") or "").lower()
            merged = f"{subject} {fact_text}"

            for rule in PROFILE_RULES.values():
                expansions = tuple(rule.get("expansions") or ())
                if not expansions:
                    continue
                if not any(term.lower() in merged for term in expansions):
                    continue
                if any(term.lower() in lowered_topic for term in expansions):
                    pieces.append(" ".join(expansions))
                    pieces.append(str(item.get("fact") or "").strip())
                    break

        deduped: list[str] = []
        for piece in pieces:
            normalized = re.sub(r"\s+", " ", piece).strip()
            if normalized and normalized not in deduped:
                deduped.append(normalized)

        return " ".join(deduped)

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
            try:
                confidence = float(item.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            try:
                stability_score = float(item.get("stability_score", confidence) or confidence)
            except (TypeError, ValueError):
                stability_score = confidence
            facts.append(
                {
                    "scope": str(item.get("scope") or "").strip() or None,
                    "subject": str(item.get("subject") or "").strip() or topic,
                    "fact": fact_text,
                    "confidence": confidence,
                    "stability_score": stability_score,
                    "sensitivity": str(item.get("sensitivity") or "").strip().lower() or None,
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
        self,
        run_id: str,
        topic: str,
        facts: list[dict[str, Any]],
        *,
        session_id: str | None = None,
    ) -> None:
        """Persist semantic facts and sync the FTS index."""

        if not facts:
            return

        resolved_session_id = session_id or self._resolve_session_id_for_run(run_id)
        now = datetime.now(timezone.utc).isoformat()
        prepared_facts = [
            self._prepare_semantic_fact(
                topic,
                item,
                session_id=resolved_session_id,
            )
            for item in facts
            if str(item.get("fact") or "").strip()
        ]
        embedding_inputs = [
            f"{topic}\n{item.get('subject') or ''}\n{item.get('fact') or ''}".strip()
            for item in prepared_facts
        ]
        embeddings = self._embed_texts(embedding_inputs)
        with self._connect() as connection:
            for idx, item in enumerate(prepared_facts):
                embedding_json = None
                if idx < len(embeddings):
                    embedding_json = json.dumps(embeddings[idx])
                existing = self._find_existing_fact(
                    connection,
                    fact=item,
                    session_id=resolved_session_id,
                )
                if existing:
                    connection.execute(
                        """
                        UPDATE semantic_facts
                        SET run_id = ?,
                            topic = ?,
                            scope = ?,
                            subject = ?,
                            fact = ?,
                            embedding = COALESCE(?, embedding),
                            memory_scope = ?,
                            stability_score = MAX(stability_score, ?),
                            sensitivity = ?,
                            source_session_id = COALESCE(source_session_id, ?),
                            confidence = MAX(confidence, ?),
                            last_verified_at = ?
                        WHERE fact_id = ?
                        """,
                        (
                            run_id,
                            topic,
                            item.get("scope"),
                            item.get("subject"),
                            item.get("fact"),
                            embedding_json,
                            item.get("memory_scope"),
                            float(item.get("stability_score", 0.0) or 0.0),
                            item.get("sensitivity"),
                            resolved_session_id,
                            float(item.get("confidence", 0.0) or 0.0),
                            now,
                            existing["fact_id"],
                        ),
                    )
                    continue

                fact_id = uuid4().hex
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
                        memory_scope,
                        stability_score,
                        sensitivity,
                        source_session_id,
                        confidence,
                        created_at,
                        last_verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact_id,
                        run_id,
                        topic,
                        item.get("scope"),
                        item.get("subject"),
                        item.get("fact"),
                        embedding_json,
                        item.get("memory_scope"),
                        float(item.get("stability_score", 0.0) or 0.0),
                        item.get("sensitivity"),
                        resolved_session_id,
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

    def _prepare_semantic_fact(
        self,
        topic: str,
        item: dict[str, Any],
        *,
        session_id: str | None,
    ) -> dict[str, Any]:
        """Normalize fact metadata and determine its memory scope."""

        fact_text = str(item.get("fact") or "").strip()
        subject = str(item.get("subject") or "").strip() or topic
        scope = str(item.get("scope") or "").strip() or None
        confidence = min(max(float(item.get("confidence", 0.0) or 0.0), 0.0), 1.0)
        stability_score = min(
            max(float(item.get("stability_score", confidence) or confidence), 0.0),
            1.0,
        )
        sensitivity = str(item.get("sensitivity") or "").strip().lower()
        if sensitivity not in {LOW_SENSITIVITY, MEDIUM_SENSITIVITY, HIGH_SENSITIVITY}:
            sensitivity = self._infer_fact_sensitivity(topic, subject, fact_text)

        memory_scope = str(item.get("memory_scope") or "").strip().lower()
        if memory_scope not in {SESSION_MEMORY_SCOPE, GLOBAL_MEMORY_SCOPE, PROFILE_MEMORY_SCOPE}:
            memory_scope = self._classify_memory_scope(
                topic=topic,
                subject=subject,
                fact=fact_text,
                confidence=confidence,
                stability_score=stability_score,
                sensitivity=sensitivity,
            )

        return {
            "scope": scope,
            "subject": subject,
            "fact": fact_text,
            "confidence": confidence,
            "stability_score": stability_score,
            "sensitivity": sensitivity,
            "memory_scope": memory_scope,
            "source_session_id": session_id,
        }

    def _classify_memory_scope(
        self,
        *,
        topic: str,
        subject: str,
        fact: str,
        confidence: float,
        stability_score: float,
        sensitivity: str,
    ) -> str:
        """Assign facts to session/global/profile scope using conservative rules."""

        merged = f"{subject} {fact}".lower()
        if subject.startswith(("goal:", "preference:", "constraint:", "interest:")) or "用户" in fact:
            return PROFILE_MEMORY_SCOPE
        if self._is_medical_or_drug_text(topic, merged):
            return SESSION_MEMORY_SCOPE
        if (
            confidence >= GLOBAL_PROMOTION_CONFIDENCE
            and stability_score >= GLOBAL_PROMOTION_STABILITY
            and sensitivity == LOW_SENSITIVITY
        ):
            return GLOBAL_MEMORY_SCOPE
        return SESSION_MEMORY_SCOPE

    def _infer_fact_sensitivity(self, topic: str, subject: str, fact: str) -> str:
        """Infer a conservative sensitivity label for a semantic fact."""

        merged = f"{topic} {subject} {fact}".lower()
        if self._is_medical_or_drug_text(merged):
            return HIGH_SENSITIVITY
        if any(token in merged for token in {"预算", "偏好", "约束", "目标", "用户"}):
            return MEDIUM_SENSITIVITY
        return LOW_SENSITIVITY

    def _is_medical_or_drug_text(self, *texts: str) -> bool:
        """Return whether text belongs to the conservative medical/drug domain."""

        merged = " ".join(texts).lower()
        return any(token in merged for token in MEDICAL_MEMORY_KEYWORDS)

    def _resolve_session_id_for_run(self, run_id: str) -> str | None:
        """Look up the session owning a given run."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM research_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return str(row["session_id"] or "").strip() or None

    def _find_existing_fact(
        self,
        connection: sqlite3.Connection,
        *,
        fact: dict[str, Any],
        session_id: str | None,
    ) -> sqlite3.Row | None:
        """Find an existing fact row for minimal deduplication."""

        memory_scope = str(fact.get("memory_scope") or SESSION_MEMORY_SCOPE)
        subject = str(fact.get("subject") or "").strip()
        fact_text = str(fact.get("fact") or "").strip()
        if not fact_text:
            return None

        if memory_scope == SESSION_MEMORY_SCOPE:
            return connection.execute(
                """
                SELECT fact_id
                FROM semantic_facts
                WHERE memory_scope = ?
                  AND source_session_id = ?
                  AND COALESCE(subject, '') = ?
                  AND fact = ?
                LIMIT 1
                """,
                (memory_scope, session_id, subject, fact_text),
            ).fetchone()

        return connection.execute(
            """
            SELECT fact_id
            FROM semantic_facts
            WHERE memory_scope = ?
              AND COALESCE(subject, '') = ?
              AND fact = ?
            LIMIT 1
            """,
            (memory_scope, subject, fact_text),
        ).fetchone()

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

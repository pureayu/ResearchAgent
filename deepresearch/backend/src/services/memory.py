"""Structured memory service skeleton for the deep research workflow."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from openai import OpenAI

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

from config import Configuration
from models import SummaryState, TodoItem
from prompts import (
    memory_fact_rerank_instructions,
    profile_fact_extraction_instructions,
    semantic_fact_extraction_instructions,
)

SESSION_MEMORY_SCOPE = "session"
GLOBAL_MEMORY_SCOPE = "global"
PROFILE_MEMORY_SCOPE = "profile"
LOW_SENSITIVITY = "low"
MEDIUM_SENSITIVITY = "medium"
HIGH_SENSITIVITY = "high"

GLOBAL_PROMOTION_CONFIDENCE = 0.85
GLOBAL_PROMOTION_STABILITY = 0.80
MEMORY_CANDIDATE_LIMIT = 8
MEMORY_RESULT_LIMIT = 5
PROFILE_EXTRACTION_LIMIT = 4
SEMANTIC_EXTRACTION_LIMIT = 8

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

class BaseMemoryService:
    """Shared semantic-memory helpers for the PostgreSQL-backed memory service."""

    def __init__(self, config: Configuration) -> None:
        self._config = config
        self._llm_client, self._llm_model = self._build_llm_client()
        self._embedding_client, self._embedding_model = self._build_embedding_client()

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

    def capture_profile_memory(
        self,
        run_id: str,
        session_id: str,
        topic: str,
    ) -> list[dict[str, Any]]:
        """Extract and persist model-derived profile facts from the raw user topic."""

        facts = self.extract_profile_facts(topic)
        self.save_semantic_facts(
            run_id,
            topic,
            facts,
            session_id=session_id,
        )
        return facts

    def extract_profile_facts(self, topic: str) -> list[dict[str, Any]]:
        """Derive user-goal/profile facts from the raw query with the configured LLM."""

        if not topic.strip():
            return []

        payload = self._run_json_completion(
            system_prompt=profile_fact_extraction_instructions,
            user_content=f"用户原始问题：{topic}",
        )
        candidate_facts = payload.get("facts") if isinstance(payload, dict) else None
        return self._normalize_extracted_facts(
            candidate_facts,
            fallback_subject=topic,
            default_memory_scope=PROFILE_MEMORY_SCOPE,
            limit=PROFILE_EXTRACTION_LIMIT,
        )
    #从一次研究最终产出的 report 里，抽取“长期可复用的稳定事实”，然后准备存进 memory。
    def extract_semantic_facts(self, report: str, topic: str) -> list[dict[str, Any]]:
        """Extract stable semantic facts from the final report."""

        if not report.strip():
            return []

        payload = self._run_json_completion(
            system_prompt=semantic_fact_extraction_instructions,
            user_content=f"研究主题：{topic}\n\n研究报告：\n{report}",
        )
        candidate_facts = payload.get("facts") if isinstance(payload, dict) else None
        return self._normalize_extracted_facts(
            candidate_facts,
            fallback_subject=topic,
            limit=SEMANTIC_EXTRACTION_LIMIT,
        )

    def _run_json_completion(
        self,
        *,
        system_prompt: str,
        user_content: str,
    ) -> dict[str, Any] | list | None:
        """Run a deterministic JSON-only completion and parse the first JSON payload."""

        if self._llm_client is None or not self._llm_model:
            return None

        messages = [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_content},
        ]

        try:
            response = self._llm_client.chat.completions.create(
                model=self._llm_model,
                messages=messages,
                temperature=0.0,
            )
        except Exception:
            return None

        content = (response.choices[0].message.content or "").strip()
        return self._extract_json_payload(content)

    def _normalize_extracted_facts(
        self,
        candidate_facts: Any,
        *,
        fallback_subject: str,
        limit: int,
        default_memory_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Normalize fact objects emitted by LLM extraction prompts."""

        if not isinstance(candidate_facts, list):
            return []

        facts: list[dict[str, Any]] = []
        for item in candidate_facts[:limit]:
            if not isinstance(item, dict):
                continue
            fact_text = str(item.get("fact") or "").strip()
            if not fact_text:
                continue
            confidence = self._clamp_score(item.get("confidence"), default=0.0)
            stability_score = self._clamp_score(
                item.get("stability_score"),
                default=confidence,
            )
            sensitivity = self._normalize_sensitivity(item.get("sensitivity"))
            memory_scope = self._normalize_memory_scope(item.get("memory_scope"))
            if memory_scope is None:
                memory_scope = default_memory_scope
            facts.append(
                {
                    "scope": str(item.get("scope") or "").strip() or None,
                    "subject": str(item.get("subject") or "").strip() or fallback_subject,
                    "fact": fact_text,
                    "confidence": confidence,
                    "stability_score": stability_score,
                    "sensitivity": sensitivity,
                    "memory_scope": memory_scope,
                }
            )

        return facts

    def _clamp_score(self, value: Any, *, default: float) -> float:
        """Convert numeric fields into the closed interval [0, 1]."""

        try:
            resolved = float(value if value is not None else default)
        except (TypeError, ValueError):
            resolved = default
        return min(max(resolved, 0.0), 1.0)

    def _normalize_sensitivity(self, value: Any) -> str | None:
        """Normalize sensitivity labels from LLM outputs."""

        sensitivity = str(value or "").strip().lower()
        if sensitivity in {LOW_SENSITIVITY, MEDIUM_SENSITIVITY, HIGH_SENSITIVITY}:
            return sensitivity
        return None

    def _normalize_memory_scope(self, value: Any) -> str | None:
        """Normalize memory scope labels from LLM outputs."""

        memory_scope = str(value or "").strip().lower()
        if memory_scope in {
            SESSION_MEMORY_SCOPE,
            GLOBAL_MEMORY_SCOPE,
            PROFILE_MEMORY_SCOPE,
        }:
            return memory_scope
        return None

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

    def consolidate_semantic_facts(
        self,
        run_id: str,
        topic: str,
        report: str,
    ) -> list[dict[str, Any]]:
        """Extract and persist semantic facts derived from a final report."""

        facts = self.extract_semantic_facts(report, topic)
        self.save_semantic_facts(run_id, topic, facts)
        return facts
    #代码规范化，合法化检查以及memory_scope检查
    def _prepare_semantic_fact(
        self,
        topic: str,
        item: dict[str, Any],
        *,
        session_id: str | None,
    ) -> dict[str, Any]:
        """Normalize fact metadata and apply minimal scope/sensitivity guardrails."""

        fact_text = str(item.get("fact") or "").strip()
        subject = str(item.get("subject") or "").strip() or topic
        scope = str(item.get("scope") or "").strip() or None
        confidence = self._clamp_score(item.get("confidence"), default=0.0)
        stability_score = self._clamp_score(
            item.get("stability_score"),
            default=confidence,
        )
        sensitivity = self._normalize_sensitivity(item.get("sensitivity"))
        if sensitivity is None:
            sensitivity = self._infer_fact_sensitivity(topic, subject, fact_text)

        memory_scope = self._guardrail_memory_scope(
            requested_scope=self._normalize_memory_scope(item.get("memory_scope")),
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

    def _guardrail_memory_scope(
        self,
        *,
        requested_scope: str | None,
        topic: str,
        subject: str,
        fact: str,
        confidence: float,
        stability_score: float,
        sensitivity: str,
    ) -> str:
        """Apply minimal scope guardrails while keeping model-produced scope as primary."""

        memory_scope = requested_scope or SESSION_MEMORY_SCOPE
        if (
            memory_scope == GLOBAL_MEMORY_SCOPE
            and (
                sensitivity != LOW_SENSITIVITY
                or confidence < GLOBAL_PROMOTION_CONFIDENCE
                or stability_score < GLOBAL_PROMOTION_STABILITY
                or self._is_medical_or_drug_text(topic, subject, fact)
            )
        ):
            return SESSION_MEMORY_SCOPE
        return memory_scope

    def _infer_fact_sensitivity(self, topic: str, subject: str, fact: str) -> str:
        """Infer a conservative fallback sensitivity label when the model omits one."""

        if self._is_medical_or_drug_text(topic, subject, fact):
            return HIGH_SENSITIVITY
        return MEDIUM_SENSITIVITY

    def _is_medical_or_drug_text(self, *texts: str) -> bool:
        """Return whether text belongs to the conservative medical/drug domain."""

        merged = " ".join(texts).lower()
        return any(token in merged for token in MEDICAL_MEMORY_KEYWORDS)

    def _rerank_recalled_facts(
        self,
        topic: str,
        *,
        session_candidates: list[dict[str, Any]],
        profile_candidates: list[dict[str, Any]],
        global_candidates: list[dict[str, Any]],
        limit: int = MEMORY_RESULT_LIMIT,
    ) -> dict[str, list[dict[str, Any]]]:
        """Rerank per-scope fact candidates with LLM selection and deterministic fallback."""

        fallback = {
            "session_facts": self._sorted_fact_candidates(session_candidates)[:limit],
            "profile_facts": self._sorted_fact_candidates(profile_candidates)[:limit],
            "global_facts": self._sorted_fact_candidates(global_candidates)[:limit],
        }
        if not any(fallback.values()):
            return fallback

        payload = self._run_json_completion(
            system_prompt=memory_fact_rerank_instructions,
            user_content=json.dumps(
                {
                    "topic": topic,
                    "session_facts": [
                        self._serialize_fact_candidate(item)
                        for item in session_candidates
                    ],
                    "profile_facts": [
                        self._serialize_fact_candidate(item)
                        for item in profile_candidates
                    ],
                    "global_facts": [
                        self._serialize_fact_candidate(item)
                        for item in global_candidates
                    ],
                },
                ensure_ascii=False,
                default=self._json_default,
            ),
        )
        selection = self._extract_rerank_selection(payload)
        if selection is None:
            return fallback

        results: dict[str, list[dict[str, Any]]] = {}
        scope_map = {
            "session_facts": session_candidates,
            "profile_facts": profile_candidates,
            "global_facts": global_candidates,
        }
        for result_key, selection_key in (
            ("session_facts", "session_fact_ids"),
            ("profile_facts", "profile_fact_ids"),
            ("global_facts", "global_fact_ids"),
        ):
            selected = self._select_fact_candidates(
                scope_map[result_key],
                selection[selection_key],
                limit=limit,
            )
            if not selected and selection[selection_key]:
                results[result_key] = fallback[result_key]
            else:
                results[result_key] = selected
        return results

    def _extract_rerank_selection(
        self,
        payload: dict[str, Any] | list | None,
    ) -> dict[str, list[str]] | None:
        """Parse LLM rerank output into per-scope fact-id lists."""

        if not isinstance(payload, dict):
            return None

        selection: dict[str, list[str]] = {}
        for key in ("session_fact_ids", "profile_fact_ids", "global_fact_ids"):
            value = payload.get(key, [])
            if value is None:
                value = []
            if not isinstance(value, list):
                return None
            selection[key] = [str(item).strip() for item in value if str(item).strip()]
        return selection

    def _select_fact_candidates(
        self,
        candidates: list[dict[str, Any]],
        fact_ids: list[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return candidates in the exact order selected by the reranker."""

        indexed = {
            str(item.get("fact_id") or "").strip(): item
            for item in candidates
            if str(item.get("fact_id") or "").strip()
        }
        selected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for fact_id in fact_ids:
            if fact_id in seen_ids:
                continue
            item = indexed.get(fact_id)
            if item is None:
                continue
            selected.append(item)
            seen_ids.add(fact_id)
            if len(selected) >= limit:
                break
        return selected

    def _sorted_fact_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Deterministically rank fact candidates when rerank is unavailable."""

        return sorted(
            candidates,
            key=self._fact_sort_key,
            reverse=True,
        )

    def _fact_sort_key(self, item: dict[str, Any]) -> tuple[float, float, float, float]:
        """Sort by similarity first, then confidence, stability, and recency."""

        return (
            self._clamp_score(item.get("similarity"), default=0.0),
            self._clamp_score(item.get("confidence"), default=0.0),
            self._clamp_score(item.get("stability_score"), default=0.0),
            self._timestamp_sort_value(item.get("last_verified_at")),
        )

    def _timestamp_sort_value(self, value: Any) -> float:
        """Convert timestamps into sortable floats for deterministic fallback ranking."""

        if isinstance(value, datetime):
            return value.timestamp()
        if isinstance(value, str):
            normalized = value.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(normalized).timestamp()
            except ValueError:
                return 0.0
        return 0.0

    def _serialize_fact_candidate(self, item: dict[str, Any]) -> dict[str, Any]:
        """Reduce fact candidate payloads before sending them to the reranker."""

        return {
            "fact_id": item.get("fact_id"),
            "scope": item.get("scope"),
            "subject": item.get("subject"),
            "fact": item.get("fact"),
            "memory_scope": item.get("memory_scope"),
            "similarity": item.get("similarity"),
            "confidence": item.get("confidence"),
            "stability_score": item.get("stability_score"),
            "sensitivity": item.get("sensitivity"),
            "last_verified_at": item.get("last_verified_at"),
        }

    def _json_default(self, value: Any) -> str:
        """Serialize otherwise non-JSON-native values for rerank prompts."""

        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

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


class MemoryService(BaseMemoryService):
    """PostgreSQL + pgvector implementation for structured research memory."""

    def __init__(self, config: Configuration) -> None:
        super().__init__(config)
        self._database_url = config.resolved_memory_database_url()
        if not self._database_url:
            raise ValueError(
                "MEMORY_DATABASE_URL is required for the PostgreSQL memory service"
            )
        if psycopg is None or dict_row is None:
            raise RuntimeError(
                "psycopg is required for the PostgreSQL memory backend. "
                "Install backend dependencies before starting the service."
            )
        self._init_db()

    @property
    def database_url(self) -> str:
        """Return the configured PostgreSQL URL."""

        return self._database_url

    def _connect(self) -> Any:
        """Open a PostgreSQL connection for memory operations."""

        return psycopg.connect(self._database_url, row_factory=dict_row)

    def _init_db(self) -> None:
        """Create the PostgreSQL tables and pgvector extension if needed."""

        with self._connect() as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_sessions (
                    session_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
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
                    started_at TIMESTAMPTZ NOT NULL,
                    finished_at TIMESTAMPTZ,
                    final_report TEXT,
                    report_note_id TEXT,
                    task_count INTEGER DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_memories (
                    id BIGSERIAL PRIMARY KEY,
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
                    top_score DOUBLE PRECISION DEFAULT 0.0,
                    summary TEXT,
                    sources_summary TEXT,
                    note_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL
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
                    embedding vector,
                    memory_scope TEXT NOT NULL DEFAULT 'session',
                    stability_score DOUBLE PRECISION DEFAULT 0.0,
                    sensitivity TEXT NOT NULL DEFAULT 'medium',
                    source_session_id TEXT,
                    confidence DOUBLE PRECISION DEFAULT 0.0,
                    created_at TIMESTAMPTZ NOT NULL,
                    last_verified_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.execute(
                "ALTER TABLE task_memories ADD COLUMN IF NOT EXISTS round_id INTEGER DEFAULT 1"
            )
            connection.execute(
                "ALTER TABLE task_memories ADD COLUMN IF NOT EXISTS origin TEXT DEFAULT 'planner'"
            )
            connection.execute(
                "ALTER TABLE task_memories ADD COLUMN IF NOT EXISTS parent_task_id INTEGER"
            )
            connection.execute(
                "ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS memory_scope TEXT NOT NULL DEFAULT 'session'"
            )
            connection.execute(
                "ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS stability_score DOUBLE PRECISION DEFAULT 0.0"
            )
            connection.execute(
                "ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'medium'"
            )
            connection.execute(
                "ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS source_session_id TEXT"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_research_runs_session_started
                ON research_runs (session_id, started_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_memories_run_created
                ON task_memories (run_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_semantic_facts_scope_verified
                ON semantic_facts (memory_scope, last_verified_at DESC)
                """
            )
            connection.execute(
                """
                UPDATE semantic_facts
                SET memory_scope = %s
                WHERE memory_scope IS NULL OR memory_scope = ''
                """,
                (SESSION_MEMORY_SCOPE,),
            )
            connection.execute(
                """
                UPDATE semantic_facts
                SET stability_score = COALESCE(stability_score, confidence, 0.0)
                WHERE stability_score IS NULL OR stability_score = 0.0
                """
            )
            connection.execute(
                """
                UPDATE semantic_facts
                SET sensitivity = %s
                WHERE sensitivity IS NULL OR sensitivity = ''
                """,
                (MEDIUM_SENSITIVITY,),
            )
            connection.execute(
                """
                UPDATE semantic_facts sf
                SET source_session_id = rr.session_id
                FROM research_runs rr
                WHERE rr.run_id = sf.run_id
                  AND (sf.source_session_id IS NULL OR sf.source_session_id = '')
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
                ) VALUES (%s, %s, %s, %s)
                """,
                (run_id, session_id, topic, started_at),
            )
            connection.execute(
                """
                UPDATE research_sessions
                SET updated_at = %s,
                    last_run_id = %s
                WHERE session_id = %s
                """,
                (started_at, run_id, session_id),
            )
            connection.commit()

        return run_id

    def save_task_log(self, run_id: str, task: TodoItem) -> None:
        """Persist a task-level audit log record for the current run."""

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
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            self._prune_task_logs(connection, run_id)
            connection.commit()

    def save_report_memory(self, run_id: str, state: SummaryState, report: str) -> None:
        """Persist the final report and run-level summary information."""

        finished_at = datetime.now(timezone.utc).isoformat()
        task_count = len(state.todo_items)

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE research_runs
                SET finished_at = %s,
                    final_report = %s,
                    report_note_id = %s,
                    task_count = %s
                WHERE run_id = %s
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
                WHERE session_id = %s
                """,
                (resolved_session_id,),
            ).fetchone()

            if existing:
                connection.execute(
                    """
                    UPDATE research_sessions
                    SET updated_at = %s
                    WHERE session_id = %s
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
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (resolved_session_id, topic, now, now),
                )
            connection.commit()

        return resolved_session_id

    def load_recent_task_logs(
        self,
        session_id: str | None,
        *,
        exclude_run_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Load recent task-log rows for explicit history-recall flows."""

        if not session_id or limit <= 0:
            return []

        with self._connect() as connection:
            sql = """
                SELECT tm.run_id, tm.task_id, tm.title, tm.status, tm.summary, tm.created_at
                FROM task_memories tm
                JOIN research_runs rr ON rr.run_id = tm.run_id
                WHERE rr.session_id = %s
            """
            params: list[Any] = [session_id]
            if exclude_run_id:
                sql += " AND tm.run_id != %s"
                params.append(exclude_run_id)
            sql += " ORDER BY tm.created_at DESC, tm.id DESC LIMIT %s"
            params.append(limit)

            rows = connection.execute(sql, params).fetchall()

        return [
            {
                "run_id": row["run_id"],
                "task_id": row["task_id"],
                "title": row["title"],
                "status": row["status"],
                "summary": row["summary"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # 给当前 topic 准备一包“默认可注入”的历史上下文，供后面的 planner / reviewer / direct answer 使用。
    def load_relevant_context(
        self,
        session_id: str | None,
        topic: str,
        *,
        exclude_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Load recalled memory context for a new topic."""

        session_runs: list[dict[str, Any]] = []
        session_facts: list[dict[str, Any]] = []
        profile_facts: list[dict[str, Any]] = []
        global_facts: list[dict[str, Any]] = []

        #如果这次有 session_id，就先去数据库找这个 session 最近 3 次研究 run
        with self._connect() as connection:
            run_rows: list[dict[str, Any]] = []
            if session_id:
                sql = """
                    SELECT run_id, session_id, topic, started_at, finished_at, final_report, task_count
                    FROM research_runs
                    WHERE session_id = %s
                """
                params: list[Any] = [session_id]
                if exclude_run_id:
                    sql += " AND run_id != %s"
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

            session_candidates: list[dict[str, Any]] = []
            profile_candidates: list[dict[str, Any]] = []
            global_candidates: list[dict[str, Any]] = []
            run_ids = [row["run_id"] for row in run_rows]

            if run_ids:
                session_candidates = self._search_semantic_facts(
                    connection,
                    topic,
                    run_ids=run_ids,
                    memory_scope=SESSION_MEMORY_SCOPE,
                    limit=MEMORY_CANDIDATE_LIMIT,
                )
            profile_candidates = self._search_semantic_facts(
                connection,
                topic,
                memory_scope=PROFILE_MEMORY_SCOPE,
                limit=MEMORY_CANDIDATE_LIMIT,
            )
            global_candidates = self._search_semantic_facts(
                connection,
                topic,
                memory_scope=GLOBAL_MEMORY_SCOPE,
                limit=MEMORY_CANDIDATE_LIMIT,
            )
            reranked = self._rerank_recalled_facts(
                topic,
                session_candidates=session_candidates,
                profile_candidates=profile_candidates,
                global_candidates=global_candidates,
            )
            session_facts = reranked["session_facts"]
            profile_facts = reranked["profile_facts"]
            global_facts = reranked["global_facts"]

        return {
            "session_runs": session_runs,
            "session_facts": session_facts,
            "profile_facts": profile_facts,
            "global_facts": global_facts,
            "semantic_facts": session_facts,
        }

    def _prune_task_logs(self, connection: Any, run_id: str) -> None:
        """Keep task-log retention bounded per session."""

        retention = max(0, int(self._config.task_log_retention_per_session))
        if retention <= 0:
            return

        row = connection.execute(
            """
            SELECT session_id
            FROM research_runs
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
        session_id = str((row or {}).get("session_id") or "").strip()
        if not session_id:
            return

        connection.execute(
            """
            DELETE FROM task_memories
            WHERE id IN (
                SELECT tm.id
                FROM task_memories tm
                JOIN research_runs rr ON rr.run_id = tm.run_id
                WHERE rr.session_id = %s
                ORDER BY tm.created_at DESC, tm.id DESC
                OFFSET %s
            )
            """,
            (session_id, retention),
        )

    def _search_semantic_facts(
        self,
        connection: Any,
        topic: str,
        *,
        run_ids: list[str] | None = None,
        memory_scope: str | None = None,
        limit: int = MEMORY_RESULT_LIMIT,
    ) -> list[dict[str, Any]]:
        """Retrieve the most relevant semantic facts for the current topic."""

        embedding_results = self._search_semantic_facts_by_embedding(
            connection,
            topic,
            run_ids=run_ids,
            memory_scope=memory_scope,
            limit=limit,
        )
        if embedding_results:
            return embedding_results

        tokens = re.findall(
            r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9_-]{1,}", topic or ""
        )
        deduped: list[str] = []
        for token in tokens:
            normalized = token.strip()
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        if not deduped:
            return []

        token_clauses: list[str] = []
        params: list[Any] = []
        for token in deduped[:6]:
            pattern = f"%{token}%"
            token_clauses.append(
                "(topic ILIKE %s OR COALESCE(subject, '') ILIKE %s OR fact ILIKE %s)"
            )
            params.extend([pattern, pattern, pattern])

        where_clauses = [f"({' OR '.join(token_clauses)})"]
        if memory_scope:
            where_clauses.append("memory_scope = %s")
            params.append(memory_scope)
        if run_ids:
            where_clauses.append("run_id = ANY(%s)")
            params.append(run_ids)

        rows = connection.execute(
            f"""
            SELECT fact_id, run_id, topic, scope, subject, fact, confidence,
                   stability_score, sensitivity, memory_scope, source_session_id,
                   last_verified_at
            FROM semantic_facts
            WHERE {' AND '.join(where_clauses)}
            ORDER BY confidence DESC, last_verified_at DESC
            LIMIT %s
            """,
            params + [limit],
        ).fetchall()

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
        connection: Any,
        topic: str,
        *,
        run_ids: list[str] | None = None,
        memory_scope: str | None = None,
        limit: int = MEMORY_RESULT_LIMIT,
    ) -> list[dict[str, Any]]:
        """Retrieve semantic facts by vector similarity inside PostgreSQL."""

        query_embedding = self._embed_query(topic)
        if not query_embedding:
            return []

        vector_literal = self._vector_literal(query_embedding)
        where_clauses = ["embedding IS NOT NULL"]
        params: list[Any] = [vector_literal]
        if memory_scope:
            where_clauses.append("memory_scope = %s")
            params.append(memory_scope)
        if run_ids:
            where_clauses.append("run_id = ANY(%s)")
            params.append(run_ids)
        params.append(vector_literal)

        rows = connection.execute(
            f"""
            SELECT fact_id, run_id, topic, scope, subject, fact, confidence, stability_score,
                   sensitivity, memory_scope, source_session_id, last_verified_at,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM semantic_facts
            WHERE {' AND '.join(where_clauses)}
            ORDER BY embedding <=> %s::vector ASC, confidence DESC
            LIMIT %s
            """,
            params + [limit],
        ).fetchall()

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
                "similarity": row["similarity"],
            }
            for row in rows
        ]

    def save_semantic_facts(
        self,
        run_id: str,
        topic: str,
        facts: list[dict[str, Any]],
        *,
        session_id: str | None = None,
    ) -> None:
        """Persist semantic facts into PostgreSQL."""

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
                embedding_vector = None
                if idx < len(embeddings):
                    embedding_vector = self._vector_literal(embeddings[idx])
                existing = self._find_existing_fact(
                    connection,
                    fact=item,
                    session_id=resolved_session_id,
                )
                if existing:
                    update_params: list[Any] = [
                        run_id,
                        topic,
                        item.get("scope"),
                        item.get("subject"),
                        item.get("fact"),
                    ]
                    set_clauses = [
                        "run_id = %s",
                        "topic = %s",
                        "scope = %s",
                        "subject = %s",
                        "fact = %s",
                    ]
                    if embedding_vector is not None:
                        set_clauses.append("embedding = %s::vector")
                        update_params.append(embedding_vector)
                    set_clauses.extend(
                        [
                            "memory_scope = %s",
                            "stability_score = GREATEST(COALESCE(stability_score, 0.0), %s)",
                            "sensitivity = %s",
                            "source_session_id = COALESCE(source_session_id, %s)",
                            "confidence = GREATEST(COALESCE(confidence, 0.0), %s)",
                            "last_verified_at = %s",
                        ]
                    )
                    update_params.extend(
                        [
                            item.get("memory_scope"),
                            float(item.get("stability_score", 0.0) or 0.0),
                            item.get("sensitivity"),
                            resolved_session_id,
                            float(item.get("confidence", 0.0) or 0.0),
                            now,
                            existing["fact_id"],
                        ]
                    )
                    connection.execute(
                        f"""
                        UPDATE semantic_facts
                        SET {', '.join(set_clauses)}
                        WHERE fact_id = %s
                        """,
                        update_params,
                    )
                    continue

                fact_id = uuid4().hex
                if embedding_vector is not None:
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
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            fact_id,
                            run_id,
                            topic,
                            item.get("scope"),
                            item.get("subject"),
                            item.get("fact"),
                            embedding_vector,
                            item.get("memory_scope"),
                            float(item.get("stability_score", 0.0) or 0.0),
                            item.get("sensitivity"),
                            resolved_session_id,
                            float(item.get("confidence", 0.0) or 0.0),
                            now,
                            now,
                        ),
                    )
                else:
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
                        ) VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            fact_id,
                            run_id,
                            topic,
                            item.get("scope"),
                            item.get("subject"),
                            item.get("fact"),
                            item.get("memory_scope"),
                            float(item.get("stability_score", 0.0) or 0.0),
                            item.get("sensitivity"),
                            resolved_session_id,
                            float(item.get("confidence", 0.0) or 0.0),
                            now,
                            now,
                        ),
                    )
            connection.commit()

    def _resolve_session_id_for_run(self, run_id: str) -> str | None:
        """Look up the session owning a given run."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM research_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return str(row["session_id"] or "").strip() or None

    def _find_existing_fact(
        self,
        connection: Any,
        *,
        fact: dict[str, Any],
        session_id: str | None,
    ) -> dict[str, Any] | None:
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
                WHERE memory_scope = %s
                  AND source_session_id IS NOT DISTINCT FROM %s
                  AND COALESCE(subject, '') = %s
                  AND fact = %s
                LIMIT 1
                """,
                (memory_scope, session_id, subject, fact_text),
            ).fetchone()

        return connection.execute(
            """
            SELECT fact_id
            FROM semantic_facts
            WHERE memory_scope = %s
              AND COALESCE(subject, '') = %s
              AND fact = %s
            LIMIT 1
            """,
            (memory_scope, subject, fact_text),
        ).fetchone()

    def _vector_literal(self, values: list[float]) -> str:
        """Serialize a Python embedding list into pgvector text format."""

        return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"

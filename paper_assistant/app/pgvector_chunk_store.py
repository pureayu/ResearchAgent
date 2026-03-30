from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.config import Settings

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None


_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PGVectorChunkStore:
    """Persist and retrieve RAG chunks from PostgreSQL + pgvector."""

    def __init__(self, settings: Settings) -> None:
        if psycopg is None or dict_row is None:
            raise RuntimeError(
                "psycopg is required for the pgvector RAG backend. "
                "Install requirements.txt before using local RAG."
            )
        self.settings = settings
        self.database_url = settings.resolved_rag_database_url()
        self.table_name = settings.rag_chunk_table
        if not self.database_url:
            raise RuntimeError(
                "RAG_DATABASE_URL is required when RAG_VECTOR_BACKEND=postgres"
            )
        if not _VALID_IDENTIFIER.fullmatch(self.table_name):
            raise RuntimeError(
                f"Invalid RAG_CHUNK_TABLE value: {self.table_name!r}"
            )
        self._init_db()

    def _connect(self) -> Any:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _init_db(self) -> None:
        table_name = self.table_name
        with self._connect() as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    chunk_uid TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    chunk_order INTEGER,
                    title TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    page INTEGER,
                    text TEXT NOT NULL,
                    embedding vector,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{table_name}_doc_order
                ON {table_name} (doc_id, chunk_order)
                """
            )
            connection.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{table_name}_filepath
                ON {table_name} (filepath)
                """
            )
            connection.commit()

    def reset(self) -> None:
        with self._connect() as connection:
            connection.execute(f"TRUNCATE TABLE {self.table_name}")
            connection.commit()

    def load_existing_chunk_uids(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT chunk_uid FROM {self.table_name}"
            ).fetchall()
        return {str(row["chunk_uid"]) for row in rows}

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            for chunk in chunks:
                vector_literal = self._vector_literal(list(chunk.get("embedding") or []))
                connection.execute(
                    f"""
                    INSERT INTO {self.table_name} (
                        chunk_uid,
                        doc_id,
                        chunk_id,
                        chunk_order,
                        title,
                        filepath,
                        page,
                        text,
                        embedding,
                        created_at,
                        updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                    ON CONFLICT (chunk_uid) DO UPDATE SET
                        doc_id = EXCLUDED.doc_id,
                        chunk_id = EXCLUDED.chunk_id,
                        chunk_order = EXCLUDED.chunk_order,
                        title = EXCLUDED.title,
                        filepath = EXCLUDED.filepath,
                        page = EXCLUDED.page,
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        self._sanitize_text(chunk["chunk_uid"]),
                        self._sanitize_text(chunk["doc_id"]),
                        self._sanitize_text(chunk["chunk_id"]),
                        chunk.get("order"),
                        self._sanitize_text(chunk["title"]),
                        self._sanitize_text(chunk["filepath"]),
                        chunk.get("page"),
                        self._sanitize_text(chunk["text"]),
                        vector_literal,
                        now,
                        now,
                    ),
                )
            connection.commit()

    def load_chunks(
        self,
        *,
        include_embeddings: bool = False,
        doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        embedding_sql = ", embedding::text AS embedding_text" if include_embeddings else ""
        params: list[Any] = []
        where_clause = ""
        if doc_ids:
            where_clause = "WHERE doc_id = ANY(%s)"
            params.append(doc_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT chunk_uid, doc_id, chunk_id, chunk_order, title, filepath, page, text{embedding_sql}
                FROM {self.table_name}
                {where_clause}
                ORDER BY doc_id ASC, chunk_order ASC NULLS LAST, chunk_uid ASC
                """,
                params,
            ).fetchall()
        return [self._row_to_chunk(row, include_embeddings=include_embeddings) for row in rows]

    def vector_search(
        self,
        query_embedding: list[float],
        *,
        limit: int,
    ) -> list[tuple[dict[str, Any], float]]:
        vector_literal = self._vector_literal(query_embedding)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT chunk_uid, doc_id, chunk_id, chunk_order, title, filepath, page, text,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM {self.table_name}
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector ASC
                LIMIT %s
                """,
                (vector_literal, vector_literal, limit),
            ).fetchall()
        results: list[tuple[dict[str, Any], float]] = []
        for row in rows:
            score = float(row["similarity"] or 0.0)
            if score <= 0:
                continue
            results.append((self._row_to_chunk(row, include_embeddings=False), score))
        return results

    def _row_to_chunk(
        self,
        row: dict[str, Any],
        *,
        include_embeddings: bool,
    ) -> dict[str, Any]:
        embedding: list[float] = []
        if include_embeddings:
            raw_embedding = row.get("embedding_text")
            if raw_embedding:
                try:
                    embedding = json.loads(raw_embedding)
                except json.JSONDecodeError:
                    embedding = []
        return {
            "chunk_uid": row["chunk_uid"],
            "doc_id": row["doc_id"],
            "chunk_id": row["chunk_id"],
            "order": row.get("chunk_order"),
            "title": row["title"],
            "filepath": row["filepath"],
            "page": row.get("page"),
            "text": row["text"],
            "embedding": embedding,
        }

    def _vector_literal(self, values: list[float]) -> str:
        return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"

    def _sanitize_text(self, value: str) -> str:
        return str(value).replace("\x00", "")

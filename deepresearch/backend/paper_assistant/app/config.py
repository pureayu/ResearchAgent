import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseModel):
    project_root: Path = PROJECT_ROOT
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    processed_dir: Path = PROJECT_ROOT / "data" / "processed"
    metadata_dir: Path = PROJECT_ROOT / "data" / "metadata"
    outputs_dir: Path = PROJECT_ROOT / "outputs"
    vector_store_dir: Path = PROJECT_ROOT / "data" / "vector_store"
    metadata_file: Path = PROJECT_ROOT / "data" / "metadata" / "documents.json"
    manifest_file: Path = PROJECT_ROOT / "data" / "metadata" / "document_manifest.json"
    simple_index_file: Path = PROJECT_ROOT / "data" / "vector_store" / "simple_chunks.json"
    memory_dir: Path = PROJECT_ROOT / "data" / "memory"
    rag_vector_backend: str = Field(default_factory=lambda: os.getenv("RAG_VECTOR_BACKEND", "postgres"))
    rag_database_url: str | None = Field(
        default_factory=lambda: (
            os.getenv("RAG_DATABASE_URL")
            or os.getenv("MEMORY_DATABASE_URL")
            or "postgresql://postgres:postgres@localhost:54329/researchagent"
        )
    )
    rag_chunk_table: str = Field(default_factory=lambda: os.getenv("RAG_CHUNK_TABLE", "rag_chunks"))

    llm_model: str | None = Field(default_factory=lambda: os.getenv("LLM_MODEL"))
    llm_api_key: str | None = Field(default_factory=lambda: os.getenv("LLM_API_KEY"))
    llm_base_url: str | None = Field(default_factory=lambda: os.getenv("LLM_BASE_URL"))
    llm_timeout: int = Field(default_factory=lambda: int(os.getenv("LLM_TIMEOUT", "120")))

    embedding_model: str | None = Field(default_factory=lambda: os.getenv("EMBEDDING_MODEL"))
    embedding_api_key: str | None = Field(default_factory=lambda: os.getenv("EMBEDDING_API_KEY"))
    embedding_base_url: str | None = Field(default_factory=lambda: os.getenv("EMBEDDING_BASE_URL"))
    embedding_dim: int = Field(default_factory=lambda: int(os.getenv("EMBEDDING_DIM", "1536")))
    embedding_max_tokens: int = Field(default_factory=lambda: int(os.getenv("EMBEDDING_MAX_TOKENS", "8192")))

    response_language: str = Field(default_factory=lambda: os.getenv("RESPONSE_LANGUAGE", "Chinese"))

    def ensure_directories(self) -> None:
        for path in (
            self.raw_dir,
            self.processed_dir,
            self.metadata_dir,
            self.outputs_dir,
            self.vector_store_dir,
            self.memory_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def require_llm(self) -> None:
        missing = [
            name
            for name, value in (
                ("LLM_MODEL", self.llm_model),
                ("LLM_API_KEY", self.llm_api_key),
                ("LLM_BASE_URL", self.llm_base_url),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    def require_embedding(self) -> None:
        missing = [
            name
            for name, value in (
                ("EMBEDDING_MODEL", self.embedding_model),
                ("EMBEDDING_DIM", self.embedding_dim),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    def resolved_rag_vector_backend(self) -> str:
        backend = (self.rag_vector_backend or "postgres").strip().lower()
        if backend not in {"postgres", "file"}:
            raise RuntimeError("RAG_VECTOR_BACKEND must be either 'postgres' or 'file'")
        return backend

    def resolved_rag_database_url(self) -> str | None:
        if self.resolved_rag_vector_backend() != "postgres":
            return None
        if not self.rag_database_url:
            raise RuntimeError(
                "RAG_DATABASE_URL is required when RAG_VECTOR_BACKEND=postgres"
            )
        return self.rag_database_url

    @property
    def effective_embedding_api_key(self) -> str | None:
        return self.embedding_api_key or self.llm_api_key

    @property
    def effective_embedding_base_url(self) -> str | None:
        return self.embedding_base_url or self.llm_base_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings

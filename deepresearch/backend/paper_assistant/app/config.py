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
    metadata_file: Path = PROJECT_ROOT / "data" / "metadata" / "documents.json"
    manifest_file: Path = PROJECT_ROOT / "data" / "metadata" / "document_manifest.json"
    memory_dir: Path = PROJECT_ROOT / "data" / "memory"
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
    rerank_model: str | None = Field(default_factory=lambda: os.getenv("RERANK_MODEL"))
    rerank_api_key: str | None = Field(default_factory=lambda: os.getenv("RERANK_API_KEY"))
    rerank_base_url: str = Field(
        default_factory=lambda: os.getenv(
            "RERANK_BASE_URL",
            "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        )
    )
    rerank_timeout: int = Field(
        default_factory=lambda: int(
            os.getenv("RERANK_TIMEOUT", os.getenv("LLM_TIMEOUT", "120"))
        )
    )
    rerank_top_n: int = Field(default_factory=lambda: int(os.getenv("RERANK_TOP_N", "20")))
    rerank_max_chars_per_doc: int = Field(
        default_factory=lambda: int(os.getenv("RERANK_MAX_CHARS_PER_DOC", "1800"))
    )
    rerank_instruct: str | None = Field(default_factory=lambda: os.getenv("RERANK_INSTRUCT"))

    response_language: str = Field(default_factory=lambda: os.getenv("RESPONSE_LANGUAGE", "Chinese"))

    def ensure_directories(self) -> None:
        for path in (
            self.raw_dir,
            self.processed_dir,
            self.metadata_dir,
            self.outputs_dir,
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

    def resolved_rag_database_url(self) -> str:
        if not self.rag_database_url:
            raise RuntimeError("RAG_DATABASE_URL is required for the pgvector chunk store")
        return self.rag_database_url

    @property
    def effective_embedding_api_key(self) -> str | None:
        return self.embedding_api_key or self.llm_api_key

    @property
    def effective_embedding_base_url(self) -> str | None:
        return self.embedding_base_url or self.llm_base_url

    @property
    def effective_rerank_api_key(self) -> str | None:
        return self.rerank_api_key or self.llm_api_key

    @property
    def rerank_enabled(self) -> bool:
        return bool(self.rerank_model and self.effective_rerank_api_key and self.rerank_base_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings

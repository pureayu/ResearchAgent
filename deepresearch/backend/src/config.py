import os
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SearchAPI(Enum):
    PERPLEXITY = "perplexity"
    TAVILY = "tavily"
    DUCKDUCKGO = "duckduckgo"
    SEARXNG = "searxng"
    ADVANCED = "advanced"


class AcademicSearchProvider(Enum):
    ARXIV = "arxiv"


class Configuration(BaseModel):
    """Configuration options for the deep research assistant."""

    max_web_research_loops: int = Field(
        default=3,
        title="Research Depth",
        description="Number of research iterations to perform",
    )
    max_todo_items: int = Field(
        default=5,
        title="Planner Task Limit",
        description="Maximum number of planner-generated tasks to keep",
    )
    max_research_rounds: int = Field(
        default=3,
        title="Research Rounds",
        description="Maximum number of planner/reviewer research rounds per run",
    )
    max_parallel_research_tasks: int = Field(
        default=3,
        title="Parallel Research Tasks",
        description="Maximum number of same-round deep-research tasks to execute in parallel",
    )
    local_llm: str = Field(
        default="llama3.2",
        title="Local Model Name",
        description="Name of the locally hosted LLM (Ollama/LMStudio)",
    )
    llm_provider: str = Field(
        default="ollama",
        title="LLM Provider",
        description="Provider identifier (ollama, lmstudio, or custom)",
    )
    search_api: SearchAPI = Field(
        default=SearchAPI.DUCKDUCKGO,
        title="Search API",
        description="Web search API to use",
    )
    academic_search_provider: AcademicSearchProvider = Field(
        default=AcademicSearchProvider.ARXIV,
        title="Academic Search Provider",
        description="Academic search provider used for paper metadata retrieval",
    )
    academic_search_timeout_seconds: float = Field(
        default=6.0,
        title="Academic Search Timeout",
        description="Timeout in seconds for academic metadata lookups such as arXiv",
    )
    enable_notes: bool = Field(
        default=True,
        title="Enable Notes",
        description="Whether to store task progress in NoteTool",
    )
    notes_workspace: str = Field(
        default="./notes",
        title="Notes Workspace",
        description="Directory for NoteTool to persist task notes",
    )
    project_workspace_root: str = Field(
        default="./research_projects",
        title="Project Workspace Root",
        description="Directory for ARIS-style project state files and templates",
    )
    memory_database_url: Optional[str] = Field(
        default=None,
        title="Memory Database URL",
        description="PostgreSQL connection URL for structured research memory persistence",
    )
    task_log_retention_per_session: int = Field(
        default=40,
        title="Task Log Retention Per Session",
        description="Maximum number of task-log rows to retain per session",
    )
    fetch_full_page: bool = Field(
        default=True,
        title="Fetch Full Page",
        description="Include the full page content in the search results",
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        title="Ollama Base URL",
        description="Base URL for Ollama API (without /v1 suffix)",
    )
    lmstudio_base_url: str = Field(
        default="http://localhost:1234/v1",
        title="LMStudio Base URL",
        description="Base URL for LMStudio OpenAI-compatible API",
    )
    strip_thinking_tokens: bool = Field(
        default=True,
        title="Strip Thinking Tokens",
        description="Whether to strip <think> tokens from model responses",
    )
    use_tool_calling: bool = Field(
        default=False,
        title="Use Tool Calling",
        description="Use tool calling instead of JSON mode for structured output",
    )
    llm_api_key: Optional[str] = Field(
        default=None,
        title="LLM API Key",
        description="Optional API key when using custom OpenAI-compatible services",
    )
    llm_base_url: Optional[str] = Field(
        default=None,
        title="LLM Base URL",
        description="Optional base URL when using custom OpenAI-compatible services",
    )
    llm_model_id: Optional[str] = Field(
        default=None,
        title="LLM Model ID",
        description="Optional model identifier for custom OpenAI-compatible services",
    )
    embedding_model: Optional[str] = Field(
        default=None,
        title="Embedding Model ID",
        description="Optional embedding model identifier for semantic memory retrieval",
    )
    embedding_api_key: Optional[str] = Field(
        default=None,
        title="Embedding API Key",
        description="Optional API key for embedding requests",
    )
    embedding_base_url: Optional[str] = Field(
        default=None,
        title="Embedding Base URL",
        description="Optional base URL for embedding requests",
    )
    review_llm_provider: Optional[str] = Field(
        default=None,
        title="Review LLM Provider",
        description="Optional provider override for the external reviewer model",
    )
    review_llm_model_id: Optional[str] = Field(
        default=None,
        title="Review LLM Model ID",
        description="Optional model override for the external reviewer",
    )
    review_llm_api_key: Optional[str] = Field(
        default=None,
        title="Review LLM API Key",
        description="Optional API key override for the external reviewer",
    )
    review_llm_base_url: Optional[str] = Field(
        default=None,
        title="Review LLM Base URL",
        description="Optional base URL override for the external reviewer",
    )

    @classmethod
    def from_env(cls, overrides: Optional[dict[str, Any]] = None) -> "Configuration":
        """Create a configuration object using environment variables and overrides."""

        raw_values: dict[str, Any] = {}

        # Load values from environment variables based on field names
        for field_name in cls.model_fields.keys():
            env_key = field_name.upper()
            if env_key in os.environ:
                raw_values[field_name] = os.environ[env_key]

        # Additional mappings for explicit env names
        env_aliases = {
            "local_llm": os.getenv("LOCAL_LLM"),
            "llm_provider": os.getenv("LLM_PROVIDER"),
            "llm_api_key": os.getenv("LLM_API_KEY"),
            "llm_model_id": os.getenv("LLM_MODEL_ID") or os.getenv("LLM_MODEL"),
            "llm_base_url": os.getenv("LLM_BASE_URL"),
            "embedding_model": os.getenv("EMBEDDING_MODEL"),
            "embedding_api_key": os.getenv("EMBEDDING_API_KEY"),
            "embedding_base_url": os.getenv("EMBEDDING_BASE_URL"),
            "review_llm_provider": os.getenv("REVIEW_LLM_PROVIDER"),
            "review_llm_model_id": os.getenv("REVIEW_LLM_MODEL_ID")
            or os.getenv("REVIEW_LLM_MODEL"),
            "review_llm_api_key": os.getenv("REVIEW_LLM_API_KEY"),
            "review_llm_base_url": os.getenv("REVIEW_LLM_BASE_URL"),
            "lmstudio_base_url": os.getenv("LMSTUDIO_BASE_URL"),
            "ollama_base_url": os.getenv("OLLAMA_BASE_URL"),
            "max_web_research_loops": os.getenv("MAX_WEB_RESEARCH_LOOPS"),
            "max_todo_items": os.getenv("MAX_TODO_ITEMS"),
            "max_research_rounds": os.getenv("MAX_RESEARCH_ROUNDS"),
            "max_parallel_research_tasks": os.getenv("MAX_PARALLEL_RESEARCH_TASKS"),
            "fetch_full_page": os.getenv("FETCH_FULL_PAGE"),
            "strip_thinking_tokens": os.getenv("STRIP_THINKING_TOKENS"),
            "use_tool_calling": os.getenv("USE_TOOL_CALLING"),
            "search_api": os.getenv("SEARCH_API"),
            "academic_search_provider": os.getenv("ACADEMIC_SEARCH_PROVIDER"),
            "academic_search_timeout_seconds": os.getenv(
                "ACADEMIC_SEARCH_TIMEOUT_SECONDS"
            ),
            "enable_notes": os.getenv("ENABLE_NOTES"),
            "notes_workspace": os.getenv("NOTES_WORKSPACE"),
            "project_workspace_root": os.getenv("PROJECT_WORKSPACE_ROOT"),
            "memory_database_url": os.getenv("MEMORY_DATABASE_URL")
            or os.getenv("DATABASE_URL"),
            "task_log_retention_per_session": os.getenv(
                "TASK_LOG_RETENTION_PER_SESSION"
            ),
        }

        for key, value in env_aliases.items():
            if value is not None:
                raw_values.setdefault(key, value)

        if overrides:
            for key, value in overrides.items():
                if value is not None:
                    raw_values[key] = value

        # If a custom base URL is provided but no explicit provider is set,
        # avoid falling back to the local Ollama path.
        if raw_values.get("llm_base_url") and "llm_provider" not in raw_values:
            raw_values["llm_provider"] = "custom"

        return cls(**raw_values)

    def sanitized_ollama_url(self) -> str:
        """Ensure Ollama base URL includes the /v1 suffix required by OpenAI clients."""

        base = self.ollama_base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return base

    def resolved_model(self) -> Optional[str]:
        """Best-effort resolution of the model identifier to use."""

        return self.llm_model_id or self.local_llm

    def resolved_embedding_model(self) -> Optional[str]:
        """Best-effort resolution of the embedding model identifier to use."""

        return self.embedding_model

    def resolved_memory_database_url(self) -> Optional[str]:
        """Return the PostgreSQL connection URL when configured."""

        database_url = (self.memory_database_url or "").strip()
        return database_url or None

    def reviewer_config(self) -> "Configuration":
        """Return a config with reviewer-specific LLM overrides applied."""

        overrides: dict[str, Any] = {}
        if self.review_llm_provider:
            overrides["llm_provider"] = self.review_llm_provider
        if self.review_llm_model_id:
            overrides["llm_model_id"] = self.review_llm_model_id
        if self.review_llm_api_key:
            overrides["llm_api_key"] = self.review_llm_api_key
        if self.review_llm_base_url:
            overrides["llm_base_url"] = self.review_llm_base_url
        return self.model_copy(update=overrides)

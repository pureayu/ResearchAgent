from __future__ import annotations

from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.llm_client import LiteratureLLM
from app.models import Citation
from app.simple_vector_rag import SimpleVectorRAG


class LocalToolCitation(BaseModel):
    doc_id: str
    title: str
    filepath: str
    snippet: str
    content: str
    page: int | None = None
    score: float


class LocalLibrarySearchRequest(BaseModel):
    query: str
    top_k: int = 5
    retrieval_mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    filters: dict[str, str | int | bool | list[str]] | None = None


class LocalLibrarySearchResponse(BaseModel):
    tool_name: str = "LocalLibrarySearchTool"
    query: str
    resolved_mode: str
    top_k: int
    results: list[LocalToolCitation] = Field(default_factory=list)
    latency_ms: int


class LocalLibraryAnswerRequest(BaseModel):
    question: str
    evidence: list[LocalToolCitation] = Field(default_factory=list)
    response_type: str | None = None


class LocalLibraryAnswerResponse(BaseModel):
    tool_name: str = "LocalLibraryAnswerTool"
    question: str
    answer: str
    used_titles: list[str] = Field(default_factory=list)
    citation_count: int
    latency_ms: int


class LocalLibrarySearchTool:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.rag = SimpleVectorRAG(self.settings)

    def run(
        self,
        payload: LocalLibrarySearchRequest | dict,
    ) -> LocalLibrarySearchResponse:
        request = (
            payload
            if isinstance(payload, LocalLibrarySearchRequest)
            else LocalLibrarySearchRequest.model_validate(payload)
        )

        start = perf_counter()
        citations = self.rag.query(
            request.query,
            top_k=request.top_k,
            retrieval_mode=request.retrieval_mode,
        )
        latency_ms = int((perf_counter() - start) * 1000)

        results = [LocalToolCitation.model_validate(item.model_dump()) for item in citations]
        return LocalLibrarySearchResponse(
            query=request.query,
            resolved_mode=request.retrieval_mode,
            top_k=request.top_k,
            results=results,
            latency_ms=latency_ms,
        )


class LocalLibraryAnswerTool:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.llm = LiteratureLLM(self.settings)

    def run(
        self,
        payload: LocalLibraryAnswerRequest | dict,
    ) -> LocalLibraryAnswerResponse:
        request = (
            payload
            if isinstance(payload, LocalLibraryAnswerRequest)
            else LocalLibraryAnswerRequest.model_validate(payload)
        )

        citations = [
            Citation.model_validate(item.model_dump())
            for item in request.evidence
        ]

        start = perf_counter()
        answer = self.llm.answer_question(
            request.question,
            citations,
            stream=False,
        )
        latency_ms = int((perf_counter() - start) * 1000)

        used_titles = []
        for item in request.evidence:
            if item.title not in used_titles:
                used_titles.append(item.title)

        return LocalLibraryAnswerResponse(
            question=request.question,
            answer=answer,
            used_titles=used_titles,
            citation_count=len(request.evidence),
            latency_ms=latency_ms,
        )

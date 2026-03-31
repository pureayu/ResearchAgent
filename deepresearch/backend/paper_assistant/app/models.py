from typing import Literal

from pydantic import BaseModel, Field


class ChunkRecord(BaseModel):
    chunk_id: str
    order: int
    text: str
    page: int | None = None
    char_count: int


class ProcessedDocument(BaseModel):
    id: str
    filename: str
    title: str
    filepath: str
    filetype: str
    checksum: str
    text: str
    chunks: list[ChunkRecord] = Field(default_factory=list)
    extracted_at: str


class DocumentRecord(BaseModel):
    id: str
    filename: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    tags: list[str] = Field(default_factory=list)
    filepath: str
    filetype: str
    checksum: str
    status: Literal["processed", "imported", "failed", "skipped"] = "processed"
    text_chars: int = 0
    chunk_count: int = 0
    imported_at: str | None = None
    last_error: str | None = None
    abstract: str | None = None
    notes: str | None = None
    processed_path: str | None = None


class Citation(BaseModel):
    doc_id: str
    title: str
    filepath: str
    snippet: str
    content: str
    page: int | None = None
    score: float


class ManifestEntry(BaseModel):
    file_name: str | None = None
    filepath: str | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    tags: list[str] = Field(default_factory=list)
    abstract: str | None = None
    notes: str | None = None

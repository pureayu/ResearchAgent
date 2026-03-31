from pathlib import Path
import re

import pdfplumber

from app.models import ChunkRecord, ProcessedDocument
from app.utils import chunk_text, normalize_whitespace, now_iso, sha256_file, slugify

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md"}


class DocumentExtractionError(RuntimeError):
    pass


def scan_documents(raw_dir: Path) -> tuple[list[Path], list[Path]]:
    supported: list[Path] = []
    skipped: list[Path] = []
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            supported.append(path)
        else:
            skipped.append(path)
    return supported, skipped


def extract_document(path: Path) -> ProcessedDocument:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DocumentExtractionError(f"Unsupported file type: {suffix}")

    if suffix == ".pdf":
        chunks, full_text = _extract_pdf(path)
    else:
        chunks, full_text = _extract_text_like(path)

    if not full_text.strip():
        raise DocumentExtractionError("Extracted text is empty")

    title = path.stem.replace("_", " ").replace("-", " ").strip()
    checksum = sha256_file(path)
    doc_id = f"{slugify(path.stem)}-{checksum[:12]}"
    return ProcessedDocument(
        id=doc_id,
        filename=path.name,
        title=title,
        filepath=str(path.resolve()),
        filetype=suffix.lstrip("."),
        checksum=checksum,
        text=full_text,
        chunks=chunks,
        extracted_at=now_iso(),
    )


def _extract_text_like(path: Path) -> tuple[list[ChunkRecord], str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    clean_text = normalize_whitespace(text)
    chunks = _build_chunks([(None, clean_text)])
    return chunks, clean_text


def _extract_pdf(path: Path) -> tuple[list[ChunkRecord], str]:
    page_texts = _extract_pdf_with_fitz(path) if fitz is not None else []
    if not page_texts:
        page_texts = _extract_pdf_with_pdfplumber(path)

    header_footer_noise = _detect_repeated_margin_lines(page_texts)
    sections: list[tuple[int | None, str]] = []
    for page_number, page_text in page_texts:
        cleaned_page = _clean_pdf_page(page_text, page_number, header_footer_noise)
        clean_text = normalize_whitespace(cleaned_page)
        if clean_text:
            sections.append((page_number, clean_text))

    if not sections:
        raise DocumentExtractionError("No readable text found in PDF")

    chunks = _build_chunks(sections)
    full_text = "\n\n".join(text for _, text in sections)
    return chunks, full_text


def _extract_pdf_with_fitz(path: Path) -> list[tuple[int, str]]:
    if fitz is None:  # pragma: no cover
        return []
    pages: list[tuple[int, str]] = []
    with fitz.open(path) as document:
        for page_index, page in enumerate(document, start=1):
            pages.append((page_index, page.get_text("text")))
    return pages


def _extract_pdf_with_pdfplumber(path: Path) -> list[tuple[int, str]]:
    pages: list[tuple[int, str]] = []
    with pdfplumber.open(path) as document:
        for page_index, page in enumerate(document.pages, start=1):
            pages.append((page_index, page.extract_text() or ""))
    return pages


def _build_chunks(sections: list[tuple[int | None, str]]) -> list[ChunkRecord]:
    records: list[ChunkRecord] = []
    for page_number, section_text in sections:
        for piece in chunk_text(section_text):
            order = len(records) + 1
            records.append(
                ChunkRecord(
                    chunk_id=f"chunk-{order:04d}",
                    order=order,
                    text=piece,
                    page=page_number,
                    char_count=len(piece),
                )
            )
    return records


def _detect_repeated_margin_lines(page_texts: list[tuple[int, str]]) -> set[str]:
    counts: dict[str, int] = {}
    for _, text in page_texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        margin_lines = lines[:2] + lines[-2:]
        seen_in_page: set[str] = set()
        for line in margin_lines:
            key = _normalize_margin_line(line)
            if not key or key in seen_in_page:
                continue
            counts[key] = counts.get(key, 0) + 1
            seen_in_page.add(key)

    repeated: set[str] = set()
    for key, count in counts.items():
        if count >= 2:
            repeated.add(key)
    return repeated


def _clean_pdf_page(text: str, page_number: int, repeated_margin_lines: set[str]) -> str:
    lines = [line.strip() for line in text.splitlines()]
    cleaned_lines: list[str] = []
    for line in lines:
        if not line:
            cleaned_lines.append("")
            continue
        normalized = _normalize_margin_line(line)
        if normalized in repeated_margin_lines:
            continue
        if _is_page_number_line(line):
            continue
        if page_number == 1 and _looks_like_author_line(line):
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", cleaned)
    cleaned = re.sub(r"[†‡⋆]+", "", cleaned)
    return cleaned


def _normalize_margin_line(line: str) -> str:
    line = line.lower().strip()
    line = re.sub(r"\s+", " ", line)
    line = re.sub(r"\b\d+\b", "#", line)
    if len(line) < 4:
        return ""
    return line


def _is_page_number_line(line: str) -> bool:
    compact = line.strip().lower()
    return bool(
        re.fullmatch(r"\d+", compact)
        or re.fullmatch(r"page\s+\d+", compact)
        or re.fullmatch(r"\d+\s*/\s*\d+", compact)
    )


def _looks_like_author_line(line: str) -> bool:
    lowered = line.lower()
    if "@" in lowered:
        return True
    if any(token in lowered for token in ("university", "research", "institute", "college", "laboratory")) and "," in line:
        return True
    if line.count(",") >= 3 and any(symbol in line for symbol in ("†", "‡", "⋆")):
        return True
    return False

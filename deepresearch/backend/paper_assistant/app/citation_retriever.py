import re
from collections import Counter
from pathlib import Path

from app.models import Citation, DocumentRecord, ProcessedDocument
from app.utils import load_json


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
REFERENCE_PATTERN = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")
AUTHOR_MARK_PATTERN = re.compile(r"[†‡⋆*]{1,}")
BROKEN_PREFIX_PATTERN = re.compile(r"^[a-z]{1,3}[).,\-]\s")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？.!?])\s+|(?<=\.)\n+|\n{2,}")
CHALLENGE_HINTS = {"challenge", "challenges", "挑战", "问题", "困难", "limitation", "limitations"}


def retrieve_citations(
    query: str,
    documents: list[DocumentRecord],
    processed_dir: Path,
    top_k: int = 5,
) -> list[Citation]:
    query_terms = _tokenize(query)
    if not query_terms:
        return []

    scored: list[Citation] = []
    for record in documents:
        if not record.processed_path:
            continue
        processed_payload = load_json(Path(record.processed_path), None)
        if not processed_payload:
            continue
        processed = ProcessedDocument.model_validate(processed_payload)
        title_terms = Counter(_tokenize(record.title))

        for chunk in processed.chunks:
            score = _score_chunk(query, query_terms, title_terms, chunk.text)
            if score <= 0:
                continue
            snippet = _build_snippet(chunk.text, query_terms)
            scored.append(
                Citation(
                    doc_id=record.id,
                    title=record.title,
                    filepath=record.filepath,
                    snippet=snippet,
                    content=chunk.text,
                    page=chunk.page,
                    score=score,
                )
            )

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:top_k]


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text) if len(token.strip()) > 1]


def _score_chunk(query: str, query_terms: list[str], title_terms: Counter[str], chunk_text: str) -> float:
    normalized_text = _normalize_for_matching(chunk_text)
    chunk_terms = Counter(_tokenize(normalized_text))
    if not chunk_terms:
        return 0.0

    overlap = sum(min(1, chunk_terms[term]) for term in query_terms)
    if overlap == 0:
        return 0.0

    title_overlap = sum(min(1, title_terms[term]) for term in query_terms)
    phrase_bonus = 0.45 if query.lower() in normalized_text.lower() else 0.0
    density = overlap / max(len(query_terms), 1)
    repetition_bonus = min(sum(chunk_terms[term] for term in query_terms), 6) * 0.05
    challenge_bonus = _challenge_bonus(query_terms, normalized_text)
    heading_bonus = _heading_bonus(normalized_text)
    noise_penalty = _noise_penalty(chunk_text)
    return density + title_overlap * 0.15 + phrase_bonus + repetition_bonus + challenge_bonus + heading_bonus - noise_penalty


def _normalize_for_matching(text: str) -> str:
    text = text.replace("\n", " ")
    text = REFERENCE_PATTERN.sub(" ", text)
    text = AUTHOR_MARK_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _build_snippet(text: str, query_terms: list[str], window_chars: int = 280) -> str:
    clean_text = _normalize_for_display(text)
    if len(clean_text) <= window_chars:
        return clean_text

    sentence_window = _build_sentence_window(clean_text, query_terms, window_chars)
    if sentence_window:
        return sentence_window

    return f"{clean_text[: window_chars - 3].strip()}..."


def _normalize_for_display(text: str) -> str:
    text = text.replace("\n", " ")
    text = AUTHOR_MARK_PATTERN.sub(" ", text)
    text = re.sub(r"\s*\[[0-9,\s]+\]\s*", " ", text)
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _build_sentence_window(text: str, query_terms: list[str], window_chars: int) -> str:
    sentences = [part.strip() for part in SENTENCE_SPLIT_PATTERN.split(text) if part.strip()]
    if not sentences:
        return ""

    lower_terms = [term.lower() for term in query_terms]
    best_index = -1
    best_score = -1.0
    for index, sentence in enumerate(sentences):
        lower_sentence = sentence.lower()
        score = sum(1 for term in lower_terms if term in lower_sentence)
        if any(hint in lower_sentence for hint in CHALLENGE_HINTS):
            score += 1.5
        if score > best_score:
            best_index = index
            best_score = score

    if best_index < 0:
        return ""

    selected = [sentences[best_index]]
    current_len = len(selected[0])

    left = best_index - 1
    right = best_index + 1
    while current_len < window_chars and (left >= 0 or right < len(sentences)):
        candidates: list[tuple[int, str]] = []
        if left >= 0:
            candidates.append((left, sentences[left]))
        if right < len(sentences):
            candidates.append((right, sentences[right]))

        if not candidates:
            break

        chosen_index, chosen_sentence = max(
            candidates,
            key=lambda item: _sentence_priority(item[1], lower_terms),
        )
        if current_len + len(chosen_sentence) + 1 > window_chars and selected:
            break

        if chosen_index < best_index:
            selected.insert(0, chosen_sentence)
            left -= 1
        else:
            selected.append(chosen_sentence)
            right += 1
        current_len = sum(len(part) for part in selected) + max(len(selected) - 1, 0)

    snippet = " ".join(selected).strip()
    if best_index > 0 and not snippet.startswith(sentences[0]):
        snippet = f"...{snippet}"
    if best_index < len(sentences) - 1 and not snippet.endswith(sentences[-1]):
        snippet = f"{snippet}..."
    return snippet


def _sentence_priority(sentence: str, query_terms: list[str]) -> float:
    lower_sentence = sentence.lower()
    score = sum(1 for term in query_terms if term in lower_sentence)
    if any(hint in lower_sentence for hint in CHALLENGE_HINTS):
        score += 1.2
    if lower_sentence.startswith(("introduction", "abstract", "keywords")):
        score -= 0.5
    return score


def _challenge_bonus(query_terms: list[str], text: str) -> float:
    if not any(term in CHALLENGE_HINTS for term in query_terms):
        return 0.0
    hits = sum(1 for hint in CHALLENGE_HINTS if hint in text.lower())
    return min(hits, 3) * 0.12


def _heading_bonus(text: str) -> float:
    lower_text = text.lower()
    if any(marker in lower_text for marker in ("## ", "challenge", "limitation", "problem statement", "main challenges")):
        return 0.12
    return 0.0


def _noise_penalty(text: str) -> float:
    penalty = 0.0
    reference_hits = len(REFERENCE_PATTERN.findall(text))
    if reference_hits:
        penalty += min(reference_hits, 4) * 0.05

    author_marks = len(AUTHOR_MARK_PATTERN.findall(text))
    if author_marks:
        penalty += min(author_marks, 4) * 0.08

    first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if BROKEN_PREFIX_PATTERN.match(first_line.lower()):
        penalty += 0.2

    if first_line.count(",") >= 4 and any(symbol in first_line for symbol in ("†", "‡", "⋆")):
        penalty += 0.35

    return penalty

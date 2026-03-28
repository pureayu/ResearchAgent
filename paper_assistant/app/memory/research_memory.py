# 记录和保存过往对话中重要的高价值研究结论，单独存成更稳定、更可复用的研究笔记。
from datetime import datetime, timezone
from pathlib import Path

from app.memory.models import ResearchNote
from app.memory.store import MemoryStore


class ResearchMemory:
    def __init__(self, memory_dir: Path):
        self.store = MemoryStore(memory_dir)

    def append_note(
        self,
        session_id: str,
        question: str,
        conclusion: str,
        citation_titles: list[str] | None = None,
    ) -> None:
        note_session = self.store.load_research_note_session(session_id)
        next_note_id = len(note_session.notes) + 1
        note_session.notes.append(
            ResearchNote(
                note_id=next_note_id,
                session_id=session_id,
                question=question,
                conclusion=conclusion,
                citation_titles=citation_titles or [],
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        self.store.save_research_note_session(note_session)

    def list_notes(self, session_id: str) -> list[ResearchNote]:
        note_session = self.store.load_research_note_session(session_id)
        return note_session.notes

    def format_notes(self, session_id: str) -> str:
        notes = self.list_notes(session_id)
        if not notes:
            return ""

        lines: list[str] = []
        for note in notes:
            lines.append(f"[Note {note.note_id}] 问题：{note.question}")
            lines.append(f"结论：{note.conclusion}")
        return "\n".join(lines)

    def clear_notes(self, session_id: str) -> None:
        note_session = self.store.load_research_note_session(session_id)
        note_session.notes = []
        self.store.save_research_note_session(note_session)

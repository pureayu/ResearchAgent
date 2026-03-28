from pathlib import Path

from app.memory.models import ConversationSession, ResearchNoteSession
from app.utils import dump_json, load_json


class MemoryStore:
    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.research_dir = self.memory_dir / "research"
        self.research_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self.memory_dir / f"{session_id}.json"

    def load_session(self, session_id: str) -> ConversationSession:
        path = self._session_path(session_id)
        payload = load_json(path, None)
        if payload is None:
            return ConversationSession(session_id=session_id)
        return ConversationSession.model_validate(payload)

    def save_session(self, session: ConversationSession) -> Path:
        path = self._session_path(session.session_id)
        dump_json(path, session.model_dump(mode="json"))
        return path

    def _research_session_path(self, session_id: str) -> Path:
        return self.research_dir / f"{session_id}.json"

    def load_research_note_session(self, session_id: str) -> ResearchNoteSession:
        path = self._research_session_path(session_id)
        payload = load_json(path, None)
        if payload is None:
            return ResearchNoteSession(session_id=session_id)
        return ResearchNoteSession.model_validate(payload)

    def save_research_note_session(self, note_session: ResearchNoteSession) -> Path:
        path = self._research_session_path(note_session.session_id)
        dump_json(path, note_session.model_dump(mode="json"))
        return path

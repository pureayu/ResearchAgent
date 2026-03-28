from pathlib import Path

from app.memory.models import ConversationTurn, ResearchNote
from app.memory.research_memory import ResearchMemory
from app.memory.working_memory import WorkingMemory


class MemoryManager:
    """Unified entrypoint for memory operations.

    For now the manager only coordinates working memory.
    Later we can add research memory and consolidation here
    without changing the external call sites.
    """

    def __init__(self, memory_dir: Path, max_turns: int = 5):
        self.working_memory = WorkingMemory(memory_dir, max_turns=max_turns)
        self.research_memory = ResearchMemory(memory_dir)

    def append_turn(
        self,
        session_id: str,
        question: str,
        answer: str,
        resolved_question: str | None = None,
        citation_titles: list[str] | None = None,
    ) -> None:
        self.working_memory.append_turn(
            session_id=session_id,
            question=question,
            answer=answer,
            resolved_question=resolved_question,
            citation_titles=citation_titles,
        )

    def get_recent_turns(
        self,
        session_id: str,
        max_turns: int | None = None,
    ) -> list[ConversationTurn]:
        return self.working_memory.get_recent_turns(
            session_id=session_id,
            max_turns=max_turns,
        )

    def has_history(self, session_id: str) -> bool:
        return self.working_memory.has_history(session_id)

    def format_history(self, session_id: str, max_turns: int | None = None) -> str:
        return self.working_memory.format_history(
            session_id=session_id,
            max_turns=max_turns,
        )

    def search_relevant_turns(
        self,
        session_id: str,
        query: str,
        limit: int = 3,
    ) -> list[ConversationTurn]:
        return self.working_memory.search_relevant_turns(
            session_id=session_id,
            query=query,
            limit=limit,
        )

    def format_relevant_turns(
        self,
        session_id: str,
        query: str,
        limit: int = 3,
    ) -> str:
        return self.working_memory.format_relevant_turns(
            session_id=session_id,
            query=query,
            limit=limit,
        )

    def clear_session(self, session_id: str) -> None:
        self.working_memory.clear_session(session_id)

    def append_note(
        self,
        session_id: str,
        question: str,
        conclusion: str,
        citation_titles: list[str] | None = None,
    ) -> None:
        self.research_memory.append_note(
            session_id=session_id,
            question=question,
            conclusion=conclusion,
            citation_titles=citation_titles,
        )

    def list_notes(self, session_id: str) -> list[ResearchNote]:
        return self.research_memory.list_notes(session_id)

    def format_notes(self, session_id: str) -> str:
        return self.research_memory.format_notes(session_id)

    def clear_notes(self, session_id: str) -> None:
        self.research_memory.clear_notes(session_id)

from pathlib import Path

from app.memory.models import ConversationTurn
from app.memory.working_memory import WorkingMemory


class MemoryManager:
    """Unified entrypoint for memory operations.

    For now the manager only coordinates working memory.
    Later we can add research memory and consolidation here
    without changing the external call sites.
    """

    def __init__(self, memory_dir: Path, max_turns: int = 5):
        self.working_memory = WorkingMemory(memory_dir, max_turns=max_turns)

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

    def clear_session(self, session_id: str) -> None:
        self.working_memory.clear_session(session_id)

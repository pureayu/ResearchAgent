#负责业务逻辑
from datetime import datetime, timezone
from pathlib import Path

from app.memory.models import ConversationTurn
from app.memory.store import MemoryStore

class MemoryManager:
    def __init__(self, memory_dir: Path, max_turns: int = 5):
        self.store = MemoryStore(memory_dir)
        self.max_turns = max_turns

    #做参数封装
    def append_turn(
            self,
            session_id:str,
            question:str, 
            answer:str,
            resolved_question: str | None = None,
            citation_titles: list[str] | None = None,
    )->None:
        session = self.store.load_session(session_id)
        next_turn_id = len(session.turns) + 1
        session.turns.append(
            ConversationTurn(
                turn_id=next_turn_id,
                question=question,
                resolved_question=resolved_question,
                answer=answer,
                created_at=datetime.now(timezone.utc).isoformat(),
                citation_titles=citation_titles or [],
            )
        )
        session.turns = session.turns[-self.max_turns :]
        self.store.save_session(session)
    
    #加载近几轮对话
    def get_recent_turns(self, session_id:str, max_turns:int|None)->list[ConversationTurn]:
        session = self.store.load_session(session_id)
        limit = max_turns or self.max_turns
        recent_turns = session.turns[-limit:]
        return recent_turns
    
    def has_history(self, session_id:str)->bool:
        return len(self.store.load_session(session_id).turns) > 0
    

    #格式化获取的近几轮对话，交给模型
    def format_history(self, session_id: str, max_turns: int | None = None)->str:
        turns = self.get_recent_turns(session_id, max_turns=max_turns)
        if not turns:
            return ""
        lines: list[str] = []
        for turn in turns:
            lines.append(f"用户：{turn.question}")
            lines.append(f"助手：{turn.answer}")
        return "\n".join(lines)
    
    def clear_session(self, session_id:str)->None:
        session = self.store.load_session(session_id)
        session.turns = []
        self.store.save_session(session)


#负责业务逻辑
from datetime import datetime, timezone
from pathlib import Path
import re

from app.memory.models import ConversationTurn
from app.memory.store import MemoryStore


class WorkingMemory:
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

    def search_relevant_turns(
        self,
        session_id: str,
        query: str,
        limit: int = 3,
    ) -> list[ConversationTurn]:
        session = self.store.load_session(session_id)
        if not session.turns:
            return []

        query_tokens = _tokenize_text(query)
        if not query_tokens:
            return []

        scored_turns: list[tuple[float, ConversationTurn]] = []
        total_turns = len(session.turns)

        for index, turn in enumerate(session.turns, start=1):
            search_text = " ".join(
                [
                    turn.question,
                    turn.resolved_question or "",
                    turn.answer,
                    " ".join(turn.citation_titles),
                ]
            )
            text_tokens = _tokenize_text(search_text)
            if not text_tokens:
                continue

            overlap = query_tokens.intersection(text_tokens)
            overlap_score = len(overlap) / len(query_tokens) if query_tokens else 0.0
            substring_score = 0.0
            if query.strip() and query.strip().lower() in search_text.lower():
                substring_score = 1.0

            recency_score = index / total_turns
            final_score = overlap_score * 0.7 + substring_score * 0.2 + recency_score * 0.1

            if final_score > 0:
                scored_turns.append((final_score, turn))

        scored_turns.sort(key=lambda item: item[0], reverse=True)
        return [turn for _, turn in scored_turns[:limit]]

    def format_relevant_turns(
        self,
        session_id: str,
        query: str,
        limit: int = 3,
    ) -> str:
        turns = self.search_relevant_turns(session_id=session_id, query=query, limit=limit)
        if not turns:
            return ""

        lines: list[str] = []
        for turn in turns:
            lines.append(f"相关历史问题：{turn.question}")
            lines.append(f"相关历史回答：{turn.answer}")
        return "\n".join(lines)
    
    def clear_session(self, session_id:str)->None:
        session = self.store.load_session(session_id)
        session.turns = []
        self.store.save_session(session)


def _tokenize_text(text: str) -> set[str]:
    lowered = text.lower()
    english_tokens = re.findall(r"[a-z0-9_]+", lowered)
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", lowered)
    stop_chars = {"的", "了", "呢", "吗", "呀", "啊", "是", "这", "那"}
    filtered_chinese = [char for char in chinese_chars if char not in stop_chars]
    return {token for token in [*english_tokens, *filtered_chinese] if token.strip()}

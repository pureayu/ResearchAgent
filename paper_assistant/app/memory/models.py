from pydantic import BaseModel, Field

#定义memory的数据结构，字段规范
class ConversationTurn(BaseModel):
    #第几轮
    turn_id: int
    #原始问题
    question: str
    #补全后的问题：那它和微调有什么区别？->RAG 和微调有什么区别？
    resolved_question: str | None = None
    #这一轮系统的回答
    answer: str
    #这轮问答发生的时间
    created_at: str
    #citation_titles
    citation_titles: list[str] = Field(default_factory=list)


#表示一个会话，其中可能会包含多轮ConversationTurn
class ConversationSession(BaseModel):
    session_id: str
    turns: list[ConversationTurn] = Field(default_factory=list)


#一条研究结论
class ResearchNote(BaseModel):
    note_id: int
    session_id: str
    question: str
    conclusion: str
    citation_titles: list[str] = Field(default_factory=list)
    created_at: str

#一个session下的一组研究结论
class ResearchNoteSession(BaseModel):
    session_id: str
    notes: list[ResearchNote] = Field(default_factory=list)
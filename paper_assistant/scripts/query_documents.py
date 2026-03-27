import argparse
import asyncio
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.llm_client import LiteratureLLM
from app.rag_engine import LightRAGService
from app.simple_vector_rag import SimpleVectorRAG
from app.memory.manager import MemoryManager



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ask a question against local literature.")
    parser.add_argument("question", type=str, help="Question to ask.")
    parser.add_argument("--mode", type=str, default=None, help="LightRAG query mode, e.g. hybrid/local/global.")
    parser.add_argument("--backend", choices=("lightrag", "local", "simple"), default="lightrag")
    parser.add_argument(
        "--retrieval-mode",
        choices=("hybrid", "vector", "bm25"),
        default="hybrid",
        help="Retrieval mode for the simple backend.",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Number of citation snippets to display.")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming answer output.")
    parser.add_argument("--session-id", type=str, default=None, help="Session ID for multi-turn memory.")
    return parser


def _should_rewrite_question(question: str) -> bool:
    follow_up_patterns = [
        r"\b(it|this|that|they|former|latter)\b",
        r"它",
        r"这个",
        r"那个",
        r"上一个",
        r"上一",
        r"前一个",
        r"前者",
        r"后者",
        r"第二篇",
        r"第一篇",
        r"第三篇",
        r"这篇",
        r"那篇",
        r"该方法",
        r"这个方法",
        r"上一个方法",
    ]
    return any(re.search(pattern, question, re.IGNORECASE) for pattern in follow_up_patterns)


async def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    memory_manager = MemoryManager(settings.memory_dir)
    citations = []
    stream = not args.no_stream
    resolved_question = args.question
    llm = LiteratureLLM(settings)
    history_text = ""
    if args.session_id:
        history_text = memory_manager.format_history(args.session_id)
    if history_text and _should_rewrite_question(args.question):
        resolved_question = llm.rewrite_question(args.question, history_text)
        print("\n=== RESOLVED QUESTION ===")
        print(resolved_question)
    print("\n=== 回答 ===")
    answer = ""
    if args.backend == "lightrag":
        try:
            async with LightRAGService(settings) as rag:
                answer = await rag.query(
                    resolved_question,
                    mode=args.mode or settings.default_query_mode,
                    stream=stream,
                    on_chunk=lambda text: print(text, end="", flush=True),
                )
        except Exception as exc:
            print(f"LightRAG query failed, falling back to local answer synthesis: {exc}")
    elif args.backend == "simple":
        simple_rag = SimpleVectorRAG(settings)
        citations = simple_rag.query(
            resolved_question,
            top_k=args.top_k,
            retrieval_mode=args.retrieval_mode,
        )

    if not answer:
        answer = llm.answer_question(
            resolved_question,
            citations,
            stream=stream,
            on_chunk=lambda text: print(text, end="", flush=True),
        )

    if stream:
        print()
    else:
        print(answer or "没有生成有效回答。")
    if args.session_id:
        memory_manager.append_turn(
            session_id=args.session_id,
            question=args.question,
            answer=answer,
            resolved_question=resolved_question,
            citation_titles=[citation.title for citation in citations],

        )
    print("\n=== 引用来源 ===")
    if not citations:
        print("没有检索到可展示的引用。")
        return

    for index, citation in enumerate(citations, start=1):
        page_text = f" | page {citation.page}" if citation.page else ""
        print(f"[{index}] {citation.title}{page_text}")
        print(f"    {citation.filepath}")
        print(f"    {citation.snippet}")


if __name__ == "__main__":
    asyncio.run(main())

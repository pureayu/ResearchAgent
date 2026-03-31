import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.llm_client import LiteratureLLM
from app.simple_vector_rag import SimpleVectorRAG



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ask a question against local literature using the local pgvector index."
    )
    parser.add_argument("question", type=str, help="Question to ask.")
    parser.add_argument(
        "--retrieval-mode",
        choices=("hybrid", "vector", "bm25"),
        default="hybrid",
        help="Retrieval mode for the local retrieval backend.",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Number of citation snippets to display.")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming answer output.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    stream = not args.no_stream
    resolved_question = args.question
    llm = LiteratureLLM(settings)
    simple_rag = SimpleVectorRAG(settings)
    citations = simple_rag.query(
        resolved_question,
        top_k=args.top_k,
        retrieval_mode=args.retrieval_mode,
    )
    print("\n=== 回答 ===")
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
    main()

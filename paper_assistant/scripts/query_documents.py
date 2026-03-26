import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.citation_retriever import retrieve_citations
from app.config import get_settings
from app.llm_client import LiteratureLLM
from app.metadata_store import MetadataStore
from app.rag_engine import LightRAGService
from app.simple_vector_rag import SimpleVectorRAG


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
    return parser


async def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    store = MetadataStore(settings.metadata_file, settings.manifest_file)
    documents = [item for item in store.load_documents() if item.status in {"processed", "imported"}]
    citations = retrieve_citations(args.question, documents, settings.processed_dir, top_k=args.top_k)
    stream = not args.no_stream

    print("\n=== 回答 ===")
    answer = ""
    if args.backend == "lightrag":
        try:
            async with LightRAGService(settings) as rag:
                answer = await rag.query(
                    args.question,
                    mode=args.mode or settings.default_query_mode,
                    stream=stream,
                    on_chunk=lambda text: print(text, end="", flush=True),
                )
        except Exception as exc:
            print(f"LightRAG query failed, falling back to local answer synthesis: {exc}")
    elif args.backend == "simple":
        simple_rag = SimpleVectorRAG(settings)
        citations = simple_rag.query(
            args.question,
            top_k=args.top_k,
            retrieval_mode=args.retrieval_mode,
        )

    if not answer:
        llm = LiteratureLLM(settings)
        answer = llm.answer_question(
            args.question,
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
    asyncio.run(main())

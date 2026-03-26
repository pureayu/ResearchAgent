import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.citation_retriever import retrieve_citations
from app.config import get_settings
from app.llm_client import LiteratureLLM
from app.metadata_store import MetadataStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a topic summary from local literature.")
    parser.add_argument("topic", type=str, help="Topic to summarize.")
    parser.add_argument("--top-k", type=int, default=8, help="Number of evidence snippets to use.")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming summary output.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    store = MetadataStore(settings.metadata_file, settings.manifest_file)
    documents = [item for item in store.load_documents() if item.status in {"processed", "imported"}]
    citations = retrieve_citations(args.topic, documents, settings.processed_dir, top_k=args.top_k)
    stream = not args.no_stream

    llm = LiteratureLLM(settings)
    print("\n=== 主题总结 ===")
    summary = llm.summarize_topic(
        args.topic,
        citations,
        stream=stream,
        on_chunk=lambda text: print(text, end="", flush=True),
    )

    if stream:
        print()
    else:
        print(summary or "没有生成有效总结。")

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

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.citation_retriever import retrieve_citations
from app.config import get_settings
from app.metadata_store import MetadataStore
from app.simple_vector_rag import SimpleVectorRAG
from app.utils import dump_json


DEFAULT_MODES = ("local", "simple-bm25", "simple-vector", "simple-hybrid")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality across local/simple backends.")
    parser.add_argument(
        "--questions-file",
        type=Path,
        default=PROJECT_ROOT / "data" / "metadata" / "eval_questions.json",
        help="Path to retrieval evaluation question set.",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        default=list(DEFAULT_MODES),
        help="Modes to evaluate: local, simple-bm25, simple-vector, simple-hybrid",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Top-k citations to evaluate.")
    parser.add_argument(
        "--save-json",
        type=Path,
        default=None,
        help="Optional path to save raw evaluation results as JSON.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    store = MetadataStore(settings.metadata_file, settings.manifest_file)
    documents = [item for item in store.load_documents() if item.status in {"processed", "imported"}]
    questions = json.loads(args.questions_file.read_text())
    simple_rag = SimpleVectorRAG(settings)

    raw_results: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    category_summaries: list[dict[str, object]] = []
    for mode in args.modes:
        mode_rows: list[dict[str, object]] = []
        for item in questions:
            started = time.perf_counter()
            citations = _run_mode(
                mode=mode,
                question=item["question"],
                top_k=args.top_k,
                documents=documents,
                processed_dir=settings.processed_dir,
                simple_rag=simple_rag,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            retrieved_titles = [citation.title for citation in citations]
            hit_at_1 = int(bool(retrieved_titles) and retrieved_titles[0] in item["expected_titles"])
            hit_at_k = int(any(title in item["expected_titles"] for title in retrieved_titles))
            reciprocal_rank = _reciprocal_rank(retrieved_titles, item["expected_titles"])
            row = {
                "mode": mode,
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "expected_titles": item["expected_titles"],
                "retrieved_titles": retrieved_titles,
                "hit_at_1": hit_at_1,
                "hit_at_k": hit_at_k,
                "mrr": reciprocal_rank,
                "latency_ms": latency_ms,
            }
            mode_rows.append(row)
            raw_results.append(row)

        summaries.append(_summarize_mode(mode, mode_rows))
        categories = sorted({str(row["category"]) for row in mode_rows})
        for category in categories:
            category_rows = [row for row in mode_rows if row["category"] == category]
            summary = _summarize_mode(mode, category_rows)
            summary["category"] = category
            category_summaries.append(summary)

    _print_summary(summaries)
    _print_category_summary(category_summaries)

    if args.save_json is not None:
        dump_json(args.save_json, raw_results)
        print(f"\nSaved raw results to {args.save_json}")


def _run_mode(
    mode: str,
    question: str,
    top_k: int,
    documents,
    processed_dir: Path,
    simple_rag: SimpleVectorRAG,
):
    if mode == "local":
        return retrieve_citations(question, documents, processed_dir, top_k=top_k)
    if mode == "simple-bm25":
        return simple_rag.query(question, top_k=top_k, retrieval_mode="bm25")
    if mode == "simple-vector":
        return simple_rag.query(question, top_k=top_k, retrieval_mode="vector")
    if mode == "simple-hybrid":
        return simple_rag.query(question, top_k=top_k, retrieval_mode="hybrid")
    raise ValueError(f"Unsupported mode: {mode}")


def _reciprocal_rank(retrieved_titles: list[str], expected_titles: list[str]) -> float:
    for index, title in enumerate(retrieved_titles, start=1):
        if title in expected_titles:
            return 1.0 / index
    return 0.0


def _summarize_mode(mode: str, rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "mode": mode,
        "count": len(rows),
        "hit_at_1": sum(int(row["hit_at_1"]) for row in rows) / max(len(rows), 1),
        "hit_at_k": sum(int(row["hit_at_k"]) for row in rows) / max(len(rows), 1),
        "mrr": sum(float(row["mrr"]) for row in rows) / max(len(rows), 1),
        "latency_ms": statistics.mean(float(row["latency_ms"]) for row in rows) if rows else 0.0,
    }


def _print_summary(summaries: list[dict[str, object]]) -> None:
    print("mode            count  hit@1  hit@k  mrr    avg_latency_ms")
    print("----------------------------------------------------------")
    for item in summaries:
        print(
            f"{str(item['mode']):<15} "
            f"{int(item['count']):>5}  "
            f"{float(item['hit_at_1']):>5.2f}  "
            f"{float(item['hit_at_k']):>5.2f}  "
            f"{float(item['mrr']):>5.2f}  "
            f"{float(item['latency_ms']):>14.1f}"
        )


def _print_category_summary(summaries: list[dict[str, object]]) -> None:
    print("\ncategory breakdown")
    print("mode            category        count  hit@1  hit@k  mrr")
    print("--------------------------------------------------------")
    for item in summaries:
        print(
            f"{str(item['mode']):<15} "
            f"{str(item['category']):<14} "
            f"{int(item['count']):>5}  "
            f"{float(item['hit_at_1']):>5.2f}  "
            f"{float(item['hit_at_k']):>5.2f}  "
            f"{float(item['mrr']):>5.2f}"
        )


if __name__ == "__main__":
    main()

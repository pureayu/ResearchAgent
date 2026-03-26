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
from app.llm_client import LiteratureLLM
from app.metadata_store import MetadataStore
from app.simple_vector_rag import SimpleVectorRAG
from app.utils import dump_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate answer quality for local/simple backends.")
    parser.add_argument(
        "--questions-file",
        type=Path,
        default=PROJECT_ROOT / "data" / "metadata" / "answer_eval_questions.json",
        help="Path to answer evaluation question set.",
    )
    parser.add_argument(
        "--backend",
        choices=("local", "simple"),
        default="simple",
        help="Answer generation backend to evaluate.",
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=("hybrid", "vector", "bm25"),
        default="hybrid",
        help="Retrieval mode for the simple backend.",
    )
    parser.add_argument("--top-k", type=int, default=3, help="Number of citations to retrieve.")
    parser.add_argument(
        "--ids",
        nargs="+",
        default=None,
        help="Optional subset of question IDs to evaluate, e.g. a1 a2 a3.",
    )
    parser.add_argument(
        "--save-json",
        type=Path,
        default=None,
        help="Optional path to save raw answer evaluation results as JSON.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    store = MetadataStore(settings.metadata_file, settings.manifest_file)
    documents = [item for item in store.load_documents() if item.status in {"processed", "imported"}]
    questions = json.loads(args.questions_file.read_text(encoding="utf-8"))
    if args.ids:
        allowed = set(args.ids)
        questions = [item for item in questions if item["id"] in allowed]
    simple_rag = SimpleVectorRAG(settings)
    llm = LiteratureLLM(settings)

    rows: list[dict[str, object]] = []
    for index, item in enumerate(questions, start=1):
        print(f"[{index}/{len(questions)}] evaluating {item['id']} ({item['category']})")
        started = time.perf_counter()
        citations = _run_retrieval(
            backend=args.backend,
            question=item["question"],
            top_k=args.top_k,
            documents=documents,
            processed_dir=settings.processed_dir,
            simple_rag=simple_rag,
            retrieval_mode=args.retrieval_mode,
        )
        answer = llm.answer_question(item["question"], citations, stream=False)
        judge = llm.judge_answer(
            question=item["question"],
            answer=answer,
            citations=citations,
            expected_titles=item["expected_titles"],
            expected_points=item["expected_points"],
        )
        latency_ms = (time.perf_counter() - started) * 1000
        retrieved_titles = [citation.title for citation in citations]
        title_hit = int(any(title in item["expected_titles"] for title in retrieved_titles))
        row = {
            "id": item["id"],
            "category": item["category"],
            "question": item["question"],
            "expected_titles": item["expected_titles"],
            "expected_points": item["expected_points"],
            "retrieved_titles": retrieved_titles,
            "answer": answer,
            "title_hit": title_hit,
            "correctness": int(judge["correctness"]),
            "groundedness": int(judge["groundedness"]),
            "citation_use": int(judge["citation_use"]),
            "pass": bool(judge["pass"]),
            "reason": str(judge["reason"]),
            "latency_ms": latency_ms,
        }
        rows.append(row)

    _print_summary(rows)
    _print_category_summary(rows)

    if args.save_json is not None:
        dump_json(args.save_json, rows)
        print(f"\nSaved raw results to {args.save_json}")


def _run_retrieval(
    backend: str,
    question: str,
    top_k: int,
    documents,
    processed_dir: Path,
    simple_rag: SimpleVectorRAG,
    retrieval_mode: str,
):
    if backend == "local":
        return retrieve_citations(question, documents, processed_dir, top_k=top_k)
    return simple_rag.query(question, top_k=top_k, retrieval_mode=retrieval_mode)


def _print_summary(rows: list[dict[str, object]]) -> None:
    count = len(rows)
    print("count  title_hit  correctness  groundedness  citation_use  pass_rate  avg_latency_ms")
    print("-------------------------------------------------------------------------------")
    print(
        f"{count:>5}  "
        f"{_mean(rows, 'title_hit'):>9.2f}  "
        f"{_mean(rows, 'correctness'):>11.2f}  "
        f"{_mean(rows, 'groundedness'):>12.2f}  "
        f"{_mean(rows, 'citation_use'):>12.2f}  "
        f"{_mean(rows, 'pass'):>9.2f}  "
        f"{_mean(rows, 'latency_ms'):>14.1f}"
    )


def _print_category_summary(rows: list[dict[str, object]]) -> None:
    print("\ncategory breakdown")
    print("category        count  title_hit  correctness  groundedness  citation_use  pass_rate")
    print("-----------------------------------------------------------------------------------")
    categories = sorted({str(row["category"]) for row in rows})
    for category in categories:
        subset = [row for row in rows if row["category"] == category]
        print(
            f"{category:<14} "
            f"{len(subset):>5}  "
            f"{_mean(subset, 'title_hit'):>9.2f}  "
            f"{_mean(subset, 'correctness'):>11.2f}  "
            f"{_mean(subset, 'groundedness'):>12.2f}  "
            f"{_mean(subset, 'citation_use'):>12.2f}  "
            f"{_mean(subset, 'pass'):>9.2f}"
        )


def _mean(rows: list[dict[str, object]], key: str) -> float:
    if not rows:
        return 0.0
    return statistics.mean(float(row[key]) for row in rows)


if __name__ == "__main__":
    main()

"""Structured novelty checker backed by academic search and LangChain."""

from __future__ import annotations

from config import Configuration
from project_workspace.models import IdeaCandidate, NoveltyCheckOutput
from project_workspace.novelty import build_novelty_query


def build_structured_novelty_checker(config: Configuration):
    """Build a novelty checker using arXiv metadata plus structured LLM output."""

    from llm import StructuredOutputRunner, build_chat_model
    from services.source_adapters import ArxivSourceAdapter

    model = build_chat_model(config)
    runner: StructuredOutputRunner[NoveltyCheckOutput] = StructuredOutputRunner(
        model,
        system_prompt=(
            "You are a skeptical research novelty checker. Compare one candidate "
            "idea against retrieved related work. Identify overlap, the strongest "
            "remaining novelty claim, and classify the idea as novel, incremental, "
            "overlapping, or unclear. Use unclear when evidence is insufficient."
        ),
        schema=NoveltyCheckOutput,
        agent_name="NoveltyChecker",
    )
    adapter = ArxivSourceAdapter()

    def check(candidates: list[IdeaCandidate], topic: str) -> list[IdeaCandidate]:
        checked: list[IdeaCandidate] = []
        for candidate in candidates:
            query = build_novelty_query(candidate, topic=topic)
            search_result = adapter.search(query, config, loop_count=0, max_results=5)
            related_work = _format_related_work(search_result)
            if not related_work:
                checked.append(
                    candidate.model_copy(
                        update={
                            "closest_related_work": [f"Pending search query: {query}"],
                            "overlap_analysis": "No related-work results were available from the configured academic search provider.",
                            "novelty_claim": "Unverified candidate; novelty claim pending closest-work comparison.",
                            "novelty_verdict": "unclear",
                            "novelty_confidence": 0.0,
                        }
                    )
                )
                continue

            output = runner.invoke(
                f"""Topic:
{topic}

Candidate:
- title: {candidate.title}
- problem: {candidate.problem}
- hypothesis: {candidate.hypothesis}
- method_sketch: {candidate.method_sketch}
- expected_signal: {candidate.expected_signal}

Retrieved related work:
{related_work}

Return a conservative novelty assessment."""
            )
            checked.append(
                candidate.model_copy(
                    update={
                        "closest_related_work": output.closest_related_work,
                        "overlap_analysis": output.overlap_analysis,
                        "novelty_claim": output.novelty_claim,
                        "novelty_verdict": output.novelty_verdict,
                        "novelty_confidence": output.novelty_confidence,
                    }
                )
            )
        return checked

    return check


def _format_related_work(search_result: dict | str) -> str:
    if not isinstance(search_result, dict):
        return ""
    rows = []
    for index, item in enumerate(search_result.get("results") or [], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        year = str(item.get("year") or "").strip()
        abstract = str(item.get("content") or item.get("raw_content") or "").strip()
        url = str(item.get("url") or item.get("pdf_url") or "").strip()
        rows.append(
            f"{index}. {title} ({year})\nURL: {url}\nAbstract: {abstract[:1200]}"
        )
    return "\n\n".join(rows)

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from capability_types import (
    INSPECT_GITHUB_REPO_CAPABILITY,
    SEARCH_ACADEMIC_PAPERS_CAPABILITY,
    SEARCH_WEB_PAGES_CAPABILITY,
)
from config import Configuration
from execution import ResearchTaskExecutor
from execution.evidence_policy import EvidencePolicy
from models import SummaryState, TodoItem
from services.source_routing import SourceRoutePlan


class StubSummarizer:
    def summarize_task(self, state, task, context):
        del state, task, context
        return "summary"

    def stream_task_summary(self, state, task, context):
        del state, task, context
        return iter(()), lambda: "summary"


class StubSourceRouting:
    def __init__(self, preferred_capabilities, intent_label: str = "general_research") -> None:
        self._plan = SourceRoutePlan(
            intent_label=intent_label,
            preferred_capabilities=preferred_capabilities,
            confidence=0.9,
            reason="stub",
        )

    def plan_capabilities(self, research_topic: str, task: TodoItem) -> SourceRoutePlan:
        del research_topic, task
        return self._plan


def consume(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


class ResearchTaskExecutorTests(unittest.TestCase):
    def _make_executor(self, preferred_capabilities):
        return ResearchTaskExecutor(
            Configuration(strip_thinking_tokens=False),
            StubSummarizer(),
            StubSourceRouting(preferred_capabilities),
            EvidencePolicy(Configuration()),
            lambda step=None: [],
        )

    def test_academic_stop(self) -> None:
        executor = self._make_executor([SEARCH_ACADEMIC_PAPERS_CAPABILITY])
        state = SummaryState(research_topic="topic")
        task = TodoItem(id=1, title="任务", intent="目标", query="query")

        with patch(
            "execution.research_task_executor.dispatch_capability_search",
            return_value=(
                {
                    "results": [
                        {"title": "a", "url": "u1", "content": "c", "raw_content": "c", "score": 1.0, "source_type": "academic", "pdf_url": "p1"},
                        {"title": "b", "url": "u2", "content": "c", "raw_content": "c", "score": 0.9, "source_type": "academic", "pdf_url": "p2"},
                        {"title": "c", "url": "u3", "content": "c", "raw_content": "c", "score": 0.8, "source_type": "academic", "pdf_url": "p3"},
                    ],
                    "backend": "arxiv",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "arxiv",
            ),
        ):
            result = consume(executor.execute(state, task, emit_stream=False))

        self.assertEqual(result.task_patch.attempt_count, 1)
        self.assertEqual(result.task_patch.search_backend, "arxiv")
        self.assertEqual(result.task_patch.planned_capabilities, [SEARCH_ACADEMIC_PAPERS_CAPABILITY])
        self.assertEqual(result.task_patch.current_capability, SEARCH_ACADEMIC_PAPERS_CAPABILITY)
        self.assertIsNone(result.task_patch.evidence_gap_reason)

    def test_multi_query_academic_merges_evidence(self) -> None:
        executor = self._make_executor([SEARCH_ACADEMIC_PAPERS_CAPABILITY])
        state = SummaryState(research_topic="topic")
        task = TodoItem(
            id=1,
            title="任务",
            intent="目标",
            query="query one",
            queries=["query one", "query two"],
        )
        responses = [
            (
                {
                    "results": [
                        {"title": "a", "url": "u1", "content": "c", "raw_content": "c", "score": 1.0, "source_type": "academic", "pdf_url": "p1"},
                        {"title": "b", "url": "u2", "content": "c", "raw_content": "c", "score": 0.9, "source_type": "academic", "pdf_url": "p2"},
                    ],
                    "backend": "arxiv",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "arxiv",
            ),
            (
                {
                    "results": [
                        {"title": "c", "url": "u3", "content": "c", "raw_content": "c", "score": 0.8, "source_type": "academic", "pdf_url": "p3"},
                    ],
                    "backend": "arxiv",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "arxiv",
            ),
        ]

        with patch(
            "execution.research_task_executor.dispatch_capability_search",
            side_effect=responses,
        ) as dispatch:
            result = consume(executor.execute(state, task, emit_stream=False))

        self.assertEqual(result.task_patch.attempt_count, 2)
        self.assertEqual(result.task_patch.evidence_count, 3)
        self.assertEqual(result.task_patch.latest_query, "query one; query two")
        self.assertIsNone(result.task_patch.evidence_gap_reason)
        self.assertEqual(dispatch.call_args_list[0].args[1], "query one")
        self.assertEqual(dispatch.call_args_list[1].args[1], "query two")

    def test_research_chain_runs_web_after_sufficient_academic(self) -> None:
        executor = self._make_executor(
            [SEARCH_ACADEMIC_PAPERS_CAPABILITY, SEARCH_WEB_PAGES_CAPABILITY]
        )
        state = SummaryState(research_topic="topic")
        task = TodoItem(id=1, title="任务", intent="目标", query="query")
        responses = [
            (
                {
                    "results": [
                        {"title": "a", "url": "u1", "content": "c", "raw_content": "c", "score": 1.0, "source_type": "academic", "pdf_url": "p1"},
                        {"title": "b", "url": "u2", "content": "c", "raw_content": "c", "score": 0.9, "source_type": "academic", "pdf_url": "p2"},
                        {"title": "c", "url": "u3", "content": "c", "raw_content": "c", "score": 0.8, "source_type": "academic", "pdf_url": "p3"},
                    ],
                    "backend": "arxiv",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "arxiv",
            ),
            (
                {
                    "results": [],
                    "backend": "advanced",
                    "answer": None,
                    "notices": ["web unavailable"],
                },
                ["web unavailable"],
                None,
                "advanced",
            ),
        ]

        with patch(
            "execution.research_task_executor.dispatch_capability_search",
            side_effect=responses,
        ) as dispatch:
            result = consume(executor.execute(state, task, emit_stream=False))

        self.assertEqual(result.task_patch.attempt_count, 2)
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(result.task_patch.search_backend, "arxiv")
        self.assertIsNone(result.task_patch.evidence_gap_reason)

    def test_academic_then_web_stop(self) -> None:
        executor = self._make_executor(
            [SEARCH_ACADEMIC_PAPERS_CAPABILITY, SEARCH_WEB_PAGES_CAPABILITY]
        )
        state = SummaryState(research_topic="topic")
        task = TodoItem(id=1, title="任务", intent="目标", query="query")
        responses = [
            (
                {
                    "results": [
                        {"title": "p1", "url": "p1", "content": "abs", "raw_content": "abs", "score": 1.0, "source_type": "academic", "pdf_url": "pdf1"}
                    ],
                    "backend": "arxiv",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "arxiv",
            ),
            (
                {
                    "results": [
                        {"title": "w1", "url": "w1", "content": "c", "raw_content": "c", "score": 0.7, "source_type": "web_search"},
                        {"title": "w2", "url": "w2", "content": "c", "raw_content": "c", "score": 0.6, "source_type": "web_search"},
                        {"title": "w3", "url": "w3", "content": "c", "raw_content": "c", "score": 0.5, "source_type": "web_search"},
                    ],
                    "backend": "advanced",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "advanced",
            ),
        ]

        with patch(
            "execution.research_task_executor.dispatch_capability_search",
            side_effect=responses,
        ):
            result = consume(executor.execute(state, task, emit_stream=False))

        self.assertEqual(result.task_patch.attempt_count, 2)
        self.assertEqual(result.task_patch.search_backend, "arxiv+advanced")
        self.assertTrue(result.task_patch.needs_followup)
        self.assertEqual(
            result.task_patch.current_capability,
            SEARCH_WEB_PAGES_CAPABILITY,
        )
        self.assertIsNone(result.task_patch.evidence_gap_reason)

    def test_academic_web_stop(self) -> None:
        executor = self._make_executor(
            [
                SEARCH_ACADEMIC_PAPERS_CAPABILITY,
                SEARCH_WEB_PAGES_CAPABILITY,
            ]
        )
        state = SummaryState(research_topic="topic")
        task = TodoItem(id=1, title="任务", intent="目标", query="query")
        responses = [
            (
                {
                    "results": [
                        {"title": "p1", "url": "p1", "content": "abs", "raw_content": "abs", "score": 1.0, "source_type": "academic", "pdf_url": "pdf1"}
                    ],
                    "backend": "arxiv",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "arxiv",
            ),
            (
                {
                    "results": [
                        {"title": "w1", "url": "w1", "content": "c", "raw_content": "c", "score": 0.7, "source_type": "web_search"},
                        {"title": "w2", "url": "w2", "content": "c", "raw_content": "c", "score": 0.6, "source_type": "web_search"},
                        {"title": "w3", "url": "w3", "content": "c", "raw_content": "c", "score": 0.5, "source_type": "web_search"},
                    ],
                    "backend": "advanced",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "advanced",
            ),
        ]

        with patch(
            "execution.research_task_executor.dispatch_capability_search",
            side_effect=responses,
        ):
            result = consume(executor.execute(state, task, emit_stream=False))

        self.assertEqual(result.task_patch.attempt_count, 2)
        self.assertEqual(result.task_patch.search_backend, "arxiv+advanced")
        self.assertTrue(result.task_patch.needs_followup)
        self.assertIsNone(result.task_patch.evidence_gap_reason)

    def test_academic_web_terminal_insufficient(self) -> None:
        executor = self._make_executor(
            [
                SEARCH_ACADEMIC_PAPERS_CAPABILITY,
                SEARCH_WEB_PAGES_CAPABILITY,
            ]
        )
        state = SummaryState(research_topic="topic")
        task = TodoItem(id=1, title="任务", intent="目标", query="query")
        responses = [
            (
                {
                    "results": [
                        {"title": "p1", "url": "p1", "content": "", "raw_content": "", "score": 1.0, "source_type": "academic"}
                    ],
                    "backend": "arxiv",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "arxiv",
            ),
            (
                {
                    "results": [
                        {"title": "w1", "url": "w1", "content": "c", "raw_content": "c", "score": 0.2, "source_type": "web_search"}
                    ],
                    "backend": "advanced",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "advanced",
            ),
        ]

        with patch(
            "execution.research_task_executor.dispatch_capability_search",
            side_effect=responses,
        ):
            result = consume(executor.execute(state, task, emit_stream=False))

        self.assertEqual(result.task_patch.attempt_count, 2)
        self.assertEqual(
            result.task_patch.evidence_gap_reason,
            "terminal_insufficient_evidence",
        )
        self.assertEqual(result.task_patch.current_capability, SEARCH_WEB_PAGES_CAPABILITY)

    def test_github_then_web_stop(self) -> None:
        executor = self._make_executor(
            [INSPECT_GITHUB_REPO_CAPABILITY, SEARCH_WEB_PAGES_CAPABILITY]
        )
        state = SummaryState(research_topic="topic")
        task = TodoItem(id=1, title="任务", intent="目标", query="openai/openai-python")
        responses = [
            (
                {
                    "results": [
                        {"title": "repo", "url": "https://github.com/openai/openai-python", "content": "repo", "raw_content": "repo", "score": 1.0, "source_type": "github", "result_kind": "repo"},
                        {"title": "readme", "url": "https://github.com/openai/openai-python/blob/main/README.md", "content": "readme", "raw_content": "readme", "score": 0.9, "source_type": "github", "result_kind": "readme"},
                    ],
                    "backend": "github_mcp",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "github_mcp",
            ),
            (
                {
                    "results": [
                        {"title": "w1", "url": "w1", "content": "c", "raw_content": "c", "score": 0.7, "source_type": "web_search"},
                        {"title": "w2", "url": "w2", "content": "c", "raw_content": "c", "score": 0.6, "source_type": "web_search"},
                        {"title": "w3", "url": "w3", "content": "c", "raw_content": "c", "score": 0.5, "source_type": "web_search"},
                    ],
                    "backend": "advanced",
                    "answer": None,
                    "notices": [],
                },
                [],
                None,
                "advanced",
            ),
        ]

        with patch(
            "execution.research_task_executor.dispatch_capability_search",
            side_effect=responses,
        ):
            result = consume(executor.execute(state, task, emit_stream=False))

        self.assertEqual(result.task_patch.attempt_count, 2)
        self.assertEqual(result.task_patch.search_backend, "github_mcp+advanced")
        self.assertEqual(result.task_patch.current_capability, SEARCH_WEB_PAGES_CAPABILITY)
        self.assertIsNone(result.task_patch.evidence_gap_reason)


if __name__ == "__main__":
    unittest.main()

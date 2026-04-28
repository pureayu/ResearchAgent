import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from benchmarking import (
    aggregate_todo_items,
    extract_source_types_from_summary,
    score_case,
    summarize_scores,
)


class BenchmarkingTest(unittest.TestCase):
    def test_extract_source_types_from_summary(self) -> None:
        summary = "来源类型统计：\n- 学术论文：2\n- GitHub 仓库：1"
        self.assertEqual(
            extract_source_types_from_summary(summary),
            ["academic", "github"],
        )

    def test_aggregate_todo_items_collects_route_and_sources(self) -> None:
        aggregated = aggregate_todo_items(
            [
                {
                    "status": "completed",
                    "evidence_count": 3,
                    "top_score": 0.9,
                    "planned_capabilities": ["search_academic_papers"],
                    "current_capability": "search_academic_papers",
                    "search_backend": "arxiv",
                    "evidence_gap_reason": "",
                    "sources_summary": "来源类型统计：\n- 学术论文：1",
                },
                {
                    "status": "completed",
                    "evidence_count": 2,
                    "top_score": 0.7,
                    "planned_capabilities": ["search_web_pages"],
                    "current_capability": "search_web_pages",
                    "search_backend": "duckduckgo",
                    "evidence_gap_reason": "terminal_insufficient_evidence",
                    "sources_summary": "来源类型统计：\n- 联网网页：2",
                },
            ]
        )

        self.assertEqual(
            aggregated["planned_capabilities"],
            ["search_academic_papers", "search_web_pages"],
        )
        self.assertEqual(
            aggregated["source_types"],
            ["academic", "web_search"],
        )
        self.assertIn("terminal_insufficient_evidence", aggregated["gap_reasons"])
        self.assertEqual(aggregated["total_evidence_count"], 5)

    def test_score_case_marks_pass_when_expectations_met(self) -> None:
        case = {
            "id": "acad_001",
            "expected_route_contains": ["search_academic_papers"],
            "expected_source_types": ["academic"],
            "expected_gap_reason": None,
            "must_have_keywords": ["Self-RAG", "critique"],
            "forbidden_patterns": ["没有来源"],
            "must_have_facts": ["fact1"],
        }
        run_case = {
            "id": "acad_001",
            "status": "completed",
            "aggregated": {
                "planned_capabilities": ["search_academic_papers"],
                "source_types": ["academic"],
                "gap_reasons": [],
            },
            "response": {
                "report_markdown": "Self-RAG uses critique signals.",
                "todo_items": [{"summary": "Self-RAG critique summary"}],
            },
        }

        scored = score_case(case, run_case)
        self.assertTrue(scored["route_match"])
        self.assertTrue(scored["source_coverage"])
        self.assertEqual(scored["keyword_coverage"], 1.0)
        self.assertTrue(scored["passed"])

    def test_summarize_scores(self) -> None:
        summary = summarize_scores(
            [
                {
                    "status": "completed",
                    "passed": True,
                    "route_match": True,
                    "source_coverage": True,
                    "keyword_coverage": 1.0,
                    "forbidden_hits": [],
                    "gap_reason_match": None,
                },
                {
                    "status": "skipped_missing_features",
                    "passed": False,
                    "route_match": False,
                    "source_coverage": False,
                    "keyword_coverage": 0.0,
                    "forbidden_hits": [],
                    "gap_reason_match": None,
                },
            ]
        )
        self.assertEqual(summary["total_cases"], 2)
        self.assertEqual(summary["runnable_cases"], 1)
        self.assertEqual(summary["passed_cases"], 1)
        self.assertEqual(summary["skipped_cases"], 1)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from capability_types import (
    INSPECT_GITHUB_REPO_CAPABILITY,
    SEARCH_ACADEMIC_PAPERS_CAPABILITY,
    SEARCH_WEB_PAGES_CAPABILITY,
)
from config import Configuration
from execution.evidence_policy import EvidencePolicy


class EvidencePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = EvidencePolicy(Configuration())

    def test_academic_metadata_gap_upgrades(self) -> None:
        result = {
            "results": [
                {"title": "p1", "score": 1.0, "source_type": "academic", "url": "u1"},
                {"title": "p2", "score": 0.9, "source_type": "academic", "url": "u2"},
                {"title": "p3", "score": 0.8, "source_type": "academic", "url": "u3"},
            ]
        }

        self.assertEqual(
            self.policy.assess_evidence_gap(
                "query",
                result,
                SEARCH_ACADEMIC_PAPERS_CAPABILITY,
            ),
            "insufficient_academic_metadata",
        )

    def test_web_terminal_gap_is_normalized(self) -> None:
        result = {
            "results": [{"title": "w1", "score": 0.2, "source_type": "web_search"}]
        }

        gap = self.policy.assess_evidence_gap("query", result, SEARCH_WEB_PAGES_CAPABILITY)
        self.assertEqual(gap, "insufficient_web_coverage")
        self.assertEqual(
            self.policy.finalize_gap_reason(gap, has_next_source=False),
            "terminal_insufficient_evidence",
        )

    def test_github_missing_code_context_upgrades(self) -> None:
        result = {
            "results": [
                {"title": "repo", "score": 1.0, "source_type": "github", "result_kind": "repo"},
                {"title": "readme", "score": 0.9, "source_type": "github", "result_kind": "readme"},
            ]
        }

        self.assertEqual(
            self.policy.assess_evidence_gap("query", result, INSPECT_GITHUB_REPO_CAPABILITY),
            "missing_code_context",
        )

    def test_github_enough_stops(self) -> None:
        result = {
            "results": [
                {"title": "repo", "score": 1.0, "source_type": "github", "result_kind": "repo"},
                {"title": "readme", "score": 0.9, "source_type": "github", "result_kind": "readme"},
                {"title": "file", "score": 0.8, "source_type": "github", "result_kind": "file"},
            ]
        }

        self.assertIsNone(
            self.policy.assess_evidence_gap("query", result, INSPECT_GITHUB_REPO_CAPABILITY)
        )


if __name__ == "__main__":
    unittest.main()

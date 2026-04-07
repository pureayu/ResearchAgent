import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import Configuration
from capability_types import DEFAULT_CAPABILITY_CHAIN, INSPECT_GITHUB_REPO_CAPABILITY
from models import TodoItem
from services.source_routing import SourceRoutingService


class StubAgent:
    def __init__(self, response: str = "", *, should_raise: bool = False) -> None:
        self._response = response
        self._should_raise = should_raise
        self.clear_calls = 0

    def run(self, input_text: str, **_: object) -> str:
        if self._should_raise:
            raise RuntimeError("boom")
        return self._response

    def clear_history(self) -> None:
        self.clear_calls += 1


class SourceRoutingServiceTests(unittest.TestCase):
    def test_plan_capabilities_parses_valid_json(self) -> None:
        service = SourceRoutingService(
            StubAgent(
                """
                {
                  "intent_label": "literature_review",
                  "preferred_capabilities": ["search_academic_papers", "search_web_pages"],
                  "confidence": 0.91,
                  "reason": "先查论文，再补网页"
                }
                """
            ),
            Configuration(strip_thinking_tokens=False),
        )

        plan = service.plan_capabilities(
            "RAG 研究",
            TodoItem(id=1, title="论文调研", intent="找代表工作", query="RAG survey"),
        )

        self.assertEqual(
            plan.preferred_capabilities,
            ["search_academic_papers", "search_web_pages"],
        )
        self.assertEqual(plan.intent_label, "literature_review")
        self.assertGreater(plan.confidence, 0.9)

    def test_plan_capabilities_falls_back_on_invalid_json(self) -> None:
        service = SourceRoutingService(
            StubAgent("not json"),
            Configuration(strip_thinking_tokens=False),
        )

        plan = service.plan_capabilities(
            "主题",
            TodoItem(id=1, title="任务", intent="目标", query="query"),
        )

        self.assertEqual(plan.preferred_capabilities, DEFAULT_CAPABILITY_CHAIN)
        self.assertEqual(plan.reason, "route_parse_failed")

    def test_plan_capabilities_falls_back_when_agent_fails(self) -> None:
        service = SourceRoutingService(
            StubAgent(should_raise=True),
            Configuration(strip_thinking_tokens=False),
        )

        plan = service.plan_capabilities(
            "主题",
            TodoItem(id=1, title="任务", intent="目标", query="query"),
        )

        self.assertEqual(plan.preferred_capabilities, DEFAULT_CAPABILITY_CHAIN)
        self.assertEqual(plan.reason, "route_agent_failed")

    def test_plan_capabilities_accepts_github_when_enabled(self) -> None:
        service = SourceRoutingService(
            StubAgent(
                """
                {
                  "intent_label": "implementation_investigation",
                  "preferred_capabilities": ["inspect_github_repo", "search_web_pages"],
                  "confidence": 0.88,
                  "reason": "仓库调研优先 GitHub"
                }
                """
            ),
            Configuration(strip_thinking_tokens=False, enable_github_mcp=True),
        )

        plan = service.plan_capabilities(
            "研究项目实现",
            TodoItem(id=1, title="看仓库", intent="分析 repo 结构", query="openai/openai-python"),
        )

        self.assertEqual(
            plan.preferred_capabilities,
            [INSPECT_GITHUB_REPO_CAPABILITY, "search_web_pages"],
        )


if __name__ == "__main__":
    unittest.main()

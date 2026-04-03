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
    SEARCH_LOCAL_DOCS_CAPABILITY,
    SEARCH_WEB_PAGES_CAPABILITY,
)
from config import Configuration
from services.capabilities import CapabilityExecutor, CapabilityRegistry
from services.search import dispatch_capability_search


class FakeAdapter:
    def __init__(self, response):
        self._response = response

    def search(self, query: str, config: Configuration, *, loop_count: int, max_results: int = 5):
        del query, config, loop_count, max_results
        return self._response


class SearchDispatchTests(unittest.TestCase):
    def test_registry_maps_capabilities_to_backing_sources(self) -> None:
        registry = CapabilityRegistry()

        self.assertEqual(
            registry.require(SEARCH_LOCAL_DOCS_CAPABILITY).backing_source_id,
            "local_library",
        )
        self.assertEqual(
            registry.require(SEARCH_ACADEMIC_PAPERS_CAPABILITY).backing_source_id,
            "academic_search",
        )
        self.assertEqual(
            registry.require(SEARCH_WEB_PAGES_CAPABILITY).backing_source_id,
            "web_search",
        )

    def test_dispatch_normalizes_academic_source_type(self) -> None:
        with patch(
            "services.capabilities.get_source_adapters",
            return_value={
                "academic_search": FakeAdapter(
                    {
                        "results": [{"title": "paper", "url": "https://a"}],
                        "backend": "arxiv",
                        "answer": None,
                        "notices": [],
                    }
                )
            },
        ):
            payload, notices, answer, backend = dispatch_capability_search(
                SEARCH_ACADEMIC_PAPERS_CAPABILITY,
                "query",
                Configuration(),
                0,
            )

        self.assertEqual(backend, "arxiv")
        self.assertEqual(notices, [])
        self.assertIsNone(answer)
        self.assertEqual(payload["results"][0]["source_type"], "academic")

    def test_registry_exposes_github_capability_when_enabled(self) -> None:
        registry = CapabilityRegistry(Configuration(enable_github_mcp=True))
        spec = registry.require(INSPECT_GITHUB_REPO_CAPABILITY)
        self.assertEqual(spec.backing_source_id, "github_mcp")
        self.assertEqual(spec.source_type, "github")

    def test_executor_uses_registered_backing_source(self) -> None:
        executor = CapabilityExecutor(CapabilityRegistry())
        with patch(
            "services.capabilities.get_source_adapters",
            return_value={
                "web_search": FakeAdapter(
                    {
                        "results": [{"title": "page", "url": "https://b"}],
                        "backend": "advanced",
                        "answer": None,
                        "notices": [],
                    }
                )
            },
        ):
            payload, notices, answer, backend = executor.execute(
                SEARCH_WEB_PAGES_CAPABILITY,
                "query",
                Configuration(),
                0,
            )

        self.assertEqual(backend, "advanced")
        self.assertEqual(notices, [])
        self.assertIsNone(answer)
        self.assertEqual(payload["results"][0]["source_type"], "web_search")

    def test_executor_uses_github_handler(self) -> None:
        executor = CapabilityExecutor(CapabilityRegistry(Configuration(enable_github_mcp=True)))
        with patch(
            "services.capabilities.GitHubRepoCapabilityHandler.execute",
            return_value={
                "results": [{"title": "repo", "url": "https://github.com/o/r", "source_type": "github"}],
                "backend": "github_mcp",
                "answer": None,
                "notices": [],
            },
        ) as handler_execute:
            payload, notices, answer, backend = executor.execute(
                INSPECT_GITHUB_REPO_CAPABILITY,
                "openai/openai-python",
                Configuration(enable_github_mcp=True),
                0,
            )

        handler_execute.assert_called_once()
        self.assertEqual(backend, "github_mcp")
        self.assertEqual(notices, [])
        self.assertIsNone(answer)
        self.assertEqual(payload["results"][0]["source_type"], "github")


if __name__ == "__main__":
    unittest.main()

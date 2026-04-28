import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from capability_types import (
    SEARCH_ACADEMIC_PAPERS_CAPABILITY,
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


if __name__ == "__main__":
    unittest.main()

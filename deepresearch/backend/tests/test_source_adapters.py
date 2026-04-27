import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import Configuration
from services.source_adapters import ArxivSourceAdapter


EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query: empty</title>
</feed>
"""

TARGET_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2407.05858v2</id>
    <updated>2024-12-15T15:26:41Z</updated>
    <published>2024-07-08T12:20:45Z</published>
    <title>Fast On-device LLM Inference with NPUs</title>
    <summary>We present an LLM inference system using on-device NPU offloading.</summary>
    <author><name>Daliang Xu</name></author>
    <link href="http://arxiv.org/pdf/2407.05858v2" title="pdf" />
  </entry>
</feed>
"""


def _response(text: str) -> Mock:
    response = Mock()
    response.text = text
    response.raise_for_status = Mock()
    return response


class ArxivSourceAdapterTests(unittest.TestCase):
    def test_search_executes_one_planner_query_without_expansion(self) -> None:
        adapter = ArxivSourceAdapter()

        def fake_get(*args, **kwargs):
            del args
            search_query = kwargs["params"]["search_query"]
            if search_query == "all:on-device LLM inference NPU":
                return _response(TARGET_FEED)
            return _response(EMPTY_FEED)

        with patch("services.source_adapters.requests.get", side_effect=fake_get) as get:
            payload = adapter.search(
                "on-device LLM inference NPU",
                Configuration(),
                loop_count=0,
                max_results=5,
            )

        titles = [item["title"] for item in payload["results"]]
        self.assertIn("Fast On-device LLM Inference with NPUs", titles)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs["params"]["search_query"], "all:on-device LLM inference NPU")
        self.assertEqual(payload["query"], "all:on-device LLM inference NPU")
        self.assertEqual(payload["results"][0]["id"], "2407.05858")
        self.assertEqual(payload["results"][0]["url"], "http://arxiv.org/abs/2407.05858v2")
        self.assertEqual(payload["results"][0]["year"], "2024")

    def test_search_supports_direct_arxiv_id_lookup(self) -> None:
        adapter = ArxivSourceAdapter()

        with patch(
            "services.source_adapters.requests.get",
            return_value=_response(TARGET_FEED),
        ) as get:
            payload = adapter.search(
                "https://arxiv.org/abs/2407.05858v2",
                Configuration(),
                loop_count=0,
                max_results=5,
            )

        self.assertEqual(get.call_args.kwargs["params"], {"id_list": "2407.05858"})
        self.assertEqual(payload["results"][0]["id"], "2407.05858")


if __name__ == "__main__":
    unittest.main()

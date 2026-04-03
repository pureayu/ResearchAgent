import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import Configuration
from models import TodoItem
from services.github_mcp import GitHubMcpClient, GitHubRepoCapabilityHandler


class _AsyncContextManager:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False


class GitHubMcpClientTests(unittest.TestCase):
    def test_call_tool_initializes_and_calls_session(self) -> None:
        config = Configuration(enable_github_mcp=True)
        client = GitHubMcpClient(config)
        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(
            return_value=SimpleNamespace(
                tools=[
                    SimpleNamespace(name="search_repositories"),
                    SimpleNamespace(name="get_file_contents"),
                    SimpleNamespace(name="search_code"),
                ]
            )
        )
        fake_session.call_tool = AsyncMock(
            return_value=SimpleNamespace(
                isError=False,
                structuredContent={"items": [{"full_name": "openai/openai-python"}]},
                content=[],
            )
        )

        with (
            patch.dict(os.environ, {"GITHUB_PERSONAL_ACCESS_TOKEN": "token"}, clear=False),
            patch("services.github_mcp.StdioServerParameters", side_effect=lambda **kwargs: kwargs),
            patch(
                "services.github_mcp.stdio_client",
                return_value=_AsyncContextManager(("read", "write")),
            ),
            patch(
                "services.github_mcp.ClientSession",
                return_value=_AsyncContextManager(fake_session),
            ),
        ):
            result = client.call_tool("search_repositories", {"query": "openai/openai-python"})

        fake_session.initialize.assert_awaited_once()
        fake_session.list_tools.assert_awaited_once()
        fake_session.call_tool.assert_awaited_once()
        self.assertIn("structured", result)
        self.assertEqual(result["structured"]["items"][0]["full_name"], "openai/openai-python")

    def test_call_tool_requires_token(self) -> None:
        client = GitHubMcpClient(Configuration(enable_github_mcp=True))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                client.call_tool("search_repositories", {"query": "x"})


class GitHubRepoCapabilityHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = GitHubRepoCapabilityHandler()

    def test_explicit_repo_ref_uses_exact_lookup(self) -> None:
        task = TodoItem(
            id=1,
            title="研究 openai/openai-python",
            intent="看仓库结构",
            query="openai/openai-python 实现",
        )
        with (
            patch.object(
                self.handler,
                "_get_repo_metadata",
                return_value={
                    "owner": "openai",
                    "repo": "openai-python",
                    "full_name": "openai/openai-python",
                    "html_url": "https://github.com/openai/openai-python",
                    "description": "sdk",
                    "stargazers_count": 1,
                    "language": "Python",
                    "default_branch": "main",
                },
            ) as exact_lookup,
            patch.object(self.handler, "_search_best_repository") as search_lookup,
            patch.object(self.handler, "_get_readme_result", return_value=None),
            patch.object(self.handler, "_get_code_results", return_value=[]),
        ):
            payload = self.handler.execute(
                task.query,
                Configuration(enable_github_mcp=True),
                loop_count=0,
                task=task,
            )

        exact_lookup.assert_called_once()
        search_lookup.assert_not_called()
        self.assertEqual(payload["results"][0]["result_kind"], "repo")

    def test_search_best_repository_used_when_no_explicit_ref(self) -> None:
        task = TodoItem(
            id=1,
            title="研究 openai python sdk",
            intent="看仓库结构",
            query="OpenAI Python SDK repository",
        )
        with (
            patch.object(self.handler, "_get_repo_metadata") as exact_lookup,
            patch.object(
                self.handler,
                "_search_best_repository",
                return_value={
                    "owner": "openai",
                    "repo": "openai-python",
                    "full_name": "openai/openai-python",
                    "html_url": "https://github.com/openai/openai-python",
                    "description": "sdk",
                    "stargazers_count": 1,
                    "language": "Python",
                    "default_branch": "main",
                },
            ) as search_lookup,
            patch.object(self.handler, "_get_readme_result", return_value=None),
            patch.object(self.handler, "_get_code_results", return_value=[]),
        ):
            payload = self.handler.execute(
                task.query,
                Configuration(enable_github_mcp=True),
                loop_count=0,
                task=task,
            )

        exact_lookup.assert_not_called()
        search_lookup.assert_called_once()
        self.assertEqual(payload["results"][0]["repo_full_name"], "openai/openai-python")

    def test_readme_result_is_normalized(self) -> None:
        fake_client = MagicMock()
        fake_client.call_tool.side_effect = [
            {"structured": [{"path": "README.md"}], "text_blocks": []},
            {
                "structured": {"path": "README.md", "content": "# Title\nbody"},
                "text_blocks": [],
            },
        ]

        result = self.handler._get_readme_result(fake_client, SimpleNamespace(owner="openai", repo="openai-python", full_name="openai/openai-python"))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["result_kind"], "readme")
        self.assertEqual(result["source_type"], "github")

    def test_code_results_include_code_and_file(self) -> None:
        fake_client = MagicMock()
        fake_client.call_tool.side_effect = [
            {
                "structured": {
                    "items": [
                        {
                            "path": "src/client.py",
                            "html_url": "https://github.com/openai/openai-python/blob/main/src/client.py",
                            "text_matches": [{"fragment": "class Client"}],
                        }
                    ]
                },
                "text_blocks": [],
            },
            {
                "structured": {"path": "src/client.py", "content": "class Client:\n    pass"},
                "text_blocks": [],
            },
        ]

        results = self.handler._get_code_results(
            fake_client,
            SimpleNamespace(owner="openai", repo="openai-python", full_name="openai/openai-python"),
            "client implementation",
            max_results=3,
        )

        self.assertEqual(results[0]["result_kind"], "code")
        self.assertEqual(results[1]["result_kind"], "file")
        self.assertTrue(all(item["source_type"] == "github" for item in results))


if __name__ == "__main__":
    unittest.main()

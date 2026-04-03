"""GitHub repo capability backed by the official GitHub MCP server."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any

from config import Configuration
from models import TodoItem
from source_types import GITHUB_MCP_BACKEND, GITHUB_SOURCE_TYPE

logger = logging.getLogger(__name__)

GITHUB_REQUIRED_TOOLS = (
    "search_repositories",
    "get_file_contents",
    "search_code",
)
README_CANDIDATES = (
    "README.md",
    "readme.md",
    "README.rst",
    "README.txt",
    "README",
)
REPO_URL_PATTERN = re.compile(
    r"github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
REPO_REF_PATTERN = re.compile(
    r"(?<![\w.-])(?P<owner>[A-Za-z0-9_.-]{1,100})/(?P<repo>[A-Za-z0-9_.-]{1,100})(?![\w./-])"
)

try:  # pragma: no cover - exercised indirectly and guarded in runtime
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover - runtime guard
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None


@dataclass(frozen=True)
class RepositoryRef:
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class GitHubMcpClient:
    """Thin stdio MCP client for a single GitHub capability execution."""

    def __init__(self, config: Configuration) -> None:
        self._config = config

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return asyncio.run(self._call_tool(name, arguments or {}))

    async def _call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self._ensure_available()
        server_params = self._build_server_parameters()

        assert stdio_client is not None
        assert ClientSession is not None
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed_tools = await session.list_tools()
                available_tool_names = {tool.name for tool in listed_tools.tools}
                if name not in available_tool_names:
                    raise RuntimeError(
                        f"GitHub MCP tool not available: {name}. "
                        f"Available tools: {sorted(available_tool_names)}"
                    )

                result = await session.call_tool(name, arguments=arguments)
                if getattr(result, "isError", False):
                    raise RuntimeError(self._extract_error_message(result))
                return self._normalize_result(result)

    def _build_server_parameters(self):
        if not self._config.enable_github_mcp:
            raise RuntimeError("GitHub MCP is disabled by configuration.")

        token = (
            os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
            or os.getenv("GITHUB_PAT")
            or ""
        ).strip()
        if not token:
            raise RuntimeError("Missing GITHUB_PERSONAL_ACCESS_TOKEN for GitHub MCP.")

        raw_command = (self._config.github_mcp_server_command or "").strip()
        if not raw_command:
            raise RuntimeError("Missing github_mcp_server_command configuration.")

        command_parts = shlex.split(raw_command)
        if not command_parts:
            raise RuntimeError("Invalid github_mcp_server_command configuration.")

        command = command_parts[0]
        args = command_parts[1:] + [
            "--read-only",
            "--tools",
            ",".join(GITHUB_REQUIRED_TOOLS),
        ]
        env = dict(os.environ)
        env["GITHUB_PERSONAL_ACCESS_TOKEN"] = token

        assert StdioServerParameters is not None
        return StdioServerParameters(command=command, args=args, env=env)

    @staticmethod
    def _ensure_available() -> None:
        if ClientSession is None or StdioServerParameters is None or stdio_client is None:
            raise RuntimeError("Python MCP SDK is not installed; add the `mcp` dependency.")

    @staticmethod
    def _extract_error_message(result: Any) -> str:
        for text in GitHubMcpClient._extract_text_blocks(result):
            if text.strip():
                return text.strip()
        return "GitHub MCP tool call failed"

    @staticmethod
    def _normalize_result(result: Any) -> dict[str, Any]:
        return {
            "structured": getattr(result, "structuredContent", None),
            "text_blocks": GitHubMcpClient._extract_text_blocks(result),
        }

    @staticmethod
    def _extract_text_blocks(result: Any) -> list[str]:
        blocks: list[str] = []
        for item in getattr(result, "content", []) or []:
            item_type = getattr(item, "type", None)
            if item_type == "text":
                text = str(getattr(item, "text", "") or "").strip()
                if text:
                    blocks.append(text)
                continue
            if item_type == "resource":
                resource = getattr(item, "resource", None)
                text = str(getattr(resource, "text", "") or "").strip()
                if text:
                    blocks.append(text)
        return blocks


class GitHubRepoCapabilityHandler:
    """Implement repository investigation through the GitHub MCP server."""

    def execute(
        self,
        query: str,
        config: Configuration,
        *,
        loop_count: int,
        max_results: int = 5,
        task: TodoItem | None = None,
    ) -> dict[str, Any]:
        del loop_count

        client = GitHubMcpClient(config)
        context_query = self._build_context_query(task, query)

        try:
            repo_ref = self._extract_repo_ref(context_query)
            repo_metadata = (
                self._get_repo_metadata(client, repo_ref)
                if repo_ref is not None
                else self._search_best_repository(client, context_query)
            )
        except Exception as exc:
            logger.warning("GitHub repo capability failed before evidence collection: %s", exc)
            return {
                "results": [],
                "backend": GITHUB_MCP_BACKEND,
                "answer": None,
                "notices": [str(exc)],
            }

        if not repo_metadata:
            return {
                "results": [],
                "backend": GITHUB_MCP_BACKEND,
                "answer": None,
                "notices": ["No matching GitHub repository found."],
            }

        repo_ref = RepositoryRef(
            owner=str(repo_metadata.get("owner") or ""),
            repo=str(repo_metadata.get("repo") or ""),
        )
        if not repo_ref.owner or not repo_ref.repo:
            return {
                "results": [],
                "backend": GITHUB_MCP_BACKEND,
                "answer": None,
                "notices": ["GitHub repository metadata is incomplete."],
            }

        results: list[dict[str, Any]] = [self._build_repo_result(repo_metadata)]
        notices: list[str] = []

        try:
            readme_result = self._get_readme_result(client, repo_ref)
            if readme_result is not None:
                results.append(readme_result)
        except Exception as exc:
            logger.info("GitHub README retrieval failed for %s: %s", repo_ref.full_name, exc)
            notices.append(str(exc))

        try:
            code_results = self._get_code_results(client, repo_ref, query, max_results=max_results)
            results.extend(code_results)
        except Exception as exc:
            logger.info("GitHub code retrieval failed for %s: %s", repo_ref.full_name, exc)
            notices.append(str(exc))

        ordered_results = sorted(
            results,
            key=lambda item: float(item.get("score") or 0.0),
            reverse=True,
        )[: max(3, max_results + 2)]

        return {
            "results": ordered_results,
            "backend": GITHUB_MCP_BACKEND,
            "answer": None,
            "notices": notices,
        }

    @staticmethod
    def _build_context_query(task: TodoItem | None, query: str) -> str:
        task_parts = []
        if task is not None:
            task_parts.extend([task.title, task.intent])
        task_parts.append(query)
        return " ".join(part.strip() for part in task_parts if part and part.strip()).strip()

    @staticmethod
    def _extract_repo_ref(text: str) -> RepositoryRef | None:
        if not text:
            return None

        url_match = REPO_URL_PATTERN.search(text)
        if url_match:
            return RepositoryRef(
                owner=url_match.group("owner"),
                repo=url_match.group("repo").removesuffix(".git"),
            )

        for match in REPO_REF_PATTERN.finditer(text):
            owner = match.group("owner")
            repo = match.group("repo")
            if owner.lower() in {"http", "https", "github.com"}:
                continue
            return RepositoryRef(owner=owner, repo=repo)
        return None

    def _search_best_repository(
        self,
        client: GitHubMcpClient,
        query: str,
    ) -> dict[str, Any] | None:
        payload = client.call_tool(
            "search_repositories",
            {
                "query": query,
                "perPage": 5,
            },
        )
        candidates = self._extract_repo_candidates(payload)
        if not candidates:
            return None
        return candidates[0]

    def _get_repo_metadata(
        self,
        client: GitHubMcpClient,
        repo_ref: RepositoryRef,
    ) -> dict[str, Any] | None:
        payload = client.call_tool(
            "search_repositories",
            {
                "query": f"repo:{repo_ref.full_name}",
                "perPage": 1,
            },
        )
        candidates = self._extract_repo_candidates(payload)
        if candidates:
            return candidates[0]
        return {
            "owner": repo_ref.owner,
            "repo": repo_ref.repo,
            "full_name": repo_ref.full_name,
            "html_url": f"https://github.com/{repo_ref.full_name}",
            "description": "",
            "stargazers_count": 0,
            "language": "",
            "default_branch": "",
        }

    def _get_readme_result(
        self,
        client: GitHubMcpClient,
        repo_ref: RepositoryRef,
    ) -> dict[str, Any] | None:
        directory_listing = None
        try:
            directory_listing = client.call_tool(
                "get_file_contents",
                {
                    "owner": repo_ref.owner,
                    "repo": repo_ref.repo,
                    "path": "",
                },
            )
        except Exception:
            directory_listing = None

        readme_path = self._find_readme_path(directory_listing)
        candidates = (readme_path,) if readme_path else README_CANDIDATES
        for index, candidate in enumerate(candidates, start=1):
            if not candidate:
                continue
            try:
                payload = client.call_tool(
                    "get_file_contents",
                    {
                        "owner": repo_ref.owner,
                        "repo": repo_ref.repo,
                        "path": candidate,
                    },
                )
            except Exception:
                continue

            file_payload = self._extract_file_payload(payload)
            if file_payload is None:
                continue
            content = self._decode_file_content(file_payload)
            if not content.strip():
                continue

            path = str(file_payload.get("path") or candidate).strip()
            return {
                "title": f"{repo_ref.full_name} README",
                "url": f"https://github.com/{repo_ref.full_name}/blob/HEAD/{path}",
                "content": content[:1600],
                "raw_content": content,
                "score": max(0.7, 0.9 - (index - 1) * 0.05),
                "source_type": GITHUB_SOURCE_TYPE,
                "result_kind": "readme",
                "repo_full_name": repo_ref.full_name,
                "path": path,
            }
        return None

    def _get_code_results(
        self,
        client: GitHubMcpClient,
        repo_ref: RepositoryRef,
        query: str,
        *,
        max_results: int,
    ) -> list[dict[str, Any]]:
        payload = client.call_tool(
            "search_code",
            {
                "query": f"{query} repo:{repo_ref.full_name}",
                "perPage": min(max_results, 3),
            },
        )
        hits = self._extract_code_candidates(payload)
        results: list[dict[str, Any]] = []

        for index, hit in enumerate(hits[:3], start=1):
            path = str(hit.get("path") or "").strip()
            if not path:
                continue
            html_url = str(hit.get("html_url") or "").strip() or (
                f"https://github.com/{repo_ref.full_name}/blob/HEAD/{path}"
            )
            snippet = self._extract_best_text(hit)
            results.append(
                {
                    "title": f"{repo_ref.full_name}:{path}",
                    "url": html_url,
                    "content": snippet[:600],
                    "raw_content": snippet,
                    "score": max(0.3, 0.75 - (index - 1) * 0.05),
                    "source_type": GITHUB_SOURCE_TYPE,
                    "result_kind": "code",
                    "repo_full_name": repo_ref.full_name,
                    "path": path,
                }
            )

            try:
                file_payload = client.call_tool(
                    "get_file_contents",
                    {
                        "owner": repo_ref.owner,
                        "repo": repo_ref.repo,
                        "path": path,
                    },
                )
                file_data = self._extract_file_payload(file_payload)
                if file_data is None:
                    continue
                file_content = self._decode_file_content(file_data)
                if not file_content.strip():
                    continue
                results.append(
                    {
                        "title": f"{repo_ref.full_name}:{path} file",
                        "url": html_url,
                        "content": file_content[:1600],
                        "raw_content": file_content,
                        "score": max(0.25, 0.7 - (index - 1) * 0.05),
                        "source_type": GITHUB_SOURCE_TYPE,
                        "result_kind": "file",
                        "repo_full_name": repo_ref.full_name,
                        "path": path,
                    }
                )
            except Exception as exc:
                logger.info("GitHub file fetch failed for %s: %s", path, exc)

        return results

    @staticmethod
    def _build_repo_result(repo_metadata: dict[str, Any]) -> dict[str, Any]:
        full_name = str(repo_metadata.get("full_name") or "").strip()
        description = str(repo_metadata.get("description") or "").strip()
        language = str(repo_metadata.get("language") or "").strip()
        default_branch = str(repo_metadata.get("default_branch") or "").strip()
        stars = repo_metadata.get("stargazers_count") or 0
        html_url = str(repo_metadata.get("html_url") or "").strip() or (
            f"https://github.com/{full_name}" if full_name else ""
        )
        summary_parts = [description]
        if language:
            summary_parts.append(f"Language: {language}")
        if default_branch:
            summary_parts.append(f"Default branch: {default_branch}")
        if stars:
            summary_parts.append(f"Stars: {stars}")
        summary = " | ".join(part for part in summary_parts if part)

        return {
            "title": full_name or "GitHub repository",
            "url": html_url,
            "content": summary,
            "raw_content": json.dumps(repo_metadata, ensure_ascii=False),
            "score": 1.0,
            "source_type": GITHUB_SOURCE_TYPE,
            "result_kind": "repo",
            "repo_full_name": full_name,
            "path": "",
        }

    @staticmethod
    def _extract_repo_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                normalized = GitHubRepoCapabilityHandler._normalize_repo_candidate(node)
                if normalized is not None:
                    candidates.append(normalized)
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(payload.get("structured"))
        for text in payload.get("text_blocks") or []:
            try:
                visit(json.loads(text))
            except json.JSONDecodeError:
                continue
        return GitHubRepoCapabilityHandler._deduplicate_repo_candidates(candidates)

    @staticmethod
    def _normalize_repo_candidate(node: dict[str, Any]) -> dict[str, Any] | None:
        full_name = str(node.get("full_name") or "").strip()
        owner_value = node.get("owner")
        owner = ""
        if isinstance(owner_value, dict):
            owner = str(owner_value.get("login") or owner_value.get("name") or "").strip()
        elif isinstance(owner_value, str):
            owner = owner_value.strip()
        repo = str(node.get("name") or "").strip()

        if not full_name and owner and repo:
            full_name = f"{owner}/{repo}"
        elif full_name and not owner and "/" in full_name:
            owner, repo = full_name.split("/", 1)

        if not full_name or not owner or not repo:
            return None

        try:
            stars = int(node.get("stargazers_count") or node.get("stars") or 0)
        except (TypeError, ValueError):
            stars = 0

        return {
            "owner": owner,
            "repo": repo,
            "full_name": full_name,
            "html_url": str(node.get("html_url") or node.get("url") or "").strip(),
            "description": str(node.get("description") or "").strip(),
            "stargazers_count": stars,
            "language": str(node.get("language") or "").strip(),
            "default_branch": str(node.get("default_branch") or "").strip(),
        }

    @staticmethod
    def _deduplicate_repo_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        ordered: list[dict[str, Any]] = []
        for item in candidates:
            key = str(item.get("full_name") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(item)
        return ordered

    @staticmethod
    def _extract_code_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                if "path" in node and ("html_url" in node or "repository" in node or "url" in node):
                    candidates.append(node)
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(payload.get("structured"))
        for text in payload.get("text_blocks") or []:
            try:
                visit(json.loads(text))
            except json.JSONDecodeError:
                continue
        return candidates

    @staticmethod
    def _extract_file_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        def visit(node: Any) -> dict[str, Any] | None:
            if isinstance(node, dict):
                if any(key in node for key in ("content", "decoded_content", "text")):
                    return node
                for value in node.values():
                    found = visit(value)
                    if found is not None:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = visit(item)
                    if found is not None:
                        return found
            return None

        found = visit(payload.get("structured"))
        if found is not None:
            return found

        for text in payload.get("text_blocks") or []:
            try:
                found = visit(json.loads(text))
                if found is not None:
                    return found
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _find_readme_path(payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None

        entries: list[dict[str, Any]] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                if "path" in node or "name" in node:
                    entries.append(node)
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(payload.get("structured"))
        for text in payload.get("text_blocks") or []:
            try:
                visit(json.loads(text))
            except json.JSONDecodeError:
                continue

        for entry in entries:
            path = str(entry.get("path") or entry.get("name") or "").strip()
            if path.lower() in {candidate.lower() for candidate in README_CANDIDATES}:
                return path
        return None

    @staticmethod
    def _decode_file_content(file_payload: dict[str, Any]) -> str:
        content = str(
            file_payload.get("decoded_content")
            or file_payload.get("text")
            or file_payload.get("content")
            or ""
        )
        encoding = str(file_payload.get("encoding") or "").strip().lower()
        if encoding == "base64" and content:
            try:
                return base64.b64decode(content).decode("utf-8", errors="replace")
            except Exception:
                return content
        return content

    @staticmethod
    def _extract_best_text(node: dict[str, Any]) -> str:
        for key in ("text_matches", "fragment", "content", "text", "summary"):
            value = node.get(key)
            if isinstance(value, list):
                parts = []
                for item in value:
                    if isinstance(item, dict):
                        fragment = str(item.get("fragment") or item.get("text") or "").strip()
                        if fragment:
                            parts.append(fragment)
                    elif isinstance(item, str) and item.strip():
                        parts.append(item.strip())
                if parts:
                    return "\n".join(parts)
            elif isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(node, ensure_ascii=False)[:600]

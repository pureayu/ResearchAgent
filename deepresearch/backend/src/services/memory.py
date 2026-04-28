"""ARIS-style file memory for the deep research workflow.

This module intentionally avoids a database-backed long-term memory. The durable
memory source is the project workspace on disk: PROJECT_STATUS.json, CLAUDE.md,
research_contract.md, review state, and experiment trackers.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import Configuration
from models import SummaryState, TodoItem

logger = logging.getLogger(__name__)


class FileMemoryService:
    """Lightweight memory adapter backed by ARIS-style project files."""

    STATUS_FILE = "PROJECT_STATUS.json"
    WORKSPACE_INDEX_FILE = "PROJECT_INDEX.md"

    def __init__(self, config: Configuration) -> None:
        self._config = config
        self._root = Path(config.project_workspace_root).expanduser().resolve()

    def get_or_create_session(self, session_id: str | None, topic: str) -> str:
        """Use the project id as session id when provided; otherwise create an ephemeral id."""

        del topic
        return str(session_id or uuid4().hex)

    def start_run(self, session_id: str, topic: str) -> str:
        """Create a run id without persisting an extra database row."""

        del topic
        return f"{session_id}-{uuid4().hex[:8]}"

    def load_relevant_context(
        self,
        session_id: str | None,
        topic: str,
        *,
        exclude_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Load a compact project-memory snapshot for planner injection."""

        del exclude_run_id
        project_summaries = self._load_project_summaries(session_id=session_id, topic=topic)
        working_memory_summary = self._format_working_memory(project_summaries)
        return {
            "working_memory_summary": working_memory_summary,
            "recent_turns": [],
            "profile_facts": [],
            "global_facts": [
                {
                    "fact_id": item["project_id"],
                    "fact": item["summary"],
                    "memory_scope": "project",
                    "source": item["root_path"],
                }
                for item in project_summaries
            ],
            "project_memory": project_summaries,
        }

    def load_recent_task_logs(
        self,
        session_id: str | None,
        *,
        exclude_run_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Expose project active tasks as history-recall task logs."""

        del exclude_run_id
        if not session_id:
            return []

        status = self._read_status_for_project(session_id)
        if not status:
            return []

        active_tasks = status.get("active_tasks") or []
        if not isinstance(active_tasks, list):
            active_tasks = []

        rows: list[dict[str, Any]] = []
        for idx, task in enumerate(active_tasks[: max(0, limit)], start=1):
            rows.append(
                {
                    "run_id": str(session_id),
                    "task_id": idx,
                    "title": str(task),
                    "status": str(status.get("stage") or "unknown"),
                    "summary": str(status.get("next_action") or ""),
                    "created_at": str(status.get("updated_at") or status.get("created_at") or ""),
                }
            )
        return rows

    def save_task_log(self, run_id: str, task: TodoItem) -> None:
        """Task logs are persisted by project workspace files, not by this adapter."""

        del run_id, task

    def save_report_memory(self, run_id: str, state: SummaryState, report: str) -> None:
        """Project reports are written by ProjectWorkspaceService."""

        del run_id, state, report

    def save_session_turn(self, state: SummaryState, assistant_response: str) -> None:
        """No database turn table is maintained in ARIS-style file memory."""

        del state, assistant_response

    def refresh_working_memory(self, session_id: str | None) -> dict[str, Any]:
        """Refresh project-memory context after a run."""

        project_summaries = self._load_project_summaries(session_id=session_id, topic="")
        return {
            "working_memory_summary": self._format_working_memory(project_summaries),
            "recent_turns": [],
            "project_memory": project_summaries,
        }

    def capture_profile_memory(
        self,
        run_id: str,
        session_id: str,
        topic: str,
    ) -> None:
        """User-profile memory is intentionally not persisted."""

        del run_id, session_id, topic

    def _load_project_summaries(
        self,
        *,
        session_id: str | None,
        topic: str,
    ) -> list[dict[str, Any]]:
        direct_project_dir: Path | None = None
        if session_id:
            direct = self._project_dir(session_id)
            if direct.exists():
                direct_project_dir = direct
        topic_terms = self._terms(topic)

        # Stage 1: rank only the lightweight root PROJECT_INDEX.md entries.
        workspace_index_entries = self._load_workspace_index_entries()
        ranked_index_entries = self._rank_workspace_index_entries(
            workspace_index_entries,
            topic_terms=topic_terms,
            direct_project_id=direct_project_dir.name if direct_project_dir else None,
        )
        candidate_dirs = self._candidate_dirs_from_index(ranked_index_entries, limit=8)

        # Backward-compatible fallback for old workspaces without a root index.
        if not workspace_index_entries and not candidate_dirs:
            candidate_dirs = self._recent_project_dirs(limit=8)

        if direct_project_dir and direct_project_dir not in candidate_dirs:
            candidate_dirs.insert(0, direct_project_dir)

        # Stage 2: load real project files only for the coarse-ranked candidates.
        ranked: list[tuple[int, float, dict[str, Any]]] = []
        for project_dir in candidate_dirs:
            status = self._read_status(project_dir)
            if not status:
                continue
            is_direct = bool(direct_project_dir and project_dir == direct_project_dir)
            search_blob = self._project_search_blob(project_dir, status)
            score = self._match_score(search_blob, topic_terms)
            if topic_terms and not is_direct and score <= 0:
                continue
            summary = self._summarize_project(project_dir, status, search_blob=search_blob)
            ranked.append((1 if is_direct else 0, score, summary))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        summaries = [item[2] for item in ranked[:3]]
        return summaries

    def _rank_workspace_index_entries(
        self,
        entries: list[dict[str, str]],
        *,
        topic_terms: list[str],
        direct_project_id: str | None,
    ) -> list[tuple[int, float, dict[str, str]]]:
        ranked: list[tuple[int, float, dict[str, str]]] = []
        for entry in entries:
            project_id = entry.get("project_id", "")
            searchable = "\n".join(
                [
                    entry.get("name", ""),
                    entry.get("description", ""),
                    entry.get("topic", ""),
                    entry.get("selected_idea", ""),
                ]
            ).lower()
            is_direct = bool(direct_project_id and project_id == direct_project_id)
            score = self._match_score(searchable, topic_terms)
            if topic_terms and not is_direct and score <= 0:
                continue
            ranked.append((1 if is_direct else 0, score, entry))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return ranked

    def _candidate_dirs_from_index(
        self,
        ranked_entries: list[tuple[int, float, dict[str, str]]],
        *,
        limit: int,
    ) -> list[Path]:
        candidate_dirs: list[Path] = []
        seen: set[str] = set()
        for _, _, entry in ranked_entries:
            project_id = entry.get("project_id", "").strip()
            if not project_id or project_id in seen:
                continue
            project_dir = self._project_dir(project_id)
            if not project_dir.exists():
                continue
            candidate_dirs.append(project_dir)
            seen.add(project_id)
            if len(candidate_dirs) >= limit:
                break
        return candidate_dirs

    def _load_workspace_index_entries(self) -> list[dict[str, str]]:
        index_path = self._root / self.WORKSPACE_INDEX_FILE
        try:
            text = index_path.read_text(encoding="utf-8")
        except OSError:
            return []

        entries: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("## "):
                if current:
                    entries.append(current)
                current = {"name": line.removeprefix("## ").strip()}
                continue
            if current is None or not line.startswith("- "):
                continue
            key, separator, value = line[2:].partition(":")
            if not separator:
                continue
            current[key.strip()] = value.strip()
        if current:
            entries.append(current)
        return entries

    def _recent_project_dirs(self, *, limit: int) -> list[Path]:
        if not self._root.exists():
            return []
        dirs = [
            item
            for item in self._root.iterdir()
            if item.is_dir() and (item / self.STATUS_FILE).exists()
        ]
        return sorted(dirs, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]

    def _project_dir(self, project_id: str) -> Path:
        safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in project_id).strip(".-")
        if not safe_id:
            safe_id = "unknown-project"
        path = (self._root / safe_id).resolve()
        if self._root not in {path, *path.parents}:
            raise ValueError("project id resolved outside workspace root")
        return path

    def _read_status_for_project(self, project_id: str) -> dict[str, Any] | None:
        project_dir = self._project_dir(project_id)
        return self._read_status(project_dir)

    def _read_status(self, project_dir: Path) -> dict[str, Any] | None:
        status_path = project_dir / self.STATUS_FILE
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _summarize_project(
        self,
        project_dir: Path,
        status: dict[str, Any],
        *,
        search_blob: str = "",
    ) -> dict[str, Any]:
        project_id = str(status.get("project_id") or project_dir.name)
        topic = str(status.get("topic") or "")
        name = str(status.get("name") or topic or project_id)
        description = str(status.get("description") or "")
        stage = str(status.get("stage") or "unknown")
        selected_idea = str(status.get("selected_idea") or "")
        next_action = str(status.get("next_action") or "")
        active_tasks = [
            str(item)
            for item in (status.get("active_tasks") or [])
            if str(item).strip()
        ][:5]
        files = self._available_memory_files(project_dir)
        snippets = self._matching_snippets(search_blob)
        summary_parts = [
            f"项目 {project_id}",
            f"名称：{name}" if name else "",
            f"描述：{description}" if description else "",
            f"主题：{topic}" if topic else "",
            f"阶段：{stage}",
            f"选中方向：{selected_idea}" if selected_idea else "",
            f"下一步：{next_action}" if next_action else "",
        ]
        if active_tasks:
            summary_parts.append("当前任务：" + "；".join(active_tasks))
        if files:
            summary_parts.append("可恢复文件：" + "、".join(files))
        if snippets:
            summary_parts.append("相关片段：" + " / ".join(snippets))

        return {
            "project_id": project_id,
            "name": name,
            "description": description,
            "topic": topic,
            "stage": stage,
            "selected_idea": selected_idea,
            "next_action": next_action,
            "active_tasks": active_tasks,
            "root_path": str(project_dir),
            "summary": "；".join(part for part in summary_parts if part),
        }

    def _available_memory_files(self, project_dir: Path) -> list[str]:
        relative_paths = [
            "PROJECT_CARD.md",
            "PROJECT_INDEX.md",
            "CLAUDE.md",
            "IDEA_REPORT.md",
            "IDEA_CANDIDATES.md",
            "docs/research_contract.md",
            "REVIEW_STATE.json",
            "AUTO_REVIEW.md",
            "refine-logs/REVISION_PLAN.md",
            "refine-logs/EXPERIMENT_TRACKER.md",
            "EXPERIMENT_LOG.md",
            "findings.md",
        ]
        return [relative for relative in relative_paths if (project_dir / relative).exists()]

    def _format_working_memory(self, project_summaries: list[dict[str, Any]]) -> str:
        if not project_summaries:
            return ""
        lines = ["项目工作区记忆："]
        for idx, item in enumerate(project_summaries, start=1):
            lines.append(f"{idx}. {item['summary']}")
        return "\n".join(lines)

    @staticmethod
    def _terms(text: str) -> list[str]:
        normalized = (text or "").lower()
        normalized = re.sub(r"[，。！？、；：,.!?;:()\[\]{}<>《》“”\"'`~|/\\_-]+", " ", normalized)
        raw_terms = [token.strip() for token in normalized.split() if len(token.strip()) >= 2]
        terms: list[str] = []
        for token in raw_terms:
            terms.append(token)
            if re.search(r"[\u4e00-\u9fff]", token):
                cjk = re.sub(r"[^\u4e00-\u9fff]", "", token)
                for width in (2, 3, 4):
                    terms.extend(cjk[index : index + width] for index in range(max(0, len(cjk) - width + 1)))

        deduped: list[str] = []
        seen: set[str] = set()
        for term in terms:
            if term and term not in seen:
                deduped.append(term)
                seen.add(term)
            if len(deduped) >= 32:
                break
        return deduped

    def _project_search_blob(self, project_dir: Path, status: dict[str, Any]) -> str:
        fields = [
            status.get("name"),
            status.get("description"),
            status.get("topic"),
            status.get("selected_idea"),
            status.get("next_action"),
            " ".join(str(item) for item in status.get("active_tasks") or []),
        ]
        fragments = [str(field) for field in fields if field]
        for relative_path in self._searchable_memory_files():
            path = project_dir / relative_path
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fragments.append(text[:4000])
        return "\n".join(fragments).lower()

    @staticmethod
    def _searchable_memory_files() -> list[str]:
        return [
            "PROJECT_CARD.md",
            "PROJECT_INDEX.md",
            "CLAUDE.md",
            "IDEA_REPORT.md",
            "IDEA_CANDIDATES.md",
            "docs/research_contract.md",
            "AUTO_REVIEW.md",
            "refine-logs/REVISION_PLAN.md",
            "findings.md",
        ]

    @staticmethod
    def _match_score(search_blob: str, topic_terms: list[str]) -> float:
        if not topic_terms:
            return 0.0
        score = 0.0
        for term in topic_terms:
            occurrences = search_blob.count(term)
            if occurrences:
                score += min(3, occurrences) * min(4, len(term))
        return score

    @staticmethod
    def _matching_snippets(search_blob: str) -> list[str]:
        snippets: list[str] = []
        for raw_line in search_blob.splitlines():
            line = re.sub(r"\s+", " ", raw_line.strip())
            if not line:
                continue
            if line.startswith(("name:", "description:", "- selected_idea:", "- topic:")):
                snippets.append(line[:180])
            if len(snippets) >= 3:
                break
        return snippets


def create_memory_service(config: Configuration) -> FileMemoryService:
    """Return ARIS-style file memory."""

    return FileMemoryService(config)

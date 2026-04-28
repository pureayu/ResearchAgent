"""File-backed project workspace service aligned with the ARIS protocol."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from project_workspace.models import (
    IdeaCandidate,
    ProjectSnapshot,
    ProjectStage,
    ProjectStatus,
    utc_now_iso,
)
from project_workspace.templates import (
    STATIC_TEMPLATES,
    render_claude_md,
    render_experiment_log,
    render_experiment_plan,
    render_experiment_tracker,
    render_idea_candidates,
    render_project_card,
    render_research_contract,
    render_review_state,
    render_workspace_index,
)


class ProjectWorkspaceService:
    """Create, load, and update durable research project files."""

    STATUS_FILE = "PROJECT_STATUS.json"
    HUMAN_STATUS_FILE = "CLAUDE.md"
    PROJECT_CARD_FILE = "PROJECT_CARD.md"
    WORKSPACE_INDEX_FILE = "PROJECT_INDEX.md"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def create_project(
        self,
        *,
        topic: str,
        project_id: str | None = None,
        selected_idea: str | None = None,
    ) -> ProjectSnapshot:
        """Create a project directory with ARIS-style state files."""

        clean_topic = topic.strip()
        if not clean_topic:
            raise ValueError("topic must not be empty")

        status = ProjectStatus(
            project_id=self._safe_project_id(project_id or self._project_id_from_topic(clean_topic)),
            topic=clean_topic,
            name=self._project_name_from_topic(clean_topic),
            description=self._project_description_from_topic(clean_topic),
            selected_idea=(selected_idea or "").strip(),
        )
        project_dir = self._project_dir(status.project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "docs").mkdir(exist_ok=True)
        (project_dir / "refine-logs").mkdir(exist_ok=True)

        self._write_status(project_dir, status)
        self._write_text_if_missing(project_dir / self.PROJECT_CARD_FILE, render_project_card(status))
        self._write_text_if_missing(project_dir / self.HUMAN_STATUS_FILE, render_claude_md(status))
        self._write_text_if_missing(project_dir / status.contract_path, render_research_contract(status))
        self._write_text_if_missing(project_dir / status.experiment_plan_path, render_experiment_plan(status))
        self._write_text_if_missing(project_dir / status.experiment_tracker_path, render_experiment_tracker(status))
        self._write_text_if_missing(project_dir / "EXPERIMENT_LOG.md", render_experiment_log(status))
        self._write_text_if_missing(project_dir / "REVIEW_STATE.json", render_review_state())
        for relative_path, content in STATIC_TEMPLATES.items():
            self._write_text_if_missing(project_dir / relative_path, content)
        self._refresh_workspace_index()

        return self.snapshot(status.project_id)

    def snapshot(self, project_id: str) -> ProjectSnapshot:
        """Load project status and known protocol files."""

        safe_id = self._safe_project_id(project_id)
        project_dir = self._project_dir(safe_id)
        status = self.load_status(safe_id)
        files = {
            "status": str(project_dir / self.STATUS_FILE),
            "workspace_index": str(self.root / self.WORKSPACE_INDEX_FILE),
            "project_card": str(project_dir / self.PROJECT_CARD_FILE),
            "human_status": str(project_dir / self.HUMAN_STATUS_FILE),
            "idea_report": str(project_dir / "IDEA_REPORT.md"),
            "idea_candidates": str(project_dir / "IDEA_CANDIDATES.md"),
            "idea_candidates_json": str(project_dir / "IDEA_CANDIDATES.json"),
            "research_contract": str(project_dir / status.contract_path),
            "experiment_plan": str(project_dir / status.experiment_plan_path),
            "experiment_tracker": str(project_dir / status.experiment_tracker_path),
            "draft_experiment_tracker": str(project_dir / "refine-logs" / "DRAFT_EXPERIMENT_TRACKER.md"),
            "revision_plan": str(project_dir / "refine-logs" / "REVISION_PLAN.md"),
            "experiment_log": str(project_dir / "EXPERIMENT_LOG.md"),
            "auto_review": str(project_dir / "AUTO_REVIEW.md"),
            "review_state": str(project_dir / "REVIEW_STATE.json"),
            "findings": str(project_dir / "findings.md"),
        }
        return ProjectSnapshot(
            project_id=safe_id,
            root_path=str(project_dir),
            status=status,
            files=files,
        )

    def load_status(self, project_id: str) -> ProjectStatus:
        """Read canonical project status from disk."""

        safe_id = self._safe_project_id(project_id)
        status_path = self._project_dir(safe_id) / self.STATUS_FILE
        if not status_path.exists():
            raise FileNotFoundError(f"project status not found: {safe_id}")
        return ProjectStatus(**json.loads(status_path.read_text(encoding="utf-8")))

    def update_status(
        self,
        project_id: str,
        patch: dict[str, Any],
        *,
        refresh_contract: bool = True,
    ) -> ProjectSnapshot:
        """Patch canonical status and refresh the human-readable status file."""

        safe_id = self._safe_project_id(project_id)
        project_dir = self._project_dir(safe_id)
        status = self.load_status(safe_id).merged(patch)
        self._write_status(project_dir, status)
        self._write_text(project_dir / self.PROJECT_CARD_FILE, render_project_card(status))
        self._write_text(project_dir / self.HUMAN_STATUS_FILE, render_claude_md(status))
        if refresh_contract:
            self._write_text(project_dir / status.contract_path, render_research_contract(status))
        self._refresh_workspace_index()
        return self.snapshot(safe_id)

    def write_idea_discovery_outputs(
        self,
        project_id: str,
        *,
        report_markdown: str,
        candidates: list[IdeaCandidate],
        auto_select_top: bool = True,
        selected_candidate: IdeaCandidate | None = None,
    ) -> ProjectSnapshot:
        """Persist idea discovery report, candidates, and selected contract."""

        safe_id = self._safe_project_id(project_id)
        project_dir = self._project_dir(safe_id)
        status = self.load_status(safe_id)
        selected = selected_candidate
        if selected is None and candidates and auto_select_top:
            selected = candidates[0]

        self._write_text(project_dir / "IDEA_REPORT.md", report_markdown.rstrip() + "\n")
        self._write_text(project_dir / "IDEA_CANDIDATES.md", render_idea_candidates(candidates))
        self._write_text(
            project_dir / "IDEA_CANDIDATES.json",
            json.dumps(
                [candidate.model_dump() for candidate in candidates],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
        )

        patch: dict[str, Any] = {
            "stage": ProjectStage.REFINE_PLAN if selected else ProjectStage.HUMAN_GATE,
            "description": self._project_description_from_discovery(
                report_markdown,
                selected,
            ),
            "active_tasks": [
                "Review IDEA_CANDIDATES.md",
                "Validate selected idea and refine experiment plan",
            ],
            "next_action": (
                "Review the auto-selected idea and refine EXPERIMENT_PLAN.md."
                if selected
                else "Select one candidate idea before drafting the research contract."
            ),
        }
        if selected:
            patch["selected_idea"] = selected.title
        status = status.merged(patch)

        self._write_status(project_dir, status)
        self._write_text(project_dir / self.PROJECT_CARD_FILE, render_project_card(status))
        self._write_text(project_dir / self.HUMAN_STATUS_FILE, render_claude_md(status))
        self._write_text(project_dir / status.contract_path, render_research_contract(status, selected))
        self._write_text(project_dir / status.experiment_plan_path, render_experiment_plan(status, selected))
        self._refresh_workspace_index()
        return self.snapshot(safe_id)

    def update_selected_idea_candidate(
        self,
        project_id: str,
        candidate: IdeaCandidate,
    ) -> ProjectSnapshot:
        """Persist a refined selected idea and refresh downstream planning files."""

        safe_id = self._safe_project_id(project_id)
        project_dir = self._project_dir(safe_id)
        status = self.load_status(safe_id)
        candidates_path = project_dir / "IDEA_CANDIDATES.json"
        candidates: list[IdeaCandidate] = []
        if candidates_path.exists():
            candidates = [
                IdeaCandidate.model_validate(item)
                for item in json.loads(candidates_path.read_text(encoding="utf-8") or "[]")
                if isinstance(item, dict)
            ]

        replaced = False
        updated_candidates: list[IdeaCandidate] = []
        for existing in candidates:
            if existing.title == status.selected_idea or existing.title == candidate.title:
                updated_candidates.append(candidate)
                replaced = True
            else:
                updated_candidates.append(existing)
        if not replaced:
            updated_candidates.insert(0, candidate)

        self._write_text(project_dir / "IDEA_CANDIDATES.md", render_idea_candidates(updated_candidates))
        self._write_text(
            candidates_path,
            json.dumps(
                [item.model_dump() for item in updated_candidates],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
        )

        status = status.merged(
            {
                "stage": ProjectStage.REFINE_PLAN,
                "selected_idea": candidate.title,
                "description": self._project_description_from_candidate(candidate),
                "active_tasks": [
                    "Review refined selected idea",
                    "Run external review",
                    "Generate experiment tracker",
                ],
                "next_action": "Run external review for the refined selected idea.",
            }
        )
        self._write_status(project_dir, status)
        self._write_text(project_dir / self.PROJECT_CARD_FILE, render_project_card(status))
        self._write_text(project_dir / self.HUMAN_STATUS_FILE, render_claude_md(status))
        self._write_text(project_dir / status.contract_path, render_research_contract(status, candidate))
        self._write_text(project_dir / status.experiment_plan_path, render_experiment_plan(status, candidate))
        self._refresh_workspace_index()
        return self.snapshot(safe_id)

    def read_text(self, project_id: str, relative_path: str) -> str:
        """Read one protocol file from a project workspace."""

        path = self._resolve_project_file(project_id, relative_path)
        return path.read_text(encoding="utf-8")

    def write_text(self, project_id: str, relative_path: str, content: str) -> None:
        """Write one protocol file inside a project workspace."""

        self._write_text(self._resolve_project_file(project_id, relative_path), content)

    def _project_dir(self, project_id: str) -> Path:
        path = self.root / project_id
        if self.root not in path.resolve().parents and path.resolve() != self.root:
            raise ValueError("project_id resolved outside workspace root")
        return path

    def _resolve_project_file(self, project_id: str, relative_path: str) -> Path:
        project_dir = self._project_dir(self._safe_project_id(project_id))
        path = (project_dir / relative_path).resolve()
        if project_dir.resolve() not in path.parents and path != project_dir.resolve():
            raise ValueError("relative_path resolved outside project workspace")
        return path

    def _write_status(self, project_dir: Path, status: ProjectStatus) -> None:
        data = status.model_dump()
        data["updated_at"] = data.get("updated_at") or utc_now_iso()
        self._write_text(
            project_dir / self.STATUS_FILE,
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        )

    def _refresh_workspace_index(self) -> None:
        statuses: list[ProjectStatus] = []
        if self.root.exists():
            for status_path in self.root.glob(f"*/{self.STATUS_FILE}"):
                try:
                    statuses.append(
                        ProjectStatus(
                            **json.loads(status_path.read_text(encoding="utf-8"))
                        )
                    )
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
        statuses.sort(key=lambda status: status.updated_at, reverse=True)
        self._write_text(self.root / self.WORKSPACE_INDEX_FILE, render_workspace_index(statuses))

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)

    def _write_text_if_missing(self, path: Path, content: str) -> None:
        if path.exists():
            return
        self._write_text(path, content)

    @staticmethod
    def _safe_project_id(project_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", project_id.strip()).strip(".-")
        if not safe:
            raise ValueError("project_id must contain at least one safe character")
        if safe in {".", ".."}:
            raise ValueError("invalid project_id")
        return safe[:96]

    @staticmethod
    def _project_id_from_topic(topic: str) -> str:
        base = re.sub(r"[^A-Za-z0-9]+", "-", topic.lower()).strip("-")
        if not base:
            base = "research-project"
        return f"{base[:48]}-{utc_now_iso().replace(':', '').replace('+', 'Z')}"

    @staticmethod
    def _project_name_from_topic(topic: str) -> str:
        normalized = re.sub(r"\s+", " ", topic.strip())
        return normalized[:80] or "Research Project"

    @staticmethod
    def _project_description_from_topic(topic: str) -> str:
        normalized = re.sub(r"\s+", " ", topic.strip())
        return f"Research workspace for: {normalized[:180]}"

    def _project_description_from_discovery(
        self,
        report_markdown: str,
        selected: IdeaCandidate | None,
    ) -> str:
        if selected is not None:
            return self._project_description_from_candidate(selected)
        return self._first_meaningful_paragraph(report_markdown)[:220]

    @staticmethod
    def _project_description_from_candidate(candidate: IdeaCandidate) -> str:
        text = (
            candidate.title.strip()
            or candidate.problem.strip()
            or candidate.hypothesis.strip()
        )
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _first_meaningful_paragraph(markdown: str) -> str:
        for block in re.split(r"\n\s*\n", markdown):
            cleaned = re.sub(r"^#+\s*", "", block.strip())
            cleaned = re.sub(r"\s+", " ", cleaned)
            if len(cleaned) >= 20 and "No idea discovery" not in cleaned:
                return cleaned
        return "Idea discovery report generated for this project."

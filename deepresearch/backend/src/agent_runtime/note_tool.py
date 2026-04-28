"""Local note tool compatible with the existing task-note workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class NoteRecord:
    """In-memory representation of one persisted note."""

    note_id: str
    title: str
    note_type: str
    tags: list[str]
    created_at: str
    updated_at: str
    content: str


class NoteTool:
    """Simple markdown note store with create/read/update actions."""

    def __init__(self, workspace: str) -> None:
        self._workspace = Path(workspace)
        self._workspace.mkdir(parents=True, exist_ok=True)

    def run(self, payload: dict[str, Any]) -> str:
        """Execute one note action and return a human-readable result."""

        action = str(payload.get("action") or "").strip().lower()
        if action == "create":
            return self._create(payload)
        if action == "read":
            return self._read(payload)
        if action == "update":
            return self._update(payload)
        return f"❌ Unsupported note action: {action or 'unknown'}"

    def _create(self, payload: dict[str, Any]) -> str:
        note_id = self._generate_note_id()
        title = str(payload.get("title") or "未命名笔记").strip() or "未命名笔记"
        note_type = str(payload.get("note_type") or "note").strip() or "note"
        tags = self._normalize_tags(payload.get("tags"))
        content = str(payload.get("content") or "").strip()
        now = datetime.now().isoformat()

        record = NoteRecord(
            note_id=note_id,
            title=title,
            note_type=note_type,
            tags=tags,
            created_at=now,
            updated_at=now,
            content=content,
        )
        self._write_record(record)
        return f"✅ Note created\nID: {note_id}\nPath: {self._path_for(note_id)}"

    def _read(self, payload: dict[str, Any]) -> str:
        note_id = str(payload.get("note_id") or "").strip()
        if not note_id:
            return "❌ Missing note_id for read"

        path = self._path_for(note_id)
        if not path.exists():
            return f"❌ Note not found\nID: {note_id}"

        return path.read_text(encoding="utf-8")

    def _update(self, payload: dict[str, Any]) -> str:
        note_id = str(payload.get("note_id") or "").strip()
        if not note_id:
            return "❌ Missing note_id for update"

        path = self._path_for(note_id)
        if not path.exists():
            return f"❌ Note not found\nID: {note_id}"

        existing = self._read_record(note_id)
        if existing is None:
            return f"❌ Failed to parse note\nID: {note_id}"

        updated = NoteRecord(
            note_id=note_id,
            title=str(payload.get("title") or existing.title).strip() or existing.title,
            note_type=(
                str(payload.get("note_type") or existing.note_type).strip() or existing.note_type
            ),
            tags=self._normalize_tags(payload.get("tags")) or existing.tags,
            created_at=existing.created_at,
            updated_at=datetime.now().isoformat(),
            content=str(payload.get("content") or existing.content).strip(),
        )
        self._write_record(updated)
        return f"✅ Note updated\nID: {note_id}\nPath: {path}"

    def _read_record(self, note_id: str) -> NoteRecord | None:
        path = self._path_for(note_id)
        if not path.exists():
            return None

        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return NoteRecord(
                note_id=note_id,
                title=note_id,
                note_type="note",
                tags=[],
                created_at="",
                updated_at="",
                content=text.strip(),
            )

        try:
            _, front_matter, content = text.split("---\n", 2)
        except ValueError:
            return None

        metadata: dict[str, Any] = {}
        for line in front_matter.strip().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()

        raw_tags = metadata.get("tags", "[]")
        try:
            tags = json.loads(raw_tags)
            if not isinstance(tags, list):
                tags = []
        except json.JSONDecodeError:
            tags = []

        return NoteRecord(
            note_id=str(metadata.get("id") or note_id).strip(),
            title=str(metadata.get("title") or note_id).strip(),
            note_type=str(metadata.get("type") or "note").strip(),
            tags=[str(item).strip() for item in tags if str(item).strip()],
            created_at=str(metadata.get("created_at") or "").strip(),
            updated_at=str(metadata.get("updated_at") or "").strip(),
            content=content.strip(),
        )

    def _write_record(self, record: NoteRecord) -> None:
        payload = (
            "---\n"
            f"id: {record.note_id}\n"
            f"title: {record.title}\n"
            f"type: {record.note_type}\n"
            f"tags: {json.dumps(record.tags, ensure_ascii=False)}\n"
            f"created_at: {record.created_at}\n"
            f"updated_at: {record.updated_at}\n"
            "---\n\n"
            f"{record.content.strip()}\n"
        )
        self._path_for(record.note_id).write_text(payload, encoding="utf-8")

    def _generate_note_id(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return f"note_{timestamp[:-3]}"

    def _path_for(self, note_id: str) -> Path:
        return self._workspace / f"{note_id}.md"

    @staticmethod
    def _normalize_tags(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

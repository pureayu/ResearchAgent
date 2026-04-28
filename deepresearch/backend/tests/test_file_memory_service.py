import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import Configuration
from services.memory import FileMemoryService, create_memory_service


class FileMemoryServiceTests(unittest.TestCase):
    def test_load_relevant_context_reads_project_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "proj-1"
            (project_dir / "docs").mkdir(parents=True)
            (project_dir / "refine-logs").mkdir()
            (project_dir / "PROJECT_STATUS.json").write_text(
                json.dumps(
                    {
                        "project_id": "proj-1",
                        "topic": "端侧推理",
                        "stage": "human_gate",
                        "selected_idea": "KV cache placement",
                        "active_tasks": ["Review candidate"],
                        "next_action": "Select one direction",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (project_dir / "CLAUDE.md").write_text("## Pipeline Status\n", encoding="utf-8")
            (project_dir / "docs" / "research_contract.md").write_text(
                "contract",
                encoding="utf-8",
            )

            service = FileMemoryService(Configuration(project_workspace_root=tmp))
            context = service.load_relevant_context("proj-1", "端侧推理")

        self.assertIn("项目工作区记忆", context["working_memory_summary"])
        self.assertIn("KV cache placement", context["working_memory_summary"])
        self.assertEqual(context["global_facts"][0]["memory_scope"], "project")
        self.assertEqual(context["project_memory"][0]["project_id"], "proj-1")

    def test_create_memory_service_returns_file_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = create_memory_service(Configuration(project_workspace_root=tmp))

        self.assertIsInstance(service, FileMemoryService)

    def test_load_recent_task_logs_exposes_active_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "proj-2"
            project_dir.mkdir(parents=True)
            (project_dir / "PROJECT_STATUS.json").write_text(
                json.dumps(
                    {
                        "project_id": "proj-2",
                        "topic": "agent",
                        "stage": "refine_plan",
                        "active_tasks": ["Revise novelty", "Update baselines"],
                        "next_action": "Run reviewer again",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            service = FileMemoryService(Configuration(project_workspace_root=tmp))
            logs = service.load_recent_task_logs("proj-2")

        self.assertEqual([item["title"] for item in logs], ["Revise novelty", "Update baselines"])
        self.assertEqual(logs[0]["status"], "refine_plan")


if __name__ == "__main__":
    unittest.main()

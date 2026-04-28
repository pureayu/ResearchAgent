import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import Configuration
from project_workspace.service import ProjectWorkspaceService
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

    def test_workspace_index_and_project_card_are_created_with_name_and_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ProjectWorkspaceService(tmp)
            snapshot = workspace.create_project(topic="手机端大模型推理研究方向")
            workspace_index_text = (Path(tmp) / "PROJECT_INDEX.md").read_text(encoding="utf-8")
            card_text = Path(snapshot.files["project_card"]).read_text(encoding="utf-8")

        self.assertIn("## 手机端大模型推理研究方向", workspace_index_text)
        self.assertIn("- project_id:", workspace_index_text)
        self.assertIn("Name: 手机端大模型推理研究方向", card_text)
        self.assertIn("Description: Research workspace for: 手机端大模型推理研究方向", card_text)

    def test_load_relevant_context_searches_local_project_index_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unrelated = Path(tmp) / "unrelated"
            unrelated.mkdir(parents=True)
            (unrelated / "PROJECT_STATUS.json").write_text(
                json.dumps(
                    {
                        "project_id": "unrelated",
                        "topic": "图像生成",
                        "name": "Diffusion image project",
                        "description": "A project about image generation.",
                        "stage": "done",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            related = Path(tmp) / "mobile-llm"
            related.mkdir(parents=True)
            (related / "PROJECT_STATUS.json").write_text(
                json.dumps(
                    {
                        "project_id": "mobile-llm",
                        "topic": "端侧推理",
                        "name": "Mobile LLM inference",
                        "description": "手机端 NPU KV cache 和 speculative decoding 调研资料",
                        "stage": "human_gate",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (related / "PROJECT_CARD.md").write_text(
                "Name: Mobile LLM inference\nDescription: 手机端 NPU KV cache 和 speculative decoding 调研资料\n",
                encoding="utf-8",
            )
            (related / "IDEA_REPORT.md").write_text(
                "这里记录了端侧大模型推理、手机 NPU、KV cache 优化方向。",
                encoding="utf-8",
            )

            service = FileMemoryService(Configuration(project_workspace_root=tmp))
            context = service.load_relevant_context(None, "手机端大模型推理 NPU 方向")

        self.assertEqual(context["project_memory"][0]["project_id"], "mobile-llm")
        self.assertIn("Mobile LLM inference", context["working_memory_summary"])
        self.assertNotIn("Diffusion image project", context["working_memory_summary"])


if __name__ == "__main__":
    unittest.main()

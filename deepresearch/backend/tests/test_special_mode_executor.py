import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import Configuration
from execution.special_mode_executor import (
    RESPONSE_MODE_DEEP_RESEARCH,
    RESPONSE_MODE_DIRECT_ANSWER,
    RESPONSE_MODE_MEMORY_RECALL,
    SpecialModeExecutor,
)
from models import SummaryState


class StubAgent:
    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.clear_calls = 0

    def run(self, input_text: str, **_: object) -> str:
        self.prompts.append(input_text)
        if self._responses:
            return self._responses.pop(0)
        return ""

    def stream_run(self, input_text: str, **_: object):
        self.prompts.append(input_text)
        return iter(())

    def clear_history(self) -> None:
        self.clear_calls += 1


class SpecialModeExecutorTests(unittest.TestCase):
    def _make_executor(
        self,
        *,
        direct_answer_responses: tuple[str, ...] = ("",),
        classifier_responses: tuple[str, ...] = ("",),
        selector_responses: tuple[str, ...] = ("",),
        task_log_loader=None,
    ) -> SpecialModeExecutor:
        self.direct_answer_agent = StubAgent(*direct_answer_responses)
        self.classifier_agent = StubAgent(*classifier_responses)
        self.selector_agent = StubAgent(*selector_responses)
        return SpecialModeExecutor(
            Configuration(strip_thinking_tokens=False),
            self.direct_answer_agent,
            self.classifier_agent,
            self.selector_agent,
            task_log_loader=task_log_loader,
        )

    def test_classify_response_mode_downgrades_direct_answer_without_context(self) -> None:
        executor = self._make_executor(
            classifier_responses=(
                '{"response_mode":"direct_answer","confidence":0.92,"reason":"short question"}',
            ),
        )

        mode = executor.classify_response_mode("现在适合买吗", {})

        self.assertEqual(mode, RESPONSE_MODE_DEEP_RESEARCH)

    def test_classify_response_mode_allows_memory_recall_without_history(self) -> None:
        executor = self._make_executor(
            classifier_responses=(
                '{"response_mode":"memory_recall","confidence":0.88,"reason":"asking about history"}',
            ),
        )

        mode = executor.classify_response_mode("你还记得我之前说过什么吗", None)

        self.assertEqual(mode, RESPONSE_MODE_MEMORY_RECALL)

    def test_classify_response_mode_accepts_direct_answer_with_global_context(self) -> None:
        executor = self._make_executor(
            classifier_responses=(
                '{"response_mode":"direct_answer","confidence":0.83,"reason":"enough recalled context"}',
            ),
        )

        mode = executor.classify_response_mode(
            "这方案现在适合我吗",
            {"global_facts": [{"fact_id": "g1", "fact": "延迟和成本通常存在 tradeoff"}]},
        )

        self.assertEqual(mode, RESPONSE_MODE_DIRECT_ANSWER)

    def test_classify_response_mode_downgrades_direct_answer_when_only_task_logs_exist(self) -> None:
        executor = self._make_executor(
            classifier_responses=(
                '{"response_mode":"direct_answer","confidence":0.91,"reason":"looks answerable"}',
            ),
        )

        mode = executor.classify_response_mode(
            "这个方案适合我吗",
            {
                "task_logs": [
                    {
                        "task_id": 101,
                        "title": "分析端到端延迟",
                        "summary": "定位检索链路延迟瓶颈。",
                    }
                ]
            },
        )

        self.assertEqual(mode, RESPONSE_MODE_DEEP_RESEARCH)

    def test_memory_recall_uses_selector_output(self) -> None:
        executor = self._make_executor(
            selector_responses=(
                '{"run_ids":["run-1"],"task_ids":["101"],"fact_ids":["session-1","profile-1"]}',
            ),
        )
        state = SummaryState(
            recalled_context={
                "session_runs": [
                    {
                        "run_id": "run-1",
                        "topic": "RAG 延迟调研",
                        "finished_at": "2026-03-31T10:00:00+08:00",
                        "report_excerpt": "延迟与召回质量之间存在明显 tradeoff。",
                    },
                    {
                        "run_id": "run-2",
                        "topic": "无关主题",
                        "finished_at": "2026-03-30T10:00:00+08:00",
                        "report_excerpt": "无关内容",
                    },
                ],
                "task_logs": [
                    {
                        "run_id": "run-1",
                        "task_id": 101,
                        "title": "分析端到端延迟",
                        "summary": "定位检索和重排的延迟瓶颈。",
                    },
                    {
                        "run_id": "run-2",
                        "task_id": 202,
                        "title": "无关任务",
                        "summary": "不应被选中。",
                    },
                ],
                "working_memory_summary": "当前会话已总结：检索质量和延迟之间存在明显 tradeoff。",
                "recent_turns": [
                    {
                        "run_id": "run-1",
                        "user_query": "之前关于延迟的主要结论是什么？",
                        "assistant_response": "延迟和检索质量之间存在明显 tradeoff。",
                    }
                ],
                "profile_facts": [
                    {
                        "fact_id": "profile-1",
                        "fact": "用户长期关注端侧部署和延迟。",
                    }
                ],
            }
        )

        summary, sources_summary, evidence_count = executor._build_memory_recall_answer(
            state,
            "你还记得之前关于延迟的研究吗",
        )

        self.assertIn("RAG 延迟调研", summary)
        self.assertIn("用户长期关注端侧部署和延迟。", summary)
        self.assertIn("分析端到端延迟", summary)
        self.assertNotIn("无关主题", summary)
        self.assertIn("会话工作记忆摘要", summary)
        self.assertIn("Source: 用户画像记忆 1", sources_summary)
        self.assertGreaterEqual(evidence_count, 4)

    def test_memory_recall_selector_fallback_uses_simple_recency(self) -> None:
        executor = self._make_executor(selector_responses=("not json",))
        state = SummaryState(
            recalled_context={
                "session_runs": [
                    {
                        "run_id": "run-1",
                        "topic": "最近一次研究",
                        "finished_at": "2026-03-31T10:00:00+08:00",
                        "report_excerpt": "最近摘要",
                    }
                ],
                "task_logs": [
                    {
                        "run_id": "run-1",
                        "task_id": 1,
                        "title": "最近任务",
                        "summary": "应被回退带上。",
                    },
                    {
                        "run_id": "run-2",
                        "task_id": 2,
                        "title": "更老的任务",
                        "summary": "不应被带上。",
                    },
                ],
                "working_memory_summary": "最近一次研究的关键结论已经沉淀在工作记忆里。",
                "profile_facts": [{"fact_id": "pf-1", "fact": "用户偏好中文回答"}],
            }
        )

        summary, _, _ = executor._build_memory_recall_answer(state, "还记得最近那次研究吗")

        self.assertIn("最近一次研究", summary)
        self.assertIn("最近任务", summary)
        self.assertNotIn("更老的任务", summary)
        self.assertIn("用户偏好中文回答", summary)
        self.assertIn("工作记忆", summary)

    def test_memory_recall_loads_task_logs_via_dedicated_loader(self) -> None:
        def load_task_logs(session_id, *, exclude_run_id=None, limit=5):
            self.assertEqual(session_id, "session-1")
            self.assertEqual(exclude_run_id, "run-2")
            self.assertEqual(limit, 5)
            return [
                {
                    "run_id": "run-1",
                    "task_id": 101,
                    "title": "分析端到端延迟",
                    "summary": "定位检索链路延迟瓶颈。",
                }
            ]

        executor = self._make_executor(
            selector_responses=(
                '{"run_ids":["run-1"],"task_ids":["101"],"fact_ids":[]}',
            ),
            task_log_loader=load_task_logs,
        )
        state = SummaryState(
            session_id="session-1",
            run_id="run-2",
            recalled_context={
                "session_runs": [
                    {
                        "run_id": "run-1",
                        "topic": "RAG 延迟调研",
                        "finished_at": "2026-03-31T10:00:00+08:00",
                        "report_excerpt": "延迟与召回质量之间存在明显 tradeoff。",
                    }
                ],
                "working_memory_summary": "检索质量和延迟之间存在明显 tradeoff。",
            },
        )

        recalled_context = executor._build_memory_recall_context(state)
        summary, _, _ = executor._build_memory_recall_answer(
            state,
            "你还记得之前关于延迟的研究吗",
            recalled_context=recalled_context,
        )

        self.assertIn("分析端到端延迟", summary)
        self.assertNotIn("task_logs", state.recalled_context)

    def test_memory_recall_without_history_returns_fixed_message(self) -> None:
        executor = self._make_executor()

        summary, sources_summary, evidence_count = executor._build_memory_recall_answer(
            SummaryState(recalled_context={}),
            "你还记得我之前说过什么吗",
        )

        self.assertIn("当前没有找到足够相关的历史研究记录或用户记忆", summary)
        self.assertEqual(sources_summary, "")
        self.assertEqual(evidence_count, 0)

    def test_direct_answer_prompt_and_sources_include_global_facts(self) -> None:
        executor = self._make_executor()
        state = SummaryState(
            recalled_context={
                "profile_facts": [{"fact": "用户偏好简洁回答"}],
                "working_memory_summary": "当前会话已压缩出若干关键结论。",
                "global_facts": [{"fact": "压缩模型通常会影响召回质量"}],
                "task_logs": [
                    {"title": "不应进入短答", "summary": "task log 不应污染 direct answer"}
                ],
            }
        )

        prompt = executor._build_direct_answer_prompt(state, "这个方案适合我吗")
        sources_summary, evidence_count = executor._build_direct_answer_sources(state)

        self.assertIn("跨会话稳定知识", prompt)
        self.assertIn("压缩模型通常会影响召回质量", prompt)
        self.assertIn("当前会话工作记忆摘要", prompt)
        self.assertNotIn("最近相关任务", prompt)
        self.assertIn("Source: Global Memory 1", sources_summary)
        self.assertNotIn("Recent Task", sources_summary)
        self.assertEqual(evidence_count, 3)


if __name__ == "__main__":
    unittest.main()

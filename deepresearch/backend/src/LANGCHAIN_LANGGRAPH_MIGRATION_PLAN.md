# LangChain / LangGraph Migration Plan

## 目标

把当前 `deepresearch/backend/src` 从 `hello-agents` 驱动的自定义 runtime，逐步迁到：

- LangChain：负责模型接入、structured output、可组合调用
- LangGraph：负责工作流编排、streaming、checkpoint / execution state

原则：

- 先替换最脆弱的文本解析链路
- 再替换 workflow engine
- 保持现有 HTTP API 和前端事件格式稳定

## 当前系统分层

### 业务层

- `services/`
- `execution/`
- `services/memory.py`
- `services/search.py`
- `services/capabilities.py`

这部分保留，尽量不做语义变更。

### 运行时层

- `agent_runtime/factory.py`
- `agent_runtime/interfaces.py`
- `agent_runtime/tool_protocol.py`
- `orchestrator/deep_research.py`

这部分是主要迁移对象。

## 分阶段计划

### Phase 1: LangChain Structured Output

目标：

- 新增 LangChain model factory
- 为 planner / reviewer / source router 建立 schema 化调用
- planner 结果改由 Python 侧创建任务笔记

范围：

- 新增 `llm/` 目录
- 更新：
  - `services/planner.py`
  - `services/reviewer.py`
  - `services/source_routing.py`
  - `orchestrator/deep_research.py`
  - `pyproject.toml`

验收：

- 后端模块可正常导入
- planner / reviewer / source router 不再依赖手工 JSON 抽取
- 任务笔记仍能生成并发出 `tool_call` 事件

### Phase 2: LangGraph Workflow

目标：

- 用 graph node 重写 `DeepResearchAgent.run()` 和 `run_stream()`

预期状态字段：

- `session_id`
- `run_id`
- `response_mode`
- `research_topic`
- `recalled_context`
- `todo_items`
- `research_loop_count`
- `structured_report`

预期 node：

- `load_context`
- `classify_mode`
- `plan_tasks`
- `execute_task_round`
- `review_round`
- `generate_report`
- `persist_report`

验收：

- graph 输出与当前 `SummaryStateOutput` 对齐
- 现有 SSE 事件能够从 graph stream 重新投影

### Phase 3: 扩大 LangChain 覆盖面

目标：

- 迁移 response mode classifier / memory recall selector
- 评估 summarizer / reporter 的 note tool 兼容策略
- 减少 hello-agents 仅剩的运行时责任

可选方向：

- 继续保留自定义 `[TOOL_CALL:...]` 协议
- 或把 note 功能迁成 LangChain tool，并统一事件桥接

## 当前决策

- 不先重写前端
- 不先替换 `MemoryService`
- 不先并行化 todo task fan-out
- 不把 LangChain agent 黑盒塞进现有工作流

## 本轮开始执行的内容

- 完成 Phase 1 的基础设施与首批服务迁移

## 当前完成情况

### 已完成

- 用 LangChain 替换了原 `hello-agents` runtime 依赖
- 新增 `llm/` 目录，统一模型初始化与 structured output
- 已迁移：
  - planner
  - reviewer
  - source router
  - response mode classifier
  - memory recall selector
- 已新增 `graph/` 目录，并让 `DeepResearchAgent.run()` / `run_stream()` 进入 LangGraph workflow
- 保留了现有 SSE 事件格式和 note 文本协议

### 尚未完成

- 在真实 `MEMORY_DATABASE_URL` 环境下跑完整的端到端回归
- 将 summarizer / reporter 的 note 协议进一步收敛成更标准的 LangChain tool bridge
- 进一步降低 orchestrator 中残留的 graph node glue code

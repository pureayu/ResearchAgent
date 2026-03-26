# Agent 集成设计与跟踪

## 目标

在保留当前 `paper_assistant` 本地文献 RAG 内核的前提下，基于现有深度研究 Agent 骨架做二次开发，形成一个可规划、可调用工具、可追踪状态、可生成研究报告的研究型智能体。

核心原则：
- 不把现有 RAG 项目强行改名成 Agent。
- 不推翻现有检索与评测资产。
- 先把 RAG 能力工具化，再接入 Agent 外层编排。
- 先做最小闭环，再补联网搜索、反思和更复杂的能力。

## 当前已有资产

### 本地文献能力
- 文档导入与 PDF 清洗
- chunking 与 metadata 管理
- `local / simple / lightrag` 三条后端
- 当前主后端：`simple`
- `bm25 / vector / hybrid`
- controlled query expansion
- dual-query retrieval
- citation / snippet / full chunk 上下文组装

### 评测与工程能力
- retrieval-level eval
  - `hit@1`
  - `hit@k`
  - `mrr`
  - `latency`
- answer-level eval
  - correctness
  - groundedness
  - citation_use
  - pass_rate
- 实验日志、架构决策、待办跟踪

## 目标架构

```text
Frontend / UI
  └─ Research UI
      ├─ 任务输入
      ├─ 流式阶段状态
      ├─ 任务级检索指标
      ├─ query rewrite / search trace
      ├─ 证据面板
      └─ 最终报告展示

Agent Orchestrator
  ├─ Planner
  ├─ Tool Router
  ├─ Task Executor
  ├─ Reporter
  └─ Trace Manager

Tool Layer
  ├─ LocalLibrarySearchTool
  ├─ LocalLibraryAnswerTool
  ├─ WebSearchTool
  └─ Workspace / Note Tool

Local RAG Core
  ├─ document_loader
  ├─ metadata / processed store
  ├─ simple retrieval backend
  ├─ citation retriever
  └─ llm / embedding client
```

## 当前系统定位

当前接入后的系统已经是一个轻量 Agent workflow，而不再只是本地 RAG 脚本。

当前已经具备：
- 总控 orchestrator
- planner
- 任务总结 agent
- 报告撰写 agent
- 工具调用
- 流式事件输出
- 最小任务内执行回路

当前任务级执行形态是：

1. `planning`
2. 对每个 `TodoItem` 执行最小 researcher loop
   - `retrieving_local`
   - evidence gap 判断
   - 若证据不足，则 `query_rewrite -> retrieving_web`
   - local/web 结果合并
   - `summarizing`
3. `reporting`

当前系统还不具备：
- 多个 researcher agent 并行协作
- search -> reflect -> rewrite -> search 的多轮自驱闭环
- critic / reviewer agent
- 完整结构化 trace 与来源区分报告
- 工具调用与任务阶段的统一 trace schema

当前前端展示层已经具备：
- 研究主题输入
- 任务清单
- 流式阶段日志
- 任务级检索指标（backend / attempt / evidence / top score）
- query rewrite / search result 轨迹
- 来源列表与最终报告展示

这层定位需要写清楚，避免把当前系统误判成“最终形态 deep research multi-agent 系统”。

## 阶段拆分

### 阶段 0：冻结当前 RAG 主线

目标：
- 明确当前 `paper_assistant` 的职责边界。
- 停止继续把当前项目向“伪 Agent”方向硬扩。

交付物：
- 当前主后端、主评测、主入口稳定。
- `simple-hybrid` 作为默认主检索路径。
- 现有评测脚本保留，作为后续 Agent 集成后的回归基线。

完成标准：
- 当前项目仍能独立运行：
  - 文献导入
  - 检索问答
  - 主题总结
  - retrieval eval
  - answer eval

状态：`[x]`

验证结论：
- 对本地证据充足的研究类 query，不会触发多余补搜；
- 对非本地主题 query，已观察到：
  - `query_rewrite`
  - `retrieving_web`
  - 合并后的 `search_backend=local_library+duckduckgo`
  - `source_breakdown={'local_library': 5, 'web_search': 3}`

---

### 阶段 1：工具化现有 RAG 内核

目标：
- 把 `paper_assistant` 从“应用主体”改成“可被 Agent 调用的工具层”。

要做的事：
- 定义统一的工具输入输出 schema。
- 拆出最小工具接口：
  - `LocalLibrarySearchTool`
  - `LocalLibraryAnswerTool`
- 统一返回字段：
  - `query`
  - `titles`
  - `citations`
  - `scores`
  - `evidence`
  - `latency`
- 给工具调用增加结构化日志。

建议接口：

#### LocalLibrarySearchTool
- 输入：
  - `query: str`
  - `top_k: int`
  - `retrieval_mode: bm25|vector|hybrid`
  - `filters: dict | None`
- 输出：
  - `query`
  - `resolved_mode`
  - `results: list[CitationLike]`
  - `latency_ms`

#### LocalLibraryAnswerTool
- 输入：
  - `question: str`
  - `evidence: list[CitationLike]`
  - `response_type: str | None`
- 输出：
  - `answer`
  - `used_titles`
  - `citation_map`
  - `grounded: bool | None`

交付物：
- 工具层接口文档
- 工具层 Python 包装代码
- 最小调用示例脚本

完成标准：
- 不依赖 CLI 入口，也能单独调用“检索”和“基于证据回答”。
- 输出 schema 固定，不再靠打印文本解析。

状态：`[x]`

---

### 阶段 2：接入 Agent 骨架并形成最小闭环

目标：
- 把已有 Agent 骨架作为外层 orchestrator，先形成一个能跑通的研究闭环。

最小闭环：
1. 用户输入研究主题
2. Planner 生成子任务
3. Router 选择工具
4. 执行本地文献检索
5. 汇总子任务结果
6. 生成最终报告

当前约束：
- 先不做复杂 memory
- 先不做多 agent 协作
- 先不做联网补搜

交付物：
- 一个可运行的 research workflow
- 至少一个 Planner
- 至少一个本地文献检索工具接入
- 最终报告输出

完成标准：
- 能针对一个研究主题输出分阶段结果和最终报告。
- 不是单次问答，而是“规划 -> 执行 -> 汇总”结构。

状态：`[x]`

---

### 阶段 3：补状态流与 trace

目标：
- 让系统具备可观测性，而不是黑盒一次性出结果。

最小状态集合：
- `planning`
- `retrieving_local`
- `retrieving_web`
- `summarizing`
- `reporting`
- `done`
- `failed`

trace 字段建议：
- `task_id`
- `stage`
- `tool_name`
- `tool_input`
- `tool_output_summary`
- `selected_evidence`
- `latency_ms`
- `timestamp`

交付物：
- 流式阶段状态
- 每次工具调用 trace
- 研究任务结果存档

完成标准：
- 前端或日志中能明确看到系统每一步做了什么。
- 出错时能定位卡在哪个阶段、哪个工具。

状态：`[ ]`

---

### 阶段 3.5：强化任务内 researcher loop

目标：
- 把当前“local 不足则直接 web 搜”的最小补检索，升级为更像研究员行为的受控回路。

要做的事：
- 基于证据缺口判断是否继续追搜；
- 在 follow-up 前生成改写 query，而不是原 query 原样再搜；
- 合并 local / web 结果，避免网页补搜覆盖本地文献证据；
- 把 query rewrite 和 evidence gap 写入事件流，便于 trace。

交付物：
- `evidence_gap_reason`
- `latest_query`
- `query_rewrite` 事件
- local + web 结果合并策略

完成标准：
- 对本地证据不足的任务，事件流中能看到：
  - 缺口原因
  - follow-up query
  - 再检索结果
- 最终总结上下文不再只保留单一路径结果。

状态：`[x]`

---

### 阶段 4：加入联网搜索补充能力

目标：
- 形成“本地文献优先，外部搜索补充”的双源研究模式。

策略：
- 本地文献足够时，不联网。
- 本地证据不足时，触发 `WebSearchTool`。
- 报告中区分：
  - 本地文献来源
  - 联网来源

交付物：
- `WebSearchTool`
- 简单的路由规则
- 本地/联网来源标记

完成标准：
- 至少有一类研究问题会先查本地，不足时自动转联网。
- 最终报告中保留来源类型。

状态：`[ ]`

---

### 阶段 5：补反思、回路和评测

目标：
- 让系统能判断“证据是否不足”，并触发二次动作。

优先做的闭环：
- 证据不足 -> 触发补检索
- 结果冲突 -> 补充搜索或重新总结
- 报告缺引用 -> 回退到 evidence gathering

评测建议：
- 工具层调用成功率
- 子任务完成率
- 报告引用覆盖率
- 最终回答 groundedness
- 总任务耗时

完成标准：
- 至少一个真实闭环被实现，而不是 prompt 里写一句“请反思”。

状态：`[ ]`

## 开发顺序

严格按下面顺序推进：

1. 阶段 1：工具化 RAG 内核
2. 阶段 2：接 Agent 骨架跑最小闭环
3. 阶段 3：补 trace 和状态流
4. 阶段 4：补联网搜索
5. 阶段 5：补反思和闭环

禁止事项：
- 不先上 memory
- 不先上多 agent
- 不先做很重的 reflection
- 不在工具接口没稳定前改太多前端

## 当前最小闭环状态

当前已经跑通的链路：

1. `DeepResearchAgent.run()` / `run_stream()`
2. `PlanningService`
3. `_execute_task()`
4. `dispatch_search()`
   - `local_library` 分支 -> `LocalLibrarySearchTool` -> `SimpleVectorRAG.query()`
   - 默认网页分支 -> `SearchTool.run()`
5. `prepare_research_context()`
6. `SummarizationService`
7. `ReportingService`

当前 `_execute_task()` 已经从“单次搜一次”升级为最小 task-level researcher loop：

- 第一轮固定本地检索
- 根据 `evidence_count / top_score / query domain hint` 判断是否需要 follow-up
- 需要时再触发网页检索
- 之后进入总结

也就是说，现在已经有：
- 多阶段 Agent 编排
- 本地/网页双路检索
- 最小任务内补检索回路

但还没有：
- 更强的 researcher reflection loop

## 下一步主线

当前不继续扩工具，也不直接上 memory / critic / 多 agent。

接下来只做三件事，顺序固定：

1. 继续补统一 trace
   - 把工具调用 trace 和任务阶段 trace 收敛成一套稳定结构

2. 再进入下一阶段的 researcher loop 强化
   - 例如 evidence gap -> rewrite query -> re-search

## 当前迭代任务

- [x] 设计 `LocalLibrarySearchTool` 输入输出 schema
- [x] 设计 `LocalLibraryAnswerTool` 输入输出 schema
- [x] 把现有 `simple` 检索封装成工具接口
- [x] 在第十四章后端增加 `local_library` 搜索分支
- [ ] 增加工具调用 trace 结构
- [x] 盘点目标 Agent 骨架的实际代码结构
- [x] 明确第一版接入点：Planner / SearchTool / Reporter
- [x] 让任务执行阶段能显式区分 `retrieving_local` 与 `retrieving_web`
- [x] 让本地搜索结果进入任务总结与最终报告链路
- [x] 给 `TodoItem` 增加最小执行态字段
  - [x] `attempt_count`
  - [x] `search_backend`
  - [x] `evidence_count`
  - [x] `top_score`
  - [x] `needs_followup`
- [x] 修 Planner 的结构化输出与解析，避免退回 fallback task
- [x] 在 SSE 中补全 `backend / attempt_count / evidence_count / top_score / needs_followup`
- [x] 让最终报告显式区分本地文献来源与联网来源
- [x] 用 `MAX_TODO_ITEMS=2` 完成缩小规模端到端验收
- [x] 修非流式 `run()` 路径，确保真正执行 `_execute_task()`
- [x] 修 reporter 的 groundedness，避免把已完成任务写成 `pending`
- [x] 修 reporter 对来源摘要的遵循，避免把已有本地文献写成“暂无来源”
- [ ] 继续补工具 trace 的统一结构

## 已确认的 Agent 骨架接入点

目标骨架仓库：
- [hello-agents](/home/pureayu/code/hello-agents)

第十四章项目位置：
- [helloagents-deepresearch](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch)

### 已确认的关键文件

- 后端入口：
  [main.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/main.py)
- 主编排器：
  [agent.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/agent.py)
- planner：
  [planner.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/planner.py)
- search service：
  [search.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/search.py)
- summarizer：
  [summarizer.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/summarizer.py)
- reporter：
  [reporter.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/reporter.py)
- 状态模型：
  [models.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/models.py)
- 工具事件追踪：
  [tool_events.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/tool_events.py)

### 当前调用链结论

当前第十四章后端主链路是：

1. `main.py` 暴露 `/research` 与 `/research/stream`
2. `DeepResearchAgent.run()` / `run_stream()` 作为总入口
3. `PlanningService` 生成 `TodoItem`
4. `_execute_task()` 中调用 `dispatch_search()`
5. `prepare_research_context()` 组装检索上下文
6. `SummarizationService` 生成任务总结
7. `ReportingService` 汇总所有任务生成最终报告

### 第一版最合适的接入层

不建议一开始就直接改 Planner 或 Reporter。

第一版接入点应该是：
- `services/search.py`

原因：
- 当前搜索能力集中在 `dispatch_search()` 和 `prepare_research_context()`
- 这一层正好是 Agent 与工具层之间的桥
- 修改这一层，可以最小侵入地把“网页搜索”扩展成：
  - 本地文献搜索
  - 网页搜索
  - 后续本地优先、外部补充的双源策略

### 第一版接入策略

第一版不直接替换整个 SearchTool，而是：

1. 在 `paper_assistant` 中先实现：
   - `LocalLibrarySearchTool`
   - `LocalLibraryAnswerTool`
2. 在第十四章后端新增一个本地搜索适配层
3. 让 `services/search.py` 具备最小的路由能力：
   - 本地文献任务 -> 调本地检索工具
   - 默认任务 -> 保留原网页搜索

### 暂不改动的部分

第一版先不改：
- Planner prompt 结构
- Reporter prompt 结构
- NoteTool 逻辑
- 前端页面
- 多轮反思闭环
- memory

## 第一版实施顺序（已细化）

1. 先在 `paper_assistant` 内定义工具 schema
2. 实现本地工具 Python 适配层
3. 写一个最小的本地搜索调用示例
4. 在第十四章 `services/search.py` 增加本地搜索分支
5. 跑通 `planning -> local search -> summarize -> report`
6. 再考虑状态流里补充“本地检索”事件类型

## 风险与约束

### 风险 1：变成“套壳 Agent”
表现：
- 外面加了 planner 名字
- 里面还是单轮检索问答

规避：
- 工具必须真调用
- trace 必须可见
- 规划输出必须结构化

### 风险 2：工具粒度过粗
表现：
- `planner -> super_rag_tool -> done`

规避：
- 搜索、回答、工作区管理分开

### 风险 3：功能过多导致失控
表现：
- 同时改骨架、前端、联网、报告、trace

规避：
- 只做最小闭环
- 每阶段单独验收

## 跟踪日志

后续每完成一个阶段或关键子任务，在这里追加简短记录：

### 2026-03-26
- 初始化 Agent 集成阶段计划。
- 当前状态：阶段 0 完成，准备进入阶段 1。
- 已 clone 并盘点 `hello-agents` 第十四章代码结构。
- 已确认第一版接入点是 `backend/src/services/search.py`，不是直接改 Planner 或 Reporter。
- 已在 `paper_assistant` 中新增本地工具接口文件：
  [local_library_tools.py](/home/pureayu/code/paper_assistant/app/local_library_tools.py)
- 当前已完成：
  - `LocalLibrarySearchTool` schema
  - `LocalLibraryAnswerTool` schema
  - 第十四章后端 `local_library` 搜索分支
  - 最小 task-level researcher loop
  - `TodoItem` 最小执行态字段
- 当前判断：
  - 系统已经是轻量 Agent workflow
  - 但还不是完整 deep research multi-agent / reflection loop
  - 下一步主线是补 trace 与来源区分
- 已修复 Planner 输出/解析链：
  - 收紧 prompt，要求最终仅输出 JSON
  - parser 增加 `JSON -> TOOL_CALL note create -> Markdown` 多层兜底
  - 真机验证已能稳定产出 5 个 `TodoItem`，不再退回 fallback task
- 已补最小 SSE 检索 trace：
  - `task_stage` 事件中带出 `attempt / previous_backend / previous_evidence_count / previous_top_score`
  - 新增 `search_result` 事件，带出：
    - `backend`
    - `attempt_count`
    - `evidence_count`
    - `top_score`
    - `needs_followup`
    - `source_breakdown`
    - `titles_preview`
  - `sources` 与最终 `task_status` 事件同步包含检索摘要字段
- 已将来源类型打入总结/报告输入：
  - 搜索结果统一带 `source_type`
  - `prepare_research_context()` 会在 `sources_summary` 与 `context` 顶部加入“来源类型统计”
  - reporter prompt 已要求显式区分“本地文献来源”和“联网来源”
  - 基于 `simple` 后端与 `LiteratureLLM` 的最小工具封装
- 已在第十四章后端中新增 `search_api=local_library` 分支：
  - [config.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/config.py)
  - [search.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/search.py)
- 已完成最小联调：
  - 通过第十四章后端调用 `dispatch_search(..., search_api=local_library)`
  - 能返回本地文献库中的 `results / sources / context`
- 发现并处理的环境问题：
  - 第十四章后端自己的 `pip install -e .` 会因为 packaging 配置报错
  - 已改为在项目本地 `.venv` 中手动安装运行依赖
  - `hello-agents` 额外缺少 `huggingface_hub`，已补齐
- 当前下一步：
  - 把“本地搜索分支”接入任务状态流
  - 让结果在总结与报告阶段可见并可区分来源类型
- 已为第十四章后端补上最小 task-level researcher loop：
  - 第一轮固定 `local_library`
  - 本地证据不足时切到网页搜索
  - 在任务状态中记录：
    - `attempt_count`
    - `search_backend`
    - `evidence_count`
    - `top_score`
    - `needs_followup`
- 已完成最小行为验证：
  - 相关 query：只走 `retrieving_local`
  - 非领域 query：会进入 `retrieving_web`
- 当前剩余问题：
  - 第十四章后端的 SSE 事件里还没有完整携带“来源类型”和更细的 trace 字段
  - 目前只是最小 loop，不是真正多轮 researcher agent

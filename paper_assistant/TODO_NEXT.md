# 下一步计划

## 已完成

- [x] 建立 memory v1 基础结构：
  - [x] `app/memory/models.py`
  - [x] `app/memory/store.py`
  - [x] `app/memory/manager.py`
- [x] 在 `app/config.py` 中增加 `memory_dir`
- [x] 在 `scripts/query_documents.py` 中接入 `--session-id`
- [x] 在新仓库中重建 `simple` 索引（643 chunks）
- [x] 在 `app/llm_client.py` 中新增 `rewrite_question(history, question)`
- [x] 在 `scripts/query_documents.py` 中将检索问题切换为 `resolved_question`
- [x] 在回答完成后，将 `question / resolved_question / answer / citation_titles` 写回 working memory
- [x] 明确第一版 memory 的职责边界：
  - [x] 当前优先实现 `query rewrite / 上下文补全`
  - [x] 暂不实现完整 `intent recognition / routing`
  - [x] 若后续支持问答、总结、对比、报告等多任务入口，再单独引入意图识别层
- [x] 基础追问验收完成：
  - [x] “它和微调有什么区别？”
  - [x] “第二篇论文的方法呢？”已避免乱改写，但仍不能稳定解析“第二篇”的具体指向
  - [x] “重点讲上一个方法的局限”已避免脑补新实体，但抽象指代解析仍不稳定

## 当前主线

- [ ] 拆分 `working_memory.py`
  - [ ] 新建 `app/memory/working_memory.py`
  - [ ] 将 `append_turn / get_recent_turns / has_history / format_history / clear_session` 从 `manager.py` 移入 `WorkingMemory`
  - [ ] 将 `manager.py` 收敛为薄封装，作为后续 `working + research` 的统一入口
- [ ] 验证拆分后 CLI 行为不变
  - [ ] 同一 `session_id` 下追问补全仍可用
  - [ ] `resolved_question` 仍参与检索
  - [ ] 回答后写回 session JSON 仍正常

## 下一阶段增强

- [ ] 为 working memory 增加“最近几轮 + query 检索”能力
  - [ ] 新增 `search_relevant_turns(session_id, query, limit)`
  - [ ] 第一版优先采用轻量打分（关键词 / TF-IDF），不引入新的 embedding 依赖
  - [ ] 在 `build_context` 中组合“最近几轮 + 相关历史”
- [ ] 序号型指代解析
  - [ ] 利用上一轮 `citation_titles` 做“第一篇 / 第二篇论文”映射
- [ ] 抽象型指代解析
  - [ ] 利用上一轮检索结果或回答摘要解析“上一个方法 / 前者 / 后者”

## 后续工作

- [ ] 引入 `research_memory.py`
  - [ ] 沉淀高价值研究结论，而不是只保存 turn history
  - [ ] 设计最小 `ResearchNote` 结构
- [ ] 做 working / research memory 的 consolidate 规则
  - [ ] working memory 保持短期、受限
  - [ ] 高价值结论进入 research memory
- [ ] 统一 CLI 与 Web 的 session / memory 语义
  - [ ] Web 请求透传 `session_id`
  - [ ] 后端入口复用同一套 memory manager

## chapter14 并行项

- [x] 按 [AGENT_INTEGRATION_PLAN.md](/home/pureayu/code/paper_assistant/AGENT_INTEGRATION_PLAN.md) 推进阶段 1：工具化现有 RAG 内核
- [x] 设计 `LocalLibrarySearchTool` 输入输出 schema
- [x] 设计 `LocalLibraryAnswerTool` 输入输出 schema
- [x] 将现有 `simple` 检索封装为可调用工具接口
- [x] 盘点目标 Agent 骨架的代码结构与可接入点
- [x] 明确第一版最小闭环：
  - [x] planner
  - [x] local library search
  - [x] summarizer / reporter
  - [x] 流式状态输出
- [x] 在 chapter14 后端接入 `local_library` 搜索分支
- [x] 将任务执行升级为最小 researcher loop
- [x] 修 Planner 的结构化输出与解析，避免退回 fallback task
- [x] 在 SSE 事件中补全 `backend / attempt_count / evidence_count / top_score / needs_followup`
- [x] 让最终报告显式区分本地文献来源与联网来源
- [x] 用 `MAX_TODO_ITEMS=2` 跑一遍缩小规模完整验收
- [x] 修非流式 `run()` 路径，确保真正执行 `_execute_task()`
- [x] 修 reporter 的 groundedness，避免把已完成任务写成 `pending`
- [x] 修 reporter 对来源摘要的遵循，避免把已有本地文献结果写成“暂无来源”
- [x] 开始设计并落地 stronger researcher loop（evidence gap -> rewrite query -> re-search）
- [x] 验证 stronger researcher loop 在本地不足时是否真的触发 query rewrite 和结果合并
- [x] 为 chapter14 前端接入任务指标与执行轨迹展示
- [x] 前端构建验证通过
- [ ] 为工具调用和任务阶段补统一 trace 结构
- [ ] 跑一次完整前后端联调，确认页面能实时展示新事件字段

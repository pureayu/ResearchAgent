# 下一步计划

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
- [ ] 为工具调用和任务阶段补统一 trace 结构
- [x] 在 SSE 事件中补全 `backend / attempt_count / evidence_count / top_score / needs_followup`
- [x] 让最终报告显式区分本地文献来源与联网来源
- [x] 用 `MAX_TODO_ITEMS=2` 跑一遍缩小规模完整验收
- [x] 修非流式 `run()` 路径，确保真正执行 `_execute_task()`
- [x] 修 reporter 的 groundedness，避免把已完成任务写成 `pending`
- [x] 修 reporter 对来源摘要的遵循，避免把已有本地文献结果写成“暂无来源”
- [ ] 继续补工具调用 trace 的统一结构
- [x] 开始设计并落地 stronger researcher loop（evidence gap -> rewrite query -> re-search）
- [x] 验证 stronger researcher loop 在本地不足时是否真的触发 query rewrite 和结果合并
- [x] 为 chapter14 前端接入任务指标与执行轨迹展示
- [x] 前端构建验证通过
- [ ] 跑一次完整前后端联调，确认页面能实时展示新事件字段

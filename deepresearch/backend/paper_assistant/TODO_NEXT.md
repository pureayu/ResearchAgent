# 下一步计划

## 当前决策

- [x] 明确 memory ownership：
  - [x] Web / Agent 后端是 memory 的主归属层
  - [x] `paper_assistant/app/memory/*` 不再作为主线继续扩展
  - [x] `paper_assistant/app/memory/*` 已从代码层移除
  - [x] 后续多轮对话、研究续写、planner recall、自循环 loop 优先在后端实现

## 已完成

- [x] 建立过 `paper_assistant` memory 原型并完成验证
  - [x] working memory / research memory 原型已跑通
  - [x] 基础追问补全曾在 CLI 中验证通过
  - [x] research note 最小骨架已实现过
  - [x] 现已确认这层不是后续主线

## 当前主线

- [ ] 强化 Web / Agent 后端的 memory 主线
  - [ ] 明确 `session / run / task / semantic fact` 的职责边界
  - [ ] 统一前端 `session_id`、后端 session recall、报告续写语义
  - [ ] 让 planner / summarizer / reporter 使用统一的 recalled context
  - [ ] 评估并消除后端 memory 与本地工具层 memory 的重复部分

- [ ] 将网页端从“单次研究任务”升级为“多轮研究对话”
  - [ ] 前端引入稳定的 `session_id`
  - [ ] 同一会话内连续提问不重置上下文
  - [ ] 后端 research 接口接收并透传 `session_id`
  - [ ] 当前研究报告与后续追问共享同一 memory
  - [ ] 明确“开始新对话 / 继续当前对话”的页面交互语义

- [ ] 将当前 workflow 从“规划-执行-总结”升级为“有限轮自循环研究”
  - [ ] 在 task 内局部 follow-up loop 之外，增加 run 级 reflection
  - [ ] 对复杂问题支持 `plan -> execute -> reflect -> replan -> execute`
  - [ ] 增加明确的停止条件，避免无限循环与无效烧 token
  - [ ] 优先做可控的 2~3 轮 research loop，而不是一步上无限自循环

## 下一阶段增强

- [ ] 用后端 memory 提升 planner recall 质量
  - [ ] 区分 session runs / recent tasks / semantic facts 的权重
  - [ ] 限制 planner recall 注入长度，避免 prompt 膨胀
- [ ] 用后端 memory 支持报告续写与追问
  - [ ] 明确“继续研究”时如何继承已有报告与任务结果
  - [ ] 明确“同会话新主题”与“继续同主题”的差异
- [ ] 用后端 memory 支持更强的反思式 loop
  - [ ] 让反思阶段利用已有 task memory 判断缺口
  - [ ] 在补任务前复用已有证据与 semantic facts

## 后续工作

- [x] 清理 `paper_assistant/app/memory/*`
  - [x] 移除已废弃的 working / research memory 原型代码
  - [x] 清理 CLI 对旧 memory manager 的依赖
  - [x] 避免新人误解为主线 memory 所在
- [ ] 为仓库补一份统一架构文档
  - [ ] 明确 `deepresearch` 与 `paper_assistant` 的职责边界
  - [ ] 明确 memory 的单一所有权在后端 orchestrator 层

## chapter14 并行项

- [x] 按 [AGENT_INTEGRATION_PLAN.md](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/AGENT_INTEGRATION_PLAN.md) 推进阶段 1：工具化现有 RAG 内核
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

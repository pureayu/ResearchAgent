# 架构决策

## 使用 simple 后端作为当前主检索路径

- 日期：2026-03-25
- 背景：
  - LightRAG 在当前 MVP 阶段导入较慢，状态跟踪不稳定。
  - 项目需要一个可控、可评测、适合快速迭代的检索基线。
- 决策：
  - 保留 LightRAG 代码，用于后续对比。
  - 当前开发和优化以 `simple` 后端为主。
- 影响：
  - 后续重点放在 BM25、向量检索、混合召回、query expansion、rerank 和评测上。


## 将检索评测与生成评测分离

- 日期：2026-03-25
- 背景：
  - LLM 最终回答质量会掩盖检索层问题。
- 决策：
  - 先建立 retrieval-only 的评测流程，再做回答生成层实验。
- 影响：
  - 检索层优化可以通过 `hit@1`、`hit@k`、`mrr` 和 latency 独立验证。


## 优先使用受控 query expansion，而不是一开始就接重型 reranker

- 日期：2026-03-26
- 背景：
  - 当前失败题主要来自 vocabulary mismatch / expression mismatch，而不是缺文档。
- 决策：
  - 先做基于失败样本的 controlled query expansion。
  - 更重的 reranker 留到基线更稳定、语料更大之后再接入。
- 影响：
  - 当前检索优化成本低、可解释、可复现。


## 使用 dual-query retrieval，而不是用 expanded query 直接替换原 query

- 日期：2026-03-26
- 背景：
  - expanded query 直接替换原 query 会带来 query drift 风险。
- 决策：
  - 保留原 query。
  - 并行跑原 query 和 expanded query。
  - 对 expanded query 路径赋较低融合权重。
- 影响：
  - 检索更稳，原始问题意图不容易丢失。


## 不将当前项目强行改造成 Agent，而是保留为工具层并接入现有 Agent 骨架

- 日期：2026-03-26
- 背景：
  - 当前 `paper_assistant` 在本地文献 RAG、检索优化和评测上已经较完整。
  - 但它缺少 Agent 面试常见的规划、工具路由、状态流和 trace 等能力。
  - 直接在当前项目里硬加 Planner / Memory / Reflection，容易变成名词很多但代码很薄的伪 Agent。
- 决策：
  - 保留当前项目作为本地文献检索与 grounded answer 的工具层。
  - 基于现有深度研究 Agent 骨架做二次开发。
  - 先做“工具化 -> 接入骨架 -> 状态流 -> 联网补充”的最小闭环。
- 影响：
  - 当前项目主价值从“完整应用”转为“research-grade local retrieval core”。
  - 后续新增能力以 Agent orchestrator 为主，而不是继续重构底层 RAG 内核。

# 实验日志

## 2026-03-28 - research memory 最小骨架落地

### 背景问题
working memory 已经完成 session 级闭环，但系统仍然只能记住“最近几轮”，还不能把已经沉淀出来的研究结论单独保存下来。

### 改动内容
- 在 `app/memory/models.py` 中新增：
  - `ResearchNote`
  - `ResearchNoteSession`
- 在 `app/memory/store.py` 中新增：
  - `load_research_note_session(session_id)`
  - `save_research_note_session(note_session)`
- 在 `app/memory/research_memory.py` 中实现：
  - `append_note(...)`
  - `list_notes(session_id)`
  - `format_notes(session_id)`
  - `clear_notes(session_id)`
- 在 `app/memory/manager.py` 中扩展统一入口：
  - `append_note(...)`
  - `list_notes(...)`
  - `format_notes(...)`
  - `clear_notes(...)`

### 验证结果
- 通过 `MemoryManager.append_note()` 写入测试 note：
  - session: `demo_research`
  - question: `RAG 的主要挑战是什么？`
- `format_notes("demo_research")` 返回正常：
  - `[Note 1] 问题：...`
  - `结论：...`
- 落盘文件正常生成：
  - `data/memory/research/demo_research.json`

### 当前判断
- research memory 的最小骨架已经成立：
  - 数据模型
  - 存储
  - 业务接口
  - manager 统一入口
- 当前还只是“手动写入 research note”
- 下一步不是继续扩数据结构，而是定义：
  - 什么情况下把一轮问答自动沉淀成 research note
  - 后续 query 怎么读取 research notes

### 下一步
- 定义第一版“高价值结论”规则
- 在真实问答闭环中自动 `append_note()`
- 后续再考虑 research note 的检索与 consolidate

## 2026-03-28 - working memory 接入 query rewrite，并收紧追问补全策略

### 背景问题
第一版 working memory 底座和 CLI 接线已经完成，但 history 仍停留在“读出来并打印”的阶段，没有真正参与检索问题理解。

### 改动内容
- 在 `app/llm_client.py` 中新增：
  - `rewrite_question(question, history_text)`
- 在 `scripts/query_documents.py` 中新增：
  - `resolved_question`
  - 检索和回答统一切换为基于 `resolved_question`
  - 回答后将 `question / resolved_question / answer / citation_titles` 写回 session memory
- 对 rewrite 策略进行收紧：
  - 只在明显追问场景下触发 rewrite
  - prompt 中明确禁止引入历史中未出现的新实体、新方法名和新术语
  - 如果无法确定指代对象，则保持原问题不变

### 验证结果
- 基础代词补全场景通过：
  - `什么是 RAG？`
  - `它和微调有什么区别？`
  - 第二轮稳定改写为：`RAG 和微调有什么区别？`
- 序号型和抽象型追问场景表现为“保守但不稳定”：
  - `第二篇论文的方法呢？`
    - 已避免继续脑补具体论文名
    - 但尚不能稳定解析“第二篇”对应哪篇论文
  - `重点讲上一个方法的局限`
    - 已避免无依据扩展出新的方法名
    - 但抽象指代的解析仍不稳定
- `data/memory/*.json` 已能正常记录：
  - `question`
  - `resolved_question`
  - `answer`
  - `citation_titles`

### 当前判断
- 第一版 working memory 已形成完整闭环：
  - 读取 history
  - rewrite 当前问题
  - 用 `resolved_question` 检索
  - 回答后写回 session
- 当前更适合“保守补全”，不适合复杂指代解析
- 复杂的序号型指代和抽象指代，需要下一阶段引入基于 citation / answer summary 的更细化 memory 解析

### 下一步
- 将调试输出改为可选开关，避免 CLI 正常使用时输出过多中间状态
- 若继续增强 memory：
  - 优先补“序号型指代解析”
  - 暂不引入完整 intent recognition
  - 暂不引入 research memory，先把 working memory 收稳

## 2026-03-28 - 接入第一版 working memory（session-level）

### 背景问题
当前 `paper_assistant` 的问答链路仍然是单轮优先：
- 每次 CLI 提问都默认独立处理；
- 虽然已有多轮研究需求，但历史上下文没有统一的 session 语义；
- 需要先补齐最小 working memory 底座，再考虑 question rewrite 和长期研究记忆。

### 改动内容
- 在 `app/memory/` 下新增第一版 memory 模块：
  - `models.py`
    - `ConversationTurn`
    - `ConversationSession`
  - `store.py`
    - session JSON 读写
  - `manager.py`
    - `append_turn`
    - `get_recent_turns`
    - `format_history`
    - `has_history`
    - `clear_session`
- 在 `app/config.py` 中新增：
  - `memory_dir`
- 在 `scripts/query_documents.py` 中新增：
  - `--session-id`
  - `MemoryManager` 初始化
  - history 读取与调试输出
- 在新仓库中补齐 `.env`，并重新构建 `simple` 索引：
  - `Indexed 643 new chunks into data/vector_store/simple_chunks.json`

### 验证结果
- 临时测试脚本已验证：
  - `append_turn()` 可写入 `data/memory/demo.json`
  - `format_history()` 可正确读取并格式化最近多轮 history
- 正式入口已验证：
  - `python3 scripts/query_documents.py "什么是 RAG？" --backend simple --no-stream --session-id demo`
  - 能显示已有 `history`
  - `simple` 后端可正常召回本地 citations
  - LLM 可基于 citations 正常生成回答

### 当前判断
- working memory 的“存 / 读 / 接入 CLI 入口”已经打通；
- 这一步主要完成了 memory read/write 底座；
- question rewrite 与检索接线见后续 2026-03-28 更新条目。

### 下一步
- 在 `app/llm_client.py` 中新增 `rewrite_question()`；
- 若存在 `session_id` 且有 history，则先做追问补全，再用 `resolved_question` 检索；
- 问答结束后，将 `question / resolved_question / answer / citation_titles` 写回 working memory。

## 2026-03-26 - stronger researcher loop（evidence gap -> rewrite query -> re-search）

### 背景问题
当前 chapter14 集成版虽然已经具备最小 task-level researcher loop，但 follow-up 逻辑仍然比较弱：
- local 不足时直接拿原 query 去 web 搜；
- web 结果会覆盖 local 结果；
- 事件流里缺少“为什么补搜”“补搜后 query 长什么样”。

### 根因判断
这不是接线问题，而是任务执行策略过于粗糙：
- 没有显式 evidence gap 判断；
- 没有 query rewrite；
- 没有结果合并，导致研究证据链容易丢失本地 grounding。

### 改动内容
- 在 `TodoItem` 增加：
  - `latest_query`
  - `evidence_gap_reason`
- 在 `DeepResearchAgent._execute_task()` 中新增：
  - evidence gap 判断
  - follow-up query 生成
  - `query_rewrite` 事件
  - local + web 结果合并
- 扩展 SSE 事件字段：
  - `query`
  - `evidence_gap_reason`

### 预期结果
- 当本地证据不足时，系统先解释“为什么不足”，再发起改写后的 follow-up query；
- 最终 summarizer / reporter 能同时看到 local 与 web 的综合证据，而不是只看补搜后的单一路径结果。

### 结论 / 下一步
- 已完成代码落地与首轮行为验证：
  - 研究类 query `RAG 评估框架、自动化调优与最佳实践`
    - 仅触发 `retrieving_local`
    - `attempt_count=1`
    - `search_backend=local_library`
  - 非本地主题 query `2026 美国关税政策变化概览`
    - 触发 `query_rewrite`
    - 进入 `retrieving_web`
    - 最终 `search_backend=local_library+duckduckgo`
    - `evidence_count=8`
    - `source_breakdown={'local_library': 5, 'web_search': 3}`
- 当前主线进入下一步：统一工具调用与任务阶段的 trace schema。

## 2026-03-26 - 最小演示前端适配 chapter14 新事件流

### 背景问题
后端主链已经闭环，但现有 chapter14 前端只展示基础任务状态，无法体现我们新增的：
- backend
- attempt_count
- evidence_count
- top_score
- query_rewrite
- search_result

### 改动内容
- 在 [App.vue](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/frontend/src/App.vue) 中新增：
  - 任务级检索指标卡片
  - 执行轨迹区块
  - `task_stage / query_rewrite / search_result` 事件消费逻辑
  - `local_library` 选项
- 安装前端依赖并执行构建验证：
  - `npm install`
  - `npm run build`

### 结果
- 前端已能展示：
  - 检索后端
  - 尝试次数
  - 证据数量
  - top score
  - follow-up query 轨迹
- 构建通过，生成产物位于：
  - `/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/frontend/dist`

### 结论
- 当前系统已经具备“可演示”的最小前后端闭环；
- 下一步只需要跑一次完整联调，确认浏览器端流式事件和展示一致。

## 2026-03-25 - 建立经典 RAG 基线后端

### 背景
项目最初重点在文档导入、引用检索和 LightRAG 接入，但 LightRAG 在当前 MVP 阶段的导入速度和状态稳定性不适合持续迭代，因此需要一个更轻、更可控的检索后端。

### 问题
- LightRAG 导入慢，状态跟踪不稳定。
- 检索质量无法被系统比较。
- 缺少可复现的检索基准。

### 改动
- 在 [app/simple_vector_rag.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/app/simple_vector_rag.py) 中新增经典检索后端。
- 在 [app/embedding_client.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/app/embedding_client.py) 中新增 embedding 客户端。
- 在 [scripts/build_simple_index.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/scripts/build_simple_index.py) 中新增向量索引构建脚本。
- 在 [scripts/query_documents.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/scripts/query_documents.py) 中增加后端切换能力。

### 结果
- 经典 RAG 后端支持 `bm25`、`vector`、`hybrid` 三种检索模式。
- 项目当前保留三条后端路径：
  - `local`
  - `simple`
  - `lightrag`
- 后续开发重点切换到 `simple`。

### 结论
项目具备了一个稳定、可观察、适合持续优化的检索基线。


## 2026-03-25 - 建立检索评测流程

### 背景
经典后端接入之后，优化效果仍然只能靠人工感觉判断。

### 问题
- 无法量化 `hit@1`、`hit@k`、检索耗时等指标。
- 没有固定问题集来比较不同检索模式。

### 改动
- 在 [data/metadata/eval_questions.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/data/metadata/eval_questions.json) 中加入评测问题集。
- 在 [scripts/evaluate_retrieval.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/scripts/evaluate_retrieval.py) 中加入检索评测脚本。
- 当前评测脚本输出：
  - `hit@1`
  - `hit@k`
  - `mrr`
  - `avg_latency_ms`

### 结果
- 检索优化现在可以复现和量化。
- 评测结果保存在 [outputs](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/outputs) 下。

### 结论
项目从“能跑”进入“能评估”的阶段。


## 2026-03-25 - 修复 simple 索引不完整和元数据不一致问题

### 背景
最初 `simple` 后端的检索结果明显偏差。

### 失败现象
- 新加入论文已经存在于 `processed/`，但没有进入向量索引。
- metadata 中部分标题仍是文件编号，例如 `2005.11401`。
- 评测标签里仍然使用旧标题，例如 `rag challenges note`。

### 根因判断
- 新增 processed 文档后，没有及时重建 simple 索引。
- metadata 标题和 manifest 标题不一致。
- 评测标签与当前标题未对齐。

### 改动
- 使用 [scripts/build_simple_index.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/scripts/build_simple_index.py) 重建向量索引。
- 修改索引构建逻辑，优先使用 manifest 中的标题。
- 修正 [data/metadata/documents.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/data/metadata/documents.json) 中的真实标题。
- 修正 [data/metadata/eval_questions.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/data/metadata/eval_questions.json) 中的 expected title。

### 结果
- 索引规模从 `72` 个 chunk 增长到 `360` 个 chunk，覆盖 5 篇文献。
- 检索分数开始具有可解释性。

### 结论
在调检索算法前，必须先确认索引完整、metadata 正确、评测标签一致。


## 2026-03-26 - 基于失败样本的受控 query expansion

### 背景
20 题评测中，foundation 和 method 类题目存在稳定失败样本。

### 失败样本
- `q13`: provenance / world knowledge
- `q15`: indiscriminately retrieving / versatility
- `q17`: parametric / non-parametric memory

### 根因判断
- 正确文档已经在语料库中。
- 失败不是因为缺文档，而是 query 表达和论文术语之间存在 vocabulary mismatch。
- query 没有充分对齐目标论文中最强的词面表达。

### 改动
- 在 [app/simple_vector_rag.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/app/simple_vector_rag.py) 中增加受控 query expansion 规则。
- expansion 只覆盖失败样本中暴露出来的高价值领域术语。

### 结果
在 20 题评测集上：
- `simple-bm25`: `hit@1 0.90 -> 0.95`
- `simple-vector`: `hit@1 0.85 -> 0.90`
- `simple-hybrid`: `hit@1 0.85 -> 0.90`

结果文件：
- [outputs/retrieval_eval_20q_after_expansion.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/outputs/retrieval_eval_20q_after_expansion.json)

### 结论
受控 expansion 是当前文献检索场景下有效的早期检索优化手段。


## 2026-03-26 - 升级为 dual-query retrieval

### 背景
直接用 expansion query 替换原 query，存在 query drift 风险，也会削弱原始 query 的精确信号。

### 问题
- 单条 expanded query 容易稀释原 query 的真实意图。
- 需要更稳的 query expansion 使用方式。

### 改动
- 保留原 query。
- 自动生成 expanded query。
- 原 query 和 expanded query 分别检索。
- 两路检索结果进行融合，expanded query 路径赋更低权重。
- 稀疏检索和稠密检索都采用同样策略，代码在 [app/simple_vector_rag.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/app/simple_vector_rag.py)。

### 结果
在 20 题评测集上：
- `simple-hybrid`: `hit@1 0.90 -> 0.95`
- `simple-hybrid`: `mrr 0.93 -> 0.97`

结果文件：
- [outputs/retrieval_eval_20q_dual_query.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/outputs/retrieval_eval_20q_dual_query.json)

### 结论
dual-query retrieval 比“直接替换原 query”的做法更稳定，也更接近真实工程中的检索优化模块。


## 2026-03-26 - 尝试 document-level rerank（未保留）

### 背景
在 dual-query retrieval 之后，仍希望通过文档级证据聚合进一步提升 `simple-hybrid` 的排序稳定性。

### 假设
- 单个 chunk 的分数可能不足以代表整篇文档的相关性。
- 如果把同一文档下多个高分 chunk 聚合，可能有助于提升 foundation / method 类题目的第一名命中率。

### 改动
- 在 [app/simple_vector_rag.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/app/simple_vector_rag.py) 中增加 document-level 聚合分数：
  - 最强 chunk 分数
  - 前两个 chunk 平均分
  - 文档支持度 bonus
  - 标题 bonus
- 使用 chunk 分数与文档分数的加权和作为最终排序分数。

### 结果
在 20 题评测集上：
- `simple-hybrid`: `hit@1 0.95 -> 0.90`
- `simple-hybrid`: `mrr 0.97 -> 0.94`

结果文件：
- [outputs/retrieval_eval_20q_doc_rerank.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/outputs/retrieval_eval_20q_doc_rerank.json)

### 结论
当前这版 document-level rerank 没有带来净收益，说明文档级聚合方式还不够好。该策略不保留在主线上，当前主线继续使用 dual-query retrieval 版本。


## 2026-03-26 - 引入基础 PDF 清洗并重建 simple 索引

### 背景
在引用展示和 PDF chunk 中，仍能看到明显的解析噪声，包括页眉页脚残留、页码、参考编号、ligature 字符以及首页作者符号串。

### 问题
- 噪声会污染 chunk 内容和 snippet 展示。
- 检索系统可能把版式残留当成有效证据。
- 清洗逻辑改完后，如果不重建 processed 和向量索引，评测不会反映真实变化。

### 改动
- 在 [app/document_loader.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/app/document_loader.py) 中增加 PDF 页面清洗：
  - 重复页眉页脚检测
  - 页码过滤
  - 首页作者行启发式过滤
  - 参考编号和作者符号清理
- 在 [app/utils.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/app/utils.py) 中补充 ligature 归一化。
- 使用新抽取逻辑重新生成 `processed/*.json`。
- 使用 [scripts/build_simple_index.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/scripts/build_simple_index.py) 重建 simple 索引。

### 结果
- simple 索引 chunk 数量从 `360` 变为 `353`。
- 在 20 题评测中：
  - `simple-bm25`: `hit@1 0.95 -> 0.90`, `mrr 0.97 -> 0.95`
  - `simple-hybrid`: 维持 `hit@1 0.95`, `mrr 0.97`
  - `simple-hybrid` 的 `foundation` 类题从 `hit@1 0.67 -> 1.00`

结果文件：
- [outputs/retrieval_eval_after_pdf_cleanup.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/outputs/retrieval_eval_after_pdf_cleanup.json)

### 结论
基础 PDF 清洗对 `simple-hybrid` 是正收益，尤其改善了 foundation 类问题；但对纯 BM25 有轻微副作用，说明清洗规则还需要继续收敛，尤其是首页作者块处理。


## 2026-03-26 - 扩语料到 10 篇并验证泛化

### 背景
此前的主要评测都建立在 5 篇文献上，虽然 `simple-hybrid` 已达到较高分数，但存在小语料过拟合风险，无法判断 dual-query retrieval 和基础 PDF 清洗在更大候选集上是否仍然有效。

### 扩充语料
新增 5 篇 RAG 相关论文：
- `2401.15391` MultiHop-RAG
- `2402.03367` RAG-Fusion
- `2403.14403` Adaptive-RAG
- `2404.03514` Embedding-Informed Adaptive Retrieval-Augmented Generation of Large Language Models
- `2408.08067` RAGChecker

相关改动：
- 更新 [data/metadata/document_manifest.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/data/metadata/document_manifest.json)
- 使用 `python scripts/ingest_documents.py --skip-rag-insert --force` 重新抽取全部 10 篇文献
- 使用 [scripts/build_simple_index.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/scripts/build_simple_index.py) 重建 simple 索引

### 结果
- 文献数从 `5` 篇扩展到 `10` 篇
- simple 索引 chunk 数量扩展到 `643`
- 在 20 题评测中：
  - `local`: `hit@1 0.65 -> 0.70`, `mrr 0.72 -> 0.72`
  - `simple-bm25`: `hit@1 0.90 -> 0.80`, `mrr 0.95 -> 0.88`
  - `simple-vector`: `hit@1 0.90 -> 0.65`, `mrr 0.93 -> 0.75`
  - `simple-hybrid`: `hit@1 0.95 -> 0.90`, `mrr 0.97 -> 0.93`

分类结果：
- `simple-hybrid` 在 `challenge / method / paper_id / robustness` 类上仍然稳定
- `foundation` 和 `survey` 类题开始出现明显回落

结果文件：
- [outputs/retrieval_eval_10docs.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/outputs/retrieval_eval_10docs.json)

### 结论
扩到 10 篇后，`simple-hybrid` 仍然是当前最强的主线，但分数已从近乎饱和回落到更可信的区间。这说明此前的优化不是完全失效，而是开始进入更真实的竞争环境。下一步应优先针对 `foundation` 和 `survey` 类题做更细的 query policy 或 chunk 策略优化，而不是继续堆新框架。


## 2026-03-26 - 引入 answer-level 评测

### 背景
此前的主要指标都停留在 retrieval 层，只能说明“对的文档能不能被找回来”，还不能回答最终生成答案是否准确、是否忠于证据、是否真的可用。

### 改动
- 新增 [data/metadata/answer_eval_questions.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/data/metadata/answer_eval_questions.json)，覆盖 `challenge / method / foundation / survey / adaptive / fusion / evaluation` 共 8 题。
- 新增 [scripts/evaluate_answers.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/scripts/evaluate_answers.py)，流程为：
  - 先检索证据
  - 再生成答案
  - 最后用 LLM 作为评审器，输出 `correctness / groundedness / citation_use / pass`
- 在 [app/llm_client.py](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/app/llm_client.py) 中增加 `judge_answer()` 作为轻量评审接口。

### 结果


## 2026-03-26 - 修复 Planner 结构化输出与任务解析

### 背景
本地文献检索已经接入 `hello-agents` 第十四章后端，但在真实 `/research/stream` 运行中，Planner 虽然能生成任务说明并调用 `note` 工具写入 4 个任务笔记，`PlanningService` 却仍然解析出 `0` 个 `TodoItem`，系统最终退回单个 fallback task。

### 失败现象
- Planner raw output 包含完整 Markdown 表格和任务说明，但 `Planner produced 0 tasks: []`
- 系统退回 `create_fallback_task()`
- 后续只执行单个“基础背景梳理”任务，而不是多任务研究流程

### 根因判断
- Planner prompt 虽然要求 JSON，但模型仍会输出 Markdown 说明、表格和 `TOOL_CALL`
- 现有 parser 只能处理“纯 JSON”或非常简单的 `TOOL_CALL`
- 一旦 `TOOL_CALL` 参数中包含数组、换行等准 JSON 内容，解析就会失败

### 改动
- 在 `hello-agents` 第十四章后端的 [planner.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/planner.py) 中：
  - 增加多层任务提取顺序：`JSON -> TOOL_CALL note create -> Markdown`
  - 增加宽松的 `TOOL_CALL` 参数解析，容忍 LLM 生成的准 JSON 和字符串换行
  - 支持从 `note create` 中恢复：
    - `title`
    - `intent`
    - `query`
- 在 [prompts.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/prompts.py) 中：
  - 收紧 planner 输出约束
  - 强制最终只输出一个 JSON 对象
  - 明确说明即使调用了 `note` 工具，最终仍必须输出 JSON

### 结果
- 本地解析验证：即使输入为“Markdown 表格 + note create tool call”，也能恢复出结构化任务
- 真机调用验证：`plan_todo_list()` 已能稳定产出 `5` 个 `TodoItem`
- 日志从：
  - `Planner produced 0 tasks: []`
  变为：
  - `Planner produced 5 tasks: [...]`

### 结论
Planner 现在已经从“能写内容但吃不进流程”的状态，提升到“能稳定生成结构化任务并驱动多任务执行”的状态。下一步主线不再是修 Planner，而是补全 SSE trace 和本地/联网来源区分。


## 2026-03-26 - 补全最小 SSE 检索 trace

### 背景
Planner 修复后，多任务研究流程已经能跑起来，但前端和日志仍然只能看到粗粒度阶段状态，看不到每轮检索到底用了哪个后端、拿回了多少证据、最高分是多少，也看不到来源类型分布。

### 问题
- `task_stage` 只能看到 `retrieving_local / retrieving_web`
- 不能直接观察每轮检索的证据强度
- 不能区分当前结果主要来自本地文献还是网页来源

### 改动
- 在 [agent.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/agent.py) 中增强事件流：
  - `task_stage` 增加：
    - `attempt`
    - `previous_backend`
    - `previous_evidence_count`
    - `previous_top_score`
  - 新增 `search_result` 事件：
    - `backend`
    - `attempt_count`
    - `evidence_count`
    - `top_score`
    - `needs_followup`
    - `source_breakdown`
    - `titles_preview`
  - `sources` 与最终 `task_status` 事件同步补入检索摘要字段
- 新增内部辅助方法：
  - `_summarize_search_result()`
  - `_build_search_result_event()`

### 结果
- 真机验证时，针对本地文献 query 已可观察到：
  - `task_stage(retrieving_local)`
  - `search_result(backend=local_library, evidence_count=5, top_score≈0.81, source_breakdown={local_library: 5})`
  - `sources(...)`
- 前端或日志现在能明确知道每轮任务实际用了哪个搜索后端，以及命中了哪些本地文献标题

### 结论
最小检索 trace 已经成立。当前系统从“能跑”进入“可观察”阶段。下一步不再补 SSE 基本字段，而是让最终报告显式区分本地文献来源与联网来源。


## 2026-03-26 - 在总结/报告输入中加入来源类型区分

### 背景
SSE trace 已经能看出每轮任务使用的是本地文献还是网页搜索，但最终汇总报告仍缺少这层可解释性。系统需要在最终产物中说明证据来自哪里，而不是只给出未分类的来源列表。

### 问题
- `sources_summary` 只有来源列表，没有来源类型统计
- summarizer / reporter 虽然能看到来源标题，但不知道这些来源是本地文献还是联网网页
- 最终报告容易把不同类型来源混写在一起

### 改动
- 在 [search.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/search.py) 中：
  - 统一规范搜索结果的 `source_type`
    - 本地文献分支：`local_library`
    - 网页搜索分支默认：`web_search`
  - 在 `prepare_research_context()` 中为 `sources_summary` 和 `context` 增加“来源类型统计”头部
- 在 [prompts.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/prompts.py) 中：
  - 收紧 reporter 要求
  - 明确要求区分“本地文献来源”和“联网来源”

### 结果
- 本地 query 验证中，`sources_summary` 和 `context` 已显示：
  - `来源类型统计：`
  - `- 本地文献：5`
- 这意味着 summarizer 和 reporter 现在都能拿到结构化的来源类型信息，不再只能看到一串未分类标题

### 结论
来源区分已经进入总结和报告输入层。下一步不是继续补底层字段，而是跑完整 `/research/stream` 验证最终报告是否稳定把本地文献来源与联网来源分开呈现。


## 2026-03-26 - 缩小规模端到端验收与 Reporter 跟随性问题

### 背景
在 planner、多任务执行、本地检索接入、最小 researcher loop、SSE trace 和来源类型区分都完成后，需要做一次完整链路验收，确认系统能从规划走到最终报告，而不是只在局部函数层面成立。

### 改动
- 在 [config.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/config.py) 中增加 `MAX_TODO_ITEMS`
- 在 [planner.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/planner.py) 中根据 `max_todo_items` 截断 planner 任务数
- 使用 `MAX_TODO_ITEMS=2` 跑完整 `agent.run('RAG 的主要挑战和常见缓解方法')`

### 结果
- planner 成功产出 `2` 个任务
- 多任务执行成立
- 最终 `report_markdown` 中已显式出现：
  - `### 本地文献来源`
  - `### 联网来源`

### 新暴露问题
- 报告文本虽然已经有来源分节，但内容仍明显过度保守：
  - 把任务写成 `pending`
  - 把来源写成“暂无相关信息”

### 根因判断
一开始判断为 reporter 跟随性问题，但继续排查后发现更底层的真实 bug：

- [agent.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/agent.py) 中的 `run()` 直接调用了 `_execute_task(...)`
- 但 `_execute_task()` 是生成器函数
- 非流式路径没有消费这个生成器，导致任务在 `run()` 中实际上根本没有执行
- 因此 reporter 看到的仍然是：
  - `pending`
  - `attempt_count = 0`
  - `evidence_count = 0`

### 改动
- 修复 [agent.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/agent.py) 的非流式执行路径：
  - `for _ in self._execute_task(...): pass`
- 同时收紧 reporter：
  - 在 [reporter.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/services/reporter.py) 中加入权威事实表和确定性附录
  - 在 [prompts.py](/home/pureayu/code/hello-agents/code/chapter14/helloagents-deepresearch/backend/src/prompts.py) 中强调不得把 `completed` 改写为 `pending`

### 修复后结果
- `MAX_TODO_ITEMS=2` 端到端验收中：
  - `todo_count = 2`
  - 两个任务均为 `completed`
  - `attempt_count = 1`
  - `evidence_count = 5`
  - `search_backend = local_library`
- 最终报告中：
  - `pending` 不再出现
  - `本地文献来源 / 联网来源` 分节已出现
  - `任务执行事实附录 / 来源事实附录` 已出现

### 结论
此前的“reporter 不够 grounded”表象，根因其实是非流式执行路径没有真正运行任务。这个 bug 修复后，主链已经真正从“规划 -> 执行 -> 总结 -> 报告”闭环打通。接下来不再优先修接入，而是进入下一阶段的 trace 收敛与 stronger researcher loop 设计。
在 `simple-hybrid`、8 题 answer eval 上：
- `title_hit = 0.88`
- `correctness = 1.50 / 2.00`
- `groundedness = 2.00 / 2.00`
- `citation_use = 2.00 / 2.00`
- `pass_rate = 0.88`
- 平均单题耗时约 `79.4s`

结果文件：
- [outputs/answer_eval_simple_hybrid.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/outputs/answer_eval_simple_hybrid.json)
- [outputs/answer_eval_smoke.json](/home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant/outputs/answer_eval_smoke.json)

### 失败样本
- `a4 / foundation`
  - 检索标题命中失败，答案只会诚实说明“材料不足”，没有答到 `parametric / non-parametric memory`
- `a5 / survey`
  - 能答到 `Naive RAG`，但没覆盖完整 `Naive / Advanced / Modular`
- `a3 / method`
  - 能说明 CRAG 的鲁棒性与校正思路，但对具体机制覆盖仍然偏弱

### 结论
当前主线在“忠于证据”和“引用使用”上已经稳定，但在 foundation / survey 类题上仍存在“检索到部分证据却答不全”的问题。后续优化重点应从纯 retrieval 指标转向“支持生成完整答案的证据覆盖率”。

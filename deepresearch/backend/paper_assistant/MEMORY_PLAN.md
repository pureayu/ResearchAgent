# Memory 系统分阶段规划

> 说明：本文件记录的是 `paper_assistant` 层 memory 的设计与实验过程。  
> 截至 2026-03-29，项目已决定将 memory 的主归属收敛到 `deepresearch/backend/src/services/memory.py`。  
> 因此本文件当前主要作为历史设计记录与实验参考，不再作为后续主线实施文档。

## 目标

在不推翻当前 `paper_assistant` 文献检索主链路的前提下，为项目补齐一个可演进的 memory 系统，使其从“单轮检索问答”逐步升级为“支持多轮研究对话”的研究助手。

核心原则：
- 不一开始照搬完整的 chapter8 大系统。
- 先把最需要的短期对话记忆做稳。
- 长期研究记忆在第二阶段引入。
- memory 只负责上下文连续性与研究沉淀，不替代文献证据。
- 事实回答仍然必须以 citations / chunk evidence 为准。

当前决策更新：
- `paper_assistant` memory 原型已完成阶段性验证，但不再继续作为主线推进。
- 真正服务 Web 多轮研究对话的 memory，将统一放在 `deepresearch` 后端。
- 如后续保留 `paper_assistant/app/memory/*`，定位应为实验性或离线 CLI 参考实现。

## 当前问题

当前 `paper_assistant` 的主要痛点：
- 每次问答默认是一次性流程，跨轮上下文弱。
- 追问中的代词、省略、序号指代难以正确解析。
- 已经得出的研究结论不会沉淀为后续上下文。
- CLI 和 Web 两条链路尚未统一 session / memory 语义。

## 设计目标

memory 系统希望逐步具备：
- 会话级短期记忆
- 追问补全能力
- 研究过程中的结论沉淀
- 清晰的目录结构与职责分层
- 可被 CLI 与 Web 共用

## 建议目录结构

```text
app/memory/
  ├─ __init__.py
  ├─ models.py
  ├─ store.py
  ├─ working_memory.py
  ├─ research_memory.py
  ├─ manager.py
  ├─ prompts.py           # 可选，第二阶段再加
  └─ constants.py         # 可选，第二阶段再加
```

各文件职责：
- `models.py`
  - 定义 memory 数据结构
  - 例如 `ConversationTurn`、`ConversationSession`、`ResearchNote`
- `store.py`
  - 管理底层持久化
  - 例如 session JSON / notes JSON 的读写
- `working_memory.py`
  - 管理短期记忆
  - 例如最近 N 轮、history 格式化、session 清理
- `research_memory.py`
  - 管理研究级记忆
  - 例如阶段性结论、已比较过的方法、关键文献记录
- `manager.py`
  - 统一调度 memory
  - 决定当前 query 该读哪些 memory、何时写回、何时 consolidate

## Memory 类型

第一版只建议支持两种：

### 1. Working Memory

作用：
- 保存最近若干轮问答
- 支持追问补全
- 支持多轮上下文连续性

特点：
- session 级
- 容量小
- 优先保留最近 N 轮
- 只用于“理解当前问题”，不作为事实证据

### 2. Research Memory

作用：
- 沉淀研究过程中的高价值结论
- 记录本次研究中已比较过的方法、关键定义、阶段性结论

特点：
- 比 working memory 更稳定
- 数量更少但信息密度更高
- 可以逐步引入 summary / consolidation

## 阶段拆分

### 阶段 0：冻结当前最小可用 memory

目标：
- 保留当前已能工作的 session memory 雏形
- 明确这只是最小可用版本，不是最终结构

当前已有：
- `session_id`
- 最近几轮 history
- `rewrite_question()`
- 回答后 `append_turn()`

完成标准：
- CLI 下同一 `session_id` 的追问能被补全
- 第二轮问题能基于第一轮历史正确检索

状态：`[x]`

---

### 阶段 1：规范化 memory 模块结构

目标：
- 将当前零散实现整理进 `app/memory/`
- 建立清晰的文件职责边界

要做的事：
- 将 memory 数据结构收敛到 `app/memory/models.py`
- 将 JSON 读写收敛到 `app/memory/store.py`
- 新建 `app/memory/working_memory.py`
- 将 session / history 业务逻辑从脚本入口中剥离
- 让 `query_documents.py` 只保留 orchestration

交付物：
- `app/memory/models.py`
- `app/memory/store.py`
- `app/memory/working_memory.py`
- `app/memory/manager.py` 薄封装版

完成标准：
- 不再把 memory 逻辑散落在多个无关模块中
- `query_documents.py` 不直接负责 memory 存储细节
- `working memory` 有独立类

状态：`[x]`

---

### 阶段 2：Working Memory 完整化

目标：
- 把“短期对话记忆”做成稳定能力

要做的事：
- 支持最近 `N` 轮截断
- 统一 history 格式化
- 区分原始问题 `question` 与补全问题 `resolved_question`
- 为后续 Web 接入保留统一接口
- 增加 `clear_session()` / `has_history()` / `get_recent_turns()`

建议接口：

#### `WorkingMemory`
- `load_session(session_id)`
- `append_turn(...)`
- `get_recent_turns(session_id, max_turns)`
- `format_history(session_id, max_turns)`
- `clear_session(session_id)`
- `has_history(session_id)`

完成标准：
- 追问补全逻辑不依赖脚本级拼装
- memory 读取与写回逻辑稳定
- 历史长度可控

状态：`[x]`

补充说明：
- 第一版 `Working Memory` 已在 CLI 入口中闭环：
  - 读取最近 session history
  - 对明显追问做 `rewrite_question`
  - 使用 `resolved_question` 检索
  - 在回答后写回 `question / resolved_question / answer / citation_titles`
- 当前实现刻意采取“保守补全”策略：
  - 宁可保持原问题，也不引入历史中未出现的新实体
  - 序号型指代（如“第二篇论文”）与抽象型指代（如“上一个方法”）仍需下一阶段增强

---

### 阶段 3：Research Memory 引入

目标：
- 让系统除了“记住最近几轮”，还能“记住这次研究已经得出了什么”

要做的事：
- 新建 `ResearchNote` 数据结构
- 新建 `research_memory.py`
- 将高价值问答沉淀为 notes
- 为后续报告生成和连续研究提供复用上下文

建议最小字段：
- `note_id`
- `session_id`
- `question`
- `conclusion`
- `citation_titles`
- `created_at`

建议最小接口：

#### `ResearchMemory`
- `append_note(...)`
- `list_notes(session_id)`
- `format_notes(session_id)`
- `clear_notes(session_id)`

完成标准：
- 一次研究会话中的关键结论可被后续问题复用
- note 与普通对话轮次区分开

状态：`[~]`

当前进展：
- 已新增：
  - `ResearchNote`
  - `ResearchNoteSession`
  - `research_memory.py`
- 已实现最小接口：
  - `append_note(...)`
  - `list_notes(session_id)`
  - `format_notes(session_id)`
  - `clear_notes(session_id)`
- `MemoryManager` 已扩展为 `working + research` 双入口
- 已验证：
  - `append_note()` 可正常落盘到 `data/memory/research/{session_id}.json`
  - `format_notes()` 可正常读回并输出

尚未完成：
- 已把 `research_memory` 自动接入真实问答闭环
- 已定义第一版“高价值结论”规则：
  - 回答非空
  - citation 数量 >= 1
  - 回答长度达到最小阈值
  - 问题命中定义 / 区别 / 挑战 / 局限 / 核心 / 总结 / 方法等模式
- 自动写 note 前已增加结论压缩步骤，避免直接存整段回答
- 仍未完成：
  - 后续 query 尚未主动消费 research notes
  - 尚未定义 notes 与 working history 的上下文组合策略

---

### 阶段 4：Consolidation 与生命周期管理

目标：
- 模仿 chapter8 的思路，引入轻量的 memory 生命周期管理

要做的事：
- working memory 只保留最近 N 轮
- 高价值轮次写入 research memory
- 增加简单 consolidate 规则
- 增加基础 forget / trim 规则

建议第一版规则：
- 若回答较长且引用数 >= 2，则可沉淀为 `ResearchNote`
- working memory 仅保留最近 `MEMORY_MAX_TURNS`
- clear session 时可只清 short-term，不清 research notes

完成标准：
- memory 不会无限增长
- 研究结果能逐步沉淀
- working / research 两层职责清楚

状态：`[ ]`

---

### 阶段 5：Web 接入 session memory

目标：
- 让网页端从“单次任务内 agent”升级为“跨请求有连续记忆的研究助手”

要做的事：
- 前端请求体增加 `session_id`
- 后端接口接收并透传 `session_id`
- Web 研究入口调用 memory manager
- 同一网页会话可跨多次提问复用 working memory

需要改动的方向：
- frontend request schema
- backend request schema
- backend 调用 `paper_assistant` 的本地记忆入口
- session 生命周期设计

完成标准：
- 网页端连续提问可解析“它 / 第二个 / 刚才那篇”
- 刷新前后的 session 语义清晰

状态：`[ ]`

---

### 阶段 6：统一接口与可选 Tool 化

目标：
- 在真的需要 agent 主动调用 memory 时，再考虑 tool 化

说明：
- 当前阶段不急着做 `MemoryTool`
- 当 Web 端或 Agent 端需要“主动决定何时存记忆 / 何时搜记忆”时，再封装统一工具接口

未来可选接口：
- `add_memory`
- `search_memory`
- `summary_memory`
- `clear_memory`

完成标准：
- memory 已经有稳定的内部 manager / store / types
- 再做 tool 封装时不会反向污染底层设计

状态：`[ ]`

## 当前推荐推进顺序

1. 先完成阶段 1：规范化 memory 目录与职责
2. 再完成阶段 2：Working Memory 完整化
3. 然后完成阶段 3：Research Memory 最小版
4. 再做阶段 4：Consolidation / Forgetting
5. 最后接阶段 5：Web session memory

## 当前不建议做的事

- 一开始就照搬 chapter8 的四类 memory
- 一开始就做 `MemoryTool`
- 一开始就上向量化长期记忆
- 一开始就做多模态 memory
- 把“历史对话”直接当事实证据使用

## 验收示例

### CLI Working Memory 验收

```bash
python scripts/query_documents.py "What is ReAct?" --backend simple --retrieval-mode hybrid --no-stream --session-id demo
python scripts/query_documents.py "How is it different from Toolformer?" --backend simple --retrieval-mode hybrid --no-stream --session-id demo
```

通过标准：
- 第二轮能补全 `it = ReAct`
- 检索使用补全后的 query
- 回答仍以 citations 为依据

### 多对象指代验收

```bash
python scripts/query_documents.py "Summarize ReAct, Reflexion, and AutoGen." --backend simple --no-stream --session-id demo2
python scripts/query_documents.py "Focus on the second one." --backend simple --no-stream --session-id demo2
```

通过标准：
- 第二轮能正确识别 “the second one”
- 回答不是凭空编造，而是能落到文献证据

# ResearchAgent

一个聚合仓库，包含两部分：

- `paper_assistant/`：本地文献 RAG 内核与评测脚本
- `helloagents_deepresearch/`：基于 `hello-agents` Chapter 14 二次开发的前后端研究型 Agent 应用

## 仓库结构

```text
ResearchAgent/
  paper_assistant/
    app/
    scripts/
    data/metadata/
    outputs/
  helloagents_deepresearch/
    backend/
      src/
    frontend/
      src/
```

## 代码对应关系

### 1. 本地文献 RAG

核心目录：

- `paper_assistant/app/`
- `paper_assistant/scripts/`

关键能力：

- PDF / md / txt 文档导入与清洗
- chunking、embedding、索引构建
- `BM25 + vector + hybrid` 混合检索
- controlled query expansion
- dual-query retrieval
- retrieval / answer 评测

### 2. 研究型 Agent 应用

核心目录：

- `helloagents_deepresearch/backend/src/`
- `helloagents_deepresearch/frontend/src/`

关键能力：

- `planner + task executor + summarizer + reporter` 多角色 workflow
- 本地知识库优先检索，证据不足时网页补搜
- SSE 流式任务状态与工具调用记录
- 最终研究报告生成

### 3. 集成方式

`helloagents_deepresearch` 通过工具层调用 `paper_assistant` 的本地文献能力：

- `paper_assistant/app/local_library_tools.py`
- `paper_assistant/app/simple_vector_rag.py`

## 运行说明

两个子项目分别维护依赖环境：

### paper_assistant

参考：

- `paper_assistant/README.md`

### helloagents_deepresearch

后端：

- `helloagents_deepresearch/backend/`

前端：

- `helloagents_deepresearch/frontend/`

## 说明

- 仓库不包含本地 `.env`、虚拟环境、`node_modules`、前端构建产物、运行期 `notes/`、原始 PDF 和生成索引。
- `helloagents_deepresearch` 基于 `datawhalechina/hello-agents` Chapter 14 代码进行二次开发，许可证见根目录 `LICENSE.txt`。

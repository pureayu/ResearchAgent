# 文献检索与带引用总结助手

一个面向本地 PDF / md / txt 文献的轻量项目，先把这条最小链路跑通：

1. 导入和管理本地文献
2. 对文献内容进行检索
3. 基于文献内容问答
4. 输出带引用的回答
5. 对主题生成总结

当前实现聚焦 MVP，默认以命令行为主。本地 RAG 现已默认使用 `PostgreSQL + pgvector` 作为 chunk 向量存储。

## 目录结构

```text
deepresearch/backend/paper_assistant/
  app/
  data/
    raw/
    processed/
    metadata/
  scripts/
  outputs/
  README.md
  TASKS.md
  .env.example
```

## 已实现能力

- 扫描 `data/raw/` 下的 PDF / md / txt 文档
- 抽取文本并切分 chunk，保存在 `data/processed/` 作为导入中间产物
- 用 `documents.json` 管理元数据
- 命令行问答
- 主题总结
- 引用分开展示，尽量显示文档名、文件路径、页码和片段
- 本地 chunk 索引默认写入 `PostgreSQL + pgvector`

说明：

- 当前检索与问答主链路统一走本地 `pgvector` chunk store。
- 建库流程分成两步：先抽取文档生成 `processed/*.json`，再构建向量索引。
- 这样输出文件名 / 页码更稳定，也更方便做检索评测与重建。

## 安装

```bash
cd /home/pureayu/code/ResearchAgent/deepresearch/backend
uv sync
source .venv/bin/activate
cp .env.example .env
```

然后在后端环境里运行本地 RAG 脚本，例如：

```bash
cd /home/pureayu/code/ResearchAgent/deepresearch/backend/paper_assistant
python scripts/build_simple_index.py --rebuild
```

然后编辑 `.env`：

- `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL`
- `EMBEDDING_MODEL` / `EMBEDDING_DIM`
- 可选：`RERANK_MODEL` / `RERANK_API_KEY`

如果 embedding 服务和 chat 服务共用同一个 OpenAI-compatible 提供方，`EMBEDDING_API_KEY` 和 `EMBEDDING_BASE_URL` 可以留空。
如果你要启用阿里云 DashScope 的 `qwen3-rerank`，可额外配置：

- `RERANK_MODEL=qwen3-rerank`
- `RERANK_API_KEY=<DashScope API Key>`
- `RERANK_BASE_URL=https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank`

启用后，本地 RAG 会在混合召回候选上优先走模型型 rerank；如果接口失败，会自动回退到现有的轻量级特征重排。

本地 RAG 默认还需要一个 `pgvector` PostgreSQL：

```bash
docker run -d \
  --name paper-assistant-pgvector \
  -e POSTGRES_DB=researchagent \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 54329:5432 \
  docker.m.daocloud.io/pgvector/pgvector:pg16
```

默认环境变量：

- `RAG_DATABASE_URL=postgresql://postgres:postgres@localhost:54329/researchagent`
- `RAG_CHUNK_TABLE=rag_chunks`

## 准备文献

把测试文档放到 `data/raw/`。

如果你想手工维护标题、作者、年份、标签，可以创建 `data/metadata/document_manifest.json`：

```json
[
  {
    "file_name": "rag_survey.pdf",
    "title": "Retrieval-Augmented Generation Survey",
    "authors": ["Alice", "Bob"],
    "year": 2024,
    "tags": ["rag", "survey"]
  }
]
```

## 使用方法

1. 扫描、抽取并更新文档元数据：

```bash
python scripts/ingest_documents.py
```

2. 构建本地 `pgvector` chunk 索引：

```bash
python scripts/build_simple_index.py --rebuild
```

3. 查看文献列表：

```bash
python scripts/list_documents.py
python scripts/list_documents.py --tag rag
python scripts/list_documents.py --keyword transformer
```

4. 问答：

```bash
python scripts/query_documents.py "RAG 的主要挑战是什么？"
python scripts/query_documents.py "两篇论文的方法有什么区别？" --retrieval-mode hybrid
```

5. 主题总结：

```bash
python scripts/topic_summary.py "Transformer 的核心机制"
```

## 当前限制

- PDF 清洗还比较基础，没有做页眉页脚剔除和参考文献去噪
- 文档抽取变更后，需要手动重建本地向量索引
- 没有 Web UI
- 没有评测脚本

## 下一步建议

- 增加 `build_paper_cards.py`
- 增加评测问题集和检索对比实验
- 增加 Streamlit 界面
- 优化 PDF 清洗与 rerank

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
paper_assistant/
  app/
  data/
    raw/
    processed/
    metadata/
    rag_store/
  scripts/
  outputs/
  README.md
  TASKS.md
  requirements.txt
  .env.example
```

## 已实现能力

- 扫描 `data/raw/` 下的 PDF / md / txt 文档
- 抽取文本并切分 chunk，保存在 `data/processed/` 作为导入中间产物
- 将文档顺序写入 LightRAG
- 用 `documents.json` 管理元数据
- 命令行问答
- 主题总结
- 引用分开展示，尽量显示文档名、文件路径、页码和片段
- 本地 chunk 索引默认写入 `PostgreSQL + pgvector`

说明：

- 回答主链路默认优先走 LightRAG。
- 引用展示单独由本地 `pgvector` chunk store 完成，这样输出文件名 / 页码更稳定。
- 如果 LightRAG 查询失败，`query_documents.py` 会回退到“本地证据 + LLM 生成”的模式。

## 安装

```bash
cd /home/pureayu/code/paper_assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

然后编辑 `.env`：

- `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL`
- `EMBEDDING_MODEL` / `EMBEDDING_DIM`

如果 embedding 服务和 chat 服务共用同一个 OpenAI-compatible 提供方，`EMBEDDING_API_KEY` 和 `EMBEDDING_BASE_URL` 可以留空。

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

- `RAG_VECTOR_BACKEND=postgres`
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

1. 仅做扫描和抽取，不写入 LightRAG：

```bash
python scripts/ingest_documents.py --skip-rag-insert
```

2. 写入 LightRAG：

```bash
python scripts/ingest_documents.py
```

3. 构建本地 `pgvector` chunk 索引：

```bash
python scripts/build_simple_index.py --rebuild
```

4. 查看文献列表：

```bash
python scripts/list_documents.py
python scripts/list_documents.py --tag rag
python scripts/list_documents.py --keyword transformer
```

5. 问答：

```bash
python scripts/query_documents.py "RAG 的主要挑战是什么？"
python scripts/query_documents.py "两篇论文的方法有什么区别？" --mode hybrid
```

6. 主题总结：

```bash
python scripts/topic_summary.py "Transformer 的核心机制"
```

## 当前限制

- PDF 清洗还比较基础，没有做页眉页脚剔除和参考文献去噪
- LightRAG 和本地 `pgvector` chunk store 仍是两套链路，需要分别建库/建索引
- 没有 Web UI
- 没有评测脚本

## 下一步建议

- 增加 `build_paper_cards.py`
- 增加评测问题集和检索对比实验
- 增加 Streamlit 界面
- 优化 PDF 清洗与 rerank

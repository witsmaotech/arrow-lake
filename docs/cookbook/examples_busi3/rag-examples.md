# RAG 检索与问答示例（jd_ddd · 由浅入深）

> 针对 Arrow Lake v1.8.9 的 RAG 能力，以 `jd_ddd`（DDD 知识图谱数据集）为例，**实测可用**。
> 容器 API `127.0.0.1:8000`（header `X-API-Key: dev-api-key-for-local-testing-only`）· HugeGraph `127.0.0.1:8089`。

---

## 0. 前提与两条 RAG 路径

Arrow Lake 有**两条** RAG 路径，分工不同（见文末对照）：

| 路径 | 入口 | 检索源 | 适合 |
|------|------|--------|------|
| **湖仓 RAG** | `/api/v1/rag/query` · `lake.rag_query()` | Lance 向量/FTS/hybrid（数据集 chunk） | 问「文档里写了什么」 |
| **KG / KA RAG** | `/api/v1/kg/ask` · `/api/v1/kg/search` | hyper-extract KA 的 FAISS over 节点定义 | 问「概念的意思/关系」 |

**v1.8.9 配置**（容器 `deploy/docker-compose.prod_minimal.yml` 实测）：

- LLM = `ministral-3:3b`（ollama，容器经 `host.docker.internal:11534` 中继）
- Embedding = `qwen3-embedding:0.6b`
- KG 问答 LLM（`he_qa_llm`）= `qwen3-max`@百炼（强推理 + 中文生成）
- **Reranker 默认 `ollama` · `dengcao/Qwen3-Reranker-0.6B:F16`**（本机已拉取→真生效，不可达自动 latch `Noop` 不阻塞）

---

## 1. L1 · 基础检索（向量 / FTS / Hybrid）

底层检索走 `/api/v1/datasets/{name}/search/<api>`，返回 top-k chunk：

```bash
# 向量 ANN（语义相似）
curl -s -H "X-API-Key: dev-api-key-for-local-testing-only" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:8000/api/v1/datasets/jd_ddd/search/vector" \
  -d '{"query":"聚合根的设计原则","top_k":5}' | python3 -m json.tool | head -30

# 全文 BM25（关键词命中）
curl -s -H "X-API-Key: dev-api-key-for-local-testing-only" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:8000/api/v1/datasets/jd_ddd/search/fts" \
  -d '{"query":"限界上下文","top_k":5}' | python3 -m json.tool | head -30

# Hybrid（向量 + FTS，RRF 融合）+ 标量过滤 where
curl -s -H "X-API-Key: dev-api-key-for-local-testing-only" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:8000/api/v1/datasets/jd_ddd/search/hybrid" \
  -d '{"query":"领域事件解耦","top_k":5,"where":"page_number < 20"}' | python3 -m json.tool | head -30
```

另有 `/search/faceted`（分面，带 modality/source/doc_type 聚合）、`/search/ensemble`（多策略加权）。

---

## 2. L2 · RAG 问答（生成式）

**KG / KA RAG —— `/api/v1/kg/ask`（实测可用：351 字答案 · 召回 10）**

```bash
curl -s -H "X-API-Key: dev-api-key-for-local-testing-only" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:8000/api/v1/kg/ask" \
  -d '{"dataset":"jd_ddd","question":"限界上下文如何划分系统边界?","top_k":5}'
# → {"answer":"...","retrieved_items":[...],"retrieval_count":10}
```

**KA 语义检索（不生成、只召回节点）—— `/api/v1/kg/search`**

```bash
curl -s -H "X-API-Key: dev-api-key-for-local-testing-only" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:8000/api/v1/kg/search" \
  -d '{"dataset":"jd_ddd","query":"聚合根","top_k":5}'
# → {"nodes":[...],"edges":[...],"node_count":5,"edge_count":5}
```

**湖仓 RAG —— 宿主 facade（实测可用 · reranker 真生效）**

> 注意：容器 REST `/api/v1/rag/query` 当前在 `jd_ddd` 上报
> `Column 'text_content' not found`（列名 `text` vs `text_content` 的 schema mismatch，
> 已知问题）；**宿主 facade 走 `text` 列正常**。facade 调用（`.venv`）：

```python
# source docs/cookbook/examples_busi3/env.sh && export HTTPS_PROXY=http://127.0.0.1:7887
from arrow_lake import Lake
lake = Lake()
r = lake.rag_query(
    "聚合根的核心设计原则是什么？", "jd_ddd",
    strategy="hybrid", top_k=5,
)
print(r.answer)              # 生成答案
print(r.retrieval_count)     # 召回上下文数（reranker 重排后）
print(r.latency_ms)          # 端到端延迟
# citations = r.citations    # 启用 enable_citations 时带引用
lake.shutdown()
```

容器 REST `/api/v1/rag/query` 请求体（schema 正确，列名问题修复后即用）：

```jsonc
// POST /api/v1/rag/query
{
  "question": "聚合根的核心设计原则",     // 注意是 question，不是 query
  "dataset_name": "jd_ddd",              // 注意是 dataset_name，不是 dataset
  "retrieval_strategy": "hybrid",        // vector | fts | hybrid | ensemble | faceted
  "top_k": 5,
  "template_name": null,                 // 可选 prompt 模板
  "session_id": null                     // 可选，带对话历史
}
```

---

## 3. L3 · 召回质量（策略对比 + reranker）

**三策略 top-k 召回对比**（看语义 vs 关键词的一致性）：

```bash
for API in vector fts hybrid; do
  echo "== $API =="
  curl -s -H "X-API-Key: dev-api-key-for-local-testing-only" -H "Content-Type: application/json" \
    -X POST "http://127.0.0.1:8000/api/v1/datasets/jd_ddd/search/$API" \
    -d '{"query":"值对象的不变量","top_k":5}' \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print([c.get('chunk_index') for c in (d.get('results') or d.get('data') or [])[:5]])"
done
```

**v1.8.9 reranker 前后对比**（reranker 对 hybrid 召回重排）：

```bash
# 开（默认）：reranker=ollama，对 top_k 候选用 Qwen3-Reranker yes/no 判官重排，取 reranker_top_n
curl -s -H "X-API-Key: dev-api-key-for-local-testing-only" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:8000/api/v1/kg/ask" \
  -d '{"dataset":"jd_ddd","question":"实体与值对象的区别","top_k":10}' | python3 -m json.tool | head -20

# 关：env 设 ARROW_LAKE__RAG__RERANKER=none（→ NoopReranker，不重排）
```

**查询改写**（`query_transform`，默认 `none`）：可设 `hyde`（假设文档嵌入）或 `multi_query`（生成 N 个变体并行检索）——

```bash
# compose env: ARROW_LAKE__RAG__QUERY_TRANSFORM=hyde   # 或 multi_query
# ARROW_LAKE__RAG__HYDE_MAX_TOKENS=256
# ARROW_LAKE__RAG__MULTI_QUERY_VARIANTS=3   (对应 config.multi_query_variants)
```

---

## 4. L4 · 进阶（引用 / 会话 / 抽取 / 流式）

```bash
# 流式问答（SSE）—— POST /api/v1/rag/query/stream，body 同 /rag/query
curl -N -H "X-API-Key: dev-api-key-for-local-testing-only" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:8000/api/v1/rag/query/stream" \
  -d '{"question":"领域事件的作用","dataset_name":"jd_ddd","retrieval_strategy":"hybrid","top_k":5}'

# 结构化抽取（RAG extract，按 template 出 JSON）—— POST /api/v1/rag/extract
curl -s -H "X-API-Key: dev-api-key-for-local-testing-only" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:8000/api/v1/rag/extract" \
  -d '{"dataset_name":"jd_ddd","question":"列出所有聚合根及其职责","top_k":5}'

# 会话历史：第二次请求带相同 session_id，自动注入历史（history_injection_enabled=True）
curl -s ... -d '{"question":"那它和领域服务有何区别?","dataset_name":"jd_ddd","session_id":"sess-001","top_k":5}'
```

引用：`RAGConfig.enable_citations=True` 时，`answer` 含 `[1][2]` 角标，`r.citations` 给出对应 chunk 文本/来源。

---

## 5. 配置（RAGConfig 全字段）

`arrow_lake/config/rag.py` 关键项（env 覆盖前缀 `ARROW_LAKE__RAG__`）：

| 字段 | 默认 | 说明 |
|------|------|------|
| `default_retrieval_strategy` | `hybrid` | vector/fts/hybrid/ensemble/faceted |
| `default_top_k` | `10` | 召回数 |
| `max_context_chunks` | `20` | 进 LLM 的最大上下文块 |
| `context_budget_ratio` | `0.75` | 上下文占 context_window 的比例 |
| `reranker` | `ollama` | none/noop · ollama · cross_encoder · llm |
| `reranker_model` | `dengcao/Qwen3-Reranker-0.6B:F16` | ollama tag |
| `reranker_base_url` | `""` | 空=从 embedding api_base 推导 |
| `reranker_top_n` | `10` | 重排后保留数 |
| `query_transform` | `none` | none · hyde · multi_query |
| `enable_citations` | `True` | 答案带引用角标 |
| `history_injection_enabled` | `True` | 按 session_id 注入对话历史 |
| `history_max_turns` | `6` | 注入历史轮数 |

**reranker 开关**（compose 已参数化）：

```yaml
ARROW_LAKE__RAG__RERANKER: "${RAG_RERANKER:-ollama}"           # 设 none 关闭
ARROW_LAKE__RAG__RERANKER_MODEL: "${RAG_RERANKER_MODEL:-dengcao/Qwen3-Reranker-0.6B:F16}"
ARROW_LAKE__RAG__RERANKER_TOP_N: "${RAG_RERANKER_TOP_N:-10}"
```

reranker 基类族：`BaseReranker` / `NoopReranker` / `CrossEncoderReranker` / `LLMReranker` / `OllamaReranker`。

---

## 6. 注意事项

1. **`/api/v1/rag/query`（容器 REST）在 `jd_ddd` 报 `Column 'text_content' not found`** —— Lance 表文本列名是 `text`，容器 RAG 路径引用 `text_content`（schema mismatch，已知）。规避：用宿主 `lake.rag_query()` 或 `/api/v1/kg/ask`。
2. **reranker 不可达不报错**：ollama 端点/模型缺失时 latch 回 `Noop`（检索不阻塞，只是不重排）。
3. **KG RAG vs 湖仓 RAG 分工**：KG/KA 按「概念定义」语义召回、擅长开放问题；湖仓 RAG 按「文档 chunk」召回、擅长原文复述。见 `hugegraph-query-examples.md` 文末对照与 `dashboard.html` 对比面板。
4. **字段名易错**：`/rag/*` 用 `question`/`dataset_name`/`retrieval_strategy`；`/kg/*` 用 `question`/`dataset`（无 `_name`）。两者不同。

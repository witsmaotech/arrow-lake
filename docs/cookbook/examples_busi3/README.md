# examples_busi3 — JD DDD: RAG(KA 语义检索) vs HugeGraph 图查询

验证 v1.8.8 KG 优化 roadmap **#2 RAG 检索暴露**:把 hyper-extract Knowledge
Abstract (KA) 的语义检索 / RAG 问答能力,从 extractor 暴露到 facade + REST API,
并与既有 HugeGraph 图查询做端到端对比。

业务语料: 《京东平台研发：领域驱动设计（DDD）实践总结》(26 页 PDF)。

---

## 两条检索路径

| 路径 | 后端 | 召回方式 | 典型问题 |
|------|------|----------|----------|
| **RAG (KA)** | hyper-extract FAISS index over node 定义 | 按**意思** (语义相似) | "聚合根的核心原则?" (概念召回) |
| **图查询** | HugeGraph `kg_jd_ddd` | 按**字面** (精确 name/label) + **拓扑** (邻居遍历) | "聚合根的 1 跳邻居?" (结构召回) |

两者**互补**:RAG 擅长"按概念找定义/回答开放问题",图查询擅长"按精确实体找关系/路径"。
本例展示同一批问题下两者的差异与适用场景。

---

## 前置

```bash
# 1. 服务 (prod_minimal 栈)
docker compose -p arrow-lake -f deploy/docker-compose.prod_minimal.yml up -d
# 端口: api 8000 | hg 8089(→8080) | minio 9000 | redis 6380

# 2. ollama (本地 embedding) — 宿主侧
ollama serve  # 默认 11434; 模型: qwen3-embedding:4b

# 3. 百炼 key (LLM) — 已写在 env.sh
```

## 跑通

```bash
# 一键 (ingest → build → 重测 → 对比)
bash docs/cookbook/examples_busi3/run_all.sh

# 或分步
source docs/cookbook/examples_busi3/env.sh
.venv/bin/python3 docs/cookbook/examples_busi3/01_ingest_jd.py        # PDF → jd_ddd dataset
.venv/bin/python3 docs/cookbook/examples_busi3/02_build_kg.py         # KG build (~3-5min)
.venv/bin/python3 docs/cookbook/examples_busi3/03_test_search_chat.py # 重测 search/chat
.venv/bin/python3 docs/cookbook/examples_busi3/04_compare_rag_vs_graph.py  # 对比
```

强制重建 dataset: `run_all.sh --rebuild` 或 `01_ingest_jd.py --force`。

## HugeGraph 图查询示例

见 [`hugegraph-query-examples.md`](./hugegraph-query-examples.md) —— 针对 `kg_jd_ddd` 的图查询由浅入深
（REST + Traversers L1~L4、Gremlin 参考、性能要点），含「`g` 绑定空默认图、per-dataset 走 REST」实测避坑。

## RAG 检索与问答示例

见 [`rag-examples.md`](./rag-examples.md) —— RAG 由浅入深（向量/FTS/Hybrid 检索 L1 → RAG 问答 L2 →
召回质量+reranker L3 → 引用/会话/抽取/流式 L4），含 RAGConfig 全字段、两条 RAG 路径分工、
`/rag/query` 字段名(`question`/`dataset_name`)与 `/kg/ask`(`dataset`)差异、`text_content` 列 mismatch 避坑。

## 全量端到端仪表盘（v1.8.9）

`dashboard.html` 是 jd_ddd 的**全量端到端仪表盘**（参考 `examples_busi2/dashboard.html`），
沿真实 E2E 管线落地 v1.8.9 核心能力，自包含（cytoscape 内联、无 CDN、离线可看、双击即开）。

```bash
source docs/cookbook/examples_busi3/env.sh
export HTTPS_PROXY=http://127.0.0.1:7887          # 百炼(dashscope)须走代理
export ARROW_LAKE__API_KEY=dev-api-key-for-local-testing-only
.venv/bin/python3 docs/cookbook/examples_busi3/build_dashboard.py   # 读 live facade+REST+results → 重生成 dashboard.html
```

仪表盘内容：8 指标（66 块/26 页/KG 726 顶点·1388 边/407 实体/488 关系/10.1min/4 题）·
流水线（多格式摄入→嵌入→KG双LLM→检索reranker→治理）· DuckDB 检索分析 · per-dataset KG 子图 ·
RAG 问答（reranker 真生效）· RAG-vs-图查询对比 · v1.8.9 能力深潜（reranker/双LLM/增量KA/strict模板/多格式）·
KA 版本管理 · 架构/缺陷/性能审计（P0三连/Step2-4/P2）· 升级部署备注。

## 文件

| 文件 | 作用 |
|------|------|
| `env.sh` | 共享 env: 百炼 qwen-turbo + 本地 ollama qwen3-embedding:4b + HG 8089 |
| `京东平台研发DDD实践总结.pdf` | 源语料 |
| `01_ingest_jd.py` | pypdf 抽文 → DocumentChunker 切块 → create_dataset jd_ddd (幂等) |
| `02_build_kg.py` | clear 旧图 → lake.kg_build → 校验 HG 顶点/边 + KA dump |
| `03_test_search_chat.py` | 重测 extractor + facade 的 search/chat (任务#1) |
| `04_compare_rag_vs_graph.py` | RAG vs 图查询对比 (任务#3) |
| `data/ka/jd_ddd/ka/` | KA dump (data.json / metadata.json / index/) |
| `data/results/*.json` | 各步产物 |

## 暴露的 API (任务#2)

```
POST /api/v1/kg/search   {dataset, query, top_k}  → {nodes, edges, node_count, edge_count}
POST /api/v1/kg/ask      {dataset, question, top_k} → {answer, retrieved_items, retrieval_count}
```

两者 per-dataset 读 ACL (VIEWER+),需 `extractor_backend=he`。与既有 `/kg/query`
(gremlin)、`/kg/entities/{id}/neighbors`、`/kg/stats` 互补。

## 实测结果 (jd_ddd, 百炼 qwen-turbo + ollama qwen3-embedding:4b)

**02 build** (10min): KA dump **411 nodes / 491 edges** (100% 有 definition);HG **475 vertices / 988 edges**。

**03 重测** search/chat:三查询三问答 + facade 全通 (`search ✓ | chat ✓ | facade ✓`)。
- search_ka:0.3–1.1s 召回相关概念 (聚合根→根/聚合/聚合实例;限界上下文→3 个相关概念)
- chat_ka:1–2.4s 生成领域答案 (75–503 字)

**04 对比** — 两条路径互补,非替代:

| 维度 | RAG (KA 语义检索) | HugeGraph 图查询 |
|------|------------------|-----------------|
| 召回方式 | 按**意思** (FAISS over definitions) | 按**字面** (精确 name) + **拓扑** (邻居遍历) |
| 召回范围 | 全量 411 节点瞬间 | REST 扫描上限 ~200 + 需精确 name |
| 开放问题 | ✓ 生成式回答 | ✗ 不生成 |
| 邻居/路径 | ✗ 无拓扑视图 | ✓ 1 跳邻居 (DO→持久化/PO) |
| 全局结构 | ✗ | ✓ 475 顶点/988 边 + type 分布 |
| 典型延迟 | search 0.3–5s / chat 1–2s | 邻居 0.2s |

**结论**:RAG 擅长"按概念找定义 + 回答开放问题",图查询擅长"按精确实体找关系/路径 +
全局结构视图"。`/kg/search` `/kg/ask` 补齐了图查询缺失的语义检索能力,二者互补。


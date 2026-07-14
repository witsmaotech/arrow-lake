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


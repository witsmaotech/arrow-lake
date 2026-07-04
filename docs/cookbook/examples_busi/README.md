# 芜湖市城市生命线安全工程 — v1.8.6 端到端业务案例

真实政府/工程文档（14MB / 552 页中文 PDF）走完整 Arrow Lake 链路：**PDF 预提取 → 真实向量嵌入 → 全文/向量/混合检索 → 知识图谱全量构建 → 图谱遍历 → 纯向量 RAG → GraphRAG 联合问答**。

用容器服务（MinIO + HugeGraph + Ollama）持久化运行，验证 v1.8.6 **per-dataset 知识图谱隔离**在真实业务文档上的端到端可用性。

## 数据源

| 文件 | 说明 |
|------|------|
| `docs/cookbook/datas/5.芜湖市城市生命线安全工程一期建设方案.pdf` | 14MB / 552 页中文工程方案，pypdf 文字层完整 |

## 关键设计决策（真实跑出来的坑）

| 问题 | 根因 | 解法 |
|------|------|------|
| `ingest_documents` 对 PDF 直接失败 | `arrow-lake:1.8.6` 镜像**未装 kreuzberg**（PDF 解析库，含 paddleocr） | 宿主 `prepare_pdf.py` 用 pypdf 提取文本 → tiktoken 切块 → jsonl，容器内 `lake.ingest` |
| `num_sub_vectors must divide 1024, got 24` | bge-m3 是 **1024 维**，IVF_PQ 默认 24 不整除 | `create_vector_index(num_sub_vectors=32)`（1024/32=32） |
| HugeGraph `503 Server error`（154/552 中断） | `BUILD_CONCURRENCY=10` 并发写入过载（非内存：hg-server 34%） | 降到 `BUILD_CONCURRENCY=3` + `BATCH_DELAY=1.5` |
| 邻居遍历 `Vertex '城市生命线' does not exist` | LLM 抽取的实体名更具体，硬编码种子名不在图中 | `g.V().limit(20).valueMap(true)` 取真实顶点 id 再 kneighbor |
| `qwen3.5:9b` model not found | api 容器配的模型 ollama 没有 | 显式 `ARROW_LAKE__LLM__MODEL=qwen2.5:14b`（已存在） |

## 端到端流程（`run_e2e.py`，7 步）

```
┌──────────────────────────────────────────────────────────────────┐
│  前置   prepare_pdf.py  pypdf 提取 552 页 → tiktoken 切块 → jsonl │
│  STEP 1  文档摄入     lake.ingest(wuhu_lifeline.jsonl)            │
│  STEP 2  真实向量化   bge-m3 embed_and_add + FTS + IVF_PQ(32)     │
│  STEP 3  检索验证     text_search / search / hybrid_search        │
│  STEP 4  KG 全量构建  kg_delete_graph → kg_build(552 chunks 全量) │
│  STEP 5  KG 统计遍历  kg_stats / entity_type_counts / g.V() kneighbor │
│  STEP 6  纯向量 RAG   rag_query 检索 + qwen2.5:14b 生成           │
│  STEP 7  GraphRAG     rag_query 自动 KG 增强（per-dataset 图）    │
└──────────────────────────────────────────────────────────────────┘
```

每步结果落盘到 `results/0X_*.json`，最终汇总 `results/e2e_summary.md`。

## 运行前提

```bash
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "api|hg-server|ray-head"
# deploy-api-1 / arrow-lake-hg-server / arrow-lake-ray-head 全 Up (healthy)
```

- 存储：MinIO（`.env`: `ARROW_LAKE__STORAGE__BACKEND=minio`，bucket `arrow-lake`）
- Embedding：`bge-m3:latest`（Ollama，1024 维）
- LLM：`qwen2.5:14b`（Ollama，KG 抽取 + RAG 生成）
- HugeGraph：`hg-server:8080`（`ARROW_LAKE__HUGEGRAPH__ENABLED=true`，per-dataset 图 `kg_wuhu_lifeline`）

## 运行方式

### 1. 宿主预处理 PDF → jsonl（一次性）

```bash
cd /home/witshine/wits-projs/wits-infra-dintellihub
TIKTOKEN_CACHE_DIR=/tmp/tikcache .venv/bin/python3 \
  docs/cookbook/examples_busi/prepare_pdf.py
# 产出 docs/cookbook/examples_busi/datas/wuhu_lifeline.jsonl（552 块, ~967KB）
```

### 2. 预填 tiktoken 缓存（容器无外网）

```bash
mkdir -p /tmp/tikcache
HTTPS_PROXY=http://127.0.0.1:7887 HTTP_PROXY=http://127.0.0.1:7887 NO_PROXY= \
  TIKTOKEN_CACHE_DIR=/tmp/tikcache .venv/bin/python3 -c "import tiktoken; tiktoken.get_encoding('o200k_base')" 2>/dev/null
```

### 3. 容器网络内跑端到端

```bash
cd /home/witshine/wits-projs/wits-infra-dintellihub
NET=$(docker inspect deploy-api-1 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')  # deploy_arrow-lake-net
EX=$PWD/docs/cookbook/examples_busi
mkdir -p /tmp/busi_results && chmod 777 /tmp/busi_results   # 容器非 root 可写

# 全链路（KG 全量抽取降并发，约 30-45min）
docker run --rm -w /tmp --network=$NET --env-file .env \
  -e ARROW_LAKE__STORAGE__S3_ENDPOINT=http://minio:9000 \
  -e AWS_ACCESS_KEY_ID=minioadmin -e AWS_SECRET_ACCESS_KEY=minioadmin \
  -e ARROW_LAKE__HUGEGRAPH__BUILD_CONCURRENCY=3 \
  -e ARROW_LAKE__HUGEGRAPH__BUILD_BATCH_DELAY=1.5 \
  -e ARROW_LAKE__LLM__PROVIDER=ollama \
  -e ARROW_LAKE__LLM__MODEL=qwen2.5:14b \
  -e ARROW_LAKE__LLM__API_BASE=http://10.100.93.100:11434/v1 \
  -e ARROW_LAKE__LLM__API_KEY=ollama \
  -e ARROW_LAKE__RAG__ENABLED=true \
  -e TIKTOKEN_CACHE_DIR=/tikcache -e HTTP_PROXY= -e HTTPS_PROXY= -e http_proxy= -e https_proxy= \
  -e BUSI_EXAMPLES_DIR=/examples_busi -e BUSI_RESULTS_DIR=/results \
  -v $EX:/examples_busi:ro -v /tmp/busi_results:/results \
  -v deploy_duckdb-data:/app/.duckdb:ro -v /tmp/tikcache:/tikcache:ro \
  arrow-lake:1.8.6 python /examples_busi/run_e2e.py --no-cleanup \
  2>&1 | tee /tmp/busi_full.log
```

### 断点续跑参数

| 参数 | 作用 |
|------|------|
| `--until-step N` | 只跑到第 N 步（1-7），分阶段验证 |
| `--from-step N` | 从第 N 步开始（配合 `--no-cleanup`，复用已落 MinIO 的数据集） |
| `--skip-ingest` | 跳过 STEP 1（复用已摄入的 `wuhu_lifeline`） |
| `--skip-kg` | 跳过 STEP 4 KG 构建 |
| `--no-cleanup` | 不删 dataset（持久化到 MinIO，便于复用/排查） |

**典型分阶段验证**：先 `--until-step 3`（摄入+嵌入+搜索，~30s），通过后再全链路。

## 输出结果

```
results/
├── 01_ingest.json         # 摄入报告（552 行）
├── 02_embed_index.json    # bge-m3 嵌入 + IVF_PQ/FTS 索引
├── 03_search.json         # 全文/向量/混合检索召回样例
├── 04_kg_build.json       # KG 构建任务状态 + 实体/关系数
├── 05_kg_traversal.json   # 顶点/边统计 + 类型分布 + g.V() 顶点样本 + kneighbor
├── 06_rag_qa.json         # 纯向量 RAG 问答样例
├── 07_graphrag_qa.json    # GraphRAG 问答样例
└── e2e_summary.md         # 人类可读总报告
```

## v1.8.6 验证点

- **per-dataset KG 隔离**：`wuhu_lifeline` → 图名 `kg_wuhu_lifeline`（`graph_name_for()`），与其他 dataset 完全隔离；`kg_delete_graph(dataset)` 清图重建。
- **真实 embedding**：`embed_and_add` 调 bge-m3 生成 1024 维向量，非随机模拟。
- **GraphRAG 联合**：`rag_query` 自动从问题抽实体、检索 per-dataset 图三元组注入上下文。
- **traverser 端点**：`g.V()` / kneighbor / entity_type_counts 在 per-dataset 图上可用。

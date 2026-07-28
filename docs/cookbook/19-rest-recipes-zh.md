# REST API 实战场景（End-to-End Recipes）

> 面向真实 REST API 的端到端实战场景。每个食谱把多个端点串成一个完整工作流。逐端点
> 参考见 [10-rest-api.md](10-rest-api.md)；本章讲的是*把事做成*。

所有示例用 `curl` 访问 `http://127.0.0.1:8000`。先设好 base URL 与 key：

```bash
export API=http://127.0.0.1:8000/api/v1
export KEY=dev-api-key-for-local-testing-only
AUTH="X-API-Key: $KEY"
```

## 场景 1. 文档知识库 + 防幻觉 RAG

摄入 PDF/文档，让管线自动建向量索引，再带忠实度校验提问。

```bash
# 1. 摄入文档（自动分块 + 嵌入 + 行数 ≥256 时自动建 IVF_PQ 索引）
curl -X POST "$API/datasets/kb/ingest/documents" -H "$AUTH" \
  -F "files=@report.pdf" -F "files=@spec.md"
# => {"success": true, "total_rows": 412, ...}

# 2. RAG 问答 —— 混合检索，答案落地，忠实度校验
curl -X POST "$API/rag/query" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "query": "三季度有哪些发现？",
  "dataset": "kb",
  "retrieval_strategy": "hybrid"
}'
# 响应携带：answer、citations[]、support_ratio、unsupported[]
# support_ratio ≈ 1.0 → 依据充分；查看 `unsupported` 中有无幻觉论断。

# 3. 流式变体 —— 首帧带引用，末帧带校验信息
curl -N -X POST "$API/rag/query/stream" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "query": "总结风险", "dataset": "kb"
}'
```

## 场景 2. 多模态以图搜图 + 导出

建图像数据集，按图搜索，导出命中结果。

```bash
# 1. 摄入图像（自动嵌入到图像向量列）
curl -X POST "$API/datasets/products/ingest/images" -H "$AUTH" \
  -F "files=@red.jpg" -F "files=@blue.jpg"

# 2. 把查询图嵌入 → 向量，再搜索
QV=$(curl -X POST "$API/embed/image" -H "$AUTH" -F "file=@query.jpg" | jq -r .vector)
curl -X POST "$API/datasets/products/search/vector" -H "$AUTH" -H "Content-Type: application/json" -d "{
  \"query_vector\": $QV, \"top_k\": 8
}"

# 3. 导出数据集（异步 —— 202 返回任务 id）
TID=$(curl -X POST "$API/datasets/products/export" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"format":"parquet","columns":["id","uri"]}' | jq -r .task_id)
curl "$API/datasets/products/export/$TID/status" -H "$AUTH"     # 轮询直至完成
curl "$API/datasets/products/export/$TID/download" -H "$AUTH" -o products.parquet
```

## 场景 3. 治理闭环 —— 脱敏 + 血缘 + 审计

脱敏 PII、预览规则、追溯血缘、审计策略变更。

```bash
# 1. 发布前预览 partial 脱敏规则
curl -X POST "$API/datasets/customers/quality/mask-preview" -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"columns":["phone"],"function":"partial"}'
# => {"phone": {"before": ["13812345678"], "after": ["138****5678"]}}

# 2. 发布策略（创建自动入审计）
curl -X POST "$API/gravitino/policies/masking" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"name":"pii_mask","columns":["phone","email"],"function":"partial"}'

# 3. 追溯血缘（图谱 + 点击节点的列级血缘）
curl "$API/lineage/graph/customers?max_nodes=500" -H "$AUTH"
curl "$API/lineage/history/customers" -H "$AUTH"

# 4. 审计策略创建（零新表 —— 复用 Lance 审计轨迹）
curl "$API/audit/query?event_type=masking_policy_created" -H "$AUTH"
```

## 场景 4. OLAP 分析 + 清洗 + 血缘

跑 SQL、原地清洗数据、看血缘更新。

```bash
# 1. OLAP 查询（DuckDB SQL —— 透明应用 RBAC 行/列 ACL）
curl -X POST "$API/datasets/sales/query/olap" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY total DESC"
}'

# 2. 原地清洗（语义 steps → SQL → restore_dataset 写回）
curl -X POST "$API/datasets/sales/clean" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "steps": [{"op": "drop_nulls", "column": "amount"}]
}'

# 3. 血缘此时把清洗显示为一条派生边
curl "$API/lineage/graph/sales" -H "$AUTH" | jq .stats
```

## 场景 5. 混合检索 + 重排 + GraphRAG

对比检索模式；让 GraphRAG 回答实体关系类问题。

```bash
# 1. 混合（向量 + FTS 经 RRF 融合）—— cross-encoder（bge-reranker-v2-m3）自动重排
curl -X POST "$API/datasets/docs/search/hybrid" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "query": "认证依赖链路", "top_k": 10
}'

# 2. GraphRAG —— 适合"哪些 X 依赖 Y"类、基于知识图谱的问题
curl -X POST "$API/rag/query/graphrag" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "query": "哪些系统依赖认证服务？", "dataset": "docs"
}'
```

---

**小贴士**：上述每个写操作（摄入、清洗、脱敏策略）都被 HMAC-SHA256 审计轨迹捕获
—— 用 `GET /audit/query` 配合 `event_type` 或 `dataset_name` 过滤，即可事后还原
任意工作流。

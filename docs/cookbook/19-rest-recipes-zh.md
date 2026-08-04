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

摄入 PDF/文档，让管线自动建向量索引，再提问。

```bash
# 1. 摄入文档（自动分块 + 嵌入 + 行数 ≥256 时自动建 IVF_PQ 索引）
curl -X POST "$API/datasets/kb/ingest/documents" -H "$AUTH" \
  -F "files=@report.pdf" -F "files=@spec.md"
# => {"success": true, "total_rows": 412, ...}

# 2. RAG 问答 —— 默认 hybrid（向量 + FTS 经 RRF 融合），use_kg 默认 true 注入图谱上下文
curl -X POST "$API/rag/query" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "question": "三季度有哪些发现？",
  "dataset_name": "kb",
  "retrieval_strategy": "hybrid"
}'
# 响应：answer、citations[]、retrieval_count、latency_ms
# 纯向量对比：传 "use_kg": false 即降级（无需关 hugegraph.enabled）

# 3. 流式变体 —— 首帧带 citations，末帧带 latency/verification
curl -N -X POST "$API/rag/query/stream" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "question": "总结风险", "dataset_name": "kb"
}'

# 4.（可选）开启防幻觉校验：设 ARROW_LAKE__RAG__ENABLE_VERIFICATION=true 后，
#    响应额外携带 verification.{support_ratio, unsupported[]}，逐句标注是否被上下文支撑
```

> **字段名注意**：请求体用 `question` / `dataset_name`（不是 query/dataset）；
> `use_kg` 默认 true，GraphRAG 在 hugegraph 启用时自动注入，失败优雅降级。

## 场景 2. 多模态以图搜图 + 导出

建图像数据集，按图搜索，导出命中结果。

```bash
# 1. 摄入图像（自动嵌入到图像向量列）
curl -X POST "$API/datasets/products/ingest/images" -H "$AUTH" \
  -F "files=@red.jpg" -F "files=@blue.jpg"

# 2. 把查询图嵌入 → 向量（JSON body，images 为 base64 字符串列表），再搜索
IMG=$(base64 -w0 query.jpg)
QV=$(curl -X POST "$API/embed/image" -H "$AUTH" -H "Content-Type: application/json" -d "{
  \"images\": [\"$IMG\"]
}" | jq '.embeddings[0]')
curl -X POST "$API/datasets/products/search/vector" -H "$AUTH" -H "Content-Type: application/json" -d "{
  \"query_vector\": $QV, \"top_k\": 8
}"

# 3. 导出数据集（异步 —— 202 返回任务 id；output_path 必填）
TID=$(curl -X POST "$API/datasets/products/export" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"output_path":"exports/products.parquet","columns":["id","uri"]}' | jq -r .task_id)
curl "$API/datasets/products/export/$TID/status" -H "$AUTH"     # 轮询直至完成，产物落在 output_path
```

## 场景 3. 治理闭环 —— 脱敏 + 血缘 + 审计

脱敏 PII、预览规则、追溯血缘、审计策略变更。

```bash
# 1. 发布前预览 partial 脱敏规则（读前 5 行返 before/after；partial 保留首2尾2）
curl -X POST "$API/datasets/customers/quality/mask-preview" -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"columns":["phone"],"function":"partial"}'
# => {"phone": {"before": ["13812345678"], "after": ["13*******78"]}}

# 2. 发布策略（Gravitino policy，创建自动入审计）
curl -X POST "$API/metadata/policies/masking" -H "$AUTH" -H "Content-Type: application/json" \
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
# 1. 混合（向量 + FTS 经 RRF 融合）—— 需同时给 query_vector 与 query_text
QV=$(curl -X POST "$API/embed/text" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "texts": ["认证依赖链路"]
}' | jq '.embeddings[0]')
curl -X POST "$API/datasets/docs/search/hybrid" -H "$AUTH" -H "Content-Type: application/json" -d "{
  \"query_vector\": $QV, \"query_text\": \"认证依赖链路\", \"top_k\": 10
}"
# search 侧默认 bge-reranker-v2-m3 重排（由 SearchConfig.reranker_model 配置驱动，非请求参数）

# 2. GraphRAG —— 适合"哪些 X 依赖 Y"类、基于知识图谱的问题
curl -X POST "$API/kg/query/graphrag" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "question": "哪些系统依赖认证服务？", "dataset": "docs"
}'
```

## 场景 6. 抽取模板生命周期（v1.10.0）

KG 抽取模板不再是静态文件 —— 通过 `/api/v1/admin/extraction-templates` 运行时管理
（ADMIN 角色）。新模板在下次 `kg/build` 时生效，**无需重建镜像、无需重启 API**。

```bash
# 1. 列出已安装模板（gallery + 用户保存）
curl "$API/admin/extraction-templates" -H "$AUTH"

# 2. 保存自定义模板（校验结构；拒绝未知字段）
curl -X POST "$API/admin/extraction-templates" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "name": "my_concept_graph",
  "doc_type": "project_report",
  "schema": {"vertices": [...], "edges": [...]}
}'

# 3. 在提交前用一篇文档试跑 KG 验证（质量 harness）
TDS=$(curl -X POST "$API/admin/extraction-templates/my_concept_graph/quality/build" \
  -H "$AUTH" -H "Content-Type: application/json" -d '{"doc_path":"datas/sample.md"}' \
  | jq -r .temp_dataset)
curl "$API/admin/extraction-templates/my_concept_graph/quality/history" -H "$AUTH"

# 4. 清理临时验证数据集
curl -X DELETE "$API/admin/extraction-templates/quality/$TDS" -H "$AUTH"
```

> 相关：`/api/v1/admin/doc-type-categories` 管理动态 doc_type → 模板 category 字典
> （list / create / delete），可在运行时新增文档类别。

## 场景 7. 个人令牌 + `/me` 用户态

个人令牌（Role.VIEWER，经 `/auth/...` 发放）解锁 `/me` 用户态端点 —— 按调用用户
作用域的已保存查询、通知与偏好。**JWT 和 admin API key 调不通 `/me/*`** —— 必须用
个人令牌（放 `X-API-Key`）。

```bash
# 1. 保存私有查询
curl -X POST "$API/me/saved-queries" -H "X-API-Key: $PTOK" -H "Content-Type: application/json" -d '{
  "name": "high-value-sales",
  "dataset": "sales",
  "sql": "SELECT * FROM sales WHERE amount > 10000"
}'

# 2. 读取通知（任务完成事件落到这里）
curl "$API/me/notifications" -H "X-API-Key: $PTOK"
curl -X POST "$API/me/notifications/read?notification_id=42" -H "X-API-Key: $PTOK"

# 3. 读取/写入偏好（按用户的 UI/API 设置）
curl "$API/me/preferences" -H "X-API-Key: $PTOK"
curl -X PUT "$API/me/preferences" -H "X-API-Key: $PTOK" -H "Content-Type: application/json" \
  -d '{"theme":"dark","default_dataset":"sales"}'
```

---

**小贴士**：上述每个写操作（摄入、清洗、脱敏策略）都被 HMAC-SHA256 审计轨迹捕获
—— 用 `GET /audit/query` 配合 `event_type` 或 `dataset_name` 过滤，即可事后还原
任意工作流。OpenAPI 目前暴露 **186 条路由**（`/docs` 查看交互式浏览器）。

# REST API Recipes — End-to-End Scenarios

> Practical, end-to-end scenarios against the live REST API. Each recipe chains
> multiple endpoints into a real workflow. For per-endpoint reference see
> [10-rest-api.md](10-rest-api.md); this chapter is about *doing things*.

All examples use `curl` against `http://127.0.0.1:8000`. Set the base URL and key
once:

```bash
export API=http://127.0.0.1:8000/api/v1
export KEY=dev-api-key-for-local-testing-only
AUTH="X-API-Key: $KEY"
```

## Recipe 1. Document Knowledge Base with Anti-Hallucination RAG

Ingest PDFs/docs, let the pipeline auto-build the vector index, then ask questions
with faithfulness verification.

```bash
# 1. Ingest documents (auto chunks + embeds + builds IVF_PQ index when ≥256 rows)
curl -X POST "$API/datasets/kb/ingest/documents" -H "$AUTH" \
  -F "files=@report.pdf" -F "files=@spec.md"
# => {"success": true, "total_rows": 412, ...}

# 2. RAG query — hybrid retrieval, ground the answer, verify faithfulness
curl -X POST "$API/rag/query" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "query": "What were the Q3 findings?",
  "dataset": "kb",
  "retrieval_strategy": "hybrid"
}'
# Response carries: answer, citations[], support_ratio, unsupported[]
# support_ratio ≈ 1.0 → well grounded; review `unsupported` for hallucinated claims.

# 3. Streaming variant — first frame has citations, final frame has verification
curl -N -X POST "$API/rag/query/stream" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "query": "Summarize risks", "dataset": "kb"
}'
```

## Recipe 2. Multimodal Image Search + Export

Build an image dataset, search by image, export the hits.

```bash
# 1. Ingest images (auto-embeds into the image vector column)
curl -X POST "$API/datasets/products/ingest/images" -H "$AUTH" \
  -F "files=@red.jpg" -F "files=@blue.jpg"

# 2. Embed a query image → vector, then search
QV=$(curl -X POST "$API/embed/image" -H "$AUTH" -F "file=@query.jpg" | jq -r .vector)
curl -X POST "$API/datasets/products/search/vector" -H "$AUTH" -H "Content-Type: application/json" -d "{
  \"query_vector\": $QV, \"top_k\": 8
}"

# 3. Export the dataset (async — 202 returns a task id)
TID=$(curl -X POST "$API/datasets/products/export" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"format":"parquet","columns":["id","uri"]}' | jq -r .task_id)
curl "$API/datasets/products/export/$TID/status" -H "$AUTH"     # poll until done
curl "$API/datasets/products/export/$TID/download" -H "$AUTH" -o products.parquet
```

## Recipe 3. Governance Loop — Masking + Lineage + Audit

Mask PII, preview the rule, trace lineage, audit the policy change.

```bash
# 1. Preview a partial-mask rule before publishing
curl -X POST "$API/datasets/customers/quality/mask-preview" -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"columns":["phone"],"function":"partial"}'
# => {"phone": {"before": ["13812345678"], "after": ["138****5678"]}}

# 2. Publish the policy (creation is audited automatically)
curl -X POST "$API/gravitino/policies/masking" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"name":"pii_mask","columns":["phone","email"],"function":"partial"}'

# 3. Trace lineage (graph + column-level lineage on node click)
curl "$API/lineage/graph/customers?max_nodes=500" -H "$AUTH"
curl "$API/lineage/history/customers" -H "$AUTH"

# 4. Audit the policy creation (zero new tables — reuses Lance audit trail)
curl "$API/audit/query?event_type=masking_policy_created" -H "$AUTH"
```

## Recipe 4. OLAP Analytics + Clean + Lineage

Run SQL, clean the data in place, watch lineage update.

```bash
# 1. OLAP query (DuckDB SQL — RBAC row/column ACLs applied transparently)
curl -X POST "$API/datasets/sales/query/olap" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY total DESC"
}'

# 2. Clean in place (semantic steps → SQL → restore_dataset write-back)
curl -X POST "$API/datasets/sales/clean" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "steps": [{"op": "drop_nulls", "column": "amount"}]
}'

# 3. Lineage now shows the clean as a derived edge
curl "$API/lineage/graph/sales" -H "$AUTH" | jq .stats
```

## Recipe 5. Hybrid Search + Reranking + GraphRAG

Compare retrieval modes; let GraphRAG answer entity-relationship questions.

```bash
# 1. Hybrid (vector + FTS via RRF) — cross-encoder (bge-reranker-v2-m3) reranks automatically
curl -X POST "$API/datasets/docs/search/hybrid" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "query": "authentication dependency chain", "top_k": 10
}'

# 2. GraphRAG — for "which X depends on Y" questions over the knowledge graph
curl -X POST "$API/rag/query/graphrag" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "query": "Which systems depend on the auth service?", "dataset": "docs"
}'
```

---

**Tip**: every write above (ingest, clean, masking policy) is captured by the
HMAC-SHA256 audit trail — query `GET /audit/query` with `event_type` or
`dataset_name` filters to reconstruct any workflow after the fact.

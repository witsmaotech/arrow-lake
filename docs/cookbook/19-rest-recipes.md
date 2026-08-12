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

Ingest PDFs/docs, let the pipeline auto-build the vector index, then ask questions.

```bash
# 1. Ingest documents (auto chunks + embeds + builds IVF_PQ index when ≥256 rows)
curl -X POST "$API/datasets/kb/ingest/documents" -H "$AUTH" \
  -F "files=@report.pdf" -F "files=@spec.md"
# => {"success": true, "total_rows": 412, ...}

# 2. RAG query — default hybrid (vector + FTS via RRF); use_kg defaults true (injects graph context)
curl -X POST "$API/rag/query" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "question": "What were the Q3 findings?",
  "dataset_name": "kb",
  "retrieval_strategy": "hybrid"
}'
# Response: answer, citations[], retrieval_count, latency_ms
# Pure-vector comparison: pass "use_kg": false to downgrade (no need to disable hugegraph.enabled)

# 3. Streaming variant — first frame has citations, final frame has latency/verification
curl -N -X POST "$API/rag/query/stream" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "question": "Summarize risks", "dataset_name": "kb"
}'

# 4. (Optional) Enable faithfulness verification: set ARROW_LAKE__RAG__ENABLE_VERIFICATION=true,
#    then the response additionally carries verification.{support_ratio, unsupported[]},
#    marking per-sentence whether it is supported by the context
```

> **Field names**: the request body uses `question` / `dataset_name` (not query/dataset);
> `use_kg` defaults to true. GraphRAG auto-injects when hugegraph is enabled and degrades gracefully on failure.

## Recipe 2. Multimodal Image Search + Export

Build an image dataset, search by image, export the hits.

```bash
# 1. Ingest images (auto-embeds into the image vector column)
curl -X POST "$API/datasets/products/ingest/images" -H "$AUTH" \
  -F "files=@red.jpg" -F "files=@blue.jpg"

# 2. Embed a query image → vector (JSON body, images is a list of base64 strings), then search
IMG=$(base64 -w0 query.jpg)
QV=$(curl -X POST "$API/embed/image" -H "$AUTH" -H "Content-Type: application/json" -d "{
  \"images\": [\"$IMG\"]
}" | jq '.embeddings[0]')
curl -X POST "$API/datasets/products/search/vector" -H "$AUTH" -H "Content-Type: application/json" -d "{
  \"query_vector\": $QV, \"top_k\": 8
}"

# 3. Export the dataset (async — 202 returns a task id; output_path is required)
TID=$(curl -X POST "$API/datasets/products/export" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"output_path":"exports/products.parquet","columns":["id","uri"]}' | jq -r .task_id)
curl "$API/datasets/products/export/$TID/status" -H "$AUTH"     # poll until done; artifact lands at output_path
```

## Recipe 3. Governance Loop — Masking + Lineage + Audit

Mask PII, preview the rule, trace lineage, audit the policy change.

```bash
# 1. Preview a partial-mask rule before publishing (reads first 5 rows; partial keeps first2/last2)
curl -X POST "$API/datasets/customers/quality/mask-preview" -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"columns":["phone"],"function":"partial"}'
# => {"phone": {"before": ["13812345678"], "after": ["13*******78"]}}

# 2. Publish the policy (Gravitino policy; creation is audited automatically)
curl -X POST "$API/metadata/policies/masking" -H "$AUTH" -H "Content-Type: application/json" \
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
# 1. Hybrid (vector + FTS via RRF) — both query_vector and query_text are required
QV=$(curl -X POST "$API/embed/text" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "texts": ["authentication dependency chain"]
}' | jq '.embeddings[0]')
curl -X POST "$API/datasets/docs/search/hybrid" -H "$AUTH" -H "Content-Type: application/json" -d "{
  \"query_vector\": $QV, \"query_text\": \"authentication dependency chain\", \"top_k\": 10
}"
# The search path reranks with bge-reranker-v2-m3 by default (driven by SearchConfig.reranker_model, not a request param)

# 2. GraphRAG — for "which X depends on Y" questions over the knowledge graph
curl -X POST "$API/kg/query/graphrag" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "question": "Which systems depend on the auth service?", "dataset": "docs"
}'
```

## Recipe 6. Extraction Template Lifecycle (v1.10.0)

The KG extraction template is no longer a static file — manage it at runtime via
`/api/v1/admin/extraction-templates` (ADMIN role). A new template takes effect on
the next `kg/build` **without rebuilding the image or restarting the API**.

```bash
# 1. List installed templates (gallery + user-saved)
curl "$API/admin/extraction-templates" -H "$AUTH"

# 2. Save a custom template (validates structure; rejects unknown keys)
curl -X POST "$API/admin/extraction-templates" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "name": "my_concept_graph",
  "doc_type": "project_report",
  "schema": {"vertices": [...], "edges": [...]}
}'

# 3. Validate a KG run against a doc before committing (quality harness)
TDS=$(curl -X POST "$API/admin/extraction-templates/my_concept_graph/quality/build" \
  -H "$AUTH" -H "Content-Type: application/json" -d '{"doc_path":"datas/sample.md"}' \
  | jq -r .temp_dataset)
curl "$API/admin/extraction-templates/my_concept_graph/quality/history" -H "$AUTH"

# 4. Clean up the temporary validation dataset
curl -X DELETE "$API/admin/extraction-templates/quality/$TDS" -H "$AUTH"
```

> Related: `/api/v1/admin/doc-type-categories` manages the dynamic doc_type →
> template-category dictionary (list / create / delete), letting you add new
> document categories at runtime.

## Recipe 7. Personal Token + the `/me` Surface

Personal tokens (Role.VIEWER, issued via `/auth/...`) unlock the `/me` user-state
endpoints — saved queries, notifications, and preferences scoped to the calling
user. **JWT and admin API keys do not work on `/me/*`** — a personal token is
required (passed in `X-API-Key`).

```bash
# 1. Save a private saved query
curl -X POST "$API/me/saved-queries" -H "X-API-Key: $PTOK" -H "Content-Type: application/json" -d '{
  "name": "high-value-sales",
  "dataset": "ontime",
  "sql": "SELECT * FROM sales WHERE amount > 10000"
}'

# 2. Read your notifications (task completion events land here)
curl "$API/me/notifications" -H "X-API-Key: $PTOK"
curl -X POST "$API/me/notifications/read?notification_id=42" -H "X-API-Key: $PTOK"

# 3. Get/put preferences (per-user UI/API settings)
curl "$API/me/preferences" -H "X-API-Key: $PTOK"
curl -X PUT "$API/me/preferences" -H "X-API-Key: $PTOK" -H "Content-Type: application/json" \
  -d '{"theme":"dark","default_dataset":"ontime"}'
```

---

**Tip**: every write above (ingest, clean, masking policy) is captured by the
HMAC-SHA256 audit trail — query `GET /audit/query` with `event_type` or
`dataset_name` filters to reconstruct any workflow after the fact. The OpenAPI
surface currently exposes **186 routes** (`/docs` for the interactive explorer).

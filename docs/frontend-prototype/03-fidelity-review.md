# 前端原型功能保真度审计

> 目标：审计 `docs/frontend-prototype/` 的**页面地图**与**逐页逻辑**是否真正贴合 Arrow Lake 的实际能力 / 接口 / 流程。
> Ground truth：`arrow_lake/api/models/*.py`（真实 Pydantic 请求模型）+ `api/routers/`（18 router / 106 routes）+ 产品介绍 v1.8.6。
> 结论：**地图覆盖 6/11 能力域；3 处真实逻辑漏洞；5 个能力域缺独立页。修复后可"真正可用"。**

---

## 1. 页面地图覆盖矩阵

| 能力域 | 真实路由 | 原型有无页面 | 覆盖判定 |
|---|---|---|---|
| 总览/系统 | `health` · `version` · `tasks` · `maintenance` | dashboard.html | ✅ |
| 数据集 + 摄入 | `datasets` · `ingest/{11种}` · `upload/*` | datasets.html | ⚠️ 摄入表单通用化（见 §3） |
| **嵌入 / 索引管理** | `embed/*` · `index create/delete` · `VectorIndex/FtsIndex/ScalarIndex/FacetsIndex` | **❌ 无** | **❌ 严重缺口（检索的前置条件）** |
| 检索 | `search/{vector\|fts\|hybrid\|faceted\|ensemble}` | search.html | ⚠️ 有页，逻辑有偏差（见 §2） |
| OLAP 分析 | `query/{olap\|metadata\|graph\|daft}` | olap.html | ⚠️ 缺 `graph`(SQL-PGQ)；MV 面板存疑 |
| RAG | `rag/query` · `query/stream` · `extract` · `templates` · sessions | rag.html | ⚠️ GraphRAG 逻辑错（见 §2） |
| 知识图谱 | `kg/*` · `traversers/{8种}` · `query/graphrag` | kg.html | ⚠️ GraphRAG 参数缺（见 §2） |
| 数据质量 | `quality/{filter\|report\|deduplicate\|rules\|profile}` | dataset-detail 的质量 Tab | ✅（作为 Tab 合理） |
| 数据血缘 | `lineage/{graph\|history\|impact\|stats\|query}` | dataset-detail 的血缘 Tab | ⚠️ 仅单集，缺跨集血缘页 |
| **审计** | `audit/{record\|verify\|query\|export}` | **❌ 无独立页** | **❌ 合规缺口** |
| **元数据治理(Gravitino)** | `metadata/{catalogs\|tables\|tags\|policies\|statistics\|models\|lineage\|enforce}` | **❌ 无** | **❌ 严重缺口（产品主打）** |
| 备份恢复 | `backup/{create\|restore\|list}` | ❌ 无 | ⚠️ 缺口 |
| **用户 / RBAC** | `admin/{users\|acl/dataset\|acl/schema\|deny}` | 仅 dataset ACL Tab | **❌ 缺 users/schema ACL/deny 控制台** |

**覆盖总结**：智能域（检索/OLAP/RAG/KG）都有页；**治理 + 管理域（审计/Gravitino/RBAC/备份/索引）几乎全缺独立页**——而产品文档把"治理/安全/审计"列为与"检索/分析"并列的一等能力。这是地图最大短板。

---

## 2. 真实逻辑漏洞（P0，影响"可用"）

### 2.1 search.html · 缺"查询嵌入"步骤 + 参数错位

真实请求模型（`models/search.py`）：
- `VectorSearchRequest`: **`query_vector: list[float]`（必填）**, top_k, metric, `vector_column`, where, **`nprobes`**
- `HybridSearchRequest`: **`query_vector`（必填）+ `query_text`（必填）**, top_k, vector_column, fts_column, where
- `FacetedSearchRequest`: query_vector, **`facets: list[str]`（要分面的列）**, top_k, vector_column, where → 返回 `facets` 计数
- `EnsembleSearchRequest`: query_vector, **`columns: list[str]` + `weights: dict`**, top_k, where
- `FullTextSearchRequest`: query(text), top_k, fts_column, where, **`offset`**

原型偏差：
1. ❌ **vector/hybrid/faceted/ensemble 都要求 `query_vector`（浮点数组），不能直接用文本查**。原型让用户在所有模式直接输入文本——真实流程是 `text → embed(query) → query_vector → search`。**必须加"嵌入查询"步骤**（调用 `embed/text` 或本地模型），或在 UI 明示"文本将先经 Qwen3 嵌入"。这是"能否真正跑通"的关键。
2. ❌ **"索引类型(IVF_PQ/FLAT/HNSW)" 下拉是建索引时的配置，不是查询参数**。查询期的真实旋钮是 `nprobes`——原型没暴露，反而暴露了不属于此处的 index type。
3. ❌ **"Reranker 开关"不在任何 search 请求模型里**。重排在 v1.8.0 是 hybrid bridge 内部行为（自动），非 per-query 开关。该 toggle 疑似**虚构**，应移除或改为"hybrid 自动重排"只读说明。
4. ❌ **faceted 模式缺 `facets[]` 输入**（选哪些列做分面）；**ensemble 模式缺 `columns[]`+`weights`** 输入。当前两种模式只是换了结果展示，请求体不对。
5. ✅ `where` 过滤、`top_k`、`vector_column` 正确。

### 2.2 rag.html · GraphRAG 不是 rag/query 的开关

真实模型：
- `RAGQueryRequest`: question, dataset_name, top_k, **`retrieval_strategy` (fts/vector/hybrid)**, template_name, session_id —— **没有 graph 开关**
- GraphRAG 是**独立端点** `POST /kg/query/graphrag`，请求体 `GraphRAGQueryRequest`: question, dataset_name, top_k, **`traversal_depth`(1-10)**, **`graph_weight`(0-1)**

原型偏差：
1. ❌ **"GraphRAG 开/关" toggle 挂在 rag 对话上，是错的**。真实做法：RAG 页提供 `retrieval_strategy` 选择（fts/vector/hybrid）；GraphRAG 是**另一条入口**（要么独立 Tab "问 GraphRAG"，调 `/kg/query/graphrag` 并暴露 traversal_depth / graph_weight 两个真实参数）。
2. ❌ 原型 KG 页的 GraphRAG 试询框也未暴露 `traversal_depth` / `graph_weight`——两个真实参数全缺。
3. ⚠️ "ctx=4K" 是**响应字段**（`context_tokens` 出现在 response），请求里无 context 预算参数。原型当输入展示，略虚构。
4. ✅ session_id / top_k / dataset / citations 正确。`GET /rag/templates` 可接一个模板选择器（原型缺）。

### 2.3 索引管理是检索的前置条件，却无任何 UI

真实 `models/embedding.py` 有 `VectorIndexRequest` / `FtsIndexRequest` / `ScalarIndexRequest` / `FacetsIndexRequest` + `embed/{text,image,clip}`。
- 检索能跑的前提是数据集**已建对应索引**（向量索引、FTS 索引、标量索引、分面索引）。
- 原型完全**没有索引管理页/Tab**：用户无法 `create/list/delete` 索引，看不到索引状态。
- 这使整个检索流程在 UI 上"跑不通"——是 P0 缺口。

**建议**：数据集工作区补"**索引**"Tab（列出现有索引 + create/delete），独立"嵌入管理"页承接 `embed/*`。

---

## 3. 次级逻辑偏差（P1）

### 3.1 olap.html
- ❌ 缺 **`query/graph`（SQL-PGQ 图模式查询）**——真实端点 `GraphQueryRequest`，原型三 Tab 没含。
- ⚠️ "物化视图"Tab：DuckLake MV 在产品里是 SQL 层能力（`CREATE MATERIALIZED VIEW` 走 olap），**未见独立 MV CRUD REST 端点**。该 Tab 当 SQL 驱动即可，别伪造成有专门接口。
- ✅ olap + daft 正确。

### 3.2 datasets.html · 摄入表单过于通用
真实 11 种 ingest 端点参数差异大：
- `documents`：doc_type 路由、chunk 策略、embed、OCR
- `sql`：connection / query；`kafka`：brokers / topic；`iceberg`/`deltalake`：table path
- `images`/`videos`/`mixed`：媒体处理参数

原型用**一个通用表单**套所有来源——能跑通 documents，但 sql/kafka/iceberg 的真实字段全缺。"真正可用"需按来源切换表单字段。

### 3.3 dataset-detail.html · 缺两个 Tab
- 缺 **"嵌入/索引"Tab**（见 §2.3）
- ingest 在原型是独立页而非工作区 Tab；与最初 IA（9-Tab 含摄入/嵌入）不一致——可保留独立页，但工作区应至少有"索引"Tab。

### 3.4 kg.html · traverser 表单
- ✅ 8 traverser 齐全、参数化方向正确（产品明确"direction 枚举 + list 上限"防 DoS）。
- ⚠️ traverser 请求体字段（source/target/depth/direction）为推断，落地前应对照各 `traversers/*` 路由的实际 body（本次未逐一读取）。

---

## 4. 为"真正可用"的修订清单（按优先级）

### P0 · 逻辑漏洞（必须修）
1. **search.html**：加"查询嵌入"步骤（text→embed→vector），移除 index type 下拉与 reranker 开关，加 `nprobes`、faceted 的 `facets[]`、ensemble 的 `columns[]+weights`、fts 的 `offset`。
2. **rag.html**：把 GraphRAG toggle 改为 `retrieval_strategy`(fts/vector/hybrid) 选择；GraphRAG 走独立入口（新 Tab 或 kg.html），暴露 `traversal_depth` + `graph_weight`。
3. **新增"索引"Tab / 嵌入管理页**：承载 `embed/*` + `index create/delete/list`，让检索流程闭环。

### P1 · 页面缺口（补独立页）
4. **治理 Governance 页**（Gravitino）：catalog 树 + tag→ACL + retention/masking policy + statistics + models。**产品主打，必须有。**
5. **审计 Audit 页**：HMAC 事件流 + `verify` 完整性校验红绿灯 + query + export。合规必需。
6. **Admin / RBAC 控制台**：users + dataset ACL + **schema ACL** + **deny 规则**（现仅 dataset ACL）。
7. **备份 Backup 页**：create/restore/list/delete。

### P2 · 打磨
8. olap 加 `query/graph`(SQL-PGQ) Tab；MV Tab 改为 SQL 驱动说明。
9. datasets 摄入按来源切换字段（至少 documents / sql / kafka / iceberg 四种真实表单）。
10. RAG 引用卡补真实字段（chunk_index / row_id / text_excerpt）；加 `GET /rag/templates` 模板选择器。
11. lineage 独立页（跨数据集 DAG + impact）。

### 修订后的页面地图（建议）

```
公开: index
控制台:
  概览:  dashboard · tasks · system
  数据:  datasets · dataset-detail[+索引Tab] · ingest(独立) · embeddings/索引   ← 补
  智能:  search · rag · kg · olap[+graph Tab]
  治理:  lineage · audit · governance(Gravitino) · backup                          ← 补 3 页
  管理:  admin(users/RBAC/schema ACL/deny)                                         ← 补
```

---

## 5. 结论

原型**视觉与信息架构成熟**，签名元素（深度轨/降级徽章/View API）与产品哲学高度对齐。但作为"真正可用"的控制台，存在两类硬伤：

- **逻辑层**：检索缺嵌入步骤、RAG 的 GraphRAG 入口错位、索引管理缺失——这三处会让用户按 UI 操作**跑不通真实接口**。
- **覆盖层**：治理/审计/RBAC/备份/索引 五个能力域缺独立页，与产品"安全治理是一等公民"的定位不符。

修完 §4 的 P0（3 项）+ P1（4 页），即可从"高保真演示"升级为"可对接真实 API 的可用控制台"。

## 6. v1.9.0 扩展（阶段 A–D，2026-07-17）

按 `04-v190-extension-plan.md` 把原型从 v1.8.6 基线扩展到 v1.9.0，覆盖控制面（libSQL）+ v1.8.7–1.8.9 能力。**新页 1 + 扩展 9 页**，全部经 playwright 像素校验（渲染 / `${` 泄漏=0 / 0 pageerror）：

- **A** — 新增 `my-workspace.html`（/me/*）；`admin.html` +Personal Tokens/角色 RBAC；顶栏头像下拉（layout.js）。
- **B** — `governance.html` +治理活动日志 4 子表；`tasks.html` +历史/死信；`lineage.html` 全链路写入 banner。
- **C** — `system.html` +控制面 SystemDB 面板（v1.9.0）；`kg.html` +双 LLM/增量/版本/模板（修预存图渲染崩溃）；`rag.html` +reranker/持久会话。
- **D** — `ingest.html` 多格式+auto-embed/FTS 标注；`dashboard.html` 探活加控制面层；`showcase.html` 版本轴→v1.9.0（11→15）；README 刷新。

**顺带修复的预存缺陷**：admin/governance/lineage/ingest 多页用了 `${[...].map()}` 模板字面量直写 HTML body（浏览器不求值，渲染为字面量文本）→ 全部改 JS innerHTML；kg.html 图渲染 `colorOf[n[3]]` 用 type 缺键致 `undefined 不可迭代` 中断整脚本 → 改 `n[4]` colorKey。

**待重审**：新页（my-workspace）与扩展段（tokens/roles/治理日志/DLQ/控制面）的 View API 路由需对齐真实后端（部分为 v1.9.0 规划路由，落地后核对）；showcase 时间机器王牌的版本 diff 数据仍止于 v1.8.6（仅版本演进轴扩到 v1.9.0）。

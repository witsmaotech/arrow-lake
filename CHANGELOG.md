# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [1.10.1] - 2026-08-04

### docling GPU 推理修复(triton JIT 缺 C 编译器)

前端 PDF ingest 触发 docling GPU 解析时 gunicorn worker 反复崩溃重启(pid 8→3380+,fork 资源耗尽)。根因:`Dockerfile` 多阶段构建的 runtime 阶段(全新 python:3.12-slim)未装 gcc,而 docling GPU 走 `torch.compile → inductor → triton` JIT 编译 CUDA kernel 需要 `cc`。

- **Dockerfile runtime 加 `build-essential`**:triton `compile_module_from_src` 用 `cc` 编译 host wrapper,镜像缺 gcc/cc → `Failed to find C compiler` → InductorError 未捕获 → `terminate called` → worker 崩溃循环。builder 阶段本就有 build-essential,多阶段被扔。验证:重传 PDF → docling `mean_grade=EXCELLENT` → lineage append 入库。
- **prod_minimal.yml triton-cache**:命名卷 `triton-cache:/app/.triton` + `TRITON_CACHE_DIR=/app/.triton` + volume-init chown(read_only 根 FS 下 docling GPU 推理 triton kernel cache 写入 Errno 30)。与既有 triton-cache 坑互补。

### KG 抽取模板降级路径修复(he_extractor)

`map_reduce` 抽取阶段 per-chunk doc_type 路由,部分 chunk 被路由到 `general/workflow_graph`(模板 NoneType bug)→ 自动降级回 `entity_graph` 却 `Template not found` → misroute chunk 0 实体。

- **降级路径走 `_resolve_template_path`**:主路径 `template_path` 经 `_resolve_template`(含 stem→完整路径解析),降级路径 `default_template()` 返回 raw stem 直接喂 `_parse_fresh` → `Template.create` 不认 stem。改 `default_path = self._resolve_template_path(self._router.default_template())`,降级兜底成功,misroute/模板失败不再归零实体。

### 配置精简 + 部署 override 收敛

- 11 个 `arrow_lake/config/*.py` 精简(-209 行冗余字段)。
- 删 4 个冗余 compose override(`gpu`/`kgtest`/`prod`/`wuhu-validate`)。
- `prod_minimal.yml`:LLM/embedding model 改 `${VAR:-default}` env 可覆盖;`Makefile` 精简。
- config/后向兼容 tests 配套。

## [1.10.0] - 2026-08-03

### 知识抽取模板管理(Knowledge Extraction Template Management)

前端模板管理界面(CRUD)+ 后端 KA/KG 能根据**新模板动态抽取建图**(不 rebuild/不 restart 加载卷上用户 YAML)。

- **M1 后端动态加载 + CRUD API**:卷上 `/data/lake/templates/*.yaml` 运行时加载进 hyper-extract gallery(`reset_gallery_cache` 热重载)+ `/api/v1/admin/extraction-templates` CRUD(ADMIN)+ `template_registry` 校验(name 正则/schema/denylist/路径穿越守护)+ 查询路径模板快照(`ka_dir/template.yaml`,治 user 模板 RAG "Template not found")+ `build(template_override=)` 透传三粒度。
- **M2 CRUD 页面 + 数据集绑定**:`console/extraction-templates.html`(列表/新建/编辑 YAML+实时校验/删除/系统派生)+ 数据集绑定(system_db `dataset_template_bindings`,`/kg/build` 缺省 template→自动解析绑定模板)。
- **M2.5 LLM 辅助生成模板**:self-heal 多轮 LLM 生成 YAML + `_hyperextract_check` 权威落盘闸门 + 前端「✨AI 生成」。
- **M3 dry-run + set-default + usage**:单 chunk feed_text 试跑沙箱 + 设默认 + 用量查看。
- **M4 模板质量验证 harness**:`console/template-quality.html` 4 步(编辑→生成 ~2000字文档→ingest+kg_build+vis 图谱→RAG→清理)+ 新端点 `POST /{name}/quality/{doc,build}` + `DELETE /quality/{temp_ds}` + KA 隔离(分片根)+ 验证历史(system_db V006 `template_quality_runs`)+ 安全加固(path-traversal/XSS/DoS)。
- **M5 category↔doc_type 端到端拉通 + 动态词典**:system_db V007 `doc_type_categories` 表(seed 11 规范词)+ `/api/v1/admin/doc-type-categories` 动态 CRUD + `validate_template_yaml` category 必填且∈词典 + `_inject_category`(写 YAML `category:`)+ `GET /kg/doc-types` 动态 + ingest doc_type 下拉动态。
- **Console**:原生弹框(prompt/confirm/alert)→ 站内 modal/toast 组件。

新增 system_db 迁移:V005 `extraction_templates`、V006 `template_quality_runs`、V007 `doc_type_categories`。新增 console 页:`extraction-templates.html`、`template-quality.html`。

## [1.9.12] - 2026-08-02

**he_kg_granularity 统一默认 map_reduce**(代码/compose 三处对齐)。v1.9.8 引入 map_reduce 后,代码默认 `auto`、`prod_minimal.yml` `dataset`、`dev.override.yml` `map_reduce` 三处分歧。wuhu 等大数据集 dev.override 长期实证 map_reduce 稳定(18000+顶点/21600边)→ 统一默认为 `map_reduce`:① `config/rag.py` 默认 `auto`→`map_reduce`(顺带修正 v1.9.8 遗留的 "auto→chunk" 错注,实为 →map_reduce);② `prod_minimal.yml` `dataset`→`map_reduce`;③ 删 `dev.override.yml` 冗余覆盖(已成默认)。`auto` 分流逻辑保留(可显式选做小数据集对比)。builder.py docstring 同步。test_kg_builder 34 测试绿(全显式传 granularity,不依赖默认)。

## [1.9.11] - 2026-08-02

**GraphRAG 检索优化 + KG chunk 关联可视化**(对照 hugegraph/hyper-extract 技能 review)。① **retriever predicate 修复**:`/rag/query?use_kg=true` 三元组 predicate 从 `related_to_{label}`(丢语义)改为取边 `relation_type`——`client` 加 `get_vertex_edges`(REST `GET /graph/edges?vertex_id=`),retriever 建 neighbor_id→relation_type 映射,fallback `related_to_{label}`;② **retriever char-overlap fallback**:name 精确 miss 时(表述差异,如"市应急指挥中心" vs "应急指挥中心")按 entity snapshot 字符重叠(≥60%)命中,复刻 `_retrieve_hg_entities` 逻辑(零 embedding 依赖,避 KA FAISS 与 HugeGraph 实体错配);顺手修 `graph_rag` 漏传 `dataset_name` 的 latent bug(per-dataset 隔离失效);③ **kg.html chunk 关联展示**:后端 `kg_get_graph`+`KGGraphNode` 透传 `source_chunk`;前端按 source_chunk 算共现虚线边(同 chunk 抽取的实体视觉关联,per-chunk cap ≤8 防爆)+ hover 显示来源 chunk。+5 新测(test_kg_retriever #1 predicate + #2 char-overlap),33 核心测试绿。

## [1.9.10] - 2026-08-02

**KG 实体 source_chunk provenance 字段 — 关系可溯源**。v1.9.9 关系增强后图谱拓扑质量已达标(orphan 0.03),本版补"使用质量":实体/关系无来源 chunk,问答无法引原文、无法识别无源臆造。schema 加 `source_chunk`(SET TEXT,首个 SET cardinality property key,实体来源 chunk 列表)+ entity vertex 字段;`builder._insert_kg` 复用 references 边 owner 归并(`name2chunks`,per-chunk 用 owning_chunk_id、per-dataset/map_reduce 用 entity_chunks),零额外抽取成本;SET 空 list 时 omit key(避 HugeGraph SET 空 list 4xx,review HIGH fix)+ name2chunks 浅拷贝(MEDIUM)。wuhu 全量实证(1250 chunks):orphan 0.0315(≥v1.9.9 的 0.0336)/edges 12316/avg_degree 2.86,图拓扑不回归;source_chunk SET 注册+写入 live 验证成功(kg_kg_test_docs 11/11 + kg_wuhu_report)。**value 字段评估后砍**:原计划 value(指标/金额数值),wuhu 验证发现 hyper-extract AutoGraph node 固定 name/type/definition,value 无处抽取(非简单修);深反思认定 value 解决的"数值丢失"是伪命题(数值在 chunk 原文,RAG 已可达,无结构化数值查询需求则增量极小),post-process 成本不值——砍。+3 新测,顺带修 test_kg_insert_kg build_batch_size 陈旧 fixture;stash 验证零回归。详见 `docs/v1.9.10-kg-phase2-completion-plan.md`。

## [1.9.9] - 2026-08-02

**增强图谱顶点关系 — 关系软降级 + 启发式孤儿连接 + embedding 复用 + 模板 few-shot**。v1.9.8 的 type-pair 过滤器丢弃非法关系 → 端点孤立(orphan_rate 0.44)。本阶段:① 软降级——非法 type-pair 关系降级为「相关」(route→related_to,weight 0.4)而非丢弃,端点保连通;② 启发式孤儿连接(新 `orphan_linker.py`)——共现(同 chunk)+ embedding 余弦≥阈值 + type-pair 合法动词,把孤立顶点连到已连通实体,三重证据门控,零 LLM;③ embedding 复用——构建期算一次,消歧+连接共用;④ 模板 few-shot 降主体过判。wuhu 实证(qwen-plus, map_reduce):orphan_rate 0.4397→0.0336(−92%),entity↔entity 边 5104→11451(+124%),avg_degree 1.19→2.84,主体占比 28%→<6.6%。软降级回收 5054 条「相关」边为头号功臣。+24 新测。详见 `docs/v1.9.9-kg-relation-enhance-impl.md`。

## [1.9.8] - 2026-08-01

**map_reduce 图谱构建 + type-pair 过滤 + /kg/quality 量化**。解耦抽取与合并:per-chunk 并发抽取(MAP)→ streaming fold 全局精确名合并(SHUFFLE)→ entity_resolver 同义消歧 + type-pair 白名单过滤(REDUCE)→ 一次批量入库。兼得并发速度与全局合并质量,绕开 dataset path 串行 feed_text 卡死。配套健壮性:extract `asyncio.wait_for(120s)` 治 LLM 挂死、`_insert_kg` 批量(HugeGraph 2500/batch)、resolve embed/LLM timeout + 实体/簇 cap、`_apply_merge` O(n²)→O(n)、JSON checkpoint/resume(避 pickle RCE)。`auto` 大文件采纳 map_reduce;`he_extract_llm MAX_TOKENS=4096` 封顶(治 qwen 16k 胡言)。wuhu 实证:54min,12188v/20851e,16/16 合法关系动词,跨 chunk 关系连通。49 单测绿。详见 `docs/v1.9.8-phase1-mapreduce-impl.md`。

## [1.9.7] - 2026-07-30

**依赖升级(湖仓核心 + 安全)+ 结构化大表 web 性能(2026-07-31 追加)**。批0 uv sync 基线对齐(修复 uv.lock 停滞根因:gravitino pre-commit metadata bug)+ pylance 9.0.0;批1 ray 2.56.0 修复 CVE-2026-57516(代码注入)+ ray_serve get_deployment→get_deployment_handle 适配;批2 lancedb 0.36.0 + duckdb 1.5.5 + table_names→list_tables;批3 metaflow 2.19.35 + daft 0.7.21 + sentence-transformers 5.6.1 + encoder 适配。详见 `docs/v1.9.7-upgrade-plan.md`、`docs/v1.9.7-web-perf.md`。

### Added/Fixed — 结构化大表 web 性能(2026-07-31 追加,noaa_china 1070 万行)
noaa 详情页加载 ~50s → 翻页 0.27s。根因:kg/stats 对无 KG 数据集查 HugeGraph 6s + 预览 `COUNT(*)` 全扫(pyarrow_fallback 41s)+ `pyarrow_fallback` OFFSET 翻页不下推(每页 5.4s)。
- **OLAP scan_mode 安全默认 + per-dataset 自动加速**:`config/olap.py` 默认 auto→**pyarrow_fallback**(避免 IVF_PQ native panic);`olap.py:OlapSearchBridge._register_dataset` 新增 `_has_vector_column(source)`,**无向量列→auto**(native lance scan 下推 LIMIT/OFFSET,翻页 19×)、**有向量列→pyarrow_fallback**(RAG 安全)。**升级注意**:默认值变更,依赖 auto 的部署需显式设 env。
- **kg/stats 短路**:`client.py:get_stats` 图不存在→`{0,0}`(6s→0);`datasets.py:get_dataset` 补 `has_kg`(`_dataset_has_kg` O(1));前端 `has_kg=false` 不请求。
- **COUNT(*) 优化**:无搜索复用元数据 `num_rows`;有搜索探索式分页(LIKE COUNT 无法下推,~7s→跳过)。注:auto 下无 WHERE `COUNT(*)` 本身 **0.3s**(下推近 `count_rows` 元数据 0.001s),41s 是 pyarrow_fallback 旧值。
- **第一页缓存 + 后台预热**:翻页返回秒显 + `LIMIT 1` 预热 DuckDB session。
实测:kg/stats 6s→0 · 翻页 OFFSET 100 **5.4s→0.27s(19×)** · 翻页返回秒显。

### Changed — 依赖升级

### Changed — 依赖升级
- **批0**: `[tool.uv] override-dependencies` 修复 gravitino 1.3.0 `pre-commit==3.5.0` 与 dev group 冲突(uv.lock 长期停滞根因);pylance 4.0.1→**9.0.0**。
- **批1**: ray 2.54.1→**2.56.0**(CVE-2026-57516 代码注入);`ray_serve_encoder` `get_deployment`→`get_deployment_handle`(ray 2.56 Serve breaking)。
- **批2**: lancedb 0.33.0→**0.36.0** + duckdb 1.5.2→**1.5.5**;`_lake_ingest` `table_names`→`list_tables`(lancedb 0.36 deprecation)。核心 lance/lancedb 路径全量 5157 测试零回归。
- **批3**: metaflow 2.19.22→2.19.35, daft 0.7.8→0.7.21, sentence-transformers 5.4.0→5.6.1;`encoder` dim_getter 优先 `get_embedding_dimension`(消除 5.6 FutureWarning)。

### 已评估(未实施)
- **async FTS 迁移**(to_thread→AsyncTable):取消。lancedb 0.36 async search 要求 INVERTED index(项目 tantivy 不兼容)+ to_thread 已 non-blocking,收益微/障碍大。
- **FTS v2 评估**:`create_fts_index` deprecated 但未移除,新 `create_index(config=FTS())` 结果一致;INVERTED 路径 v1.7.1 已支持。无紧急迁移,无质量退化。

## [1.9.6] - 2026-07-28

**RAG 质量 + KG 质量/性能 + 治理兑现 + 架构 refactor + 安全加固**。批1 RAG 防幻觉/reranker/流式;批2 KG snap/strict/三路并行/缓存;批3 lineage.html 血缘可视化/masking 治理;架构评审 refactor(RAG/ingest 收口);安全加固(fail-closed + 注入防护)。详见 `docs/v1.9.6-impl-plan.md`、`docs/v1.9.6-batch3-impl-plan.md`。

### Added — 批3 治理兑现
- **P0-5 血缘可视化**: `console/lineage.html`(vis-network + max_nodes 截断 + 列级血缘);`trace_full_graph` max_nodes;`LineageEvent.column_lineage` 端到端打通(/history asdict)。
- **P0-6 masking 治理**: 4 函数(redact/hash/partial/nullify) + HMAC fail-fast(`ALLOW_MISSING_KEY` opt-in) + audit(复用 Lance) + mask-preview 端点 + governance.html 下拉/预览。

### Added — 批1 RAG 质量
- **P0-1 防幻觉**: faithfulness verify(embedding/LLM judge) + support_ratio/unsupported 标注。
- **P0-2 cross-encoder reranker**: bge-reranker-v2-m3 默认(连续分 + warmup)。
- **P1-9 流式补帧**: citations/latency/verification SSE 事件。

### Changed — 批2 KG + 架构 refactor
- **P0-3 KG snap/strict/enum**: 编辑距离归一化 + 空 definition 过滤 + enum 正则解析。
- **P0-4 GraphRAG 性能**: 三路 asyncio.gather 并行 + KA LRU(mtime 失效) + QuestionEntityCache monotonic。
- **架构评审 refactor**: #1 RAGQueryPlan+score 列;#4 `ingest_documents_and_index` 收口(parse→store→embed→FTS→vector);#6 GraphRAG 模板方法钩子;#7 reranker async 契约。

### Security — 安全加固
- masking HMAC fail-fast + `ALLOW_MISSING_KEY` opt-in;hash 128 位([:32])。
- rbac `_apply_masking`/`_apply_row_filter`/`_fetch_rules` 全 fail-closed(空表/raise)。
- mask-preview 列名 SQL 注入修复 + ADMIN 收紧(防绕 column ACL)。
- lineage XSS esc(vis title + DOT/Mermaid 渲染转义 + /record 校验)。
- Gravitino 写端点 POST body 去 `?body=` query param(PII 不入 URL 日志)。

## [1.9.5] - 2026-07-2X
RAG 问答质量(default_retrieval_strategy 死配置修复/hybrid 真生效/ingest 自动建索引/use_kg per-query/GraphRAG 延迟优化 extract_llm=qwen-turbo/qwen-plus@16384)。详见 `docs/v1.9.5-rag-quality-plan.md`。

## [1.9.4] - 2026-07-2X
血缘/审计埋点评审 + KG MERGE_FIELD(非 LLM 字段合并,治 grouped 合并爆炸) + project_concept_graph 模板(22 类型)。详见 `docs/v1.9.4-lineage-provenance-audit.md`。

## [1.9.3] - 2026-07-2X
数据集字段注释(field_comments: PyArrow/CSV sidecar 直读 + _write_table 钩子 + DB 捕获 + storage 原位写 + console chip 编辑)。详见 `docs/v1.9.3-dataset-field-comments-plan.md`。

## [1.9.2] - 2026-07-23

**console 完备化（运维/合规/治理）+ 质量深化**。把 console 从"数据智能 + 管理"扩展到覆盖全部 20 routers 的完整数据平台，配套清 v1.9.1 既有债（测试隔离 / KG 模板 / kg_build GC）。详见 `docs/v1.9.2-impl-plan.md`、`docs/v1.9.2-roadmap.md`。

### Added — console 完备化
- **运维**：`system.html`（health / version / DuckDB 会话池 / gravitino / lance-rest / metrics 摘要 + `maintenance` 子区）。
- **合规**：`audit.html`（审计事件列表，user/action/dataset/时间 过滤 + 导出）。
- **治理**：`governance.html`（Gravitino metalake/catalog/schema 浏览）+ dataset-detail 血缘 Tab / 版本 Tab（backup + Lance 版本）。
- **kg.html 工作台**：Schema·图遍历合并、起点实体可搜索 combobox、图前 3000 节点。
- **admin**：用户分页 + ACL / deny 管理。
- **olap 分析工作台增强**：导出下拉、纯 SVG 图表、列统计、SUMMARIZE 子查询、Pivot 助手（走 DuckDB PIVOT，绕开 Daft 0.7.8 pivot bug）；DuckLake MV 面板（独立 router 避路由冲突，`ducklake_enabled=False`→503 门控）。
- **多模态 + 导出**：search 以图搜图（`/embed/image` + IVF_PQ）+ 全局导出统一（export.js 接 datasets/detail/search）。

### Changed
- **gravitino router 加 `/api/v1` prefix**（实测裸 `/metadata/*`→404）。
- **rate_limit + login lockout 迁 Redis**：per-(username, ip) 失败计数 + 锁定窗口，多 worker 一致、fail-open（Redis 不可达放行避免锁死）；v1.9.1 前为单进程内存态（分布式可被并行撞库绕过）。

### Fixed
- **kg_build fire-and-forget 持强引用**：`asyncio.create_task` 未存强引用被 GC 静默杀 → 大 dataset build 卡死的真凶；现模块级 set 持有强引用。
- **audit_query `asdict` 序列化**：替代 `str(e)` repr → 修 audit.html 界面空。
- **conftest autouse 全局清理 fixture**：治全量测试隔离污染（单跑全过、全量不稳定）。
- **KG 模板收紧 + CI 校验**：concept_graph 固定 type 枚举 + 必填 description + relation snap。
- **olap 列统计 std 改样本标准差**（÷N-1，对齐 DuckDB）；Parquet 导出不传结果列（治聚合查询导出失败）。

### 测试
- 全量回归零失败；console 页 playwright 验证 0 error。


## [1.9.1] - 2026-07-23

**console 核心界面 + 安全加固 + 数据准备**。原生 JS + ES module 前端落地，admin 全功能 + my-workspace；personal token 鉴权通路打通。详见 `docs/v1.9.1-frontend-core-impl-plan.md`。

### Added — console 核心
- **admin.html 全功能**：用户 CRUD（pbkdf2 密码）/ 角色目录 / `DatasetACL`（visible_columns + row_filter）/ `SchemaACL` / deny 管理。
- **my-workspace 5 区**：saved-queries / notifications / preferences / dashboards / favorites。
- **personal_token 鉴权**：admin `POST /admin/users/{id}/tokens` 签发，请求带 `X-API-Key`；`/api/v1/me/*` 硬约束必须 personal token（JWT/api_key 不可调）。
- **数据准备**：data-prep（MinHash 近似去重 + llm_enrich）+ `tidy.html` 清洗整理（`POST /datasets/{n}/clean`，DuckDB 语义 steps→SQL→`restore_dataset` 写回）。

### Changed
- **dev.override.yml 联调热重载**：挂 `arrow_lake/` 源码 + `console/` bind-mount + uvicorn `--reload` + `PYTHONPATH=/app`，改 Python/前端秒级生效免 rebuild（须 `--force-recreate`）。


## [1.9.0] - 2026-07-17

**Turso/libSQL 统一控制面库**。引入 libSQL 作控制面统一基础数据库，接管需事务/关系/持久一致的系统级结构化数据，数据面(Lance/DuckDB/HugeGraph/MinIO)完全不动。默认 `system_db.enabled=false` 渐进 opt-in。详见 `docs/v1.9.0-turso-system-db-plan.md`。

### Added — 控制面库 (`arrow_lake/system_db/`)
- **SystemDB** 单例(libsql + 连接重试 + health probe + 写 RLock)+ 轻量 **migration runner**(V001-V004 顺序 SQL，无 alembic)。
- **10 个 store**：RbacStore / IdentityStore(personal_tokens) / CatalogStore / TaskHistoryStore / IngestDLQStore / RagSessionStore / LineageIndexStore / GovernanceStore / UserStateStore + TTLCache + FailMode。
- **P0 RBAC + 身份持久化**：PermissionChecker 持可选 RbacStore（有则路由、无则原内存字典 fallback，零行为变化）；`users` + `personal_tokens`（al_ 前缀 / sha256 / hmac.compare_digest / 撤销 / 过期）；auth 中间件懒解析 personal_token，miss 回落全局 api_key（bootstrap 逃生通道）。
- **P1 catalog/任务/DLQ/RAG**：TaskManager 加法式历史持久化（完成/失败→`task_history`，get_task 超 Redis 2h TTL 回退历史）；IngestDeadLetterQueue / SessionStore DI-ready（store 优先 + 原 fallback）。
- **P2 血缘索引 + 治理**：LineageIndexStore 接入 `LineageStore.record_event`（fail-backfill，Lance 仍 SoT）；GovernanceStore（schema_changelog / maintenance_runs / schedules / config_changelog）。
- **P3 用户态**：UserStateStore（saved_queries / dashboards / favorites / user_preferences / notifications）；admin `/users` 接 IdentityStore（不再"未实现"）；新路由 `/api/v1/me/*`（按 personal_token user_id 鉴权）。
- **compose `system-db` 服务**（`ghcr.io/tursodatabase/libsql-server`）+ 卷 + api depends_on + `ARROW_LAKE__SYSTEM_DB__*` env。pyproject 加 `libsql>=0.1`。

### Changed — domain 接线（全链路真生效）
- **血缘全写入路径**：Lake 全部写入经 `_lineage_after_ingest` helper 记血缘 —— 12 ingest 变体 + create_dataset + append_dataset（此前 lineage 全仓零调用，从未记录）。
- **治理域**：maintenance_scheduler 记 `maintenance_runs`；`_storage_advanced` 的 add_column/add_columns_table/alter_column/drop_column 记 `schema_changelog`（此前 schema 变更无落库）。
- **RAG session 注入 RAGPipeline**（`_lake_rag._build_session_store`）：RAG 对话跨重启持久激活。

### Fixed — review 修复
- **P1 [HIGH] `validate_token` last_used_at 节流**：此前每次有效 token 鉴权同步写 DB → 高频 API 经单写串行成瓶颈；现仅当陈旧 >60s 才更新（读已在同一 SELECT）。
- **P2 [MEDIUM] 血缘记录异步化**：`_lineage_after_ingest` 入有界队列（10000）+ daemon worker，此前同步 Lance append 阻塞 ingest（批量 ingest 显著延迟）；满则丢（可从 Lance 重建）。
- **stdlib logging 不吃 structlog 风格 kwargs** → 新代码全用 structlog（项目约定）。

### Security
- review 确认：SQL 注入（全 `?` 参数化）/ user_state IDOR（全 `WHERE user_id=?` + 可信中间件）/ password_hash 泄露（SELECT 排除）/ fail-close（tokens-only 部署 sqld 宕机→401；混合部署→api_key + default_role 特权下降）全过。线程安全实测 libsql 内部串行化（8 线程并发 0 错误）。

### 测试
- system_db 包 67 新测（connection/migrator/9 store + 集成 + 降级 + review 修复）；全量回归零失败（system_db + rbac + auth）。

### Deferred（低价值/高风险/需新功能）
- CatalogActor（Ray，prod_minimal 砍 Ray 故休眠）；lineage_hooks（`auto_record_ingest` 已被 facade 路径替代）；Gravitino 对账表；CDC 缓存失效；多副本。


## [1.8.9] - 2026-07-16

自 v1.8.8 以来 17 commits / 46 文件（+2258/−233）。详见 `docs/arrow-lake-v1.8.9-release-zh.md`。

### Added
- **RAG `OllamaReranker`**（`rag/reranker.py`）：Qwen3-Reranker yes/no judge；设为**默认 reranker**（`RAGConfig.reranker="ollama"`，`reranker_model="dengcao/Qwen3-Reranker-0.6B:F16"`）。此前 `_lake_rag` 未传 reranker → 恒 `Noop`。
- **KG 双阶段 LLM**：`HugeGraphConfig.he_extract_llm`（抽取，默认 ministral）/ `he_qa_llm`（问答，默认 deepseek-v3@百炼）独立配置。
- **增量 KA/KG**：`build_dataset_ka` 增量模式（fed_chunks 内容哈希 sidecar，只喂新 chunk）；REST + CLI `--incremental` 暴露；KA 版本管理（archive/list/rollback/prune）。
- **`/ingest/documents` 多格式 + append**：放开全部 kreuzberg 文档类型（非仅 PDF）+ 追加到已存数据集。

### Changed
- **KG 默认模板改 strict**：default/paper/report 指向项目本地 `concept_graph.yaml`（type/relation 枚举 + definition required），弃 gallery `general/concept_graph` 自由类型。实测定义覆盖 **0%→100%**、类型 80+→干净枚举。
- **`max_tokens` 走 config**：`he_extractor._build_client` 用 `cfg.max_tokens`（原硬编码 8192 致 `ARROW_LAKE__LLM__MAX_TOKENS` env 对 KG 抽取失效）。
- **docling `DocumentConverter` 进程级单例**（按 config 签名 key + 每 converter RLock）：避免每请求重载模型 10-30s。
- **IVF `nprobes` clamp**：`_resolve_nprobes` 限幅到 `[1, min(max_nprobes, num_partitions)]`，`max_nprobes` 配置生效（原为死配置）。

### Fixed
- **P0 stderr 永久泄漏**（`_suppress_tesseract_noise` dup2 no-op 致首解析后 stderr 永久→/dev/null）。
- **P0 type-enum 竞态**（`_current_type_enum` 在 gather 下被并发覆盖 → 局部化）。
- **reranker 三连缺陷**：死配置 / LLMReranker async-sync 错配崩溃 / `_parse_score` 反转；+ SSRF scheme 校验 + prompt-injection 过滤。
- **append 漏刷新派生结构**：FTS jieba 分词 + OLAP/facets 查询缓存 ingest 失效；FTS `_has_null_segmented` 兼容 LanceDB Table API。
- **内容哈希**：doc_id 用内容哈希（非路径）+ fed_chunks 内容哈希增量 + 解析内容哈希 LRU 缓存。
- **`feed_text` 退避重试**：防大语料 LLM 瞬时失败静默丢 chunk。
- **向量 SQL `query_vector` finite-float 校验**（闭裸插值）。
- **KA 归档跳过可重建的 `index/`**（省盘；查询路径 `_ensure_ka_index` 缺失自重建）。

### Removed
- **`_normalize_type` 死代码**（生产从未调用；dict 仅英文 key 会塌缩中文 type）。


## [1.8.8] - 2026-07-13

### Added
- **KG per-dataset KA 抽取**：dataset 下所有 chunk `feed_text` 进同一 KA，激活跨 chunk 合并/去重/悬空边裁剪；KA 产物落盘 `<base>/<ds>/ka/`。详见 `docs/v1.8.8-kg-per-dataset-ka-plan.md`。
- **KG doc_type 三层路由强化 + hyper-extract 模板暴露**（REST `list-doc-types`/`list-templates`/`describe-template`）；`he_kg_granularity`（dataset/chunk）。
- **per-dataset 动态图 `kg_{ds}` 隔离**深化 + IDOR ACL gate。

### Fixed
- 容器 E2E 四连 bug（compose ollama 端口 / `he_ka_base_dir` 只读卷 / encoder base_url 致 NO_PROXY 失效 / KA metadata stem）。


## [1.8.7] - 2026-07-10

### Added
- **Docling 全栈**：docling 库内嵌替代 kreuzberg（多格式 + RapidOCR 中文/EasyOCR 多语言），P0/P1/P2 全完成；详见 `docs/docling-ocr-migration-adr.md`。
- **Console SQL Worksheet**（Web UI）：DuckDB SQL 走 `/query/olap` 复用 RBAC。
- **旗舰展示前端**（`console/showcase.html`）：架构全景五层 + 三王牌检索宇宙/湖仓时间机器/KG 探索。
- **KG 加固**：HugeGraph 写入吞吐优化（rocksdb 参数 + 退避）+ gremlin 遍历源绑定修复。

详见 `docs/arrow-lake-v1.8.7-release-zh.md`。


## [1.8.6] - 2026-06-30

### Added

- **按 lake 路径分图隔离（per-dataset HugeGraph isolation）**：每个 Lance dataset 映射到**独立 HugeGraph 图** `kg_{dataset}`（此前所有 KG 数据写入单个 `hugegraph` 图，仅靠 `document_name` 属性区分来源 → 无逻辑隔离、删 dataset 不清图、单图索引随数据量线性膨胀）。新增 `_naming.py` graph_name 派生（含合法化 + 单测 15 例）；client 层 `graph_name` 参数化 + REST `find_vertices_by_property` + `drop_graph`（DELETE 端点 + 就绪竞态 `_wait_graph_ready`）。
- **facade 全 traverser 按 dataset 图隔离**：8 个 traverser（kneighbor/kout/shortest_paths 等）+ stats/neighbors/graph_exists/ensure/delete 加可选 `dataset_name`（缺省回退默认图，backward compat）；新增 `kg_drop_graph`。
- **builder 自动隔离**：`_execute_build` 派生 graph_name 并透传全部写入点；`kg_build` 按 dataset 自动落到独立图，调用方无感。
- **删 dataset = 删图（drop-on-delete hook）**：`_lake_admin.delete_dataset` 接 `_drop_dataset_kg_graph_best_effort`（sync/async 双上下文桥接），删 Lance dataset 时 best-effort 清理对应 KG 图，杜绝残留。
- **迁移脚本** `scripts/migrate-kg-per-dataset.py`（idempotent，把存量单图数据按 dataset 拆迁到各自 `kg_{dataset}` 图）。
- **CLI `--dataset` 便利项**：`kg stats` / `kg neighbors` + 4 traverser（all-shortest-paths / weighted / single-source / multi-node）加 `--dataset` flag。

### Changed

- **retriever 检索适配**：`retrieve()` 加 `dataset_name`，透传 graph_name 到 `get_vertex` + `traverser_kneighbor`（实测已用 REST 构造 ID，非 gremlin find_entity）。
- **`queries.py` `find_entity` 标记 deprecated**（功能由 client `find_vertices_by_property` 接替）。

### Notes

- Phase 1（分图核心）+ Phase 2（检索适配 + 删除 hook + 迁移）均完成并合入 master；420+ kg 单元测试绿，无回归；live 两图隔离性（ga=1/gb=0）+ drop 验证通过。
- 剩余 **非阻塞 minor**（留作后续）：rays/rings/crosspoints/customized traverser 的 CLI flag + API router dataset query param。facade 8 traverser 已全部支持 `dataset_name`，此处仅便利暴露。详见 `docs/v1.8.6-per-dataset-kg-isolation-plan.md` §12。


## [1.8.5] - 2026-06-30

### Fixed

- **文件上传端点 500（boto3/botocore 版本错配）**：`BlobStoreManager.__init__` 的 `import boto3` 抛 `ImportError: cannot import name 'DocumentModifiedShape' from 'botocore.docs.utils'` → presigned/multipart 上传端点全 500 → 客户端 `_ingest_via_upload` 回退到「传路径」（容器读不到宿主机文件 → `INGEST_FILE_NOT_FOUND`）。根因：Dockerfile 分两步 `uv pip install`，第二步装 modelscope 时 aiobotocore 要求 `botocore<1.43.1`，把 botocore 降到 1.43.0 但 boto3 仍 1.43.36 → 环境不一致。修：第二步 install 钉死匹配对 `boto3==1.43.0 botocore==1.43.0`（同时满足 boto3 自身与 aiobotocore）。实测镜像内 `import boto3`+`BlobStoreManager` OK，multipart 代理上传成功，cookbook examples_api 11/16/19/21–28 共 11 个此前失败的示例全部转绿。

---

## [1.8.4] - 2026-06-30

### Fixed

- **Ray readiness 探针误报 `unreachable`**：`/health/ready` 的 `_check_ray` 此前用 `ray.init(address=...)`——在 API 容器里既重（起 driver）又会因 `ray_address="auto"`（API 默认值，prod 未覆盖）找不到本地 Ray 而恒失败，导致 Ray head 明明 healthy 却报 unreachable。修：`_check_ray` 改为对 Ray dashboard 做轻量 HTTP GET `/api/version`（无副作用、不走外网代理）；新增 `ComputeConfig.ray_dashboard_url`（默认空=跳过探针），prod compose API 显式设 `ARROW_LAKE__COMPUTE__RAY_DASHBOARD_URL=http://ray-head:8265`。非 fatal，不影响 readiness 总体判定。`ray_address` 语义/计算路径未动（`server.py` 对 `"auto"` 的跳过逻辑保留）。

---

## [1.8.3] - 2026-06-29

### Fixed

- **启动慢 + 生产 HA：readiness 探针此前不反映真实就绪状态**。`/health` 与 `/health/ready` 只 gate 存储，不查 Lake/DuckDB session manager 是否初始化完成；且 prod compose healthcheck 仅 grep `"status"` 键是否存在（`degraded` 也判健康）。修：lifespan 加 `app.state.ready` 标志（required setup 完成才置 True，shutdown 复位），两探针在未就绪时返 503 `"starting"`，`/health/live` 保持纯 liveness；compose healthcheck 改为断言 `"status":"ok"`，`start_period` 60s→120s。这是任何「启动后异步初始化」能安全进行的硬前提。
- **DuckDB warmup 阻塞启动**：warmup（建 2 个连接 + 扩展 install/load，首启含下载）此前在 lifespan 同步执行、每 worker 各跑一遍。修：`Lake.get_session_manager(skip_warmup=True)` 同步建好 manager，warmup 移到后台 daemon 线程；连接池本就按需懒建，readiness 不再等 warmup。同时移除 lifespan 里两个冗余启动探针（`_check_storage_connectivity`/`_check_duckdb_extensions`，与 readiness 探针/warmup 重复，首启省 1 次扩展下载 + ≤5s）。
- **fileset 注册每轮重复 POST + rc2 冲突 400 误判**：`GravitinoBridge.register_dataset` 此前每个同步周期对每个 dataset 无条件 POST create-fileset，靠 409 判存在；rc2 server 对冲突返 400（不在 `{409,404}` 吞集）→ 被误判为 "exists" 且永不收敛、spam error 日志。修：新增 `_fileset_exists()`（GET fileset load 端点）+ `self._filesets` 缓存，先查存在再 POST；create 真失败时如实记 `register_failed` 而非 "exists"。table 路径用标准 409，未受影响。
- **Gravitino client 版本漂移**：`apache-gravitino>=1.2.1`（pyproject + Dockerfile:64 独立 `uv pip install`）无上界，部署构建时拉取最新版可能偏离 server 1.2.1-rc2。修：钉死 `==1.2.1`（与 server 同 minor，即当前已装版本）。
- **stale 测试**：`test_lake_extras_v18.py::TestDaftFromGravitino` 仍断言 v1.8.1 重构前的旧字段 `GravitinoConfig(url=,metalake=)`，对齐为 `endpoint=/metalake_name=`。

### Summary

v1.8.3 是面向生产高可用的启动性能 + 正确性修复：先补 readiness gate（此前坏掉，半初始化实例会被灌流量），再把 DuckDB warmup 移后台并去冗余探针以加速启动，顺手修 fileset 注册的重复 POST/400 误判与 Gravitino client 版本漂移。受影响测试套件全绿。

---

## [1.8.2] - 2026-06-29

### Fixed

- **export download 跨 worker 500**：`BackgroundTask.to_dict` 未序列化 `output_path`/`fmt`，`from_dict` 未还原 → 多 worker 部署下跨 worker 的 download 请求从 Redis 取回 task 时 `output_path=""` → `FilePath("").resolve()` 异常路径 → 500。修：`to_dict`/`from_dict` 补全两字段 round-trip。同 worker 读本地内存有值不受影响，故仅跨 worker download 受损。round-trip 单元实测通过。

---

## [1.8.1] - 2026-06-29

### Fixed

- **`write_lance_from_dataframe`（#16）写入丢失**：Daft `df.write_lance(...)` 是 lazy sink，原调用丢弃返回值、未 `.collect()` → 写入从不执行；且写入路径用 `_get_dataset_path`（无 `.lance` 后缀）与 lancedb 表名解析（`<name>.lance`）错配 → 即使写入也读不回。修：`.collect()` 触发执行 + 改用 `_lance_dir(name)`（带 `.lance`）作为写入 URI。现已 `list_datasets` 可见、`read_dataset` 可读。
- **`daft_from_gravitino`（#14）TypeError**：facade 把 `url=` / `metalake=` 传给 `daft.io.GravitinoConfig`，而该 PyO3 类字段为 `endpoint` / `metalake_name` → `TypeError: unexpected keyword argument 'url'`。修：`GravitinoConfig(endpoint=url, metalake_name=metalake)`。
- **cookbook VLM 示例 spec 格式**：`build_transforms` 每个 spec 须 `{"op": "decode_image", "column": ...}`（含 `op` 键），非 `{"decode_image": {...}}`。

### Summary

v1.8.1 是 v1.8.0 的 bug 修复小版本：修两个 v1.8.0 cookbook 实跑暴露的 facade bug（Daft 流式写丢失、Daft↔Gravitino 配置字段名）+ 一个示例 spec 格式问题。三项均经本地实测验证。

---

## [1.8.0] - 2026-06-29

### Summary

v1.8.0 稳定版：roadmap **19 项全部落地**（三批 + 9 项收尾），覆盖**检索精度**（Reranker 精排、CLIP 跨模态）、**数据治理**（Lance tags/branches、DuckLake 物化、DuckDB 轻图查询、blob 存储、行级 lineage、Gravitino 统一 catalog facade）、**性能并发**（Daft 内置 AI 函数、全链路 async、Daft 流式写 >16× 内存）、**多模态与联邦**（VLM decode_image、Daft↔Gravitino、DuckDB 原生 FTS、`hf://` 数据集、日文分词）；含 1 项生产 Review CRITICAL 修复（`/embed/image` 死链）；压测 gate 裁决分布式索引 / ColBERT 为 DEFER。lancedb 0.33 + pylance 7.0 + DuckDB 1.5.2 全栈升级。全量 5000+ 测试零失败。

### Roadmap 收尾（9 项独立条目，2026-06-26）

### Added

- **#4 FTS 日文分词**（`_chinese_tokenizer.py`）：加 lindera（日文假名）路由 + 模块级缓存（字典加载贵）+ `except Exception` 加宽。可选分词器，未装优雅降级。
- **#2 Blob 存储**（`_lake_admin.add_blob_column`）：原地存 image/audio/video bytes 为 Lance binary 列（经 `add_columns_table`），多模态原文 + 嵌入同库。
- **#8 hf:// 数据集**（`_lake_ingest.load_hf_dataset`）：lancedb `hf://` scheme 读 HF Lance-format 数据集（评测 / 种子），URI 自动前缀，空表 raise。
- **#12 DuckDB 原生 FTS**（`OlapSearchBridge.fts_search`）：DuckDB `fts` 扩展 BM25（PRAGMA create_fts_index + match_bm25）作 lance_fts 备选 / 对比；物化 temp table 规避 view 限制。`vss` 扩展此 build 不可用（同 pgq）。
- **#16 Daft 流式写**（`_lake_ingest.write_dataframe`）：暴露 `write_lance_from_dataframe`（Daft lazy，>16x 内存），KG build / 大批量 ingest 用。
- **#3 行级 lineage**（`_lake_lineage.lineage_record_row`）：Lance row_id 行级溯源（level=row + row_id + source_rows metadata，经现有 lineage store/query/graph）。
- **#19 Gravitino 统一 catalog facade**（`_lake_admin`）：`gravitino_register/deregister_dataset` / `sync_inbound` / `table_statistics` / `health` —— 三引擎（DuckDB/Daft/lancedb）经 Gravitino 统一 catalog（bridge 已齐全，补 facade 暴露）。
- **#14 Daft↔Gravitino**（`_lake_query.daft_from_gravitino`）：`daft.io.GravitinoConfig` 直连 Gravitino 表，联邦查询不经 DuckDB 转译（补 #19 的 Daft 侧）。
- **#18 VLM decode_image**（`transforms._build_decode_image`）：Daft `decode_image` builder，补全 VLM 链（image bytes → 解码 → classify_image/prompt），注册到 `build_transforms`。

### Re-assessed（gate DEFER）

- **#15 分布式索引** / **#7 ColBERT**：经压测 gate（`test_bench_batch3_gates.py` 1M 实测 + recall 难度 sweep）DEFER —— 单节点 21s/1M（1B+ 才需 Ray）；现实 recall 96%（病态下降是 IVF_PQ 量化，修法 HNSW，非 ColBERT 场景）。

### 生产 Review 修复（2026-06-26）

### Fixed

- **CRITICAL `/api/v1/embed/image` 死链**：`ImageEmbeddingResult` 加 `table` 字段 + `encode()` 重赋 `table = table.append_column(...)`（PyArrow Table 不可变，原返回值丢弃致计算出的向量整体丢失）+ endpoint 改读 `result.table`（原 `result.column_names`/`result.column()` 属性不存在）；`_make_vector` 的 `dim` 改取首个非 None 嵌入（原 `embeddings[0]` 为 None 时 dim 塌缩 0 → 静默畸形输出）。新增非 mock 回归断言（`result.table` 含嵌入列）守卫。
- **#10 `graph_query`**：`start_node=None` 显式 `QueryError` guard（原抛 DuckDB 晦涩 Binder 错）+ empty-edges / 无出边 边界测试。

### Added（端到端暴露补全）

- **facade**：`lake.graph_query()`（`_lake_query`，#10）、`lake.encode_text_clip()`（`_lake_search`，#6 跨模态 text→image）、`lake.create_branch/list_branches/delete_branch/read_at_branch`（`_lake_admin`，#1 branches 原 SDK-only）。
- **REST**：`POST /{name}/query/graph`（`routers/query.py` + `GraphQueryRequest` 模型，复用 `OlapQueryResponse`）、`POST /embed/clip-text`（`routers/embedding.py` + `ClipTextEmbedRequest`，复用 `EmbeddingResponse`）。

### Re-assessed（非缺陷，诚实记录）

- **#17 REST async**：REST `/search` 已用 `await run_sync(...)`（线程卸载 = 事件循环非阻塞），与新增的 `*_async` facade（`asyncio.to_thread`）功能等价；`*_async` 服务**直接 async 调用方**，REST 无需改（改了也是 no-op）。

### 第三批 🟨（压测驱动 gate 框架）

### Added

- **第三批 gate 框架**（`tests/benchmark/test_bench_batch3_gates.py`）：复用 `BenchmarkReport`，为 #17/#15/#7 三个「压测驱动」项产出数据驱动的 go/no-go。三组 gate：
  - **#17 async gate**：ThreadPool 并发查询 QPS × worker sweep（1/5/10/20），平台期检测。
  - **#15 分布式索引 gate**：`create_index` 构建时长 × 规模（10k/100k），投影单节点天花板。
  - **#7 ColBERT gate**：单向量 ANN recall@k vs 簇结构 ground truth + brute-force 基线（填补现有 `test_bench_quality` 只测 QualityFilter 的召回空白）。
- **#17 async 接口（压测驱动 GO 后实现）**：`FullTextSearchBridge.search_async` / `HybridSearchBridge.search_async` / `FacetedSearchBridge.search_async`（`asyncio.to_thread` 非阻塞包装）+ facade `text_search_async` / `hybrid_search_async` / `faceted_search_async`。lancedb 无原生 async FTS/聚合路径，故这些是线程卸载（非 GIL-free），价值 = async handler 不阻塞事件循环（vector 仍是原生 `search_async`）。测试 `tests/unit/query/test_async_bridges.py`（5 tests）。

### 压测结论（2026-06-26，本机 WSL2）

- **#17 async → ✅ GO → 已实现**：并发平台期显著——worker 1→20（20x），QPS 仅 5.8→7.2（1.24x）；已给 fts/hybrid/faceted 补 async 包装 + facade 暴露。
- **#15 分布式索引 → ⏸ DEFER**：1M 索引构建 ≈ 150s（10k 1.98s / 100k 14.43s 投影），单节点在 ~10M 行内充裕；100M+ 才需 Ray 分布式 backfill（`ray_runtime` 基建已就绪待触发）。
- **#7 ColBERT → ⏸ DEFER**：合成簇结构数据 recall@50 = 1.000（ANN vs brute-force 100% retention），无召回缺口；框架已就位，待真实细粒度语义数据复测。

### 第二批

### Added

- **#10 SQL-PGQ 轻图查询**（`arrow_lake/query/olap.py`）：`OlapSearchBridge.graph_query(edges_dataset, start_node, ...)` 用 DuckDB **递归 CTE** 做环安全 BFS 邻居/路径遍历，返回 `depth / node / path`（可选 `cost`）。PGQ（`CREATE PROPERTY GRAPH` / `MATCH`）在此 DuckDB 1.5.2 build 不可用（`pgq` 扩展无法安装，`CREATE PROPERTY GRAPH` 抛 ParserException），递归 CTE 零扩展依赖达成等价轻量图查询，与 HugeGraph 互补（重图→HG，轻查询→DuckDB）。`max_depth` 钳制 [1,10] 防 runaway，`list_contains` 环检测，支持 directed/undirected + 可选权重列。测试 `tests/unit/query/test_olap_graph.py`（11 tests，真 DuckDB 执行）。
- **#6 CLIP 跨模态 text→image**（`arrow_lake/embed/image_encoder.py`）：`CLIPImageEncoder.encode_text(texts)` 用 CLIP/SigLIP **text tower** 把文本查询编入与 image 相同的嵌入空间，补全跨模态检索缺失的一半（`encode()` 编图、`encode_text()` 编查询 → `lake.search(ds, vec, vector_column="image_embedding")`）。L2 归一化、tokenizer 懒加载缓存、模块级 `AutoTokenizer` 可 patch。cookbook `05_image_video_ingest.py` STEP 4 演示 text→image 跨模态（无模型/无图优雅跳过）。

### Changed

- **#9 DuckLake 物化视图**：经核实 `materialize()` / `cleanup_materialized()` **已实现于 `olap.py`**（非 implementation doc 误标的 `federated_engine.py`），经 `DuckLakeWorkspace` 做 TTL 持久化 + ART index + 行预算。本批仅验证（29 tests 绿）+ 标记 ✅。
- **#11 Prepared statements**：经核实 DuckLake 元数据表 INSERT/SELECT/DELETE **已全用 `$1..$4` 参数化执行**（`ducklake_workspace.py`）。DuckDB `EXECUTE` 不支持绑定参数（binder 限制），故 PREPARE/EXECUTE 与安全参数绑定不兼容；参数化执行是正确方案（DuckDB 自动缓存计划）。新增回归测试 `tests/unit/duckdb/test_ducklake_prepared.py`（4 tests）守卫参数绑定不变量。

### 第一批

### Added

- **#5 Reranker 接入 hybrid search**（`arrow_lake/query/hybrid.py`）：RRF 粗排后接 cross-encoder 精排。`HybridSearchConfig` 新增 `reranker_type`(默认 `none`,向后兼容)/`reranker_model`(默认 `BAAI/bge-reranker-v2-m3`);`HybridSearchBridge` 懒加载 `rag/reranker.py` 的 reranker,`search()` 末尾 `_rerank_table` 把结果行转 `ContextChunk` → rerank → `take` 重排 + 追加 `_rerank_score` 列。缺 text 列 / rerank 异常时优雅降级返回原表(facade config 驱动,无需改 ingest/查询路径)。
- **#13 Daft AI 函数端到端补全**：`LocalEmbeddingEncoder.encode_to_vectors`（对称 `DaftBatchEncoder`，返回向量矩阵）、`DaftBatchEncoder` 加 `expected_dim` 维度校验 + L2 归一化 + `encode(list[str])` 满足 `EmbeddingEncoderProtocol`、`/embed/text` REST 端点 DAFT 分支、PoC benchmark (`examples/benchmark_embed_daft_vs_local.py`)。实测 Daft `embed_text(provider="transformers")` vs Local `SentenceTransformer`：cosine=1.0、dim 1024、speedup 1.14x、调度代码删减 ~120 行。

### Fixed

- `Lake.embed_and_add` LOCAL 分支 `result.embeddings.tolist()` AttributeError（`EmbeddingResult` 无 `embeddings` 字段）—— 改用新增的 `LocalEmbeddingEncoder.encode_to_vectors`。

## [1.7.1] - 2026-06-25

### Summary

v1.7.1 是 Lance/LanceDB/DuckDB 技术栈的深度调优版本：lancedb 0.30.2→0.33.0、pylance 6.x→7.0.0、DuckDB 1.5.2（已最新线）三件升级 + 存储引擎调优 + 标量索引全量补齐 + 向量原生 async 入口，全部 11 项（#1–#11）完成并运行时验证（5005 passed / 0 failed），镜像 `arrow-lake:1.7.1` 发布。

### Added

- **lancedb 0.33.0 + pylance 7.0.0 升级**：验证 pylance 7.0 写入的 Lance 新格式与 DuckDB core `lance` 扩展（随 1.5.2 绑定）向后兼容——`lance_scan` / `vector_search` / `fts` 40+ 处调用全通。
- **存储引擎调优（纯 compose，零 Python）**：`LANCE_IO_THREADS=64` / `LANCE_CPU_THREADS=4` 注入 `x-storage-env` anchor → api / ray-head / ray-worker / ray-gpu-worker 4 服务继承；DuckDB `max_query_memory_mb` 512→1024 + `API_MEMORY_LIMIT` 4G→8G（4 workers × 4 并发 × 512MB 已超 4G）。
- **标量索引全量补齐**：`create_scalar_index` / `create_facet_indexes`（facet 列 modality / source / doc_type / created_at），SDK prefilter 路径补齐（DuckDB `lance_vector_search` 无 filter 参数，由标量索引 + SDK prefilter 覆盖）。
- **`search_async` 增量入口**（#9）：向量原生 `connect_async` 异步检索，连接池 + 压测验证后上高并发。
- **`use_inverted` 实验选项**（#11）：lance 原生 INVERTED index 替代 legacy `create_fts_index`，搜索兼容性按数据集验证。
- **回归 API 表面**：`create_scalar_index` / `create_facet_indexes` / `search_async` 暴露完整契约；cookbook 对齐 + README 服务端点；镜像 `arrow-lake:1.7.1` + tag `v1.7.1` 推送 Gitee。

### Changed

- DuckDB 内存预算校验（`OlapConfig.validate_memory_budget()`）在创建 session manager 前拦截超配。

---

## [1.7.0] - 2026-06-24

### Summary

v1.7.0 引入 hyper-extract KG 抽取后端 + 文档类型路由 + HugeGraph PD 集群模式（运行时多图），并完成生产就绪化修复与镜像重建部署。

### Added

- **HugeGraph PD 集群模式**（`deploy/docker-compose.prod.yml`）：`hg-pd` + `hg-store` + `hg-server`(hstore backend) 替代 standalone rocksdb，支持**运行时创建多 graph**（每文档独立 KG 隔离）。启动顺序 PD→Store→Server（healthcheck 依赖），hostname + 静态端口。
- **hyper-extract (he) 抽取后端**（`arrow_lake/knowledge_graph/he_extractor.py`）：`HugeGraphConfig.extractor_backend="he"` 启用；通过 langchain `ChatOpenAI` 驱动 hyperextract 模板，三元组精准度提升。
- **doc_type 三层路由**（`arrow_lake/knowledge_graph/doc_type_router.py`）：① config override ② `TemplateGallery` 元数据驱动匹配（扫描 hyperextract 全部 preset 的 tags/category/name/description，新模板自动可用）③ default 兜底；`normalize_doc_type` 别名归一化（论文/research_paper→paper 等）；`DocTypeClassifier` LLM 内容推断（doc_type 缺失时从内容识别）；`KNOWN_DOC_TYPES` + `validate_taxonomy()` 单一真相源 + CI 守护。
- **A 方案实体双写**（`builder.py` + `entity_router.py`）：每个实体写通用 `entity` 顶点 + 细分 label（person/organization/concept/...）；关系路由（同义词→细分边，无→`related_to` 降级）；`relation_type` 属性保留。
- **ingest doc_type 贯通**：`ingest_documents(doc_type=)` 参数贯通 上传 API → facade → Ingestor → chunk 表 → KG builder。
- 镜像 `arrow-lake:1.7.0`，新增 `he` pyproject extra（hyperextract + langchain-openai）。

### Changed

- KG builder doc_type 推断上移到**文档级**（显式 doc_type per-chunk 透传；全缺失时一次推断，所有 chunk 共享模板，省 LLM 调用）。
- he 工厂（`_lake_kg.py`）注入 `DocTypeClassifier`，P3 推断进入生产路径。
- Dockerfile：builder + runtime 双显式构建代理（WSL2 mirror 模式 buildkit 自动代理不注入）+ apt/PyPI 切 aliyun 镜像 + extras 合并一次解析。

### Fixed

- `client.clear()` PD 模式返回 204 被误判失败（原只认 200/202）→ 加 204；POST fall-through 加日志。
- `execute_build` 异常处理过窄（只 catch RuntimeError/OSError）→ 拓宽至 Exception（先 re-raise CancelledError），task 不再永久 RUNNING。
- he 静默失败不可观测 → `KGBuildTask.extraction_failures` 计数 + 非平凡文本空结果 WARNING 日志。
- `gravitino_client.py:load_table` 语法错误（重复破损片段）。
- he 默认模板 `general/default_graph` 不存在 → `general/concept_graph`；gallery 排除 `base_*` 不可抽取模板。
- 双写 `add_vertices` 长度不符静默降级 → 加 WARNING 日志。

## [1.6.3] - 2026-06-09

### Summary

v1.6.3 修复 HugeGraph Gremlin 绑定问题，并对 deploy 层进行全面安全加固、监控补全、性能优化和示例 nginx 代理兼容。

### Fixed

- HugeGraph 1.7 all-in-one 镜像 `gremlin-server.yaml` 的 `graphs: {}` 导致 Gremlin 变量未注册 — 通过 entrypoint wrapper 在服务启动前注入图绑定配置
- `export_graph()` 在 Gremlin 不可用时静默失败 — 添加 REST API 降级路径，当 Gremlin 脚本引擎抛异常时自动切换到 `GET /graphs/{name}/graph/vertices|edges`
- Redis healthcheck 使用 `redis-cli -a` 暴露密码 — 改为 `REDISCLI_AUTH` 环境变量
- Prometheus scrape targets 使用 container_name 而非 Docker 服务名 — 统一修正
- Makefile `scan`/`backup` target 缩进错误导致 make 无法识别
- Dockerfile 版本标签过时 (1.6.0) — 改为 `ARG VERSION` 动态注入
- Ollama 嵌入 API 地址硬编码 IP — 改为 `${OLLAMA_API_BASE}` 环境变量

### Added

- `deploy/scripts/entrypoint-hugegraph.sh` — HugeGraph 容器 entrypoint wrapper
- `deploy/scripts/fix-hugegraph-gremlin.sh` — 手动修复脚本
- `redis-exporter` (oliver006/redis_exporter) 侧车服务 — Prometheus 采集 Redis 指标
- Redis/MinIO/基础设施 Prometheus 告警规则 (+8 rules)
- nginx gzip 压缩、CSP 安全头、proxy buffer 调优、SSE 600s 超时
- `deploy/.env.example` 脱敏环境变量模板
- 示例脚本 SSL context 支持 (`ARROW_LAKE_SSL_VERIFY` 环境变量)
- HugeGraph overlay (`docker-compose.hugegraph.yml`) Gremlin fix entrypoint

### Changed

- `docker-compose.prod.yml` hg-server 服务：覆盖 `entrypoint` 使用 wrapper 脚本
- `_import_export.py` 的 `export_graph()` 方法：Gremlin 异常时降级到 REST API
- 镜像标签固定：socat `:latest` → `1.9.1`，curlimages/curl `:latest` → `8.12.1`
- API healthcheck: interval 30s→15s, start_period 30s→60s (适配 4 workers)
- Redis healthcheck: 新增 `start_period: 10s`
- nginx 服务：清除 proxy 环境变量避免 upstream 路由错误
- Ray worker/GPU：添加 `tmpfs /tmp`（read_only 模式兼容）
- 33 个 API 示例: `BASE_URL`/`API_KEY` 改为环境变量读取，支持 nginx HTTPS 代理模式

### Known Issues

- `tests/unit/kg/test_kg_builder.py` 的 7 个异步 builder 测试预先存在失败（与本次修改无关，是 v1.6.1 fire-and-forget 重构后的 mock 同步问题）


## [1.6.2] - 2026-06-09

### Summary

v1.6.2 聚焦于 **多 Worker 异步任务状态共享** — 通过 Redis 实现 `TaskManager` 跨进程任务状态可见性，解决多 uvicorn worker 部署时任务状态隔离问题。

### Changed

- `TaskManager` 从纯内存模式升级为 **双写 + 优先读 Redis** 模式：创建/更新时同步写入 Redis HASH，查询时优先从 Redis 读取跨 worker 状态
- `BackgroundTask` 新增 `to_dict()` / `from_dict()` 序列化方法，支持 Redis HASH 存储和 JSON 字段正确解析
- `_lake_kg.py` 的 `kg_build` 完成后同步最终状态到 Redis（entity_count, relation_count）

### Added

- `RedisTaskStore` (`arrow_lake/api/_redis_task_store.py`) — Redis HASH 后端任务存储，支持 CRUD + TTL 自动清理 + index SET 索引
- `RedisConfig.task_key_prefix` 和 `RedisConfig.task_ttl_seconds` 配置项
- `TaskManager.init_redis_store()` / `shutdown_redis_store()` — 应用生命周期管理
- 应用启动时自动初始化 Redis task store（`app.py` lifespan）

### Fixed

- `ExportTask` 测试构造函数缺少 `operation` 参数导致 `TypeError`
- `docs/cookbook/examples_api/conftest.py` 的 `kg_query` 字段名 `query` → `gremlin`（API 期望）

### Known Issues

- ~~HugeGraph 1.7 all-in-one 模式的 Gremlin 脚本引擎未注册图绑定（`g.V()` 等语法不可用），REST traverser 端点正常~~（v1.6.3 已修复）

### Performance

| 场景 | 结果 |
|------|------|
| Redis task CRUD (单次) | <1ms |
| kg_build `_audit_trail` (15 chunks) | 99s |
| kg_build benchmark (50 chunks, mock LLM) | 0.018ms |
| API 测试套件 (597 tests) | 87.6s 全部通过 |
| KG API + E2E tests (68 tests) | 全部通过 |
| RAG Query (graphrag-kb, 首次) | 55s (LLM 冷启动) |
| RAG Query (后续) | 5-18s |
| GraphRAG Query | 53s |
| SSE Streaming RAG | 8771 chars |
| Cookbook 示例 (07/06/14/15) | 4/4 ALL PASSED |

## [1.6.1] - 2026-06-08

### Summary

v1.6.1 聚焦于 **消除 API 阻塞操作** — 将重量级同步任务转为 fire-and-forget 异步模式，附带进度追踪。

### Changed

- `kg_build` 不再阻塞 API：拆分为 `prepare_build()` + `execute_build()`，立即返回 task_id
- `kg_build` 数据准备阶段（LanceDB 加载 + Arrow 列规范化）改用 `run_in_executor` 避免 event loop 阻塞
- HugeGraph `build_concurrency` 默认值 1 → 3
- HugeGraph `build_batch_delay` 默认值 3.0s → 0.5s
- `TaskManager` 泛化为通用后台任务管理器（`ExportTask` 保留为别名）

### Fixed

- **CRITICAL**: `Lake._component_lock` 从 `threading.Lock` 改为 `threading.RLock`，修复嵌套 `_get_component` 调用导致的死锁（`_create_kg_builder` 内部调用 `_get_kg_client` + `_get_kg_extractor` 各自再获取同一把锁）

### Added

- `POST /api/v1/datasets/{name}/ingest/async` — 异步文件摄取 (HTTP 202)
- `POST /api/v1/backup/create/async` — 异步备份 (HTTP 202)
- `POST /api/v1/backup/restore/async` — 异步恢复 (HTTP 202)
- `GET /api/v1/tasks/{task_id}/status` — 统一任务状态查询
- `GET /api/v1/tasks` — 任务列表（支持过滤）
- `BackgroundTask` 数据类（替代 `ExportTask`，向后兼容）

### Performance

| 操作 | 旧模式 | 新模式 |
|------|--------|--------|
| kg_build (1000 rows) | 阻塞 100+ 分钟 | <1s 返回，后台执行 |
| ingest (大文件) | 阻塞 600s | <1s 返回，后台执行 |
| backup/restore | 阻塞 10-30 分钟 | <1s 返回，后台执行 |

## [1.6.0] - 2026-06-08

### Summary

v1.6.0 聚焦于 **修好已知问题，让现有能力更稳更健壮**。不追新特性，夯实基础。

### Deployment

- **Dockerfile**: 替换 ghcr.io/astral-sh/uv 远程拉取为本地二进制 COPY，解决网络受限环境构建问题
- **Dockerfile**: builder 阶段代理清除移至网络操作之后，确保 apt-get/uv 可通过代理访问外网
- **docker-compose.prod.yml**: 版本号对齐 `arrow-lake:1.6.0`，metrics 端口改为 `9091`（避免与 API 8000 冲突）
- **Helm Chart**: `appVersion` 和 `version` 对齐至 `1.6.0`
- **.env**: 新增 `REDIS_PASSWORD`，`METRICS_PORT` 修正为 `8001`，LLM 配置同步
- **构建产物**: `deploy/uv-local` / `deploy/uvx-local` 加入 `.gitignore`
- **清理**: 移除 `_bmad-output/` 过期规划文档（已被 `docs/` 下文档替代）

### Phase 1 — 安全加固 + Bug 修复 + E2E 搜索/RAG

#### Fixed — Bug 修复
- **C1**: 修复 API server(8000) 与 metrics server(8000) 端口冲突，metrics 默认改为 8001
- **C2**: JWT auth_mode 跨字段验证缺失，新增 `@model_validator` 确保密钥完整性
- **C3**: 统一所有 `*_timeout_seconds` 字段 `ge=1` 约束
- **Q1**: RRF 公式重构为论文标准写法 `enumerate(start=1)`，数学不变但可读性提升

#### Fixed — 安全加固
- **Q4**: SQL 注入防护补强 — 新增 COMMENT/RENAME/TRUNCATE/MERGE/GRANT 等 DDL 关键字拦截
- **R1**: Prompt 注入防护扩展 — context text 也执行 sanitize，扩大 injection regex
- **A2**: Rate limit X-Forwarded-For bypass 修复 — 从右往左跳过可信代理 IP
- **A3**: Daft SQL 管道调用前也执行 `validate_sql_safety()`
- **A6**: Chunked encoding 请求也执行请求大小限制

#### Fixed — E2E 搜索/RAG
- **ER1**: 空检索结果不再送 LLM（防幻觉），抛 `RAG_RETRIEVAL_FAILED`
- **ER2**: Hybrid search 单路失败时降级到单路搜索（不再全盘失败）
- **ER3**: SSE 流异常不再静默吞掉，记录并 break（防客户端挂起）

### Phase 2 — 异常体系 + 资源控制 + E2E 可靠性

#### Added — 异常层次
- 新增 `ConcurrencyError` / `TransientError` / `ConsistencyError` 异常类
- `ErrorCode` 枚举新增 CACHE/CONCURRENT/METADATA/RESOURCE/TRANSIENT 等分类
- `errors.py` 补全 DOCUMENT_*/TRANSFORM_*/QUALITY_* HTTP 状态码映射

#### Added — 资源控制
- 新增 `ResourceLimits` 配置（查询超时、并发限制、结果行数、扫描字节）
- 新增 `BackpressureConfig` 配置（摄取队列、拒绝阈值、重试次数）
- 超时级联验证 API > OLAP > LLM 递减

#### Fixed — 基础设施健壮性
- **CO1**: 熔断器 half-open 竞态修复，失败后恢复计数器
- **CO2**: 熔断器新增 4 个 Prometheus 指标（state/failures/opens/recoveries）
- **CO3**: Structlog 添加 `format_exc_info` + `ExceptionRenderer`，JSON 日志异常栈可读
- **CO4**: HTTP client 工厂添加 timeout/limits/retries 默认值
- **Q2**: DuckDB session pool 泄漏修复 — 超时后正确释放 semaphore
- **Q3**: Query cache key 含 version，避免跨版本缓存冲突

#### Fixed — E2E 摄取可靠性
- **R2**: 嵌入 fallback 扩展 — Timeout/429/502/503/504 也触发 fallback 到本地 encoder
- **R3**: 失败图像用 null marker 替代零向量（cosine similarity 不再误判为 1.0）
- **EI1**: 嵌入批处理分片容错 — 大批次按 shard_size 分片，中间失败用 null 占位
- **EI6**: Lance dataset lock 添加 acquire timeout（30s），防死锁

#### Fixed — 数据安全
- **W1**: Backup restore 先恢复后删除（原数据安全网）
- **W2**: Workflow rollback 使用临时数据集（非原子操作安全网）
- **W3**: Audit HMAC 未配置时 verify() 返回 False（不再静默绕过）

### Phase 3 — 代码质量 + Protocol + E2E 基础设施

#### Changed — 代码质量
- **3.1**: `self._storage` 类型从 `Any` 改为 `StorageProtocol`，query bridges 依赖注入
- **3.2**: `_trace_span` 去重到共享基类，`_lake_rag.py` 改用 `self.config` 公开属性
- **3.3**: API 中间件管道显式声明 `MIDDLEWARE_PIPELINE`，Correlation ID 移至首位

#### Fixed — E2E 基础设施
- **EF1**: 熔断器集成到 Gravitino/HugeGraph/Redis（`circuit_protected()` 上下文管理器）
- **EF3**: Error mapping 补齐 — 100% ErrorCode 覆盖 HTTP 状态码
- **EF5**: 后台线程关闭有 `is_alive()` 验证 + Prometheus 指标
- **EF7**: HTTP async client 资源泄漏修复 — async shutdown 中正确调 `aclose()`

#### Fixed — 基础设施批量修复
- Gravitino client init flag 成功后才设 `_initialized=True`
- Gravitino sync 新增冲突检测（注册前检查是否存在）
- HugeGraph retry 扩展 — 新增 5xx 和 connection reset
- Entity extraction confidence 默认从 1.0 改为 0.5
- Kafka 连接器新增 retry decorator
- Quality gate 无 schema 时至少执行基本验证
- LLM 空响应新增 retry 或明确错误
- DuckDB 连接归还前健康检查
- Session pool 半创建连接清理

### Testing

- 全量测试 **4818 passed, 0 failed**
- 测试覆盖率 ≥ 80%

## [1.5.2] - 2026-06-01

### Fixed — Security Hardening & Code Quality
- **S1**: JWT 空密钥改为 raise ValueError 阻止启动（HS256 必须配置 secret_key）
- **S2**: Kerberos SPNEGO 改用 gssapi Python API，消除 principal 命令注入
- **S3**: gravitino_stats SQL 查询改为参数化，消除 table name SQL 注入
- **S4**: Redis 移除默认密码 `redisprod`，强制 `.env` 配置 `REDIS_PASSWORD`
- **S5**: Admin bypass 改用 `Role.ADMIN.value`，不再硬编码字符串
- **S6**: Refresh token 旋转后自动撤销旧 token jti
- **S7**: OAuth2 token_url 强制 HTTPS scheme
- **S8**: 异常日志移除 client_secret 泄露风险
- **S9**: `--forwarded-allow-ips` 从 `*` 改为 Docker 子网 `172.30.0.0/16`
- **S10-S11**: Docker Compose 所有非 API 端口加 `127.0.0.1` 绑定（MinIO/Redis/Ray/监控 8 个服务）
- **S12**: urlopen 前校验 URL scheme 防 SSRF
- **S13**: MD5 缓存键改为字符串直接做 dict key
- **S14**: SQL blocklist 增加 ATTACH/DETACH/PRAGMA/LOAD/CALL/SET
- **S15**: Gremlin 白名单移除 `union`（可嵌套绕过）
- **S16**: OLAP SQL 端点增加 `validate_sql_safety` 校验
- **S17**: JWT 空 key 在 `app.py` 路径也阻止启动

### Changed — Code Quality
- `@staticmethod` 中 `self` 引用修复（lineage.py）
- 4 个 F821 undefined name 修复（lineage.py/search.py/schema.py/storage.py）
- `create_task` 返回值存储 + done callback 防止 GC 回收
- 闭包循环变量默认参数绑定（rbac.py）
- `ClassVar` 注解补全（rbac.py/federated_engine.py）
- `raise ... from None` 补全（rag.py）
- `except: pass` 改为 logger 调用（storage.py/ingest_embed.py）
- Gravitino 失败日志级别 debug→warning（lineage.py）
- LineageStore 实例缓存（lineage_hooks.py）
- ruff --fix 清理 53 项 lint 问题（F401/I001）

### Metrics
- Bandit HIGH: 2 → 0
- Ruff F821: 6 → 0
- Ruff F401: 18 → 0
- Tests: 506/507 passed（1 pre-existing failure）

## [1.5.1] - 2026-05-29

### Added — Security Governance + Lineage v2
- **Gravitino Auth Providers**: Simple/OAuth2/Kerberos/Null 四种认证策略
- **Lineage Hooks**: 摄入/搜索/查询自动记录血缘事件
- **Expanded RBAC**: Schema-level ACL + Deny-first 权限模型 + GravitinoRBACBridge 扩展映射
- **Federated Pushdown**: 跨 Catalog 联邦查询下推优化

### Changed
- 权限映射扩展至 15 个 action→privilege 对
- Lineage 自动通知集成到 record_event 流程

## [1.5.0] - 2026-05-28

### Added — Platform Systematization
- **Architecture Visualization**: 完整架构图 + 依赖地图 + 术语表
- **Security Audit**: 全面安全审计报告 + 加固建议
- **CLI 场景别名**: `knowledge`/`connect`/`search`/`manage`/`explore` 五大场景入口
- **Documentation v2**: docs_v2/ 三层文档体系（Data/Knowledge/Compute Plane）
- **BMAD Agent System**: 20+ 产品/架构/开发/设计 Agent 集成
- **v1.4.5 Security Fixes**: 4 项安全漏洞修复

## [1.4.4] - 2026-05-25

### Added — RAG Quality Leap + SDK/CLI High Performance
- **RAG Reranking Pipeline**: CrossEncoder / LLM / Noop 三种重排策略，`RerankerFactory` 自动选择
- **Query Transformation**: HyDE (Hypothetical Document Embedding) / MultiQuery / Identity 三种查询改写策略
- **Multi-turn Conversation**: 对话历史注入 RAG context window，Token 预算管理
- **Context Window 升级**: Score-based 排序 + `finalize()` 语义化 API
- **GraphRAG RRF Fusion**: 知识图谱检索结果与向量/全文三路 RRF 融合
- **CLI Lake Instance Caching**: 命令间复用 Lake 实例，避免重复初始化
- **CLI Embedding Model Caching**: 搜索命令复用 encoder，消除重复加载延迟
- **Rich Progress Bars**: 批量文件摄入 (>3 files) 显示进度条 + SIGINT 信号保护
- **Shared HTTP Connection Pools**: httpx 连接池复用，减少 TCP 握手开销
- **Lake Context Manager**: `with Lake(...) as lake:` 异步资源清理
- **Anthropic Circuit Breaker**: LLM 调用熔断保护，避免级联故障
- **Latency Breakdown Tracking**: RAG 各阶段耗时分解 (retrieval/reranking/generation)
- **LRU Cache with Limits**: 缓存上限保护，防止内存无限增长
- **DuckDB Auto-warmup**: 启动时预热连接池 + 内存校验
- **Structured Error Output**: CLI 错误结构化输出，便于脚本解析

### Changed
- `pyproject.toml` 版本同步到 1.4.4
- Thread lock 管理增加 LRU 上限 (max 1024)，防止 dataset locks 内存泄漏
- RAG context window 行为变更为 score-based 排序

### Tests
- 更新 RAG context 测试匹配新的 `finalize()` API 语义
- 更新 score-based ordering 测试期望

## [1.4.3] - 2026-05-23

### Added — Production Readiness: Observability + Auto-Maintenance + Quality Gates
- **OpenTelemetry 集成**: 分布式链路追踪，gRPC OTLP exporter
- **Alertmanager 告警**: Prometheus AlertManager 集成，多渠道通知
- **Auto-Maintenance Scheduler**: 后台定时维护任务（compaction、cleanup、index rebuild）
- **Quality Gates**: 数据摄入质量门控，验证 schema / null ratio / dedup threshold
- **docker-compose.prod.yml 升级**: OTel Collector + Alertmanager sidecar
- **Latency SLO Dashboard**: Grafana SLO 看板，P50/P95/P99 延迟追踪

### Changed
- Docker Compose prod profile 增加 OTel + Alertmanager 服务
- 维护任务接入 FastAPI lifespan 管理

### Tests
- Quality gate 单元测试 + 集成测试
- Auto-maintenance scheduler 测试

## [1.4.2] - 2026-05-22

### Added — 安全加固 + Gravitino 配置化
- **FQN 注入防护**: `ValidationMixin` 全局限名验证，拒绝非法字符和路径穿越
- **SQL 注入防护增强**: 分号 + 多语句检查升级，DDL/DML 关键字黑名单扩展
- **JSON 反序列化验证**: 外部 JSON payload 深度限制 + 类型白名单
- **Thread Zombie Detection**: 线程僵尸检测，回收泄漏的工作线程
- **Gravitino 深度治理**: 配置化 Gravitino 连接参数，支持环境变量覆盖

### Changed
- Gravitino 配置从硬编码改为 pydantic-settings 管理
- 安全验证层统一到 `ValidationMixin`，消除分散的校验逻辑

### Fixed
- Gravitino Docker 初始化脚本 MinIO 凭证参数化
- 测试套件中环境隔离改进，消除跨测试状态泄漏

### Tests
- FQN 注入测试 (18 cases)
- SQL 注入增强测试
- Thread zombie 检测测试

## [1.4.1] - 2026-05-22

### Added — Gravitino 元数据治理集成
- **Gravitino Server + Lance REST Catalog**: Docker 部署 (docker-compose profile: gravitino)
- **GravitinoBridge**: DuckDB ↔ Gravitino 双向同步 (catalog/schema/table 元数据)
- **GravitinoSyncScheduler**: 后台定时同步任务，接入 FastAPI lifespan 管理
- **GravitinoRBACBridge**: 权限委托 Gravitino 决策 + 本地 RBAC 降级
- **GravitinoTagService**: 数据分类标签管理 (sensitive/PII/financial)
- **GravitinoPolicyService**: 数据保留策略 + 脱敏策略
- **GravitinoStatsCollector**: DuckDB 统计信息收集 + Gravitino 属性注册
- **GravitinoModelRegistry**: ML 模型版本化管理 + 热切换
- **ArrowLakeGravitinoClient**: Python SDK 统一封装
- **`/metadata/*` API 代理端点**: catalogs / tables / tags / policies / statistics / models
- **Lineage 自动通知**: Lance 版本变更自动通知 Gravitino
- **Daft GravitinoCatalog**: 联邦查询支持
- **健康检查**: gravitino + lance_rest 状态纳入 `/health` 端点
- **优雅降级**: Gravitino 不可用时 Arrow Lake 核心功能正常运行

### Tests
- 43 Gravitino 测试 (27 unit + 16 API e2e)

### Fixed
- Dockerfile runtime 阶段移除不必要的 protobuf-compiler
- init-gravitino.sh MinIO 凭证参数化 (不再硬编码)
- gravitino.py Request 命名冲突 (urllib vs fastapi)

## [1.4.0] - 2026-05-20

### Added — Phase 0: 补债拆分
- **DuckDB Profiling**: `enable_profiling` 配置，`explain_analyze()` 附加 profiling 输出
- **DuckDB Relational API**: `metadata.py` 新增 `_relational_query()` 类型安全查询
- **大文件拆分**: `ingestor.py` (870→200行) 拆为 `_ingest_files/media/sources.py`；`client.py` (838→300行) 拆为 `_traversers/import_export.py`
- **小修**: `__all__` 重复条目修复，DuckDB 版本兼容检查

### Added — Phase 1: 性能与规模
- **DuckDB 水平扩展**: 多实例连接池路由 (round-robin)，Redis 信号量协调
- **GPU Autoscaling**: 冷却期 + 缩容保护 + 扩缩容事件持久化
- **Schema 演进**: `SchemaCompatibilityChecker` 兼容性检查 + `POST /{name}/schema/migrate` 端点
- **Daft 原生媒体**: 批量图像/视频处理迁移到 Daft Rust 实现，感知哈希迁移到 `daft.functions.image_hash()`

### Added — Phase 2: 数据治理
- **血缘可视化 API**: `GET /lineage/graph/{name}`、`POST /lineage/impact`、`GET /lineage/stats`
- **质量规则引擎**: 声明式 `QualityRuleEngine`，支持 length/range/regex/duplicate 检查 + reject/flag/remove 动作
- **行级/列级 ACL**: `DatasetACL` 数据类，`PUT/GET/DELETE /admin/acl/{dataset}` 管理端点，查询和搜索结果自动裁剪

### Added — Phase 3: 收尾加固
- **FTS 真正分页**: `offset` 参数支持全链路（API → facade → FTS bridge → LanceDB）
- **查询结果流式**: OLAP 端点 `stream=True` 返回 SSE，每事件为 Arrow IPC batch (base64)

### Tests
- 3296+ tests passing, 0 failures
- 新增 80 个测试（质量规则 45 + ACL 35 + 流式验证）
- bandit 安全扫描: 0 高危

## [1.3.4] - 2026-05-19

### Fixed
- **代理泄漏全面修复**: 宿主机 HTTP_PROXY 通过 Docker Compose `${HTTP_PROXY:-}` 插值泄漏到容器内，导致所有 httpx 客户端 (embedding/LLM/KG) Connection refused
- **Embedding 500 错误**: router 使用请求参数默认模型名 (Qwen/Qwen3-Embedding-0.6B) 而非配置模型名，Ollama 返回 404

### Added
- **httpx 客户端工厂** (`core/http.py`): `create_http_client()` / `create_async_http_client()`，默认 `trust_env=False`，统一管理出站 HTTP 代理策略
- **DuckDB 查询缓存** (`query/_cache.py`): LRU 缓存层，支持 TTL + max_entries + 线程安全，命中时跳过 SQL 编译与执行
- **OLAP 性能调优配置**: `query_cache_enabled/ttl/max_entries`、`preserve_insertion_order`、`parquet_row_group_size`、`enable_progress_bar`
- **Loki + Promtail 日志聚合**: `deploy/monitoring/loki/` + `deploy/monitoring/promtail/`，容器日志集中收集
- **Prometheus 告警规则**: `deploy/monitoring/prometheus/rules/arrow_lake.yml`，服务健康/API 延迟/错误率告警
- **nginx TLS 反向代理**: `deploy/nginx/nginx.conf`，安全头 + 速率限制 + 请求大小限制
- **MinIO 定时备份**: `deploy/scripts/backup-minio.sh`，保留策略 + CronJob 集成
- **Grafana 多数据源**: Loki datasource 自动配置

### Changed
- **Docker Compose 代理清空**: `docker-compose.prod.yml` api/ray-head/ray-worker 显式设置 `HTTP_PROXY=""`
- **NO_PROXY 扩展**: 增加 `172.19.0.0/16`、`loki`、`promtail`
- **`.env.example` 重构**: 按类别分组 (Docker/存储/计算/监控/安全)，新增 Docker Compose 必需变量
- **FTS 搜索**: 增加 replace 支持（自动 drop 旧 segmented column 后重建）
- **会话管理器**: 增加连接池监控指标 (pool_size/active_sessions/queued_requests)

## [1.3.3] - 2026-05-18

### Added
- **Daft DataFrame 查询引擎**: LazyDaftFrame + DaftQueryEngine 完整链式操作 (sort/filter/groupby/join/sql/pivot/explode/sample/distinct/offset)
- **Daft 安全加固**: 全方法标识符验证 (`_SAFE_IDENTIFIER_RE`)、SQL DDL/DML 黑名单、collect 行数上限 (max_rows)、错误消息脱敏
- **Daft API pipeline**: POST `/query/daft` 支持链式操作 pipeline，DaftQueryRequest 扩展 8 种操作步骤
- **行数预检**: `check_feasibility()` 预检数据量，>500K 警告推荐 DuckDB，>1M 拒绝 (422)
- **DaftQueryResponse.warnings**: API 响应返回大数建议和截断警告
- **Daft API 示例**: 4 个 cookbook 示例脚本 (29-32)
- **__repr__**: LazyDaftFrame / LazyGroupedFrame / DaftQueryEngine 调试友好
- **架构文档更新**: DuckDB vs Daft 三方分工、瓶颈分析、选型矩阵、6E 查询流程图

### Changed
- **Daft 定位升级**: 从 Ingest/ETL 辅助角色升级为 DataFrame 查询引擎层
- **fill_null()**: 修复 `with_columns` 参数形式 (展开参数 → dict)
- **import inspect**: 提升到模块级，符合 PEP 8

### Tests
- 98 测试覆盖 (72 单元 + 26 API)，ruff/bandit 0 issues

## [1.3.2] - 2026-05-14

### Fixed
- **SSRF 防护**: 修复 except ValueError 吞没私有 IP 检查的 bug，HTTP 连接器添加连接后 IP 校验
- **FTS 索引**: 修复分片覆写丢失非 FTS 列数据（>50K 行时触发）
- **DuckDB session**: _env_backup 在 try 之前保存，异常时正确恢复 S3 环境变量
- **RAG batch stream**: task_to_stream 映射在创建时填充
- **KG builder**: 修复列标准化顺序 bug（content rename 覆盖 id 列）+ datetime 序列化
- **Circuit breaker**: allow_request 持锁到状态转换完成
- **DuckDB 连接池**: _close_conn 恢复 S3 环境变量
- **SQL 注入防护**: lineage 查询添加分号和多语句检查
- **JWT auth bypass**: 精确匹配替代宽泛 startswith

### Changed
- **代理配置**: 容器内改用 host.docker.internal:7888 (proxy-forward)，修复外部 API 不可达
- **备份**: 远程 blob 改用 server-side copy 替代 download→upload
- **配置合并**: _deep_merge 替代 dict.update 浅合并
- **限流**: 惰性清理过期计数器

### Added
- 20 个 API 示例脚本 (docs/cookbook/examples_api/)
- 46 个 upload/ingest 单元测试
- Docker compose: AWS env vars + hg-net 外部网络 + Ollama/DeepSeek 可配置

### Verified
- 3404 测试通过（14 失败均为 LLM 外部服务不可达）
- KG 构建端到端: 4055 vertices, 8454 edges
- HugeGraph + DeepSeek LLM + Ollama Embedding 全链路连通

## [1.3.1] - 2026-05-12

### Changed
- **pylance 升级**: 4.0.1 → 6.0.0，解锁 Lance 文件格式 v2.1/v2.2 双层编码、io_uring 高性能 I/O
- **lance-namespace 升级**: 0.6.1 → 0.7.6（pylance 6.0.0 依赖）
- **FTS 中文分词**: tantivy 后端移除后自动切换 lance-index + jieba 预分词，无需代码改动
- **文档更新**: 产品介绍 (中/英)、CLI Reference、Tech Compatibility Report 版本矩阵同步

### Verified
- 2610 单元测试通过，242 集成测试通过，覆盖率 76.61%（与升级前一致）
- Storage / Search / Vector / FTS / DuckDB lance_scan / Daft 零拷贝集成正常

## [1.3.0] - 2026-05-09

### Added
- **Redis 分布式 Session**: `RedisConfig` + `RedisSemaphore` 适配器，DuckDB Session 池支持水平扩展
- **QueryEngine Protocol**: `arrow_lake/query/engine.py` 定义 acquire/release/get_stats/shutdown 接口
- **RBAC 路由守卫**: 10 个路由文件添加 `Depends(require_role(...))`，覆盖 VIEWER/EDITOR/ADMIN 三级权限
- **JWT 黑名单 LRU**: `OrderedDict` 替换 O(n) dict rebuild，防 DoS 内存耗尽
- **Gremlin 注入防护增强**: 正则匹配裸 mutation step、闭包语法 `{}` 拒绝、`//` 行注释剥离
- **SQL 注入防护增强**: lineage SQL 验证剥离 `--` 和 `/* */` 注释
- **路径穿越防护**: export 路由 `resolve()` + `startswith()` 防止 `../` 逃逸
- **Helm HPA 模板**: `deploy/helm/arrow-lake/templates/hpa.yaml`（基于 CPU + 自定义指标）
- **Helm CronJob 备份模板**: `deploy/helm/arrow-lake/templates/cronjob-backup.yaml`（每日 02:00）
- **Helm Redis 环境变量**: Deployment 模板条件注入 Redis 配置
- **Gremlin 安全测试**: 17 个测试覆盖闭包绕过、裸 mutation、注释剥离、合法查询
- **Redis 信号量测试**: acquire/release、超时、回退、重连测试
- Cookbook examples: Redis Session (40)、RBAC Roles (41)、Gremlin Security (42)、JWT Blacklist (43)
- `sdk/` 模块: `LakeClient` 别名导出

### Changed
- **版本号**: pyproject.toml / _version.py / Chart.yaml → 1.3.0
- **Ingestor 并发修复**: ThreadPoolExecutor → 顺序执行，消除 Daft 读取竞争
- **lancedb API 兼容**: `open_dataset()` → `open_table()` (v0.30+)
- **备份测试**: `StorageBackend.MINIO` → `LOCAL`，消除 MinIO 环境污染
- **全量测试隔离**: 所有 Lake() 构造添加 `StorageConfig(backend="local")`
- **prod.yaml**: OLAP 配置完善、Redis 段、rate_limit 段、audit HMAC 注释
- **dev.yaml**: Redis 默认禁用
- `respx` 从生产依赖移至开发依赖
- 新增生产依赖 `redis[hiredis]>=5.0,<6.0`，开发依赖 `fakeredis>=2.0`

### Fixed
- Gremlin 注入绕过: `map`/`flatMap` 从白名单移除（闭包执行风险）
- Redis 信号量双释放: thread-local 后端跟踪防止 Redis→fallback 双减
- Redis TTL 幽灵许可: 仅首次 acquire 时设置 EXPIRE
- JWT 黑名单 O(n) 逐出: `OrderedDict.popitem(last=False)` O(1) 替换
- `/api/v1/version` 信息泄露: 添加 VIEWER RBAC 守卫
- `auth_service.py` coverage: 38% → 补充测试覆盖
- Gate B4 embedding 测试: HF model 下载依赖测试标记 skip
- RAG E2E 测试: auth header + `text_content` 列名修复
- KG E2E 测试: env var 隔离 + auth header 修复

### Removed
- `arrow_lake/query/_async.py`: 死代码删除（零外部引用）
- `tests/unit/duckdb/test_async_query.py`: 对应测试删除

## [1.2.2] - 2026-05-08

### Added
- `Lake.embed_and_add()`: 向量化管线 — 使用配置的 embedding 后端（HuggingFace/Ollama API）将文本列编码为向量，通过 `add_columns_table` 原位写入，无需全量重写
- `Lake.add_columns_table()`: Facade 暴露 Lance 原位列添加能力
- `Lake.config` 属性: 公开当前 ArrowLakeConfig 供外部读取
- `StorageAdvancedMixin.add_columns_table()`: Lance 原生 `add_columns` 避免全量 rewrite
- S3 远程备份/恢复: `BackupManager` 和 `BackupRestore` 支持 S3 server-side copy 路径（不再依赖本地 Path 操作）
- `ExportBridge` 自动检测 `/app/exports` 不可写时回退到 cwd
- 6 个行业分块测试数据文件: finance/tech/medical/business/education/literature

### Changed
- **chonkie 兼容性**: `TokenChunker` 参数 `token_chunk_size` → `chunk_size`，`SemanticChunker` 参数 `min_chunk_size` → `chunk_size`；`SDPMChunker` 自动 fallback（chonkie ≥1.6 移除）
- **HugeGraph 默认配置**: 端口 `8089` → `8091`，graph 名 `arrow_lake_kg` → `hugegraph`（匹配 docker-compose 部署）
- **docker-compose healthcheck**: graph 名 `arrow_lake_kg` → `hugegraph`
- Cookbook examples (34 files): `_add_vectors` 统一使用 `lake.embed_and_add()` + random fallback，不再走 `to_arrow()+restore_dataset` 全量重写路径
- `arrow_lake/query/olap.py`: 添加缺失的 `import contextlib`
- Deployment REST API 示例 (13/14/15): 修复 API Key 认证、容器内路径映射、`_post` 参数名、JSON 解析容错

### Fixed
- `e2e_chunking_scenarios.py` 数据文件缺失时 `KeyError: 'strategy'`（返回完整结果 dict）
- `olap.py` 中 `contextlib` 未导入导致 `NameError`
- `export.py` 默认 `base_dir=/app/exports` 本地运行 `PermissionError`
- `jwt_auth.py` 空 refresh token 断言过严（接受 400/401）
- `24_ensemble_search.py` `_ensemble_score` 列名检查顺序
- `26_audit_trail.py` / `27_data_lineage.py` `AuditEntry`/`LineageEvent` 的 `.get()` 调用错误
- `28_backup_restore.py` 缺少 `overwrite=True` 导致恢复失败
- `32_kg_traversal.py` KG build 使用全量数据集导致超时（改用 10 行小样本）
- `graphrag_e2e_test.py` 硬编码 HugeGraph 端口 8089（改为 8091）
- `s3_minio/01,03,04` hybrid search `_rrf_score` 列不存在（优先 `_hybrid_score`）
- `07_e2e_pipeline.py` 残留数据集未清理导致重复运行失败

## [1.2.1] - 2026-04-27

### Added
- 9 new facade methods in `_LakeAdminMixin`: `restore_dataset`, `get_dataset_version`, `list_dataset_versions`, `add_column`, `alter_column`, `drop_column`, `compact_dataset`, `read_dataset`, `scan_dataset`
- `CONTRIBUTING.md` — development setup, code standards, architecture overview, testing guidelines
- `SECURITY.md` — vulnerability reporting, auth architecture, data protection, transport security

### Changed
- Cookbook examples (39 files) unified to argparse CLI pattern (`--base-uri`, `--no-cleanup`)
- `_get_storage()` eliminated from all cookbook examples and most root examples (0 in .py files except tag operations)
- Bare `except Exception` reduced from 73 to 38 in cookbook examples (context-specific types)
- `server.py` deprecation notice updated with v2.0 removal timeline
- Duplicate `VectorSearchResult` removed from `__all__`

### Fixed
- `config_changed` NameError in `session_manager.py` — restored computation block
- Idle connection health check reliability — removed unreliable `_health_skip_seconds` optimization, always runs `SELECT 1`
- `_fallback_cache` cache pollution causing intermittent `test_fallback_encode_import_error_raises` failure
- RUF001 lint warnings in `chunker.py` — suppressed for intentional CJK punctuation

## [1.2.0] - 2026-04-24

### Added
- Kreuzberg PDF parser (Rust-core, 91+ format support) replacing marker_pdf/pypdf
- TurboOCR GPU acceleration service with circuit breaker pattern and retry logic
- Ingest dead-letter queue (`IngestDeadLetterQueue`) for failed document tracking with retry/resolve/purge
- Performance benchmark suite (`examples/query/benchmark.py`) — DuckDB query, chunking, validation, token counting baselines
- Document processing E2E test (`examples/ingestion/e2e_document_pipeline.py`)
- Test fixture generator (`tests/fixtures/documents/`) — 10 synthetic documents (EN/ZH/markdown/CSV/JSONL/multilingual)
- GraphRAG E2E test script (`examples/knowledge_graph/graphrag_e2e_test.py`)

### Changed
- Default OCR backend changed from `tesseract` to `paddleocr` (Kreuzberg config)
- `backup.py` refactored from 617 to 375 lines — extracted `_manifest_to_info`, `_restore_item`, `_paginate_keys` helpers
- `except Exception` narrowed from 17 to 4 occurrences — replaced with specific exception types across 12 files

### Fixed
- **Security**: SSRF prevention in TurboOcrClient (`_validate_endpoint` blocks private IPs)
- **Security**: Gremlin injection prevention in HugeGraph client (`_BLOCKED_GREMLIN_PATTERNS`)
- **Security**: SQL injection hardening — `escape_sql_literal()` with type check and length limit in `validation.py`
- **Security**: JWT error message sanitization — non-expiry errors return generic message
- **Security**: Blob key path sanitization in ingestor (prevents path traversal)
- **Security**: Backup dataset name validation (rejects `..`, `/`, `\\`)
- **Security**: API key empty-config defense-in-depth (rejects protected endpoints when no key configured)
- Import-order bug in `hybrid.py` (`escape_sql_literal` used before import)
- Duplicate property key creation loop removed in `knowledge_graph/client.py`
- structlog-style logger call fixed in `ray_serve_encoder.py`
- SentenceTransformer API compatibility (both `get_sentence_embedding_dimension` and `get_embedding_dimension`)

### Removed
- 52+ stale unit test files (unmaintained, referencing deleted modules)

## [1.1.0] - 2026-04-22

### Added
- Production hardening: observability, metrics, and operational tooling

## [1.0.0] - 2026-04-21

### Added

v1.0 GA release — 2224 tests, 82.92% coverage, production-ready data lakehouse.

**M0: Infrastructure & Query Migration**
- DuckDB Lance extension abstraction layer (`_base.py`, `_db.py`, `lance_adapter.py`)
- Native `__lance_scan()` with PyArrow streaming fallback
- DuckLake workspace management (materialize, TTL cleanup, metadata tracking)
- S3 storage_options schema and dual-path integration (Lance SDK + DuckDB SET)
- Lake Facade decomposed into 9 focused mixins (ingest, search, query, admin, lineage, audit, rag, kg)

**M1: Production Storage**
- S3/MinIO blob storage with BlobStoreManager (multipart upload, presigned URLs)
- Backup/restore manager (Lance + MinIO + DuckLake)
- REST API backup endpoints

**M2: RAG Pipeline**
- LLM provider abstraction (Anthropic Claude, OpenAI-compatible)
- RAG pipeline with citation support (retrieval → context assembly → generation)
- Session management for multi-turn conversations
- SSE streaming for real-time generation
- Entity extraction endpoints
- REST API: `/api/v2/rag/*`

**M3: Knowledge Graph + GraphRAG**
- HugeGraph REST client with schema management
- Entity/relation extraction from unstructured text
- Knowledge graph builder with task management
- GraphRAG retrieval with 3-way RRF fusion
- REST API: `/api/v2/kg/*`

**M4: Production Readiness**
- JWT authentication with access + refresh token flow
- RBAC with ADMIN/EDITOR/VIEWER role hierarchy
- API key authentication with rotation (90-day default)
- OpenTelemetry distributed tracing
- Separated liveness/readiness health probes
- 15 REST API routers (system, datasets, search, query, quality, embedding, export, lineage, audit, backup, rag, kg, auth, admin)
- Performance benchmark framework with baseline tracking
- 6 Grafana dashboards (system, ingestion, processing, query, OMTM, SLO)

**M5: Operations & Governance**
- Rate limiting middleware (slowapi, disabled by default, per-endpoint config)
- HTTP security response headers (HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP)
- 11 Prometheus alert rules (HTTP errors, auth failures, ingestion stalled, rate limit, memory, latency)
- SLO thresholds configuration in Helm values

**Security Hardening**
- SQL injection prevention centralized in `validation.py`
- Path traversal protection in export
- HMAC integrity verification on audit trail
- JWT state propagation fallback in `get_current_user()`

**Deploy Artifacts**
- Multi-stage Docker build (CPU + GPU variants)
- Docker Compose profiles (core, dev, gpu, monitoring, kg)
- Helm chart with PrometheusRule, NetworkPolicy
- Init scripts, TLS cert generation, bucket setup

### Changed

- Lake class decomposed from 1049-line monolith to 9 mixin modules
- Query layer migrated from direct LanceDB SDK to DuckDB-native SQL with Lance extension
- Auth middleware migrated from class-based BaseHTTPMiddleware to function-based with `@app.middleware("http")`
- All config sections registered in `_build_merged_update()` and `from_yaml()` constructor

## [0.1.0] - 2026-04-15

### Added

Initial release — 80 stories across 9 Sprints, 1414 tests.

**Core Infrastructure (Sprint 1)**
- LanceStorageManager: create, read, append, delete, version, tag, compact, schema migration
- Pydantic-based configuration system (YAML + environment variables)
- Unified exception hierarchy with error codes
- Prometheus metrics integration
- CLI via Click (`arrow-lake` command)
- HTTP API server

**Data Ingestion (Sprint 2-3)**
- Batch ingestion from Parquet, JSON, CSV, images, audio, video
- MinIO/S3-compatible object storage integration
- Multi-process distributed ingestion via Ray
- Streaming ingestion pipeline

**Embedding & Vector Search (Sprint 3-5)**
- Multi-model embedding generation (sentence-transformers)
- Semantic vector search via LanceDB
- Hybrid search (BM25 + vector scoring)
- Multi-vector index support (multi-modal)
- Faceted search with drill-down

**Data Quality (Sprint 4-5)**
- Schema validation framework
- Null value detection and statistics
- Quality filter pipeline
- Content deduplication: SHA-256 exact match + pHash perceptual hash
- Incremental cross-batch dedup with seen-hash accumulation

**Data Catalog (Sprint 6-7)**
- Dataset catalog with metadata management
- Data lineage tracking with SQL query interface (DuckDB)
- Actor-based access management
- Audit logging with HMAC integrity verification

**Data Export (Sprint 5)**
- Export to Parquet (with compression: snappy, gzip, brotli, zstd, lz4)
- Export to CSV (binary columns excluded with warnings)
- Format auto-detection from file suffix
- Column selection and version selection

**Workflow & Orchestration (Sprint 8-9)**
- Metaflow workflow integration
- Ray distributed runtime
- Pipeline orchestration and scheduling

**Testing**
- 1414 tests (unit + integration)
- 82%+ code coverage
- Comprehensive test utilities and fixtures

**Security**
- SQL injection prevention (parameterized queries, keyword validation)
- Path traversal protection
- Input validation on all public APIs
- No hardcoded credentials

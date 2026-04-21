# S3/MinIO E2E 示例实施计划 (全 9 个示例)

**版本**: examples-v2 | **日期**: 2026-04-21
**状态**: 01-05 已完成，06-09 待实施

---

## Context

基于 Arrow Lake v1.0 平台，通过 9 个 S3/MinIO E2E 示例串联所有技术点组件。前 5 个示例已完成并验证通过，本次补充文档并实施 06-09。

---

## 第一部分: 已有示例 (01-05)

---

### 示例 01: 科研论文情报平台 — 全栈检索 + 质量治理

**文件**: `examples/s3_minio/01_research_paper_intelligence.py` (665 行)
**业务场景**: 研究机构管理数千篇论文，智能检索和分析

| # | 步骤 | 覆盖组件 |
|---|------|----------|
| 1 | S3 批量上传 + S3Connector 文件发现 | BlobStoreManager, S3Connector |
| 2 | 入库 + 质量过滤 + 去重 | ingest, quality_filter, deduplicate |
| 3 | 向量索引 (IVF_PQ) + 全文索引 (BM25) | create_vector_index, create_fts_index |
| 4 | 三种检索引擎 (DuckDB native + LanceDB fallback + Hybrid RRF) | search, text_search, hybrid_search |
| 5 | Faceted 浏览 (department × year CUBE) | faceted_search |
| 6 | OLAP 多表 JOIN (papers × authors × venues) | olap_query(tables={...}) |
| 7 | 数据血缘记录 + 溯源查询 | lineage_record_event, lineage_history, lineage_query |
| 8 | 审计日志 (HMAC 完整性验证) | audit_record, audit_verify, audit_query |
| 9 | 导出分析报告到 S3 | export |

**覆盖**: ingest, quality_filter, deduplicate, search, text_search, hybrid_search, faceted_search, olap_query, lineage, audit, export, BlobStoreManager, S3Connector, create_vector_index, create_fts_index

---

### 示例 02: 电商商品发现平台 — OLAP + Faceted + Ensemble

**文件**: `examples/s3_minio/02_ecommerce_product_discovery.py` (550 行)
**业务场景**: 电商平台多维度商品浏览和智能推荐

| # | 步骤 | 覆盖组件 |
|---|------|----------|
| 1 | 创建商品数据集 (双嵌入列 text+image) | ingest, create_vector_index |
| 2 | DuckDB 多表 JOIN (商品 × 分类 × 品牌) | olap_query |
| 3 | OLAP 聚合 (品类分布, 价格带, 品牌占有率) | olap_query |
| 4 | Faceted CUBE 搜索 | faceted_search |
| 5 | Ensemble 多模态 RRF 融合搜索 | ensemble_search |
| 6 | 全文搜索 | text_search |
| 7 | 数据血缘 + 审计 | lineage, audit |
| 8 | 导出分类汇总 | export |

**覆盖**: ingest, olap_query (multi-table JOIN), faceted_search, ensemble_search, search, text_search, lineage, audit, export, create_vector_index

---

### 示例 03: 法律文档合规系统 — Lineage + Audit + Quality + Dedup + FTS

**文件**: `examples/s3_minio/03_legal_document_compliance.py` (575 行)
**业务场景**: 律师事务所合同和法律文档合规审查

| # | 步骤 | 覆盖组件 |
|---|------|----------|
| 1 | 创建法律文档数据集 | ingest |
| 2 | 质量过滤 (文本长度、字段完整度) | quality_filter |
| 3 | 精确去重 | deduplicate |
| 4 | 全文搜索 (法律条款) | text_search |
| 5 | 向量搜索 (相似判例) | search |
| 6 | 混合搜索 (语义 + 关键词) | hybrid_search |
| 7 | OLAP 分析 (文档类型分布、合规率) | olap_query |
| 8 | 数据血缘 (处理管线溯源) | lineage |
| 9 | 审计日志 (HMAC 验证) | audit |

**覆盖**: ingest, quality_filter, deduplicate, text_search, search, hybrid_search, olap_query, lineage, audit

---

### 示例 04: RAG 智能问答系统 — 检索增强生成 + 会话管理

**文件**: `examples/s3_minio/04_rag_intelligent_qa.py` (584 行)
**业务场景**: 企业内部知识库问答 (mock LLM)

| # | 步骤 | 覆盖组件 |
|---|------|----------|
| 1 | 创建技术文档数据集 (FTS + 向量索引) | ingest, create_fts_index, create_vector_index |
| 2 | 单轮 RAG 问答 (检索→上下文→LLM→引用) | rag_query |
| 3 | 多轮会话 (session_id 追踪) | rag_query |
| 4 | 三种检索策略对比 (fts/vector/hybrid) | text_search, search, hybrid_search |
| 5 | 实体抽取 | rag_extract |
| 6 | 流式输出 | rag_query_stream |
| 7 | 会话历史管理 | SessionStore CRUD |
| 8 | OLAP + 血缘 | olap_query, lineage |

**覆盖**: rag_query, rag_query_stream, rag_extract, rag_get_history, text_search, hybrid_search, search, create_fts_index, create_vector_index, olap_query, lineage, SessionStore

---

### 示例 05: 知识图谱发现系统 — KG + GraphRAG

**文件**: `examples/s3_minio/05_knowledge_graph_discovery.py` (544 行)
**业务场景**: 企业知识图谱实体关系发现和多跳推理 (mock HugeGraph + mock LLM)

| # | 步骤 | 覆盖组件 |
|---|------|----------|
| 1 | 创建领域文档数据集 + 索引 | ingest, create_fts_index, create_vector_index |
| 2 | KG 构建 (实体+关系抽取) | kg_build |
| 3 | KG 构建状态查询 | kg_build_status |
| 4 | Gremlin 查询 | kg_query |
| 5 | 邻居遍历 (多跳关系) | kg_get_neighbors |
| 6 | KG 统计 | kg_stats |
| 7 | GraphRAG 问答 | GraphRAGPipeline |
| 8 | KG 清理 | kg_delete_graph |
| 9 | 数据血缘 + 审计 | lineage, audit |

**覆盖**: kg_build, kg_build_status, kg_query, kg_get_neighbors, kg_stats, kg_delete_graph, GraphRAGPipeline, search, text_search, hybrid_search, olap_query, lineage, audit

---

## 第二部分: 新增示例 (06-09)

---

### 示例 06: 医疗设备实时监控 — 增量数据生命周期管理

**文件**: `examples/s3_minio/06_incremental_data_lifecycle.py`
**业务场景**: IoT 医疗设备传感器数据的增量入库、版本管理、compaction、blob 管理、导出归档

### 步骤设计 (10 步)

| # | 步骤 | 覆盖组件 | API |
|---|------|----------|-----|
| 1 | 从 YAML 配置文件创建 Lake | `from_yaml()` | `Lake.from_yaml(yaml_path)` |
| 2 | 初始批量入库 (300 行传感器数据) | `ingest()` | `lake.ingest("device_readings", initial_data)` |
| 3 | 增量追加 (3 批，每批 50 行) | `append_dataset()` | `lake.append_dataset("device_readings", batch)` |
| 4 | 列出数据集 + 查看目录 | `catalog()`, `list_datasets()` | `lake.catalog()`, `storage.list_datasets()` |
| 5 | 数据集版本管理 | `get_version()`, `create_tag()`, `list_versions()`, `list_tags()` | `storage.get_version()`, `.create_tag()`, `.list_versions()`, `.list_tags()` |
| 6 | 按版本读取 + tag 读取 | `read_dataset(version=N)`, `read_at_tag()` | `storage.read_dataset(name, version=1)`, `.read_at_tag(name, tag)` |
| 7 | 数据集压缩 (compaction) | `compact()` | `storage.compact("device_readings")` → `CompactionStats` |
| 8 | Blob 生命周期管理 | `BlobStoreManager` | `BlobStoreManager(config).upload()/.list_blobs()/.presigned_url()/.delete()` |
| 9 | 数据集恢复 (删除后重建) | `restore_dataset()` | `storage.restore_dataset(name, backup_data)` |
| 10 | 导出为 Parquet + CSV (多种压缩) | `export()` | `lake.export(name, path, compression="gzip"/"snappy"/"zstd")` |

### 新覆盖组件 (12 个)

`from_yaml`, `append_dataset`, `catalog`, `list_datasets`, `get_version`, `create_tag`, `list_versions`, `list_tags`, `read_at_version`, `compact`, `BlobStoreManager` (upload/list/presigned/delete), `restore_dataset`, `export` (compression)

---

## 示例 07: 金融风控数据质量治理 — 质量规则 + 物化视图 + 审计

**文件**: `examples/s3_minio/07_quality_governance_and_materialization.py`
**业务场景**: 金融交易数据入库后的 schema 验证、质量过滤 (OR 模式)、去重 (感知哈希)、死信队列、元数据查询、Daft 查询、OLAP 物化视图、审计篡改检测

### 步骤设计 (10 步)

| # | 步骤 | 覆盖组件 | API |
|---|------|----------|-----|
| 1 | 入库含脏数据的交易记录 (400 行，含 15% 脏数据) | `ingest()` | `lake.ingest("transactions", dirty_data)` |
| 2 | Schema 验证 (strict 模式) | `SchemaValidationGate` | `SchemaValidationGate(mode="strict").validate(rows, schema)` |
| 3 | 质量过滤 (mode="any") | `quality_filter()` | `lake.quality_filter("transactions", mode="any")` |
| 4 | 死信队列写入 + 读取 | `DeadLetterWriter` | `DeadLetterWriter(storage).write(name, rejected, filter_name)` |
| 5 | 感知哈希去重 | `deduplicate()` | `lake.deduplicate("transactions", strategy="perceptual")` |
| 6 | 元数据查询 (SQL) | `MetadataSearchBridge` | `lake.query("transactions", "SELECT ...")` |
| 7 | Daft 延迟查询 | `daft_query()` | `lake.daft_query("transactions").filter().sort().collect()` |
| 8 | OLAP 物化视图创建 + 查询 | `materialize()`, `cleanup_materialized()` | `lake.materialize(name, sql, view_name=...)` |
| 9 | OLAP 流式查询 + max_rows | `olap_query()` | `lake.olap_query(name, sql, max_rows=100, enable_streaming=True)` |
| 10 | 审计链 HMAC 篡改检测 | `AuditTrail` | `AuditTrail.verify_chain()` 篡改检测 |

### 新覆盖组件 (11 个)

`SchemaValidationGate`, `quality_filter(mode="any")`, `DeadLetterWriter`, `deduplicate(strategy="perceptual")`, `MetadataSearchBridge.query()`, `daft_query()`, `materialize()`, `cleanup_materialized()`, `olap_query(max_rows, enable_streaming)`, `AuditTrail.verify_chain()`

### 关键注意事项

- **SchemaValidationGate**: 直接实例化，不是 Lake 方法。`validate(rows, schema)` 返回 `(valid, rejected)` 元组
- **DeadLetterWriter**: 直接实例化 `DeadLetterWriter(storage)`，需要 `storage` 对象
- **daft_query**: 需要 `daft` 可用，否则 skip with warning
- **materialize**: 需要 `ducklake_enabled=True`，否则 skip with warning
- **OLAP max_rows/enable_streaming**: 通过 `OlapSearchBridge` 参数传递

---

## 示例 08: 跨域供应链溯源 — HTTP 入库 + 复杂血缘链 + 多表 JOIN

**文件**: `examples/s3_minio/08_complex_lineage_and_governance_olap.py`
**业务场景**: 供应链数据从多个 HTTP 源入库，建立 12+ 步血缘链，跨 4 表 OLAP JOIN 查询

### 步骤设计 (8 步)

| # | 步骤 | 覆盖组件 | API |
|---|------|----------|-----|
| 1 | HTTP 入库 (模拟 JSON API 数据) | `ingest_http()` | `lake.ingest_http("suppliers", urls)` — 注意: 实际环境可能无网络，用 try/except + fallback 到 `ingest()` |
| 2 | 入库 4 张关联表 (suppliers, products, shipments, warehouses) | `ingest()` | `lake.ingest()` x4 |
| 3 | 建立 12 步血缘链 | `lineage_record_event()` | `lake.lineage_record_event()` x12，每步 `source_datasets` 引用上游 |
| 4 | 查询血缘历史 (上游 + 下游追踪) | `lineage_history()`, `lineage_query()` | `lake.lineage_history()`, `lake.lineage_query(sql)` |
| 5 | 4 表 JOIN OLAP 查询 | `olap_query(tables={...})` | `lake.olap_query("products", sql, tables={...})` |
| 6 | 多表 Daft JOIN 查询 | `daft_query().join()` | `lake.daft_query().join(other, on=...)` |
| 7 | 跨表 Faceted 搜索 | `faceted_search()` | `lake.faceted_search()` with metadata filter |
| 8 | 混合搜索 (向量 + 全文) 跨数据集 | `hybrid_search()` | `lake.hybrid_search()` 跨表验证 |

### 新覆盖组件 (4 个核心 + 3 强化)

`ingest_http()` (核心新组件), 12 步血缘链 (核心), `olap_query(tables=4表JOIN)` (强化), `daft_query().join()` (强化)

### 关键注意事项

- **ingest_http**: 实际 MinIO 环境无外网，需用 try/except 包裹，失败时 fallback 到直接 `ingest()`
- **血缘链**: 需要用 `lake.lineage_record_event()` 逐步建立，每步的 `source_datasets` 需引用上一步的数据集
- **4 表 JOIN**: 需要 DuckDB session，通过 `tables={}` 传入辅助表
- **已知限制**: `lineage_query(sql)` 在 S3 后端可能有 bug (SQL 查询 `_lineage_events` 表不存在)，需 try/except

---

## 示例 09: 视频监控智能分析 — 多媒体端到端管线

**文件**: `examples/s3_minio/09_video_intelligent_analysis.py`
**业务场景**: 安防/工业监控场景：合成测试视频 → 关键帧提取 → CLIP 图像嵌入 → 向量搜索相似场景 → Blob 存储原始视频 → 图像质量过滤 → 去重 → 血缘追踪 → 导出分析报告

### 已有多媒体基础设施

| 组件 | 文件 | 说明 |
|------|------|------|
| `VideoProcessor` | `arrow_lake/ingest/media.py:227` | PyAV 关键帧提取，直方图场景检测 |
| `ImageProcessor` | `arrow_lake/ingest/media.py:139` | 图片处理，缩略图，EXIF，预览 |
| `CLIPImageEncoder` | `arrow_lake/embed/image_encoder.py` | 4 种模型 (CLIP-vit-base/large, SigLIP-so400m/base)，ModelScope 支持 |
| `Ingestor.ingest_videos()` | `arrow_lake/ingest/ingestor.py:219` | 视频入库 (一行一视频，含首帧 JPEG) |
| `Ingestor.ingest_images()` | `arrow_lake/ingest/ingestor.py:160` | 图片入库 (含缩略图/预览/EXIF) |
| `UnifiedTableManager` | `arrow_lake/ingest/schema.py:25` | 统一多模态表 (text/image/video + embedding) |
| `ImageResolutionFilter` | `arrow_lake/quality/builtin.py:109` | 图像分辨率过滤 (最小宽高) |
| `BlobStoreManager` | `arrow_lake/storage/blob_store.py` | 大文件分片上传 (最大 ~80GB) |

### 步骤设计 (10 步)

| # | 步骤 | 覆盖组件 | API |
|---|------|----------|-----|
| 1 | 合成测试视频 (PyAV 创建多段不同场景的 MP4) | `av` | PyAV 写入视频流，模拟不同场景 (室内/室外/夜间) |
| 2 | Blob 存储原始视频文件到 S3/MinIO | `BlobStoreManager` | `BlobStoreManager(config).upload_file(video_path, key)` |
| 3 | 关键帧提取 (VideoProcessor) | `VideoProcessor` | `VideoProcessor().extract_keyframes(video_path)` → `VideoIngestResult` |
| 4 | CLIP 图像嵌入 (关键帧) | `CLIPImageEncoder` | `CLIPImageEncoder(model_name).encode(table)` → `image_embedding` 列 |
| 5 | 构建统一多模态表 + 入库 | `UnifiedTableManager`, `ingest_videos()` | 使用关键帧元数据构建 Arrow Table，入库到 Lance |
| 6 | 向量索引 + 图像相似场景搜索 | `vector_search()` | `lake.search("video_scenes", query_vector, vector_column="image_embedding")` |
| 7 | 图像质量过滤 (分辨率) | `ImageResolutionFilter` | `lake.quality_filter("video_scenes")` + ImageResolutionFilter |
| 8 | 图像去重 (感知哈希) | `deduplicate()` | `lake.deduplicate("video_scenes", strategy="perceptual")` |
| 9 | 血缘追踪 (视频入库全链路) | `lineage_record_event()` | 记录: 原始视频→关键帧提取→嵌入→入库→质量→去重 |
| 10 | 导出分析报告 + Blob 预签名 URL | `export()`, `BlobStoreManager` | `lake.export(..., format="csv")` + `.presigned_url()` |

### 新覆盖组件 (8 个)

`VideoProcessor` (关键帧提取), `CLIPImageEncoder` (图像嵌入), `Ingestor.ingest_videos()`, `UnifiedTableManager`, `ImageResolutionFilter`, Blob 大文件上传, 多模态向量搜索, 视频血缘链

### 关键注意事项

- **合成视频**: 使用 PyAV 创建，不需要真实视频文件。生成 3-5 段不同场景 (纯色渐变 + 随机噪声帧)
- **CLIP 模型**: 首次加载会下载模型 (~600MB)。使用 `try/except ImportError` 包裹，未安装时 skip
- **CLIP 嵌入维度**: SigLIP-so400m-patch14-384 输出 384 维向量，与 text_embedding 一致
- **视频嵌入策略**: 对每个关键帧单独编码，取平均或最近帧嵌入作为视频表征
- **GPU 检测**: CLIPImageEncoder 自动检测 GPU，无 GPU 时用 CPU (较慢但可用)

---

## 组件覆盖矩阵

### 新覆盖组件 (36 个唯一 API)

| # | 组件 | 示例 | 类型 |
|---|------|------|------|
| 1 | `Lake.from_yaml()` | 06 | Lake API |
| 2 | `lake.append_dataset()` | 06 | Lake API |
| 3 | `lake.catalog()` | 06 | Lake API |
| 4 | `storage.list_datasets()` | 06 | Storage |
| 5 | `storage.get_version()` | 06 | Storage |
| 6 | `storage.create_tag()` | 06 | Storage |
| 7 | `storage.list_versions()` | 06 | Storage |
| 8 | `storage.list_tags()` | 06 | Storage |
| 9 | `storage.read_dataset(version=N)` | 06 | Storage |
| 10 | `storage.read_at_tag()` | 06 | Storage |
| 11 | `storage.compact()` | 06 | Storage |
| 12 | `BlobStoreManager` (upload/list/presigned/delete) | 06, 09 | Standalone |
| 13 | `storage.restore_dataset()` | 06 | Storage |
| 14 | `lake.export(compression=...)` | 06 | Lake API |
| 15 | `SchemaValidationGate.validate()` | 07 | Standalone |
| 16 | `lake.quality_filter(mode="any")` | 07 | Lake API |
| 17 | `DeadLetterWriter.write()` | 07 | Standalone |
| 18 | `lake.deduplicate(strategy="perceptual")` | 07, 09 | Lake API |
| 19 | `lake.query()` (MetadataSearchBridge) | 07 | Lake API |
| 20 | `lake.daft_query()` | 07, 08 | Lake API |
| 21 | `lake.materialize()` | 07 | Lake API |
| 22 | `lake.cleanup_materialized()` | 07 | Lake API |
| 23 | `lake.olap_query(max_rows, enable_streaming)` | 07 | Lake API |
| 24 | `AuditTrail.verify_chain()` | 07 | Standalone |
| 25 | `lake.ingest_http()` | 08 | Lake API |
| 26 | 12 步血缘链 | 08, 09 | Lake API |
| 27 | `olap_query(tables=4表)` | 08 | Lake API |
| 28 | `daft_query().join()` | 08 | Lake API |
| 29 | `VideoProcessor.extract_keyframes()` | 09 | Standalone |
| 30 | `CLIPImageEncoder.encode()` | 09 | Standalone |
| 31 | `Ingestor.ingest_videos()` | 09 | Lake API |
| 32 | `UnifiedTableManager` | 09 | Standalone |
| 33 | `ImageResolutionFilter` | 09 | Standalone |
| 34 | Blob 大文件上传 (视频) | 09 | Standalone |
| 35 | 多模态向量搜索 (image_embedding) | 09 | Lake API |
| 36 | `ImageProcessor` (缩略图/预览) | 09 | Standalone |

---

## 实施步骤

### Step 1: 创建 YAML 配置文件
- `examples/s3_minio/config.yaml` — 供 `Lake.from_yaml()` 使用
- 包含 S3/MinIO 配置、OLAP 配置、Media 配置

### Step 2: 实现示例 06
- `examples/s3_minio/06_incremental_data_lifecycle.py`
- 10 步，每步有清晰的 print 输出
- 数据: 传感器数据 (device_id, timestamp, heart_rate, blood_pressure, temperature, status)

### Step 3: 实现示例 07
- `examples/s3_minio/07_quality_governance_and_materialization.py`
- 10 步，含 quality/dead_letter/materialize/audit
- 数据: 交易数据 (transaction_id, amount, currency, merchant, category, timestamp, risk_score)

### Step 4: 实现示例 08
- `examples/s3_minio/08_complex_lineage_and_governance_olap.py`
- 8 步，含 ingest_http/lineage/4-table-JOIN
- 数据: 供应链 (suppliers/products/shipments/warehouses)

### Step 5: 实现示例 09
- `examples/s3_minio/09_video_intelligent_analysis.py`
- 10 步，含视频合成/关键帧提取/CLIP嵌入/多模态搜索/blob存储
- 数据: 合成视频 (PyAV)，3-5 段不同场景

### Step 6: 本地验证
```bash
uv run python examples/s3_minio/06_incremental_data_lifecycle.py
uv run python examples/s3_minio/07_quality_governance_and_materialization.py
uv run python examples/s3_minio/08_complex_lineage_and_governance_olap.py
uv run python examples/s3_minio/09_video_intelligent_analysis.py
```

---

## 已知风险与规避

| 风险 | 规避策略 |
|------|----------|
| `ingest_http()` 无外网 | try/except + fallback 到 `ingest()` |
| `lineage_query(sql)` S3 bug | try/except + 使用 `lineage_history()` 替代 |
| `daft` 未安装 | 检查 import，skip with warning |
| `materialize` 需要 ducklake | 检查配置，skip with warning |
| `BlobStoreManager` S3 需要 boto3 | 配置中已依赖，MinIO 兼容 |
| `DeadLetterWriter` 需要 storage 对象 | 从 `lake._get_storage()` 获取 |
| CLIP 模型首次下载慢 (~600MB) | try/except + ModelScope 国内源 + skip with warning |
| PyAV 合成视频兼容性 | 使用简单纯色/噪声帧，避免编码器差异 |

---

## 非目标

- 不修改框架核心代码 (除非发现必须修复的 bug)
- 不添加新的测试文件 (示例本身就是验证)
- 不修改已有 5 个示例

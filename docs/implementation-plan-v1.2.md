# Arrow Lake v1.2 实施规划

**版本**: v1.2-plan | **日期**: 2026-04-22 | **总工期**: 12 周
**基于**: v1.1 发布，25,598 行，151 文件，1802 测试全通过
**状态**: 待确认

---

## 版本定位

**v1.1 解决了"系统能跑"** — 稳定性、可观测性、DuckDB HA 框架。

**v1.2 的核心命题是"从功能完备到客户可交付 + 企业知识管理"**：
1. 完成安全加固和 DuckDB 连接治理（架构收尾）
2. 新增 PDF 文档处理能力（产品突破）
3. 统一 Embedding 标准和文档存储规范（标准化）

---

## 架构评审结论

### 四角色共识

| 维度 | 决策 | 理由 |
|------|------|------|
| **PDF 解析** | marker-pdf v1.10.2 为主 + TurboOCR 为辅 | marker-pdf 处理 80% 文本 PDF（本地部署），TurboOCR 处理 20% 扫描件（270 img/s 差异化） |
| **文档格式 MVP** | 仅 PDF（文本型 + 扫描型） | 一个格式做到极致比五个格式半成品更有说服力 |
| **文档存储** | 原始文件存 S3/MinIO，提取文本存 Lance | BlobStoreManager 已就绪，blob_key 写入 Lance metadata |
| **Embedding 标准** | Qwen3-VL-Embedding 系列，默认 2B | 多模态嵌入（文本+图像+视频），MRL 灵活维度 |
| **DuckDB HA** | 6 个 bridge 迁移 + 连接复用 + 加固 | SessionManager 已实现但零 bridge 接入 |
| **示例数据** | 25-30 真实 PDF，200-500 页 | 学术/商务/扫描/中文四类，体现现实情况 |
| **工期** | 12 周（原 8 周 + 4 周） | 文档处理新增 ~3 周开发 + ~1 周测试 |
| **不做** | DOCX/PPT/前端 UI/OAuth2/多租户/Ray 重构/告警体系 | 聚焦，不贪全 |

### Review Check 修正项

以下为最终 review 发现并修正的问题：

| # | 原始描述 | 修正后 | 原因 |
|---|---------|--------|------|
| 1 | "7 个 query bridge" | **6 个** (olap, vector, fts, hybrid, faceted, metadata) | `ensemble.py` 不直接调用 `create_duckdb_session` |
| 2 | "CORS wildcard" | **Swagger/docs 无条件暴露** | `cors_origins` 默认空列表（deny-all），实际问题是 docs 端点公开 |
| 3 | marker-pdf 作为直接依赖 | **作为外部服务或 CLI 子进程调用** | GPL-3.0 许可证与 Arrow Lake MIT 许可证冲突 |
| 4 | Embedding 维度校验已存在 | **需新增实现** | 当前仅 runtime 发现维度，无 dataset 维度一致性校验 |
| 5 | test_session_manager.py 存在 | **需新建** | 文件尚未创建 |

---

## DuckDB 高可用实现详情

### 当前状态（v1.1）

| 组件 | 状态 | 说明 |
|------|------|------|
| DuckDBSessionManager | ✅ 已实现 | 284 行，信号量限流、6 个 Prometheus 指标、优雅关闭 |
| DuckDBSession | ✅ 已实现 | Extension 加载、资源治理、S3 配置 |
| OlapConfig 治理参数 | ✅ 已实现 | `max_concurrent_queries=4`, `max_query_memory_mb=512`, `query_timeout_seconds=300` |
| 异步执行器 | ✅ 已实现 | `configure_query_executor(max_workers)`, 独立 semaphore |
| Bridge 迁移 | ❌ 未开始 | 0/6 bridge 使用 SessionManager，全部走 `create_duckdb_session()` |
| 连接复用 | ❌ 未实现 | 每次 acquire 创建新连接，release 时销毁 |
| 健康检查 | ❌ 未实现 | 无 idle connection 验证 |
| 故障恢复 | ❌ 未实现 | 无重试、无僵尸检测、无 idle eviction |

### Bridge 迁移清单（6 文件，10 个调用点）

| # | 文件 | 调用点数 | 行号 |
|---|------|---------|------|
| 1 | `query/olap.py` | 4 | L145, L249, L273, L304 |
| 2 | `query/vector.py` | 1 | L407 |
| 3 | `query/fts.py` | 1 | L296 |
| 4 | `query/hybrid.py` | 1 | L258 |
| 5 | `query/faceted.py` | 2 | L153, L213 |
| 6 | `query/metadata.py` | 1 | L135 |
| | **合计** | **10** | |

### SessionManager Phase 2 增强（连接复用）

```
DuckDBSessionManager
├── _idle_pool: deque[_ManagedSession]  ← 新增：空闲连接池
├── _idle_lock: threading.Lock          ← 新增：池操作锁
├── _idle_timeout_seconds: int          ← 新增：空闲超时（默认 300s）
├── acquire()
│   ├── 先从 _idle_pool 弹出
│   ├── 健康检查 SELECT 1
│   ├── 通过则复用，失败则创建新连接
│   └── acquire 时 RESET ALL + 幂等 LOAD lance
├── _release_session()
│   ├── 健康检查
│   ├── 通过则归还 _idle_pool（未满时）
│   └── 不通过或池满则销毁
└── shutdown()
    └── 排空 _idle_pool，逐个关闭
```

### SessionManager Phase 5 增强（故障恢复）

| 能力 | 实现 | 配置 |
|------|------|------|
| 健康检查 | `SELECT 1`，acquire 和 release 时 | 内置 |
| Idle eviction | 归还时记录时间戳，acquire 时检查 | `idle_timeout_seconds=300` |
| 僵尸检测 | 追踪 `_created_at`，超时强制关闭 | `max_session_lifetime_seconds=3600` |
| Acquire 重试 | 连接创建失败重试 1 次（非池满重试） | 内置 |
| DuckDB 状态隔离 | acquire 时 `RESET ALL`，幂等 `LOAD lance` | 内置 |

### 新增 Prometheus 指标

```python
# Phase 5 新增
duckdb_pool_health_checks_total: Counter  # label: result=pass|fail
duckdb_pool_evicted_connections_total: Counter  # label: reason=idle_timeout|max_lifetime|health_check_failed
```

### 异步执行器对齐

`_async.py` 的 `run_duckdb_query()` 需修改：
- 当 `session_manager` 可用时，使用 `session_manager.acquire()` 替代独立 semaphore + ThreadPoolExecutor
- 双重限流机制（asyncio.Semaphore + threading.Semaphore）需消除
- 保留 backward compatibility（无 session_manager 时走原路径）

### 应用层接入

需要在 `Lake` facade 中：
1. 创建 `DuckDBSessionManager` 实例（`_get_component("session_manager", _factory)`）
2. 传递给所有 bridge
3. `shutdown()` 时调用 `session_manager.shutdown()`

---

## 文档处理技术选型

### marker-pdf v1.10.2

| 项目 | 详情 |
|------|------|
| 发布日期 | 2026-01-31 |
| Python | >=3.10, <4.0 |
| 许可证 | **GPL-3.0-or-later** |
| 核心依赖 | PyTorch, surya v0.17.1, transformers, timm |
| 输出格式 | markdown, json, html, chunks（RAG-ready） |
| PDF 模式 | 原生文本提取、OCR（surya）、布局检测、表格识别 |
| LLM 增强 | 支持 Gemini/Claude/OpenAI/Ollama 提升质量 |
| 性能 | ~1-3 页/秒（GPU），heuristic score 95.67 |

**⚠️ GPL-3.0 许可证处理**：Arrow Lake 使用 MIT 许可证，marker-pdf 使用 GPL-3.0。直接 import 会导致衍生作品受 GPL 约束。

**解决方案**：
- marker-pdf 通过 **CLI 子进程调用**（`subprocess.run(["marker_single", ...])`），不直接 import
- 或通过 HTTP API 调用独立部署的 marker-pdf 服务
- TurboOCR 本身是 C++ 独立 Docker 服务（HTTP/gRPC），无许可证冲突

### TurboOCR

| 项目 | 详情 |
|------|------|
| 语言 | C++20 + CUDA + TensorRT 10.16 |
| 部署 | Docker 微服务（HTTP:8000, gRPC:50051） |
| 引擎 | PP-OCRv5 |
| 吞吐量 | 270 img/s（FUNSD 基准） |
| PDF 模式 | ocr / geometric / auto / auto_verified |
| 许可证 | 开源（无 GPL 冲突） |
| GPU 要求 | Turing+ 架构，NVIDIA 驱动 595+，8GB+ VRAM |

### 组合方案

```
PDF 上传
    │
    ├── 文本 PDF → marker-pdf CLI → Markdown + chunks
    │
    ├── 扫描 PDF → TurboOCR (mode=auto) → 文本 + bounding box
    │
    ├── 混合 PDF → marker-pdf → 失败页 fallback TurboOCR
    │
    └── 处理失败 → pypdf 纯文本提取（保底）
```

**推荐配置**：
- 默认模式：marker-pdf（CLI 子进程，无外部依赖）
- 可选增强：TurboOCR Docker 服务（扫描件/批量 OCR）
- `pdf_mode="auto"`：先 marker-pdf，失败自动 fallback TurboOCR

### Qwen3-VL-Embedding 模型标准

| 模型 | 参数量 | 最大维度 | MRL 范围 | 上下文 | 输入模态 | 许可证 | 适用场景 |
|------|--------|---------|---------|--------|---------|--------|---------|
| **Qwen3-VL-Embedding-2B** | 2B | 2048 | 64-2048 | 32K | 文本/图像/视频 | Apache-2.0 | 默认/开发，LanceDB IVF-PQ 内存友好 |
| Qwen3-VL-Embedding-8B | 8B | 4096 | 64-4096 | 32K | 文本/图像/视频 | Apache-2.0 | 生产 RAG，高精度，需 GPU |
| Qwen3-VL-Reranker-2B | 2B | - | - | 32K | 文本/图像 | Apache-2.0 | 两阶段 RAG 重排序（轻量） |
| Qwen3-VL-Reranker-8B | 8B | - | - | 32K | 文本/图像 | Apache-2.0 | 两阶段 RAG 重排序（高精度） |

**多模态输入格式**（SentenceTransformers API）：
```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("Qwen/Qwen3-VL-Embedding-2B")
# 文本
emb = model.encode("Hello world")
# 图像
emb = model.encode({"image": "https://example.com/image.png"})
# 文本+图像
emb = model.encode({"text": "描述这张图片", "image": "https://example.com/image.png"})
```

**关键特性**：
- **MRL（Matryoshka Representation Learning）**：支持任意维度输出（64-max），灵活适配索引效率需求
- **Instruction-aware**：默认 "Represent the user's input."，可自定义任务指令
- **30+ 语言**：原生中文支持
- **vLLM/SGLang 部署**：`runner="pooling"` 模式，适合大规模推理
- **依赖要求**：`transformers>=4.57.0`, `qwen-vl-utils>=0.0.14`, `torch>=2.0`

**v1.2 新增**：
- `EmbeddingConfig` 增加 `embedding_dim` 字段（0 = auto-detect），默认模型改为 `Qwen/Qwen3-VL-Embedding-2B`
- `LocalEmbeddingEncoder` 增加多模态输入支持（dict 格式）和维度映射表
- `LocalEmbeddingEncoder` 增加 MRL 维度输出控制（`truncate_dim` 参数）
- dataset 创建时校验 embedding 维度一致性
- `model_source` 验证器增加 Qwen3-VL 系列白名单 warn

---

## Phase 规划

### Phase 1: 安全紧急修复（Week 1）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 1.1 | SQL 注入修复 | `query/ducklake_workspace.py` | 16 处 f-string SQL → `validate_identifier()` + 参数化 |
| 1.2 | 认证强制 | `api/auth.py`, `api/auth_service.py` | `api_key` 为空时拒绝启动 |
| 1.3 | Swagger 生产关闭 | `api/app.py` | 配置控制 `/docs`, `/redoc`, `/openapi.json` 可见性 |
| 1.4 | CORS 确认 | `api/app.py`, `config/api.py` | 当前 `cors_origins=[]` 实为 deny-all，确认符合预期 |
| 1.5 | Rate limit 默认开 | `config/api.py` | `RateLimitConfig.enabled` 默认 `True` |

**验收**: 安全扫描 0 CRITICAL，现有 1802 测试全通过。

---

### Phase 2: SessionManager 迁移 + 连接复用（Week 2-3）

#### Week 2: 连接复用 + 应用层接入

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 2.1 | SessionManager idle pool | `query/session_manager.py` | `_idle_pool` deque + 复用逻辑 + `RESET ALL` 隔离 |
| 2.2 | 应用层 SessionManager 实例 | `_lake_ingest.py` 或 `lake.py` | `Lake` facade 创建并持有 SessionManager 实例 |
| 2.3 | Bridge 注入 SessionManager | 6 个 bridge 文件 | 新增可选 `session_manager` 参数，backward compatible |
| 2.4 | SessionManager 单元测试 | `tests/unit/test_session_manager.py` | 连接复用、健康检查、idle eviction 基础测试 |

#### Week 3: Bridge 迁移 + 异步对齐

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 2.5 | olap.py 迁移 | `query/olap.py` | 4 个 `create_duckdb_session()` → `acquire()` |
| 2.6 | vector/fts/hybrid 迁移 | 3 个文件 | 各 1 个调用点迁移 |
| 2.7 | faceted/metadata 迁移 | 2 个文件 | faceted 2 个 + metadata 1 个调用点 |
| 2.8 | 异步执行器对齐 | `query/_async.py` | 消除双重限流，无 session_manager 时保留原路径 |
| 2.9 | 全量回归测试 | 全项目 | 1802+ 测试全通过，6 个 pool 指标有值 |

**验收**: 6 个 bridge（10 个调用点）全量走 SessionManager，连接复用生效，pool 指标非零。

---

### Phase 3: 代码质量（Week 4）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 3.1 | backup.py 拆分 | `ops/backup.py` → `ops/backup_restore.py` | Restore 逻辑提取为 `BackupRestorer`，backup.py <500 行 |
| 3.2 | GraphRAG 异常硬化 | `rag/graph_rag.py` | 3 个 `except Exception`：处理 `asyncio.CancelledError` + 细化异常类型 + 死代码清理 |
| 3.3 | ducklake except 修复 | `query/ducklake_workspace.py` | 4 个 `except Exception` → `duckdb.CatalogException` / `duckdb.ParserException` |
| 3.4 | 全量回归 | 全项目 | 1802+ 测试全通过 |

**验收**: backup.py <500 行，ducklake/graph_rag 零 `except Exception`。

---

### Phase 4: 文档处理管线（Week 5-7）⭐ 核心新增

#### Week 5: 基础骨架 + PDF 解析

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 4.1 | 文档配置 | `config/document.py`（新增） | `DocumentConfig`: OCR endpoint, PDF mode, chunk 策略, blob 存储选项 |
| 4.2 | 枚举扩展 | `config/_enums.py` | `OcrBackend`, `PdfParseMode`, `ChunkStrategy` |
| 4.3 | 错误码扩展 | `exceptions.py` | `DOCUMENT_PARSE_FAILED`, `DOCUMENT_OCR_FAILED`, `DOCUMENT_CHUNK_FAILED` |
| 4.4 | marker-pdf CLI wrapper | `ingest/document.py`（新增） | `MarkerPdfWrapper`: 通过 subprocess 调用 marker CLI，规避 GPL 许可证问题 |
| 4.5 | 文档分块 | `ingest/chunker.py`（新增） | `DocumentChunker`: page / paragraph / recursive 三种策略 |

#### Week 6: TurboOCR + BlobStore 集成 + 摄入 API

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 4.6 | TurboOCR 客户端 | `ingest/ocr.py`（新增） | HTTP client wrapper，健康检查，重试，circuit breaker |
| 4.7 | 组合解析器 | `ingest/document.py` | marker-pdf 为主 + TurboOCR fallback + pypdf 保底 |
| 4.8 | BlobStore 集成 | `ingest/document.py` | 原始 PDF → S3/MinIO，blob_key 写入 Lance metadata |
| 4.9 | Ingestor 扩展 | `ingest/ingestor.py` | 新增 `ingest_documents()` 方法 |
| 4.10 | Facade 集成 | `_lake_ingest.py` | `ingest_documents()` Mixin 方法 |
| 4.11 | API endpoint | `api/routers/datasets.py` | `POST /api/v1/datasets/{name}/ingest/documents` |

#### Week 7: Embedding 标准化 + 示例数据 + 端到端

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 4.12 | Embedding 配置增强 | `config/media.py` | Qwen3-VL-Embedding 2B/8B 白名单 + 维度映射 + 多模态支持 |
| 4.13 | Encoder 维度校验 | `embed/encoder.py` | 加载后校验 embedding_dim，dataset 维度一致性检查 |
| 4.14 | 示例数据集 | `tests/fixtures/documents/`（新增） | 25-30 真实 PDF（学术/商务/扫描/中文四类，200-500 页） |
| 4.15 | E2E Demo 脚本 | `examples/document_ingest/`（新增） | 4 个场景（法务/研发/投研/GraphRAG） |
| 4.16 | 依赖更新 | `pyproject.toml` | 新增 `[document]` optional group: pypdf>=5.0, pdf2image>=1.17 |
| 4.17 | 单元测试 | `tests/unit/test_document_ingest.py`, `test_turbo_ocr.py` | 核心逻辑测试，mock 外部服务 |

**文档处理数据流**:
```
PDF 上传 → BlobStore (原始文件存 S3/MinIO, key: documents/{year}/{month}/{doc_id}/{filename})
    → DocumentParser (marker-pdf CLI / TurboOCR HTTP / pypdf fallback)
    → DocumentChunker (page/paragraph/recursive)
    → LocalEmbeddingEncoder (Qwen3-VL-Embedding, 文本+图像多模态)
    → LanceStorageManager (text + embedding + blob_key + document_id + page_number + metadata)
    → RAG / GraphRAG 可直接查询
```

**marker-pdf GPL-3.0 规避方案**:
```python
# arrow_lake/ingest/document.py — 不直接 import marker
import subprocess
import json

def _call_marker_cli(pdf_path: str, output_format: str = "chunks") -> str:
    """通过 CLI 子进程调用 marker-pdf，规避 GPL 许可证约束."""
    result = subprocess.run(
        ["marker_single", pdf_path, "--output_format", output_format],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise DocumentError(...)
    return result.stdout
```

**验收**: PDF 上传 → OCR → 分块 → 向量化 → RAG 查询全链路通过，示例数据 25+ 文档成功摄入。

---

### Phase 5: SessionManager 加固（Week 8）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 5.1 | 连接健康检查 | `query/session_manager.py` | `_health_check(conn)`: `SELECT 1`，acquire/release 时调用 |
| 5.2 | Idle eviction | `query/session_manager.py` | `_idle_timeout_seconds=300`，超时连接在 acquire 时清理 |
| 5.3 | 僵尸连接检测 | `query/session_manager.py` | `_max_session_lifetime_seconds=3600`，追踪 `_created_at` |
| 5.4 | Acquire 重试 | `query/session_manager.py` | 连接创建失败时重试 1 次（仅限创建失败，非池满） |
| 5.5 | 新增 Prometheus 指标 | `core/metrics.py` | `duckdb_pool_health_checks_total`, `duckdb_pool_evicted_connections_total` |
| 5.6 | SessionManager 测试补充 | `tests/unit/test_session_manager.py` | 健康检查、idle eviction、僵尸检测、重试测试 |

**验收**: 并发 10 查询验证池限流 + 故障恢复 + 8 个 pool 指标正确。

---

### Phase 6: 产品可交付（Week 9-10）

| # | 任务 | 说明 |
|---|------|------|
| 6.1 | 一键部署完善 | Docker Compose 含 TurboOCR 服务（`--profile ocr`），GPU 资源预留 |
| 6.2 | 性能基线 | DuckDB 查询延迟 + PDF 处理吞吐量 + Embedding 生成速度基准 |
| 6.3 | 死信队列 | 摄取失败数据进入重试队列（Ray Task 或内存 queue） |
| 6.4 | 文档处理安全 | 文件类型白名单、50MB 上限、TurboOCR 网络隔离（`expose` 非 `ports`） |
| 6.5 | TurboOCR Docker Compose | `docker-compose.ocr.yml` overlay：网络隔离、GPU 资源、healthcheck |

**TurboOCR 安全配置要点**:
- 使用 `expose` 而非 `ports`，不暴露主机端口
- 独立 `ocr-internal` Docker 网络
- GPU 资源预留 (`reservations.devices`)
- `start_period: 60s`（GPU 模型加载慢）
- 文件大小限制 50MB，页数限制 100 页

**验收**: `docker compose --profile ocr up` 一键启动全栈。

---

### Phase 7: 集成验证 + 发布（Week 11-12）

| # | 任务 | 说明 |
|---|------|------|
| 7.1 | 文档处理 E2E | PDF upload → OCR → chunk → embed → vector search → RAG query 全链路 |
| 7.2 | 回归测试 | 1802+ 单元测试 + 集成测试全通过 |
| 7.3 | S3 E2E 回归 | 9 个 S3/MinIO 示例全通过 |
| 7.4 | 性能回归 | 对比 v1.1 基准，关键路径性能无回退 |
| 7.5 | ADR-08 | 记录 SessionManager 迁移 + 文档处理架构决策 |
| 7.6 | v1.2 发布 | 打 tag `v1.2.0`，推送 Gitee |

---

## 新增文件清单

| 文件 | 行数估算 | 说明 |
|------|---------|------|
| `arrow_lake/ingest/document.py` | ~400 | 文档处理核心（DocumentParser + marker CLI wrapper + 组合解析器） |
| `arrow_lake/ingest/ocr.py` | ~150 | TurboOCR HTTP client |
| `arrow_lake/ingest/chunker.py` | ~200 | 文档分块策略 |
| `arrow_lake/config/document.py` | ~60 | 文档处理配置 |
| `arrow_lake/ops/backup_restore.py` | ~300 | Restore 逻辑（从 backup.py 提取） |
| `tests/unit/test_document_ingest.py` | ~300 | 文档处理单元测试 |
| `tests/unit/test_turbo_ocr.py` | ~150 | TurboOCR client 测试 |
| `tests/unit/test_session_manager.py` | ~400 | SessionManager 完整测试（新建） |
| `examples/document_ingest/demo_legal.py` | ~100 | 法务合同场景 Demo |
| `examples/document_ingest/demo_rd.py` | ~100 | 研发文档场景 Demo |
| `examples/document_ingest/demo_research.py` | ~80 | 投研报告场景 Demo |
| `examples/document_ingest/demo_graphrag.py` | ~100 | GraphRAG 知识图谱 Demo |
| `deploy/docker-compose.ocr.yml` | ~60 | TurboOCR Docker Compose overlay |
| `docs/adr-08-v1.2-architecture.md` | ~200 | v1.2 架构决策记录 |

## 修改文件清单

| 文件 | 改动类型 | Phase |
|------|---------|-------|
| `query/ducklake_workspace.py` | SQL 注入修复 + except硬化 | 1, 3 |
| `api/auth.py` | 认证强制 | 1 |
| `api/auth_service.py` | 认证强制 | 1 |
| `api/app.py` | Swagger 条件暴露 | 1 |
| `config/api.py` | Rate limit 默认开 | 1 |
| `query/session_manager.py` | idle pool + 健康检查 + eviction + 重试 | 2, 5 |
| `query/olap.py` | bridge 迁移 | 2 |
| `query/vector.py` | bridge 迁移 | 2 |
| `query/fts.py` | bridge 迁移 | 2 |
| `query/hybrid.py` | bridge 迁移 | 2 |
| `query/faceted.py` | bridge 迁移 | 2 |
| `query/metadata.py` | bridge 迁移 | 2 |
| `query/_async.py` | 异步执行器对齐 | 2 |
| `ops/backup.py` | 提取 restore 逻辑 | 3 |
| `rag/graph_rag.py` | except 硬化 + 死代码清理 | 3 |
| `ingest/ingestor.py` | 新增 ingest_documents() | 4 |
| `_lake_ingest.py` | ingest_documents() Mixin | 4 |
| `exceptions.py` | DOCUMENT_* 错误码 | 4 |
| `config/_enums.py` | 新增枚举 | 4 |
| `config/main.py` | 注册 DocumentConfig | 4 |
| `config/media.py` | Embedding 维度校验 | 4 |
| `embed/encoder.py` | 维度映射 + 一致性校验 | 4 |
| `core/metrics.py` | 2 个新 pool 指标 | 5 |
| `pyproject.toml` | 新增 [document] optional group | 4 |

## 依赖变更

```toml
[project.optional-dependencies]
document = [
    "pypdf>=5.0",
    "pdf2image>=1.17",
]
# marker-pdf 通过外部 CLI 安装，不作为 Python 依赖（GPL-3.0）
# TurboOCR 通过 Docker 镜像部署，不作为 Python 依赖
```

## 风险矩阵

| 风险 | 等级 | 缓解 |
|------|------|------|
| marker-pdf GPL-3.0 许可证 | **HIGH** | CLI 子进程调用，不直接 import |
| TurboOCR 服务不可用 | MEDIUM | Fallback 到 marker-pdf CLI → pypdf 保底 |
| PDF 恶意内容（炸弹/JS） | CRITICAL | 文件类型白名单 + 50MB 上限 + 禁用 JS + 解压后大小限制 |
| SessionManager 迁移回归 | HIGH | 逐 bridge 迁移 + 全量测试 + backward compatible |
| 大 PDF 内存溢出 | MEDIUM | 流式逐页处理 + 内存限制 + 优雅降级 |
| DuckDB 连接状态泄漏 | MEDIUM | acquire 时 `RESET ALL` + 幂等 extension 加载 |
| Qwen3-VL-Embedding 8B 显存不足 | LOW | 2B 作为默认（无 GPU 可用），8B 需 GPU 检测 |
| 文档 chunking 影响 RAG 质量 | MEDIUM | 多策略可选 + quality 框架集成 |

## 度量指标

| 指标 | v1.1 基线 | v1.2 目标 |
|------|----------|----------|
| `except Exception` | 15 | <10 |
| SessionManager bridge 接入 | 0/6 | 6/6 |
| 连接复用 | 无 | idle pool 生效 |
| PDF 文档支持 | 无 | PDF (文本+扫描) |
| TurboOCR 集成 | 无 | Docker 微服务 |
| Embedding 维度校验 | 无 | 自动校验 |
| backup.py 行数 | 716 | <500 |
| 示例数据规模 | 小规模 | 25-30 PDF, 200-500 页 |

## 验证计划

```bash
# 1. 单元测试
pytest tests/unit/ -x

# 2. DuckDB HA 验证
pytest tests/unit/test_session_manager.py -v
# 并发 10 查询，验证池限流和连接复用

# 3. 文档处理验证
# 准备 TurboOCR: docker compose --profile ocr up turboocr
pytest tests/unit/test_document_ingest.py -v
pytest tests/unit/test_turbo_ocr.py -v
# 运行 4 个 Demo 脚本

# 4. 全链路 E2E
pytest tests/unit/ tests/integration/ -x
uv run python examples/document_ingest/demo_legal.py
uv run python examples/document_ingest/demo_graphrag.py

# 5. S3 回归
uv run python examples/s3_minio/06_*.py
uv run python examples/s3_minio/07_*.py
uv run python examples/s3_minio/08_*.py
uv run python examples/s3_minio/09_*.py

# 6. 性能基线
uv run python -m pytest tests/benchmark/ -v
```

## 实施时间线

```
Week 1:    Phase 1 — 安全紧急修复
Week 2-3:  Phase 2 — SessionManager 迁移 + 连接复用
Week 4:    Phase 3 — 代码质量
Week 5-7:  Phase 4 — 文档处理管线 ⭐
Week 8:    Phase 5 — SessionManager 加固
Week 9-10: Phase 6 — 产品可交付
Week 11-12: Phase 7 — 集成验证 + 发布
```

## 关键文件路径

| 类别 | 文件 |
|------|------|
| SessionManager | `arrow_lake/query/session_manager.py` |
| DuckDB Session | `arrow_lake/query/_db.py` |
| 异步执行器 | `arrow_lake/query/_async.py` |
| 待迁移的 6 个 bridge | `query/{olap,vector,fts,hybrid,faceted,metadata}.py` |
| OLAP 配置 | `arrow_lake/config/olap.py` |
| Pool 指标 | `arrow_lake/core/metrics.py` |
| Blob 存储 | `arrow_lake/storage/blob_store.py` |
| 文档处理（新增） | `arrow_lake/ingest/{document,ocr,chunker}.py` |
| 文档配置（新增） | `arrow_lake/config/document.py` |
| Embedding | `arrow_lake/embed/encoder.py` |
| Embedding 配置 | `arrow_lake/config/media.py` |
| ADR-07 | `docs/adr-07-duckdb-high-availability.md` |
| ADR-08（新增） | `docs/adr-08-v1.2-architecture.md` |

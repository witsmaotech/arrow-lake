# Daft 在 Arrow Lake 中的两个定位实施计划

## Context

Daft 目前在 Arrow Lake 中只被当作文件读取器（`daft.read_*() → .to_arrow()`），其分布式多模态 DataFrame + AI 函数的核心能力完全没有发挥。本计划分两个定位重新激活 Daft：

- **定位 A**: 摄取管道 DataFrame 引擎 — 让数据在 Daft DataFrame 层停留更久，支持 ETL 转换
- **定位 B**: 多模态 + AI 批处理引擎 — 用 Daft 并行分区嵌入替代单线程批处理

分 4 个 Sprint 交付，每个 Sprint 独立可验证、向后兼容。

---

## Sprint 1: 定位 A 阶段 1 — DataFrame 摄取管道 + 转换钩子

### 目标
让 Ingestor 内部数据以 `daft.DataFrame` 形式流转，不再读文件后立刻 `.to_arrow()`，并支持可选的 ETL transforms。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/ingest/ingestor.py` | 新增 `_read_file_df()` 返回 `daft.DataFrame`；`ingest()` 增加 `transforms` 参数；`_read_file()` 改为调用 `_read_file_df().to_arrow()` |
| `arrow_lake/ingest/transforms.py` | **新建**。`build_transforms(spec) → list[Callable]`，从 JSON 规范构建 Daft 转换函数（rename、select、filter、cast、add_constant） |
| `arrow_lake/_lake_ingest.py` | `ingest()` 和 `ingest_http()` 增加 `transforms` 参数透传 |
| `arrow_lake/config/infra.py` | `DaftConfig` 增加 `ingest_use_daft_pipeline: bool = True` |
| `arrow_lake/api/routers/datasets.py` | 摄取请求模型增加 `transforms` 字段（JSON 规范） |

### 数据流变化
```
Before: daft.read_csv(path) → .to_arrow() → pa.Table → LanceDB.add()
After:  daft.read_csv(path) → [transforms] → .to_arrow() → LanceDB.add()
```

### 关键实现细节

`_read_file_df()` — 与现有 `_read_file()` 签名相同但返回 `daft.DataFrame`：
```python
@staticmethod
def _read_file_df(path, file_type, *, columns=None) -> daft.DataFrame:
    read_kwargs = {}
    if columns and file_type in ("csv", "parquet"):
        read_kwargs["columns"] = columns
    if file_type == "csv":
        return daft.read_csv(str(path), **read_kwargs)
    elif file_type == "json":
        return daft.read_json(str(path))
    elif file_type == "parquet":
        return daft.read_parquet(str(path), **read_kwargs)
```

`ingest()` 核心循环变更：
```python
for fp in file_paths:
    ft = self._detect_file_type(fp)
    df = self._read_file_df(fp, ft)
    if transforms:
        for t in transforms:
            df = t(df)
    table = df.to_arrow()
    self._write_table(dataset_name, table, sources, fp)
```

### 验证
- 现有测试不变（无 transforms 时行为相同）
- 新增单元测试 `_read_file_df()` 返回类型
- 新增测试 transforms 参数正确应用

### 风险：低
- `_read_file()` 向后兼容（内部调用 `_read_file_df().to_arrow()`）
- 图像/视频/文档摄取路径不经过 `_read_file()`，不受影响

---

## Sprint 2: 定位 B 阶段 1 — Daft 批量嵌入管道

### 目标
新增 `DaftBatchEncoder`，使用 `daft.functions.embed_text()` 做并行分区嵌入，作为与 LocalEmbeddingEncoder 平行的可选后端。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/embed/daft_encoder.py` | **新建**。`DaftBatchEncoder` 类，`encode_column(table, column) → EmbeddingResult` |
| `arrow_lake/config/_enums.py` | `EmbeddingBackend` 增加 `DAFT = "daft"` |
| `arrow_lake/config/media.py` | `EmbeddingConfig` 增加 `daft_num_partitions: int = 4`、`daft_provider: str = "transformers"` |
| `arrow_lake/_lake_ingest.py` | `embed_and_add()` 增加 DAFT 后端分支 |
| `arrow_lake/api/routers/embedding.py` | embed 端点支持 DAFT 后端 |

### 关键实现细节

`DaftBatchEncoder.encode_column()`：
```python
def encode_column(self, table: pa.Table, column: str = "text_content") -> EmbeddingResult:
    import daft
    import daft.functions as F

    df = daft.from_arrow(table)
    df = df.into_partitions(self._num_partitions)
    emb_col = f"{column}_embedding"

    df = df.with_column(emb_col,
        F.embed_text(daft.col(column), provider=self._provider, model=self._model))

    result = df.select(emb_col).to_arrow()
    # 提取 FixedSizeListArray，构建 EmbeddingResult
```

与 `embed_and_add()` 集成：
```python
if emb_cfg.backend == EmbeddingBackend.DAFT:
    encoder = DaftBatchEncoder(
        provider=emb_cfg.daft_provider,
        model=emb_cfg.model,
        num_partitions=emb_cfg.daft_num_partitions,
    )
    result = encoder.encode_column(table, column=text_column)
```

### 验证
- 单元测试 mock `daft.functions.embed_text`
- 集成测试对比 Daft/Local 编码器余弦相似度
- 配置 `EMBEDDING__BACKEND=daft` 切换后端

### 风险：低-中
- 新后端是 opt-in，不影响现有 LOCAL/OPENAI/RAY_SERVE
- `daft.functions.embed_text()` 需要模型可达，失败时有明确错误

---

## Sprint 3: 定位 A 阶段 2 — Daft 直接写入 Lance + 多文件批量摄取

### 目标
大文件/多文件摄取时用 `daft.write_lance()` 一次性写入，跳过逐文件 Arrow 转换。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/ingest/storage.py` | `LanceStorageManager` 新增 `write_lance_from_dataframe()` |
| `arrow_lake/ingest/ingestor.py` | 新增 `ingest_batch()` 方法 — 多文件一次读取 + 转换 + 写入 |
| `arrow_lake/_lake_ingest.py` | 新增 `ingest_batch()` facade 方法 |

### 关键实现细节

`write_lance_from_dataframe()`：
```python
def write_lance_from_dataframe(self, name, df, mode="create"):
    with self._dataset_lock(name):
        uri = self._get_dataset_path(name)
        df.write_lance(uri, mode=mode, io_config=self._io_config)
```

`ingest_batch()`：
```python
def ingest_batch(self, dataset_name, file_paths, *, transforms=None):
    # 按文件类型分组
    for file_type, paths in grouped.items():
        df = _read_files_df(paths, file_type)  # daft.read_csv([path1, path2, ...])
        if transforms:
            for t in transforms:
                df = t(df)
        self._manager.write_lance_from_dataframe(dataset_name, df, mode)
```

### 验证
- 对比 `ingest()` 和 `ingest_batch()` 写入的数据一致性
- 大文件性能基准测试

### 风险：中
- `daft.write_lance()` 绕过 lancedb 连接池，需协调锁
- 新写入路径需要验证 Lance schema 兼容性

---

## Sprint 4: 定位 B 阶段 2 — 摄取+嵌入一体化管道

### 目标
合并摄取和嵌入为单次 Daft DataFrame 管道，消除 Arrow 中转和 Lance 读-改-写循环。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/ingest/ingest_embed.py` | **新建**。`IngestEmbedPipeline` — 读文件 → 嵌入 → 写 Lance，一次遍历 |
| `arrow_lake/_lake_ingest.py` | 新增 `ingest_and_embed()` facade |
| `arrow_lake/embed/daft_encoder.py` | 新增 `encode_image_column()` 图像嵌入 |

### 关键实现细节

`IngestEmbedPipeline.ingest_and_embed()`：
```python
df = daft.read_csv(paths)
df = df.into_partitions(num_partitions)
df = df.with_column("text_embedding",
    F.embed_text(daft.col("text_content"), provider="transformers", model=model))
df.write_lance(lance_uri, mode="create", io_config=io_config)
```

### 验证
- 端到端测试：摄取 CSV → 自动嵌入 → 向量搜索可用
- 对比分步（ingest → embed_and_add）vs 一体化性能

### 风险：中
- 单次管道的 schema 推理需要 Daft 能推断嵌入维度
- 新路径需要充分测试 Lance 向量索引兼容性

---

## 实施顺序总览

```
Sprint 1 (定位A-1)  ────  Sprint 2 (定位B-1)  ────  Sprint 3 (定位A-2)  ────  Sprint 4 (定位B-2)
DataFrame摄取+transforms      Daft批量嵌入           Daft直写Lance+批量     摄取嵌入一体化
约 3-4 天                     约 3-4 天              约 2-3 天              约 3-4 天
向后兼容，低风险               opt-in后端，低-中风险   需锁协调，中风险       需schema验证，中风险
```

每个 Sprint 完成后独立可交付，不影响其他 Sprint。

---

## 依赖版本要求

| 依赖 | 最低版本 | 说明 |
|------|----------|------|
| `daft` | >= 0.4.x | `read_csv/json/parquet`、`into_partitions`、`write_lance`、`daft.functions.embed_text` |
| `lancedb` | >= 0.21.x | 与 Daft `write_lance` 的 Lance 格式兼容 |
| `pyarrow` | >= 16.0 | Arrow 中转层，Daft `to_arrow()` 返回格式 |
| `numpy` | >= 1.26 | 嵌入向量 `FixedSizeListArray` 构建 |
| `torch` | >= 2.1 | Daft `embed_text(provider="transformers")` 的 PyTorch 后端（Sprint 2+ 可选） |

> **注意**: Daft `write_lance()` 和 `daft.functions` 的 API 在快速迭代中，锁定 `daft>=0.4,<0.5` 以保证稳定性。每个 Sprint 开始前需确认目标 API 在锁定版本内可用。

---

## API 变更文档

### Sprint 1: 摄取端点增加 transforms

**端点**: `POST /api/v1/datasets/{name}/ingest`

**请求体变更**（IngestFilesRequest 新增字段）:

```json
{
  "file_paths": ["/data/sales.csv"],
  "transforms": [
    {"op": "rename", "from": "old_name", "to": "new_name"},
    {"op": "select", "columns": ["id", "name", "value"]},
    {"op": "filter", "expr": "value > 100"},
    {"op": "cast", "column": "id", "dtype": "int64"},
    {"op": "add_constant", "column": "source", "value": "batch_2024"}
  ]
}
```

**transforms 规范**:
- `rename`: 重命名字段
- `select`: 列选择（白名单）
- `filter`: 行过滤表达式（Daft 表达式字符串）
- `cast`: 类型转换
- `add_constant`: 添加常量列
- 多个 transforms 按数组顺序串行执行
- `transforms` 为可选字段，省略时行为与当前完全一致

### Sprint 2: 嵌入端点支持 DAFT 后端

**端点**: `POST /api/v1/datasets/{name}/embed`

**配置切换**:

```bash
# 环境变量
EMBEDDING__BACKEND=daft
EMBEDDING__DAFT_PROVIDER=transformers
EMBEDDING__DAFT_NUM_PARTITIONS=4
```

**行为**: 使用 `daft.functions.embed_text()` 并行分区编码，结果写入方式与 LOCAL/OPENAI 一致。

### Sprint 3: 批量摄取端点

**端点**: `POST /api/v1/datasets/{name}/ingest-batch`

```json
{
  "file_paths": ["/data/part1.csv", "/data/part2.csv", "/data/part3.csv"],
  "transforms": [{"op": "select", "columns": ["id", "text"]}]
}
```

同类型文件一次性读入 Daft DataFrame，转换后 `write_lance()` 直接写入，跳过 Arrow 中转。

### Sprint 4: 一体化摄取+嵌入端点

**端点**: `POST /api/v1/datasets/{name}/ingest-and-embed`

```json
{
  "file_paths": ["/data/articles.csv"],
  "text_column": "content",
  "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
  "num_partitions": 8
}
```

单次管道完成：读取 → 转换 → 嵌入 → 写入 Lance。

---

## 错误处理策略

### 分层错误处理

```
API 层 (FastAPI)
  └─ 捕获 → 统一错误响应格式 {success, error, metadata}
  │
业务层 (_LakeIngestMixin)
  └─ 捕获 → IngestError / EmbeddingError / StorageError
  │
引擎层 (Daft DataFrame)
  └─ 捕获 → 转换为 Arrow Lake 异常类型
```

### 各 Sprint 错误场景

| Sprint | 场景 | 处理 |
|--------|------|------|
| 1 | transform 语法错误 | `TransformError`，返回错误位置和原因 |
| 1 | transform 引用不存在的列 | `TransformError`，列名列表 + 建议修正 |
| 1 | 无 transforms 时路径不变 | 现有 IngestError 不受影响 |
| 2 | Daft embed_text 模型不可达 | `EmbeddingError(backend="daft")`，提示检查模型路径 |
| 2 | 分区数 > 数据量 | 自动降为 1 分区，日志 warning |
| 3 | write_lance schema 不兼容 | `StorageError`，建议先删除或迁移 |
| 3 | 锁冲突（并发写入） | 重试 3 次，间隔指数退避 |
| 4 | 嵌入维度推断失败 | `EmbeddingError`，提示手动设置 expected_dim |
| 4 | 管道中断部分数据已写入 | 标记为 partial write，不自动回滚 |

### 新增异常类型

```python
class TransformError(ArrowLakeError):
    """ETL transform 执行失败."""

class DaftPipelineError(ArrowLakeError):
    """Daft 管道执行异常（Sprint 3-4）."""
```

---

## 回滚策略

| Sprint | 回滚方式 | 说明 |
|--------|----------|------|
| 1 | `ingest_use_daft_pipeline=False` | 回退到纯 Arrow 路径，transforms 参数忽略 |
| 2 | `EMBEDDING__BACKEND=local` | 切回单线程本地编码 |
| 3 | 使用 `ingest()` 替代 `ingest_batch()` | 逐文件写入路径始终可用 |
| 4 | 使用分步 `ingest()` → `embed_and_add()` | 一体化管道是快捷方式，分步始终可用 |

**原则**: 每个 Sprint 的旧路径永远保留，新路径通过配置/参数 opt-in。生产环境可通过配置回退到任意旧版本行为。

---

## 监控与可观测性

### 新增指标

| 指标名 | 类型 | 标签 | Sprint |
|--------|------|------|--------|
| `ingest_transform_duration_seconds` | Histogram | `transform_op` | 1 |
| `ingest_transform_errors_total` | Counter | `transform_op` | 1 |
| `embedding_daft_partitions` | Gauge | `dataset` | 2 |
| `embedding_daft_duration_seconds` | Histogram | `model` | 2 |
| `ingest_batch_duration_seconds` | Histogram | `file_type` | 3 |
| `ingest_batch_files_total` | Counter | `file_type` | 3 |
| `ingest_embed_pipeline_duration_seconds` | Histogram | `model` | 4 |
| `ingest_embed_rows_total` | Counter | `dataset` | 4 |

### 日志规范

```
# Transform 应用日志
INFO  arrow_lake.ingest.transforms  op=rename from=old_name to=new_name rows_before=1000 rows_after=1000

# Daft 嵌入日志
INFO  arrow_lake.embed.daft_encoder  model=Qwen/... partitions=4 rows=5000 dim=1024 duration_ms=3200

# 批量摄取日志
INFO  arrow_lake.ingest.ingestor  method=ingest_batch files=10 type=csv rows_total=50000 duration_ms=1500

# 一体化管道日志
INFO  arrow_lake.ingest.ingest_embed  pipeline=ingest_embed files=3 embed_dim=1024 lance_write_ms=800 total_ms=4500
```

---

## 性能基准预期

| 场景 | 当前（Arrow 中转） | 优化后（Daft 原生） | 预期提升 |
|------|---------------------|----------------------|----------|
| 10 文件 CSV 摄取（1GB 总计） | ~12s（逐文件读+写） | ~5s（批量读+write_lance） | 2-3x |
| 10 万行文本嵌入（local） | ~45s（单线程） | ~15s（4 分区并行） | 3x |
| 摄取+嵌入一体化（10 万行） | ~60s（分步，2 次 Lance IO） | ~20s（单次管道，1 次 IO） | 3x |
| 多模态图像嵌入（1 万张） | ~120s（串行） | ~40s（8 分区并行） | 3x |

> **基准测试方法**: 使用 `tests/benchmarks/bench_daft_pipeline.py`，在相同数据集上对比 `ingest()` vs `ingest_batch()`、`embed_and_add()` vs `DaftBatchEncoder`、分步 vs 一体化管道。

---

## 测试策略

### 单元测试

| Sprint | 测试文件 | 覆盖内容 |
|--------|----------|----------|
| 1 | `tests/test_transforms.py` | `build_transforms()` 5 种 op、空 transforms、无效 op 抛异常 |
| 1 | `tests/test_ingestor.py`（扩展） | `_read_file_df()` 返回类型、transforms 应用验证 |
| 2 | `tests/test_daft_encoder.py` | mock `daft.functions.embed_text`、分区数验证、维度验证 |
| 2 | `tests/test_embed_config.py` | DAFT 后端配置验证 |
| 3 | `tests/test_ingest_batch.py` | 多文件分组、write_lance 调用验证 |
| 4 | `tests/test_ingest_embed.py` | 端到端 mock 管道、schema 推理验证 |

### 集成测试

| Sprint | 测试内容 |
|--------|----------|
| 1 | CSV 文件 + transforms → Lance 可查询 |
| 2 | Daft 嵌入 → 向量搜索余弦相似度验证 |
| 3 | `ingest_batch()` 写入数据与 `ingest()` 逐文件写入一致性 |
| 4 | 一体化管道结果与分步（ingest → embed_and_add）结果一致性 |

### 性能测试

位于 `tests/benchmarks/`，使用 `pytest-benchmark`：

```bash
.venv/bin/python3 -m pytest tests/benchmarks/bench_daft_pipeline.py --benchmark-only
```

---

## 配置示例

### 最小配置（Sprint 1 only）

```bash
# 无需额外配置，transforms 是 API 参数
DAFT__ENABLED=true
```

### 完整配置（Sprint 1-4）

```bash
# Daft 引擎
DAFT__ENABLED=true
DAFT__DEFAULT_NUM_PARTITIONS=8
DAFT__INGEST_USE_DAFT_PIPELINE=true

# 嵌入
EMBEDDING__BACKEND=daft          # local | openai | ray_serve | daft
EMBEDDING__MODEL=Qwen/Qwen3-Embedding-0.6B
EMBEDDING__DAFT_PROVIDER=transformers
EMBEDDING__DAFT_NUM_PARTITIONS=4
```

### Docker Compose 集成

```yaml
services:
  arrow-lake:
    environment:
      DAFT__ENABLED: "true"
      DAFT__INGEST_USE_DAFT_PIPELINE: "true"
      EMBEDDING__BACKEND: "daft"
      EMBEDDING__DAFT_NUM_PARTITIONS: "4"
```

---

## 实施状态

| Sprint | 状态 | 完成项 | 剩余项 |
|--------|------|--------|--------|
| 1 | ✅ 100% | `_read_file_df()`、`ingest()` transforms 参数、`transforms.py`、API 模型 transforms 字段、`ingest_use_daft_pipeline` config | — |
| 2 | ✅ 100% | `DaftBatchEncoder`、`EmbeddingBackend.DAFT`、`EmbeddingConfig.daft_*` 字段、`_lake_ingest.py` DAFT 分支 | — |
| 3 | ✅ 100% | `write_lance_from_dataframe()`、`ingest_batch()`、`_read_files_df()`、`_group_by_type()` | — |
| 4 | ✅ 100% | `IngestEmbedPipeline`、`ingest_and_embed()` facade、`encode_image_column()` | — |

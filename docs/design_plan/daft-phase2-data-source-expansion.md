# Daft Phase 2 — 数据源扩展 + 多目标写入 + 流式摄取

## Context

Phase 1（Sprint 1-4）已完成 Daft 作为 DataFrame 引擎的基础设施：transforms 管道、并行嵌入、直写 Lance、一体化摄取管道。但 Daft 0.7.8 提供了 **21 种数据源读取** + **12 种写入目标** + **200+ 内置函数**，当前只用了约 5%。

Phase 2 的核心目标：**把 Daft 的多源连接能力暴露给 Arrow Lake API 用户**，让一次 API 调用就能从 PostgreSQL/Kafka/Iceberg 等外部系统摄取数据到 Lance。

## 能力差距速览

```
当前: CSV/JSON/Parquet 本地+S3 → Daft → Lance
目标: 21 种数据源 → Daft DataFrame → 12 种写入目标
```

| 维度 | Phase 1 | Phase 2 新增 |
|------|---------|-------------|
| 数据源 | CSV/JSON/Parquet (3) | +SQL/Kafka/Iceberg/Delta/HuggingFace/Text (6 优先) |
| 写入目标 | Lance (1) | +Parquet/CSV/ClickHouse/Iceberg (4 优先) |
| 变换 | 5 种基础 ETL | +join/union/window/regexp (6+ 种) |
| AI 函数 | embed_text | +classify_text/image/llm_generate/prompt (4 种) |
| 流式 | 无 | Kafka 有界批量摄取 |

---

## Sprint 5: SQL 数据库直连摄取

### 目标
支持通过 `read_sql()` 从 PostgreSQL/MySQL/ClickHouse 等关系型数据库直接摄取数据到 Lance，无需导出中间文件。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/ingest/connectors_sql.py` | **新建**。`SqlConnector` — 封装 `daft.read_sql()` 连接工厂 |
| `arrow_lake/ingest/ingestor.py` | 新增 `ingest_sql()` 方法 |
| `arrow_lake/_lake_ingest.py` | 新增 `ingest_sql()` facade |
| `arrow_lake/api/routers/datasets.py` | 新增 `POST /{name}/ingest/sql` 端点 |
| `arrow_lake/api/models/dataset.py` | 新增 `IngestSqlRequest` 模型 |

### 关键实现

```python
# connectors_sql.py
class SqlConnector:
    def __init__(self, connection_url: str, *, partition_col: str | None = None,
                 num_partitions: int | None = None) -> None: ...

    def read(self, sql: str) -> daft.DataFrame:
        return daft.read_sql(
            sql, self._conn,
            partition_col=self._partition_col,
            num_partitions=self._num_partitions,
        )
```

```python
# ingestor.py
def ingest_sql(self, dataset_name, *, sql, connection_url,
               partition_col=None, num_partitions=None, transforms=None):
    connector = SqlConnector(connection_url, partition_col=partition_col,
                              num_partitions=num_partitions)
    df = connector.read(sql)
    if transforms:
        for t in transforms:
            df = t(df)
    self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")
```

### API 示例

```bash
POST /api/v1/datasets/pg_orders/ingest/sql
{
  "sql": "SELECT * FROM orders WHERE created_at > '2024-01-01'",
  "connection_url": "postgresql://user:pass@db:5432/mydb",
  "partition_col": "id",
  "num_partitions": 4,
  "transforms": [{"op": "select", "columns": ["id", "customer_id", "total"]}]
}
```

### 安全考量
- `connection_url` 不允许包含内网地址（复用 SSRF 检测逻辑）
- `sql` 只允许 SELECT（禁止 INSERT/UPDATE/DELETE/DROP）
- 连接凭据通过环境变量注入，不记录到日志

### 风险：低
- 纯新增路径，不影响现有摄取
- `read_sql` 依赖 SQLAlchemy/ConnectorX，需确认已安装

### 实施状态：✅ 完成

| 文件 | 状态 |
|------|------|
| `arrow_lake/ingest/connectors_sql.py` | ✅ 新建 — SqlConnector + SQL 只读验证 |
| `arrow_lake/ingest/ingestor.py` | ✅ 新增 `ingest_sql()` |
| `arrow_lake/_lake_ingest.py` | ✅ 新增 `ingest_sql()` facade |
| `arrow_lake/api/routers/datasets.py` | ✅ 新增 `POST /{name}/ingest/sql` |
| `arrow_lake/api/models/dataset.py` | ✅ 新增 `IngestSqlRequest` |
| `tests/unit/ingest/test_connectors_sql.py` | ✅ 12 passed |

---

## Sprint 6: Kafka 有界批量摄取

### 目标
从 Kafka Topic 拉取指定范围的消息，写入 Lance 数据集。不是流式消费，是有界批量。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/ingest/connectors_kafka.py` | **新建**。`KafkaConnector` — 封装 `daft.read_kafka()` |
| `arrow_lake/ingest/ingestor.py` | 新增 `ingest_kafka()` 方法 |
| `arrow_lake/_lake_ingest.py` | 新增 `ingest_kafka()` facade |
| `arrow_lake/api/routers/datasets.py` | 新增 `POST /{name}/ingest/kafka` 端点 |
| `arrow_lake/api/models/dataset.py` | 新增 `IngestKafkaRequest` 模型 |

### 关键实现

```python
# connectors_kafka.py
class KafkaConnector:
    def read(self, *, bootstrap_servers, topics, start="earliest",
             end="latest", group_id="arrow-lake-kafka-reader") -> daft.DataFrame:
        return daft.read_kafka(
            bootstrap_servers=bootstrap_servers,
            topics=topics,
            start=start,
            end=end,
            group_id=group_id,
        )
```

### API 示例

```bash
POST /api/v1/datasets/click_events/ingest/kafka
{
  "bootstrap_servers": "kafka:9092",
  "topics": ["user-clicks"],
  "start": "2025-01-01T00:00:00Z",
  "end": "latest"
}
```

Kafka 消息通常是 JSON，Daft 返回 `(key, value, partition, offset, timestamp)` 结构。摄取管道需要：
1. 读取 Kafka DataFrame
2. `F.json_decode(daft.col("value"))` 解析 JSON payload
3. 展开为列 → 写入 Lance

### 风险：中
- `read_kafka` 标记为 experimental API
- 需要确认 rdkafka 已安装
- 仅支持有界批量，非流式

### 实施状态：✅ 完成

| 文件 | 状态 |
|------|------|
| `arrow_lake/ingest/connectors_kafka.py` | ✅ 新建 — KafkaConnector |
| `arrow_lake/ingest/ingestor.py` | ✅ 新增 `ingest_kafka()` + JSON 自动解码 |
| `arrow_lake/_lake_ingest.py` | ✅ 新增 `ingest_kafka()` facade |
| `arrow_lake/api/routers/datasets.py` | ✅ 新增 `POST /{name}/ingest/kafka` |
| `arrow_lake/api/models/dataset.py` | ✅ 新增 `IngestKafkaRequest` |
| `tests/unit/ingest/test_connectors_kafka.py` | ✅ 6 passed |

---

## Sprint 7: Lakehouse 格式互操作（Iceberg + Delta Lake）

### 目标
支持从 Apache Iceberg 和 Delta Lake 表直接读取数据写入 Lance，实现跨 Lakehouse 格式迁移。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/ingest/connectors_lakehouse.py` | **新建**。`IcebergConnector` / `DeltaConnector` |
| `arrow_lake/ingest/ingestor.py` | 新增 `ingest_iceberg()` / `ingest_deltalake()` |
| `arrow_lake/_lake_ingest.py` | 新增对应 facade |
| `arrow_lake/api/routers/datasets.py` | 新增 `POST /{name}/ingest/iceberg` + `/{name}/ingest/deltalake` |
| `arrow_lake/api/models/dataset.py` | 新增请求模型 |

### 关键实现

```python
def ingest_iceberg(self, dataset_name, *, table_uri, transforms=None):
    df = daft.read_iceberg(table_uri)
    if transforms:
        for t in transforms:
            df = t(df)
    self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")
```

```python
def ingest_deltalake(self, dataset_name, *, table_uri, version=None, transforms=None):
    df = daft.read_deltalake(table_uri, version=version)
    # ... 同上
```

### API 示例

```bash
POST /api/v1/datasets/migrated_orders/ingest/iceberg
{"table_uri": "s3://warehouse/db/orders", "transforms": [...]}

POST /api/v1/datasets/delta_import/ingest/deltalake
{"table_uri": "s3://delta-bucket/sales", "version": 5}
```

### 风险：低-中
- Iceberg 需要 PyIceberg + Catalog 配置
- Delta Lake 需要 delta-rs
- S3 访问复用现有 IOConfig

---

## Sprint 8: 多目标写入 + 数据导出增强

### 目标
Daft DataFrame 不只写入 Lance，还能写 Parquet/CSV/ClickHouse/Iceberg，把 Arrow Lake 变成数据枢纽。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/ingest/ingestor.py` | 新增 `export_to()` 通用方法 |
| `arrow_lake/ingest/storage.py` | 新增 `export_dataframe()` 路由到 Daft write_* |
| `arrow_lake/_lake_ingest.py` | 新增 `export_to()` facade |
| `arrow_lake/api/routers/datasets.py` | 增强 export 端点 |

### 关键实现

```python
# storage.py
_EXPORT_WRITERS = {
    "parquet": lambda df, uri, **kw: df.write_parquet(uri, **kw),
    "csv":     lambda df, uri, **kw: df.write_csv(uri, **kw),
    "iceberg": lambda df, uri, **kw: df.write_iceberg(uri, **kw),
    "clickhouse": lambda df, uri, **kw: df.write_clickhouse(uri, **kw),
}

def export_dataframe(self, df, target_uri, format, **kwargs):
    writer = _EXPORT_WRITERS[format]
    writer(df, target_uri, io_config=self._get_io_config(), **kwargs)
```

### API 示例

```bash
# Lance → Parquet（现有数据集导出到 S3）
POST /api/v1/datasets/sales/export
{"format": "parquet", "target_uri": "s3://exports/sales/"}

# Lance → ClickHouse
POST /api/v1/datasets/analytics/export
{"format": "clickhouse", "target_uri": "clickhouse://ch-server:9000/db.table"}
```

### 风险：中
- ClickHouse 写入需要 clickhouse-driver
- 写入目标的安全策略（不允许写入任意地址）

---

## Sprint 9: AI 函数扩展 — 分类 + LLM 生成

### 目标
在摄取管道中集成 Daft 的 `classify_text`, `classify_image`, `llm_generate`, `prompt` 函数，实现摄取时的自动标注和生成。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/ingest/transforms.py` | 新增 `classify_text`, `classify_image`, `llm_generate`, `prompt` transform ops |
| `arrow_lake/ingest/ingest_embed.py` | `IngestEmbedPipeline` 增加 `classify` 和 `generate` 步骤 |
| `arrow_lake/embed/daft_encoder.py` | 新增 `classify_column()`, `generate_column()` 方法 |

### 关键实现

transforms.py 扩展：
```python
builders = {
    # ... 现有 5 种
    "classify_text": _build_classify_text,   # F.classify_text(col, provider, model)
    "classify_image": _build_classify_image, # F.classify_image(col, provider, model)
    "llm_generate": _build_llm_generate,     # F.llm_generate(col, provider, model, prompt_template)
    "prompt": _build_prompt,                 # F.prompt(col, provider, model)
}
```

API 使用：
```json
{
  "file_paths": ["/data/reviews.csv"],
  "transforms": [
    {"op": "classify_text", "column": "review", "provider": "huggingface", "model": "sentiment"},
    {"op": "llm_generate", "column": "review", "provider": "openai", "model": "gpt-4",
     "prompt_template": "Summarize this review in one sentence: {text}"}
  ]
}
```

### 风险：中
- LLM 函数需要 API key 和网络访问
- HuggingFace 分类模型需要 GPU 或较长加载时间
- 需要速率限制和错误回退策略

---

## Sprint 10: 高级 DataFrame 变换 — Join/Union/Window

### 目标
支持在摄取管道中进行跨数据集 Join、Union、Window 操作。

### 改动文件

| 文件 | 改动 |
|------|------|
| `arrow_lake/ingest/transforms.py` | 新增 `join`, `union`, `window_rank`, `deduplicate` ops |
| `arrow_lake/ingest/ingestor.py` | 新增 `ingest_join()`, `ingest_union()` 方法 |
| `arrow_lake/_lake_ingest.py` | 新增 facade |

### 关键实现

```python
# join: 从另一个 Lance 数据集读取并 join
{"op": "join", "right_dataset": "products", "left_on": "product_id", "right_on": "id", "how": "left"}

# union: 合并多个数据集
{"op": "union", "datasets": ["sales_2024_q1", "sales_2024_q2"]}

# window: 排名/去重
{"op": "window_rank", "partition_by": ["customer_id"], "order_by": "amount", "desc": true, "rank_column": "rank"}
```

### 风险：中
- Join 需要 Lance → Daft 的读取路径成熟
- 大表 Join 内存管理需要关注

---

## 实施顺序总览

```
Sprint 5 (SQL摄取)     ──→  Sprint 6 (Kafka摄取)    ──→  Sprint 7 (Lakehouse互操作)
约 3-4 天                    约 2-3 天                    约 2-3 天
低风险                       中风险(API experimental)     低-中风险

Sprint 8 (多目标写入)  ──→  Sprint 9 (AI函数扩展)    ──→  Sprint 10 (Join/Union/Window)
约 3-4 天                    约 3-4 天                    约 3-4 天
中风险                       中风险(LLM依赖)              中风险(内存管理)
```

### 依赖关系

- Sprint 5/6/7 互相独立，可并行
- Sprint 8 依赖 Phase 1 的 `write_lance_from_dataframe` 基础
- Sprint 9 依赖 Phase 1 的 transforms 管道
- Sprint 10 依赖 Sprint 5（read_sql 支持读 Lance 回写）

### 新增依赖

| Sprint | 需要安装 |
|--------|----------|
| 5 | `sqlalchemy`, `connectorx` (可选) |
| 6 | `confluent-kafka` 或 `rdkafka` |
| 7 | `pyiceberg`, `deltalake` |
| 8 | `clickhouse-driver` (可选) |
| 9 | 无额外（复用 Daft 内置 AI 函数） |
| 10 | 无额外 |

## 实施状态总览

| Sprint | 状态 | 核心交付物 | 测试 |
|--------|------|------------|------|
| 5 SQL 摄取 | ✅ | `connectors_sql.py` + `ingest_sql()` + API | 12 passed |
| 6 Kafka 摄取 | ✅ | `connectors_kafka.py` + `ingest_kafka()` + JSON 解码 | 6 passed |
| 7 Lakehouse 互操作 | ✅ | `connectors_lakehouse.py` + Iceberg/Delta | 6 passed |
| 8 多目标写入 | ✅ | `export_dataframe()` + 5 种写入格式 | 4 passed |
| 9 AI 函数扩展 | ✅ | 4 个 AI transform ops (classify/generate/prompt) | 22 passed |
| 10 Join/Union/Window | ✅ | `ingest_join()` + `ingest_union()` + deduplicate | 5 passed |

**Phase 1 + Phase 2 合计: 78 tests, 全部通过**

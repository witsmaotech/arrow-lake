# Arrow Lake 快速入门

> 5 分钟从零创建数据湖、写入数据、执行 SQL 查询并导出结果。

***

## 1. 环境准备

### 系统要求

* Python 3.11+
* 操作系统：Linux / macOS / WSL2

### 安装依赖

```bash
# 克隆仓库
git clone https://github.com/witshine/wits-infra-dintellihub.git
cd wits-infra-dintellihub

# 使用 uv 安装所有依赖 (推荐)
uv sync

# 或使用 pip（核心安装；按需追加 [rag,he,docling,fts] 等 extras）
pip install -e .
```

### 验证安装

```bash
# 检查版本与依赖
arrow-lake version

# 输出示例:
# ┌───────────┬──────────┐
# │ Component │ Version  │
# ├───────────┼──────────┤
# │ arrow-lake│ 1.10.0   │
# │ python    │ 3.12.4   │
# │ daft      │ 0.7.21   │
# │ pyarrow   │ 23.0.1   │
# │ duckdb    │ 1.5.5    │
# └───────────┴──────────┘
```

***

## 2. 五分钟示例：创建 → 摄取 → 查询 → 导出

```python
"""quickstart_demo.py — Arrow Lake 最小可运行示例"""
from arrow_lake import Lake
import pyarrow as pa

# 1. 初始化 Lake (数据存储在 ./my_lake 目录)
lake = Lake(base_uri="./my_lake")

# 2. 创建 dataset — 直接从 Arrow Table 写入
data = pa.table({
    "name": ["Alice", "Bob", "Charlie", "Diana"],
    "age": [30, 25, 35, 28],
    "department": ["工程", "产品", "工程", "设计"],
})
lake.create_dataset("users", data)
print(f"已创建 users: {data.num_rows} 行")

# 3. 追加数据 — schema 必须匹配
more_data = pa.table({
    "name": ["Eve", "Frank"],
    "age": [32, 27],
    "department": ["工程", "产品"],
})
lake.append_dataset("users", more_data)

# 4. SQL 查询
# 注意：SQL 中的表名必须与 dataset 名称一致（"users"）
result = lake.query("users", "SELECT * FROM users WHERE age > 26")
print(result.to_pandas())

# 5. OLAP 聚合查询 — 支持 GROUP BY, 窗口函数，JOIN
olap_result = lake.olap_query(
    "users",
    "SELECT department, COUNT(*) AS cnt, AVG(age) AS avg_age "
    "FROM users GROUP BY department ORDER BY cnt DESC",
)
print(olap_result.table.to_pandas())

# 6. 导出为 Parquet
# 注意：父目录 "output/" 必须事先存在
lake.export("users", "output/users.parquet", columns=["name", "age"])

# 7. 查看目录
catalog = lake.catalog()
for ds in catalog.datasets:
    print(f"  {ds.name}: {ds.num_rows} 行，v{ds.version}")

# 8. 清理
lake.shutdown()

# 提示：使用上下文管理器自动清理
# with Lake(base_uri="./my_lake") as lake:
#     lake.create_dataset("users", data)
#     # ... 退出时自动调用 lake.shutdown()
```

***

## 3. CLI 命令速查

Arrow Lake 提供了 `arrow-lake` 命令行工具，覆盖日常操作。

### 启动 API 服务

```bash
# 生产模式
arrow-lake serve --host 0.0.0.0 --port 8000

# 开发模式 (热重载)
arrow-lake serve --reload

# 访问 Swagger 文档
# http://localhost:8000/docs
```

### 查看数据湖状态

```bash
# 列出所有 dataset 及其元数据
arrow-lake --base-uri ./my_lake status

# 输出示例:
# ┌──────────┬──────┬──────────────────┬─────────┐
# │ Name     │ Rows │ Columns          │ Version │
# ├──────────┼──────┼──────────────────┼─────────┤
# │ users    │    6 │ name, age, dep…  │       2 │
# │ products │  120 │ title, price, …  │       1 │
# └──────────┴──────┴──────────────────┴─────────┘
```

### 摄取数据

```bash
# 从本地文件摄取到指定 dataset
arrow-lake --base-uri ./my_lake ingest files sales datas/transactions/sales_2024_cn.csv

# 支持的文件格式: CSV, JSON, JSONL, Parquet
```

### 搜索

```bash
# 全文搜索
arrow-lake --base-uri ./my_lake search fts sales \
    --query "电子产品" \
    --top-k 10

# 混合搜索 (向量 + 全文 RRF 融合)
arrow-lake --base-uri ./my_lake search hybrid sales \
    --query "无线鼠标"
```

### 交互式 Demo

```bash
# 运行内置 Demo (无需 Docker, 无需配置)
arrow-lake demo --base-uri ./demo_data

# 保留 Demo 数据
arrow-lake demo --no-cleanup
```

***

## 4. 目录结构说明

Arrow Lake 使用 [Lance](https://lancedb.github.io/lance/) 列式格式存储数据。
初始化后，`base_uri` 目录结构如下：

```text
my_lake/                          # base_uri 根目录
├── users/                        # dataset 名称
│   ├── .lance/                   # Lance 元数据目录
│   │   ├── versions/             # 数据版本管理
│   │   │   ├── 1.manifest        # 版本 1 清单
│   │   │   └── 2.manifest        # 版本 2 清单 (追加后生成)
│   │   └── _metadata.json        # Schema 和统计信息
│   ├── data/                     # Lance 列式数据文件
│   │   ├── xxx.lance             # 数据分片文件
│   │   └── ...
│   └── indices/                  # 索引文件 (可选)
│       ├── vector/               # 向量索引 (IVF-PQ)
│       └── fts/                  # 全文索引 (Tantivy)
├── products/                     # 另一个 dataset
│   └── ...
└── ingest_dlq.jsonl              # 死信队列 (摄取失败记录)
```

### 关键概念

| 概念            | 说明                                        |
| ------------- | ----------------------------------------- |
| **dataset**   | 一个 Lance dataset，等价于一张表                   |
| **version**   | Lance 原生版本控制，每次写入自增                       |
| **base\_uri** | Lake 存储根目录，支持本地路径或 S3 URI                 |
| **S3 后端**     | 设为 `s3://bucket/prefix` 即可使用 MinIO/AWS S3。凭证配置参见 [03-配置系统](./03-configuration-zh.md#3-存储配置-storageconfig)。 |

***

## 5. 从 YAML 配置创建 Lake

对于生产环境，推荐使用 YAML 配置文件管理所有参数：

```yaml
# config.yaml —— 顶层 section 对应 ArrowLakeConfig 字段（见 config/main.py）
storage:
  backend: local          # 本地开发；生产用 minio/s3（见 12-部署）
  base_uri: ./data        # 存储根（本地路径或 s3://bucket/prefix）

olap:
  max_result_rows: 100000 # 单查询最大返回行数
  lance_scan_mode: "auto" # 合法值仅：auto / native / pyarrow_fallback

vector:                   # VectorSearchConfig
  metric: "cosine"        # cosine / l2 / dot
  default_index_type: "IVF_PQ"
  num_sub_vectors: 24     # 1024 维推荐 24（须为 8 的倍数）

fts:                      # FullTextSearchConfig
  fts_column: "text_content"
  tokenizer_type: "jieba" # 中文推荐 jieba 分词

# v1.9.0 控制面（RBAC / 身份 / personal_token / 任务历史 / RAG 会话走 libSQL）
system_db:
  enabled: false          # 生产置 true 并配 url（见 12-部署）；本地默认关
```

```python
from arrow_lake import Lake

# 从配置文件创建 Lake 实例
lake = Lake.from_yaml("config.yaml", base_uri="./production_data")
```

> **生产必配（v1.9.6）**：脱敏治理需设环境变量 `ARROW_LAKE__MASKING__HMAC_KEY`（fail-fast，
> 缺失则启动阻断；该 key 是纯环境变量，不在 YAML 内）。`system_db.enabled: true` 时
> RBAC/身份/personal_token 走 libSQL，store 不可达则 fail-closed（返回 401）。
> 详见 [12-部署与运维](./12-deployment-zh.md)。

***

## 6. 下一步

完成快速入门后，可以继续探索以下 Cookbook 章节：

* **[02-数据摄取指南](./02-ingestion-zh.md)** — CSV/JSON/Parquet/图像/视频/PDF 多模态摄取
* **[04-向量搜索](./04-vector-search-zh.md)** — `lake.search()`, `lake.create_vector_index()`
* **[05-全文搜索](./05-fulltext-search-zh.md)** — `lake.text_search()`, `lake.create_fts_index()`
* **[06-混合与分面搜索](./06-hybrid-faceted-zh.md)** — `lake.hybrid_search()`, `lake.faceted_search()`, `lake.ensemble_search()`
* **[07-OLAP 分析](./07-olap-analytics-zh.md)** — `lake.olap_query()`, `lake.materialize()`, `lake.daft_query()`
* **[08-RAG 问答管线](./08-rag-pipeline-zh.md)** — `lake.rag_query()`, `lake.rag_extract()`, 流式 RAG
* **[09-知识图谱](./09-knowledge-graph-zh.md)** — `lake.kg_build()`, `lake.kg_query()`, GraphRAG
* **[10-REST API 指南](./10-rest-api-zh.md)** — `arrow-lake serve` 启动后完整 HTTP API 参考
* **[11-数据质量与去重](./11-quality-dedup-zh.md)** — `lake.quality_filter()`, `lake.deduplicate()`
* **[12-部署与运维](./12-deployment-zh.md)** — Docker、Helm、生产环境检查清单
* **[13-CLI 参考](./13-cli-reference.md)** — CLI 完全命令参考手册
* **[15-Gravitino](./15-gravitino-metadata-zh.md)** — Apache Gravitino 元数据治理

***

> **环境变量**: Arrow Lake 支持通过 `.env` 文件或环境变量配置 S3 凭证、LLM API Key 等。
> 参见 `ArrowLakeConfig` 文档了解所有可配置项。

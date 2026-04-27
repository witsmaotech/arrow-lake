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
git clone https://github.com/your-org/wits-infra-dintellihub.git
cd wits-infra-dintellihub

# 使用 uv 安装所有依赖 (推荐)
uv sync

# 或使用 pip
pip install -e ".[all]"
```

### 验证安装

```bash
# 检查版本与依赖
arrow-lake version

# 输出示例:
# ┌───────────┬──────────┐
# │ Component │ Version  │
# ├───────────┼──────────┤
# │ arrow-lake│ 1.2.1    │
# │ python    │ 3.12.4   │
# │ daft      │ 0.7.8    │
# │ pyarrow   │ 23.0.1   │
# │ duckdb    │ 1.5.2    │
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
lake.export("users", "output/users.parquet", columns=["name", "age"])

# 7. 查看目录
catalog = lake.catalog()
for ds in catalog.datasets:
    print(f"  {ds.name}: {ds.num_rows} 行，v{ds.version}")

# 8. 清理
lake.shutdown()
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
arrow-lake --base-uri ./my_lake ingest files sales examples/data/transactions/sales_2024_cn.csv

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

```
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
| **S3 后端**     | 设为 `s3://bucket/prefix` 即可使用 MinIO/AWS S3 |

***

## 5. 从 YAML 配置创建 Lake

对于生产环境，推荐使用 YAML 配置文件管理所有参数：

```yaml
# config.yaml
storage:
  backend: local
  base_path: ./data

olap:
  max_rows: 10000
  lance_scan_mode: "batch"

vector:
  default_metric: "cosine"
  default_index_type: "IVF_PQ"

fts:
  default_column: "text_content"
```

```python
from arrow_lake import Lake

# 从配置文件创建 Lake 实例
lake = Lake.from_yaml("config.yaml", base_uri="./production_data")
```

***

## 6. 下一步

完成快速入门后，可以继续探索以下 Cookbook 章节：

* **[02-数据摄取指南](./02-ingestion-zh.md)** — CSV/JSON/Parquet/图像/视频/PDF 多模态摄取
* **向量搜索** — `lake.search()`, `lake.hybrid_search()`, `lake.faceted_search()`
* **OLAP 分析** — `lake.olap_query()`, `lake.materialize()`
* **RAG 问答** — `lake.rag_query()`, `lake.rag_extract()`
* **REST API** — `arrow-lake serve` 启动后访问 `/docs` 查看完整接口

***

> **环境变量**: Arrow Lake 支持通过 `.env` 文件或环境变量配置 S3 凭证、LLM API Key 等。
> 参见 `ArrowLakeConfig` 文档了解所有可配置项。

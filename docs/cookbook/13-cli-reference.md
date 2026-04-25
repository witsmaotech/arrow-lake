# Arrow Lake CLI 完全参考手册

> 涵盖全部 40+ 命令、参数说明、示例输出与 Python SDK 对应关系。配合 5 个端到端实战场景，从本地开发到 S3/MinIO 生产部署一气呵成。

**示例数据**: 本教程所有实战场景使用的数据文件位于 [`docs/cookbook/datas/`](datas/README.md) 目录，可直接运行。包含论文元数据 CSV、交易记录 CSV、知识库 JSONL 等真实示例。

---

## 全局选项

所有子命令均继承主命令的两个全局选项：

```bash
arrow-lake --base-uri ./data/lake --config prod.yaml <子命令>
```

| 选项 | 默认值 | 环境变量 | 说明 |
|------|--------|----------|------|
| `--base-uri` | `./data/lake` | `ARROW_LAKE_BASE_URI` | 数据湖存储根路径（本地路径或桶内前缀） |
| `--config` | 无 | — | YAML 配置文件路径 |

> **注意**: 全局选项必须放在子命令**之前**。`arrow-lake --base-uri ./lake status` 正确，`arrow-lake status --base-uri ./lake` 错误。

---

## 第一部分：命令手册

### 1. 顶层命令

#### `arrow-lake serve` — 启动 API 服务

```bash
arrow-lake serve --host 0.0.0.0 --port 8000
arrow-lake serve --reload              # 开发模式，代码修改自动重载
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `0.0.0.0` | 绑定地址 |
| `--port` | `8000` | 监听端口 |
| `--reload` | 否 | 启用热重载 |

启动后访问 `http://localhost:8000/docs` 查看 Swagger 文档。

#### `arrow-lake version` — 查看版本信息

```bash
arrow-lake version
```

输出示例：

```
┏━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Component  ┃ Version ┃
┡━━━━━━━━━━━━╇━━━━━━━━━┩
│ arrow-lake │ 1.0.0   │
│ python     │ 3.11.9  │
│ pyarrow    │ 18.1.0  │
│ duckdb     │ 1.2.1   │
│ lancedb    │ 0.18.0  │
└────────────┴─────────┘
```

#### `arrow-lake status` — 列出数据集

`status` 是 `catalog list` 的快捷别名：

```bash
arrow-lake status                     # 使用默认路径
arrow-lake --base-uri ./my_lake status
```

#### `arrow-lake demo` / `arrow-lake multimodal-demo` — 交互式演示

```bash
arrow-lake demo                      # 合成数据，演示向量/SQL/FTS 三类查询
arrow-lake demo --no-cleanup          # 保留演示数据不清理
arrow-lake multimodal-demo            # 多模态演示（图片 + 文本 + 结构化数据）
```

---

### 2. `arrow-lake catalog` — 数据集管理

管理数据集的生命周期：列出、查看详情、删除。

#### `catalog list` — 列出全部数据集

```bash
arrow-lake catalog list
arrow-lake catalog list --json        # JSON 格式输出
```

输出示例：

```
┏━━━┳━━━━━━━━━━┓
┃ # ┃ Name      ┃
┡━━━╇━━━━━━━━━━┩
│ 1 │ papers    │
│ 2 │ images    │
│ 3 │ sales_2024│
└───┴──────────┘
```

**SDK 等价:**

```python
from arrow_lake import Lake
lake = Lake("./data")
datasets = lake.list_datasets()  # -> ['papers', 'images', 'sales_2024']
```

#### `catalog info <name>` — 查看数据集详情

```bash
arrow-lake catalog info papers
```

输出示例：

```
Dataset: papers
┏━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Property ┃ Value          ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ Rows     │ 12580          │
│ Columns  │ 8              │
│ Version  │ 3              │
└─────────┴────────────────┘

Schema
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Column        ┃ Type               ┃ Nullable ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ id           │ string             │ true     │
│ title        │ string             │ false    │
│ text_content │ string             │ true     │
│ category     │ string             │ true     │
│ word_count   │ int64              │ true     │
│ text_embedding│ fixed_size_list[768][float32]│ true│
└──────────────┴────────────────────┴─────────┘
```

#### `catalog delete <name>` — 删除数据集

```bash
arrow-lake catalog delete old_data          # 交互确认
arrow-lake catalog delete old_data --yes    # 跳过确认
```

> **警告**: 删除不可恢复。建议先执行 `backup create`。

---

### 3. `arrow-lake ingest` — 数据摄取

支持 5 种数据源的摄取，每种对应一个子命令。

#### `ingest files <dataset> <paths...>` — 本地文件摄取

支持格式：CSV、JSON、JSONL、Parquet。

```bash
# 单文件
arrow-lake ingest files sales docs/cookbook/datas/transactions/sales_2024.csv

# 多文件（混合格式）
arrow-lake ingest files logs ./logs/api.jsonl ./logs/service.json

# 通配符
arrow-lake ingest files raw_data ./csv/*.csv ./parquet/*.parquet
```

输出示例：

```
Ingestion: 3 file(s) -> sales
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Metric          ┃ Value        ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Rows ingested   │ 15000        │
│ Dataset         │ sales        │
│ Files processed │ 3            │
│ Duration (s)    │ 1.23         │
└────────────────┴─────────────┘
```

**SDK 等价:**

```python
lake.ingest("sales", ["./data/sales_2024.csv"])
```

#### `ingest http <dataset> <urls...>` — 远程 URL 摄取

```bash
arrow-lake ingest http papers \
    https://arxiv.org/papers/2401.00001 \
    https://arxiv.org/papers/2401.00002
```

**SDK 等价:**

```python
lake.ingest_http("papers", ["https://arxiv.org/papers/2401.00001"])
```

#### `ingest images <dataset> <paths...>` — 图片摄取

自动提取缩略图和 EXIF 元数据。

```bash
arrow-lake ingest files photos ./photos/vacation/*.jpg ./photos/portrait/*.png
```

**SDK 等价:**

```python
lake.ingest_images("photos", ["./photos/vacation/*.jpg"])
```

#### `ingest documents <dataset> <paths...>` — PDF 文档摄取

自动解析 PDF、OCR 识别、文本分块。

```bash
arrow-lake ingest docs papers ./papers/report.pdf ./papers/whitepaper.pdf
```

**SDK 等价:**

```python
lake.ingest_documents("papers", ["./papers/report.pdf"])
```

#### `ingest videos <dataset> <paths...>` — 视频摄取

自动提取关键帧。

```bash
arrow-lake ingest videos frames ./videos/lecture.mp4 ./videos/interview.mp4
```

**SDK 等价:**

```python
lake.ingest_videos("frames", ["./videos/lecture.mp4"])
```

---

### 4. `arrow-lake search` — 搜索

三种搜索模式，覆盖向量检索、全文检索和混合检索。

#### `search vector <dataset>` — 向量相似度搜索

先用嵌入模型将查询文本编码为向量，再执行 ANN 搜索。

```bash
arrow-lake search vector papers \
    --query "transformer attention mechanism" \
    --top-k 5 \
    --column text_embedding \
    --model Qwen/Qwen3-Embedding-0.6B
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--query` | — (**必填**) | 搜索文本 |
| `--top-k` | `10` | 返回结果数 |
| `--column` | `text_embedding` | 向量列名 |
| `--model` | `Qwen/Qwen3-Embedding-0.6B` | 嵌入模型 |

输出示例：

```
Results (5 rows)
┏━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ # ┃ ID                  ┃ Category ┃ Distance ┃ Text     ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ 1 │ doc_0042            │ ml       │ 0.1234   │ Attention...│
│ 2 │ doc_0187            │ dl       │ 0.1567   │ Transfor...│
│ 3 │ doc_0091            │ ml       │ 0.1890   │ Self-att...│
└───┴────────────────────┴──────────┴─────────┴──────────┘
```

**SDK 等价:**

```python
from arrow_lake.embed.encoder import LocalEmbeddingEncoder

encoder = LocalEmbeddingEncoder()
vec = encoder._load_model().encode(["transformer attention mechanism"])[0].tolist()
result = lake.search("papers", vec, top_k=5, vector_column="text_embedding")
```

#### `search fts <dataset>` — 全文搜索 (BM25)

基于 BM25 算法的全文检索，需要先创建 FTS 索引。

```bash
arrow-lake search fts papers \
    --query "attention mechanism" \
    --top-k 10
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--query` | — (**必填**) | 搜索文本 |
| `--top-k` | `10` | 返回结果数 |
| `--column` | `text_content` | 全文索引列名 |

> 中文文本会自动使用 jieba 分词后再建立索引。

**SDK 等价:**

```python
result = lake.text_search("papers", "attention mechanism", top_k=10)
```

#### `search hybrid <dataset>` — 混合搜索 (RRF 融合)

融合向量检索和全文检索结果，使用 Reciprocal Rank Fusion (RRF) 算法。

```bash
arrow-lake search hybrid papers \
    --query "attention mechanism" \
    --top-k 10 \
    --vector-column text_embedding \
    --fts-column text_content
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--query` | — (**必填**) | 搜索文本 |
| `--top-k` | `10` | 返回结果数 |
| `--vector-column` | `text_embedding` | 向量列名 |
| `--fts-column` | `text_content` | 全文索引列名 |
| `--model` | `Qwen/Qwen3-Embedding-0.6B` | 嵌入模型 |

**SDK 等价:**

```python
result = lake.hybrid_search("papers", vec, "attention mechanism",
                            top_k=10, vector_column="text_embedding")
```

---

### 5. `arrow-lake index` — 索引管理

#### `index vector <dataset>` — 创建向量索引

```bash
arrow-lake index vector papers \
    --column text_embedding \
    --metric l2 \
    --type IVF_PQ \
    --replace
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--column` | `text_embedding` | 向量列名 |
| `--metric` | `cosine` | 距离度量: `l2`, `cosine`, `dot` |
| `--type` | `IVF_PQ` | 索引类型: `IVF_PQ`, `IVF_FLAT`, `IVF_HNSW_PQ` |
| `--replace/--no-replace` | replace | 是否替换已有索引 |

**SDK 等价:**

```python
lake.create_vector_index("papers", metric="l2", index_type="IVF_PQ")
```

#### `index fts <dataset>` — 创建全文搜索索引

```bash
arrow-lake index fts papers --column text_content
```

> 中文文本会自动使用 jieba 分词后再建立索引。

**SDK 等价:**

```python
lake.create_fts_index("papers", fts_column="text_content")
```

---

### 6. `arrow-lake query` — SQL 查询

#### `query sql <dataset>` — DuckDB SQL 查询

通过 DuckDB 执行 SQL 分析查询，支持聚合、窗口函数、JOIN 等。

```bash
arrow-lake query sql sales \
    --sql "SELECT category, COUNT(*) as cnt, AVG(amount) as avg_amount
           FROM sales GROUP BY category ORDER BY cnt DESC" \
    --max-rows 50
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--sql` | — (**必填**) | SQL 查询语句 |
| `--max-rows` | `100` | 最大显示行数 |

输出示例：

```
Query Result (5 rows)
┏━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━┓
┃ category  ┃ cnt  ┃ avg_amount ┃
┡━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━┩
│ electronics│ 5420│ 234.56     │
│ clothing   │ 3210│ 89.12      │
│ books      │ 2870│ 34.78      │
│ food       │ 2150│ 45.23      │
│ sports     │ 1350│ 156.89     │
└───────────┴─────┴────────────┘
```

**SDK 等价:**

```python
result = lake.olap_query("sales", sql, max_rows=50)
```

#### `query materialize <dataset>` — 物化视图

将 SQL 查询结果持久化为可复用的物化视图。

```bash
arrow-lake query materialize sales \
    --sql "SELECT category, COUNT(*) as cnt FROM sales GROUP BY category" \
    --name category_summary \
    --ttl-days 30
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--sql` | — (**必填**) | SQL 查询语句 |
| `--name` | — (**必填**) | 物化视图名称 |
| `--ttl-days` | 无限 | 保留天数 |

**SDK 等价:**

```python
row_count = lake.materialize("sales", sql, view_name="category_summary", ttl_days=30)
```

---

### 7. `arrow-lake export` — 数据导出

```bash
arrow-lake export papers --output result.parquet --format parquet
arrow-lake export papers --output result.csv --format csv
arrow-lake export papers --output subset.parquet --columns id,title,text_content
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--output` | — (**必填**) | 输出文件路径 |
| `--format` | 自动推断 | 输出格式: `parquet` 或 `csv` |
| `--columns` | 全部列 | 逗号分隔的列名 |

**SDK 等价:**

```python
lake.export("papers", "result.parquet", format="parquet", columns=["id", "title"])
```

---

### 8. `arrow-lake embed` — 向量生成

独立使用嵌入模型生成向量，不依赖数据集。

#### `embed text <text>` — 文本向量生成

```bash
arrow-lake embed text "transformer attention mechanism" \
    --model Qwen/Qwen3-Embedding-0.6B \
    --source huggingface
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--model` | `Qwen/Qwen3-Embedding-0.6B` | 嵌入模型名称 |
| `--source` | `huggingface` | 模型来源: `huggingface` 或 `modelscope`（国内镜像） |

输出示例：

```
Loading model Qwen/Qwen3-Embedding-0.6B... done
Encoding... done
  Dimension: 768
  Norm: 1.000000
  First 5 values: [0.0234, -0.0567, 0.0891, -0.0123, 0.0456]
```

#### `embed image <path>` — 图片向量生成

```bash
arrow-lake embed image ./photos/cat.jpg --model openai/clip-vit-base-patch32
```

---

### 9. `arrow-lake quality` — 数据质量

#### `quality dedup <dataset>` — 数据去重

```bash
# 精确去重（内容完全相同）
arrow-lake quality dedup sales --strategy exact --action remove

# 感知哈希去重（近似重复的图片/文本）
arrow-lake quality dedup photos --strategy perceptual --action flag --threshold 10

# 两者结合
arrow-lake quality dedup papers --strategy both --action flag
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--strategy` | — (**必填**) | 去重策略: `exact`, `perceptual`, `both` |
| `--action` | — (**必填**) | 操作方式: `flag`（标记）或 `remove`（删除） |
| `--threshold` | `10` | 感知哈希 Hamming 距离阈值 |

**SDK 等价:**

```python
result = lake.deduplicate("photos", strategy="perceptual", action="flag", perceptual_threshold=10)
```

#### `quality filter <dataset>` — 质量过滤

```bash
arrow-lake quality filter papers --filters "null_check,min_length" --mode all
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--filters` | — (**必填**) | 逗号分隔的过滤器名称 |
| `--mode` | `all` | 过滤模式: `all`（全部通过）或 `any`（任一通过） |

---

### 10. `arrow-lake backup` — 备份恢复

#### `backup create` — 创建备份

```bash
# 备份指定数据集
arrow-lake backup create --datasets papers images

# 备份所有数据集 + 自定义 ID
arrow-lake backup create --backup-id daily-2024-04-24
```

#### `backup list` — 列出备份

```bash
arrow-lake backup list
```

输出示例：

```
Backups
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ Backup ID           ┃ Created          ┃ Datasets   ┃ Size     ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│ daily-2024-04-24    │ 2024-04-24 10:30 │ papers,... │ 256 MB   │
│ daily-2024-04-23    │ 2024-04-23 10:30 │ papers,... │ 248 MB   │
└─────────────────────┴─────────────────┴────────────┴─────────┘
```

#### `backup restore <id>` — 恢复备份

```bash
arrow-lake backup restore daily-2024-04-24
arrow-lake backup restore daily-2024-04-24 --datasets papers
```

#### `backup delete <id>` — 删除备份

```bash
arrow-lake backup delete daily-2024-04-24
```

> 备份数据存储在 S3/MinIO 时，备份也存入对象存储的 `backups/` 前缀下。本地存储时备份存放在 `{base_uri}/.backups/` 目录。

---

### 11. `arrow-lake kg` — 知识图谱

> 所有 KG 命令为异步操作，需要 HugeGraph 服务运行中。

#### `kg build <dataset>` — 构建知识图谱

```bash
arrow-lake kg build papers
```

返回 `task_id`，用于查询构建进度。

#### `kg status <task_id>` — 查看构建进度

```bash
arrow-lake kg status task_abc123
```

#### `kg stats` — 图谱统计

```bash
arrow-lake kg stats
```

输出示例：

```
Knowledge Graph Stats
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Metric        ┃ Value      ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ vertex_count  │ 12580      │
│ edge_count    │ 34520      │
│ relation_types│ 12         │
└──────────────┴───────────┘
```

#### `kg query <gremlin>` — Gremlin 查询

```bash
arrow-lake kg query "g.V().has('type','paper').limit(10)"
```

#### `kg neighbors <entity_id>` — 邻居遍历

```bash
arrow-lake kg neighbors "paper:2401.00001" --depth 2
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--depth` | `1` | 遍历深度 |

#### `kg delete` — 删除图谱

```bash
arrow-lake kg delete --yes
```

> **警告**: 不可恢复，需重建。

---

### 12. `arrow-lake rag` — RAG 问答

#### `rag query <dataset> <question>` — RAG 问答

```bash
arrow-lake rag query papers \
    "Transformer 的自注意力机制是如何工作的？" \
    --top-k 5 \
    --strategy hybrid \
    --session-id session_001
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--top-k` | `5` | 检索上下文块数量 |
| `--strategy` | `hybrid` | 检索策略: `vector`, `fts`, `hybrid` |
| `--template` | `default_qa` | 提示词模板: `default_qa`, `graph_qa` |
| `--session-id` | 无 | 会话 ID（用于多轮对话） |

输出示例：

```
Running RAG query...

Answer:
Transformer 的自注意力机制通过 Query-Key-Value 三元组实现...

Citations: (3 sources)
  1. doc_0042 — Attention Is All You Need
  2. doc_0187 — Self-Attention with Relative Position
  3. doc_0091 — A Survey of Transformers

Latency: 1234.5ms
Context tokens: 2048
```

#### `rag templates` — 列出提示词模板

```bash
arrow-lake rag templates
```

内置模板：

| 模板名 | 类型 | 用途 |
|--------|------|------|
| `default_qa` | QA | 通用问答 |
| `graph_qa` | QA | 知识图谱增强问答 |
| `summarize` | SUMMARY | 文本摘要 |
| `entity_extract` | EXTRACT | 实体抽取 |
| `entity_extract_from_question` | EXTRACT | 从问题中抽取实体 |

---

### 13. `arrow-lake config` — 配置管理

#### `config show` — 显示当前配置

```bash
arrow-lake config show
arrow-lake --config prod.yaml config show
```

输出默认配置的完整 JSON（所有 30 个配置分区）。

#### `config init` — 生成配置模板

```bash
arrow-lake config init                    # 默认: arrow-lake.yaml
arrow-lake config init --output prod.yaml  # 自定义文件名
```

生成的配置文件包含全部可配置项和注释说明，可直接编辑使用。

---

## 第二部分：存储配置

### 本地存储（默认）

无需额外配置，直接使用：

```bash
arrow-lake --base-uri ./my_lake catalog list
arrow-lake --base-uri ./my_lake ingest files my_data data.csv
```

数据存储在 `./my_lake/` 目录下，每个数据集一个子目录。

### S3 / MinIO 远程存储

Arrow Lake 支持将数据存储在 S3 或 MinIO 上，CLI 命令**不需要改变**——只需通过配置文件或环境变量指定 S3 连接信息。

**核心原理**：`--base-uri` 在 S3 模式下是**桶内前缀**，不是完整路径。实际 S3 路径由系统自动拼接：

```
实际路径 = s3://{s3_bucket}/{base_uri}/{dataset}.lance
```

例如 `--base-uri ./data` + `s3_bucket=arrow-lake` → 数据集存储在 `s3://arrow-lake/data/papers.lance`。

#### 配置方式一：YAML 文件（推荐）

创建配置文件 `minio.yaml`：

```yaml
storage:
  backend: minio
  s3_endpoint: "http://localhost:9000"
  s3_access_key: "minioadmin"
  s3_secret_key: "minioadmin"
  s3_bucket: "arrow-lake"
  s3_region: "us-east-1"
```

使用：

```bash
arrow-lake --config minio.yaml --base-uri ./data status
arrow-lake --config minio.yaml --base-uri ./data ingest files papers data.csv
arrow-lake --config minio.yaml --base-uri ./data search fts papers --query "AI"
arrow-lake --config minio.yaml --base-uri ./data export papers --output result.parquet
```

#### 配置方式二：环境变量（ARROW_LAKE__ 前缀）

```bash
export ARROW_LAKE__STORAGE__BACKEND=minio
export ARROW_LAKE__STORAGE__S3_ENDPOINT=http://localhost:9000
export ARROW_LAKE__STORAGE__S3_ACCESS_KEY=minioadmin
export ARROW_LAKE__STORAGE__S3_SECRET_KEY=minioadmin
export ARROW_LAKE__STORAGE__S3_BUCKET=arrow-lake
export ARROW_LAKE__STORAGE__S3_REGION=us-east-1

arrow-lake --base-uri ./data status
```

#### 配置方式三：AWS 标准环境变量

```bash
export S3_ENDPOINT=http://localhost:9000
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
export S3_BUCKET=arrow-lake
export AWS_REGION=us-east-1

arrow-lake --config minio.yaml status
```

#### 配置方式四：使用 AWS 凭据（无需配密钥）

```yaml
storage:
  backend: s3
  s3_bucket: "my-prod-bucket"
  s3_region: "us-east-1"
  # s3_access_key 和 s3_secret_key 留空，使用 IAM Role / EC2 实例配置文件
```

#### 完整 MinIO YAML 模板

`arrow-lake config init` 生成的模板已包含 `storage` 分区，以下是完整示例：

```yaml
# arrow-lake.yaml
storage:
  backend: minio              # minio | s3 | gcs | local
  base_uri: "./data"         # 桶内前缀
  s3_endpoint: "http://localhost:9000"
  s3_access_key: ""          # 留空则使用 AWS 凭证链
  s3_secret_key: ""
  s3_bucket: "arrow-lake"
  s3_region: "us-east-1"

# 搜索配置
vector:
  metric: cosine
  default_top_k: 10
  default_index_type: IVF_PQ

fts:
  default_top_k: 10
  fts_column: "text_content"
  tokenizer_type: "jieba"     # 中文分词

# 嵌入模型配置
embedding:
  model: "Qwen/Qwen3-Embedding-0.6B"
  model_source: huggingface  # huggingface | modelscope

# OLAP 查询配置
olap:
  max_result_rows: 100000
  query_timeout_seconds: 300

# API 配置
api:
  host: "0.0.0.0"
  port: 8000
  docs_enabled: true

# RAG 配置
rag:
  enabled: true
  default_retrieval_strategy: hybrid
  default_top_k: 10

# 知识图谱配置
hugegraph:
  enabled: false
  host: "localhost"
  port: 8089
  graph_name: "arrow_lake_kg"
```

#### 凭据检测机制

系统通过以下逻辑判断是否使用 S3：

```python
has_real_creds = (
    backend != LOCAL
    and s3_access_key != ""          # 有密钥
    and not s3_access_key.startswith("<")  # 不是占位符
)
```

只有满足条件时才会传递 S3 配置给 Lance 引擎，否则回退为本地存储。即使配置了 `backend: minio`，如果密钥为空也不会出错。

#### StorageConfig 字段速查

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `backend` | enum | `minio` | 存储后端: `minio`, `s3`, `gcs`, `local` |
| `base_uri` | str | `./data` | 存储根路径/桶内前缀 |
| `s3_endpoint` | str | `http://localhost:9000` | S3 兼容端点 |
| `s3_access_key` | str | `""` | 访问密钥 |
| `s3_secret_key` | str | `""` | 秘密密钥 |
| `s3_bucket` | str | `arrow-lake` | 默认桶名 |
| `s3_region` | str | `us-east-1` | 区域 |

---

## 第三部分：实战场景

### 场景一：科研论文管理（本地存储）

从零搭建一个论文数据集，完成摄取、索引、搜索、导出的完整流程。

**示例数据**:
- `docs/cookbook/datas/papers/metadata.csv` — 20 条英文论文元数据（Transformer、BERT、CLIP、GPT-4、LoRA 等）
- `docs/cookbook/datas/papers/metadata_zh.csv` — 12 条中文论文元数据（知识图谱、向量数据库、RAG、MinIO 等）
- `docs/cookbook/datas/papers/full_text/` — 18 篇来自 arxiv 的真实论文 PDF

**步骤 1：创建数据集并摄取数据**

```bash
# 摄取英文论文元数据
arrow-lake --base-uri ./paper_lake ingest files papers docs/cookbook/datas/papers/metadata.csv

# 摄取中文论文元数据（jieba 自动分词）
arrow-lake --base-uri ./paper_lake ingest files papers_zh docs/cookbook/datas/papers/metadata_zh.csv

# 摄取 PDF 原文
arrow-lake --base-uri ./paper_lake ingest docs papers docs/cookbook/datas/papers/full_text/*.pdf
```

**步骤 2：查看数据集**

```bash
arrow-lake --base-uri ./paper_lake catalog info papers
```

**步骤 3：创建索引**

```bash
# 全文搜索索引（中文论文自动 jieba 分词）
arrow-lake --base-uri ./paper_lake index fts papers --column text_content

# 向量索引（加速向量搜索）
arrow-lake --base-uri ./paper_lake index vector papers \
    --column text_embedding --type IVF_PQ
```

**步骤 4：搜索论文**

```bash
# 全文搜索
arrow-lake --base-uri ./paper_lake search fts papers \
    --query "attention mechanism" --top-k 5

# 向量搜索（语义相似）
arrow-lake --base-uri ./paper_lake search vector papers \
    --query "how does self-attention work" --top-k 10

# 混合搜索（综合排序）
arrow-lake --base-uri ./paper_lake search hybrid papers \
    --query "transformer architecture"

# 中文全文搜索（jieba 自动分词）
arrow-lake --base-uri ./paper_lake search fts papers_zh \
    --query "知识图谱 大模型" --top-k 5
```

**步骤 5：SQL 分析**

```bash
arrow-lake --base-uri ./paper_lake query sql papers \
    --sql "SELECT category, COUNT(*) as cnt, MIN(year) as earliest, MAX(year) as latest
           FROM papers GROUP BY category ORDER BY cnt DESC"
```

**步骤 6：导出结果**

```bash
arrow-lake --base-uri ./paper_lake export papers \
    --output ml_papers.parquet --columns id,title,authors,year
```

---

### 场景二：多媒体数据湖（本地存储）

管理图片和视频数据，实现跨模态搜索。

**示例数据**: `docs/cookbook/datas/photos/` 目录已包含 6 张示例图片。视频需自行放入 `docs/cookbook/datas/videos/`。

**步骤 1：摄取多媒体数据**

```bash
# 图片摄取（自动提取缩略图 + EXIF）
arrow-lake --base-uri ./media_lake ingest files photos docs/cookbook/datas/photos/*.jpg docs/cookbook/datas/photos/*.png

# 视频摄取（自动提取关键帧）
arrow-lake --base-uri ./media_lake ingest videos clips docs/cookbook/datas/videos/lecture_demo.mp4 docs/cookbook/datas/videos/interview_clip.mp4
```

**步骤 2：生成嵌入向量**

```bash
# 单张图片的向量
arrow-lake embed image docs/cookbook/datas/photos/sunset.jpg --model openai/clip-vit-base-patch32

# 单条文本的向量
arrow-lake embed text "golden hour landscape photography"
```

**步骤 3：创建索引并搜索**

```bash
# 向量索引
arrow-lake --base-uri ./media_lake index vector photos --column image_embedding

# 语义搜索图片
arrow-lake --base-uri ./media_lake search vector photos \
    --query "sunset over the ocean" --column image_embedding
```

---

### 场景三：数据分析工作流（本地存储）

从原始数据到质量管控再到分析报告的完整流程。

**示例数据**:
- `docs/cookbook/datas/transactions/sales_2024.csv` — 50 条英文交易记录
- `docs/cookbook/datas/transactions/sales_2024_cn.csv` — 50 条中文交易记录（适合中文 FTS 演示）

**步骤 1：摄取原始数据**

```bash
# 英文交易数据
arrow-lake --base-uri ./analytics_lake ingest files transactions docs/cookbook/datas/transactions/sales_2024.csv

# 中文交易数据（jieba 自动分词，适合中文全文搜索演示）
arrow-lake --base-uri ./analytics_lake ingest files transactions_cn docs/cookbook/datas/transactions/sales_2024_cn.csv
```

**步骤 2：质量检查**

```bash
# 去重
arrow-lake --base-uri ./analytics_lake quality dedup transactions \
    --strategy both --action flag

# 质量过滤
arrow-lake --base-uri ./analytics_lake quality filter transactions \
    --filters "null_check,range_check" --mode all
```

**步骤 3：SQL 分析**

```bash
# 每日交易趋势
arrow-lake --base-uri ./analytics_lake query sql transactions \
    --sql "SELECT DATE(timestamp) as day,
           COUNT(*) as tx_count,
           SUM(amount) as total,
           AVG(amount) as avg_amount
           FROM transactions
           GROUP BY day ORDER BY day DESC
           LIMIT 30"

# 中文交易数据：按城市统计销售额
arrow-lake --base-uri ./analytics_lake query sql transactions_cn \
    --sql "SELECT 城市, COUNT(*) as 订单数, SUM(金额) as 总额, AVG(金额) as 平均金额
           FROM transactions_cn GROUP BY 城市 ORDER BY 总额 DESC"
```

**步骤 4：物化常用报表**

```bash
arrow-lake --base-uri ./analytics_lake query materialize transactions \
    --sql "SELECT user_id, COUNT(*) as tx_count, SUM(amount) as total_spent
           FROM transactions GROUP BY user_id" \
    --name user_summary \
    --ttl-days 7
```

**步骤 5：备份**

```bash
arrow-lake --base-uri ./analytics_lake backup create \
    --datasets transactions --backup-id pre-cleanup
```

---

### 场景四：RAG 问答系统（本地存储）

构建一个基于知识图谱增强的 RAG 问答系统。

**示例数据**:
- `docs/cookbook/datas/kb/knowledge.jsonl` — 10 条英文知识库条目（Arrow、Parquet、DuckDB、LanceDB、RAG、HNSW、MinIO 等）
- `docs/cookbook/datas/kb/knowledge_zh.jsonl` — 10 条中文知识库条目（适合中文 RAG 问答演示）

**步骤 1：摄取知识库数据**

```bash
# 英文知识库
arrow-lake --base-uri ./rag_lake ingest files knowledge docs/cookbook/datas/kb/knowledge.jsonl

# 中文知识库
arrow-lake --base-uri ./rag_lake ingest files knowledge_zh docs/cookbook/datas/kb/knowledge_zh.jsonl
```

**步骤 2：创建索引**

```bash
# 向量索引
arrow-lake --base-uri ./rag_lake index vector knowledge --column text_embedding

# 全文索引
arrow-lake --base-uri ./rag_lake index fts knowledge --column text_content
```

**步骤 3：构建知识图谱**

```bash
# 启动构建（异步，返回 task_id）
arrow-lake --base-uri ./rag_lake kg build knowledge

# 查看进度
arrow-lake --base-uri ./rag_lake kg status <task_id>

# 查看统计
arrow-lake --base-uri ./rag_lake kg stats

# 图谱查询
arrow-lake --base-uri ./rag_lake kg query "g.V().has('type','concept').limit(20)"
```

**步骤 4：RAG 问答**

```bash
# 单轮问答
arrow-lake --base-uri ./rag_lake rag query knowledge \
    "Arrow 格式和 Parquet 格式有什么区别？"

# 中文知识库问答
arrow-lake --base-uri ./rag_lake rag query knowledge_zh \
    "HNSW 算法的时间复杂度是多少？"

# 多轮对话
arrow-lake --base-uri ./rag_lake rag query knowledge \
    "它支持哪些压缩算法？" \
    --session-id sess_001
```

**步骤 5：查看提示词模板**

```bash
arrow-lake --base-uri ./rag_lake rag templates
```

---

### 场景五：MinIO 生产部署

全部 CLI 命令在 S3/MinIO 环境下零改动使用，只需一次配置。

**步骤 1：准备配置文件**

```bash
arrow-lake config init --output prod.yaml
# 编辑 prod.yaml，填入 MinIO 连接信息
```

编辑 `prod.yaml`：

```yaml
storage:
  backend: minio
  s3_endpoint: "http://minio.example.com:9000"
  s3_access_key: "prod-access-key"
  s3_secret_key: "prod-secret-key"
  s3_bucket: "company-data"
  s3_region: "us-east-1"
  base_uri: "./lake"

rag:
  enabled: true
  default_retrieval_strategy: hybrid

hugegraph:
  enabled: true
  host: "hugegraph.internal"
  port: 8089
```

**步骤 2：完整数据工作流**

```bash
# 所有命令只需加 --config prod.yaml，--base-uri 是桶内前缀

# 摄取数据
arrow-lake --config prod.yaml --base-uri ./datasets ingest files reports ./reports/*.csv

# 查看数据
arrow-lake --config prod.yaml --base-uri ./datasets catalog info reports

# 创建索引
arrow-lake --config prod.yaml --base-uri ./datasets index vector reports
arrow-lake --config prod.yaml --base-uri ./datasets index fts reports --column text_content

# 搜索
arrow-lake --config prod.yaml --base-uri ./datasets search hybrid reports \
    --query "Q4 revenue analysis" --top-k 5

# SQL 分析
arrow-lake --config prod.yaml --base-uri ./datasets query sql reports \
    --sql "SELECT region, SUM(revenue) FROM reports GROUP BY region"

# 导出给下游团队
arrow-lake --config prod.yaml --base-uri ./datasets export reports \
    --output /tmp/q4_summary.parquet --columns region,revenue,department

# 备份到 S3
arrow-lake --config prod.yaml --base-uri ./datasets backup create \
    --datasets reports --backup-id q4-2024-snapshot

# RAG 问答
arrow-lake --config prod.yaml --base-uri ./datasets rag query reports \
    "上季度各区域的收入对比如何？" --top-k 10

# 知识图谱
arrow-lake --config prod.yaml --base-uri ./datasets kg build reports
```

> **所有命令完全相同**，只是加了 `--config prod.yaml`。数据实际存储在 `s3://company-data/lake/datasets/reports.lance`。

---

## 附录

### A. 命令速查表

| 场景 | 命令 |
|------|------|
| 查看数据集 | `arrow-lake status` |
| 摄取文件 | `arrow-lake ingest files <ds> <paths...>` |
| 摄取图片 | `arrow-lake ingest files <ds> <images...>` |
| 摄取 PDF | `arrow-lake ingest docs <ds> <pdfs...>` |
| 远程摄取 | `arrow-lake ingest http <ds> <urls...>` |
| 向量搜索 | `arrow-lake search vector <ds> --query <text>` |
| 全文搜索 | `arrow-lake search fts <ds> --query <text>` |
| 混合搜索 | `arrow-lake search hybrid <ds> --query <text>` |
| 创建向量索引 | `arrow-lake index vector <ds>` |
| 创建全文索引 | `arrow-lake index fts <ds>` |
| SQL 查询 | `arrow-lake query sql <ds> --sql <sql>` |
| 物化视图 | `arrow-lake query materialize <ds> --sql <sql> --name <n>` |
| 导出数据 | `arrow-lake export <ds> --output <path>` |
| 生成向量 | `arrow-lake embed text <text>` |
| 数据去重 | `arrow-lake quality dedup <ds> --strategy <s> --action <a>` |
| 质量过滤 | `arrow-lake quality filter <ds> --filters <names>` |
| 创建备份 | `arrow-lake backup create --datasets <ds...>` |
| 恢复备份 | `arrow-lake backup restore <id>` |
| 构建知识图谱 | `arrow-lake kg build <ds>` |
| 图谱查询 | `arrow-lake kg query <gremlin>` |
| RAG 问答 | `arrow-lake rag query <ds> <question>` |
| 生成配置 | `arrow-lake config init --output <file>` |
| 启动服务 | `arrow-lake serve` |
| 版本信息 | `arrow-lake version` |

### B. 配置优先级

```
YAML 配置文件 (最高) > 环境变量 (ARROW_LAKE__*) > .env 文件 > 代码默认值
```

### C. S3 环境变量对照表

| YAML 字段 | ARROW_LAKE__ 前缀 | AWS 标准变量 |
|-----------|-----------------|---------------|
| `storage.backend` | `ARROW_LAKE__STORAGE__BACKEND` | — |
| `storage.base_uri` | `ARROW_LAKE__STORAGE__BASE_URI` | — |
| `storage.s3_endpoint` | `ARROW_LAKE__STORAGE__S3_ENDPOINT` | `S3_ENDPOINT` / `S3_ENDPOINT_URL` |
| `storage.s3_access_key` | `ARROW_LAKE__STORAGE__S3_ACCESS_KEY` | `AWS_ACCESS_KEY_ID` |
| `storage.s3_secret_key` | `ARROW_LAKE__STORAGE__S3_SECRET_KEY` | `AWS_SECRET_ACCESS_KEY` |
| `storage.s3_bucket` | `ARROW_LAKE__STORAGE__S3_BUCKET` | `S3_BUCKET` |
| `storage.s3_region` | `ARROW_LAKE__STORAGE__S3_REGION` | `AWS_REGION` / `AWS_DEFAULT_REGION` |

### D. 常见问题

**Q: `--base-uri` 能直接写 `s3://bucket/prefix` 吗？**

不能。`--base-uri` 始终是本地路径或桶内前缀。S3 连接信息通过 `--config` 或环境变量单独提供。系统内部会拼接为 `s3://{bucket}/{base_uri}/{dataset}.lance`。

**Q: 配置了 S3 但忘了填密钥会怎样？**

会静默回退为本地存储。系统检测到 `s3_access_key` 为空或以 `<` 开头时，不传递 S3 配置给 Lance 引擎。

**Q: 备份命令在 S3 模式下行为有什么不同？**

本地存储时备份存放在 `{base_uri}/.backups/` 目录。S3/MinIO 时备份存入对象存储的 `backups/{backup_id}/` 前缀，数据实际也在 S3 上。

**Q: `--config` 和环境变量同时存在时以谁为准？**

`--config` 指定的 YAML 文件优先级最高，会覆盖同名环境变量。

**Q: 切换存储后端需要改 CLI 命令吗？**

不需要。所有 CLI 命令与存储后端无关，切换只需改配置。

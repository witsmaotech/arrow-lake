# Arrow Lake CLI 完全参考手册

> 涵盖全部 100+ 命令、参数说明、示例输出与 Python SDK 对应关系。配合 5 个端到端实战场景，从本地开发到 S3/MinIO 生产部署一气呵成。

**示例数据**: 本教程所有实战场景使用的数据文件位于 [`examples/data/`](datas/README.md) 目录，可直接运行。包含论文元数据 CSV、交易记录 CSV、知识库 JSONL 等真实示例。

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
| `--verbose` / `-v` | `0` | — | 增加输出详细程度（可叠加: -v, -vv, -vvv） |
| `--quiet` / `-q` | 否 | — | 仅显示错误输出 |
| `--format` | `table` | — | 输出格式: `table`, `json`, `csv` |

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

```text
┏━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Component  ┃ Version ┃
┡━━━━━━━━━━━━╇━━━━━━━━━┩
│ arrow-lake │ 1.10.0  │
│ python     │ 3.11.9  │
│ pyarrow    │ 23.0.1  │
│ duckdb     │ 1.5.2   │
│ lancedb    │ 0.30.2  │
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

```text
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

```text
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
│ text_embedding│ fixed_size_list[1024][float32]│ true│
└──────────────┴────────────────────┴─────────┘
```

#### `catalog delete <name>` — 删除数据集

```bash
arrow-lake catalog delete old_data          # 交互确认
arrow-lake catalog delete old_data --yes    # 跳过确认
```

> **警告**: 删除不可恢复。建议先执行 `backup create`。

#### `catalog rename <name> <new_name>` — 重命名数据集

```bash
arrow-lake catalog rename old_name new_name
```

**SDK 等价:**

```python
lake.rename_dataset("old_name", "new_name")
```

#### `catalog copy <name> <new_name>` — 复制数据集

```bash
arrow-lake catalog copy documents documents_backup
```

**SDK 等价:**

```python
lake.copy_dataset("documents", "documents_backup")
```

#### `catalog merge --sources <src1,src2,...> <target>` — 合并数据集

所有源数据集必须有相同的 schema。

```bash
arrow-lake catalog merge --sources "q1_2024,q2_2024,q3_2024" yearly_sales
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--sources` | — (**必填**) | 逗号分隔的源数据集名称 |
| `target` | — (**位置参数**) | 目标数据集名称 |

#### `catalog health` — 系统健康检查

```bash
arrow-lake catalog health
```

检查存储可达性、DuckDB 会话池、运行时间等。

#### `catalog inspect <name>` — 查看数据集元数据（catalog 视图）

```bash
arrow-lake catalog inspect documents
arrow-lake catalog inspect documents --json
```

---

### 3. `arrow-lake ingest` — 数据摄取

支持多种数据源的摄取，包括文件、远程 URL、图片、PDF 和视频，另有 create/append/upsert/delete-rows/update-rows 等数据集级操作命令。

#### `ingest files <dataset> <paths...>` — 本地文件摄取

支持格式：CSV、JSON、JSONL、Parquet。

```bash
# 单文件
arrow-lake ingest files sales examples/data/transactions/sales_2024.csv

# 多文件（混合格式）
arrow-lake ingest files logs ./logs/api.jsonl ./logs/service.json

# 通配符
arrow-lake ingest files raw_data ./csv/*.csv ./parquet/*.parquet
```

输出示例：

```text
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
arrow-lake ingest images photos ./photos/vacation/*.jpg ./photos/portrait/*.png
```

**SDK 等价:**

```python
lake.ingest_images("photos", ["./photos/vacation/*.jpg"])
```

#### `ingest documents <dataset> <paths...>` — PDF 文档摄取

自动解析 PDF、OCR 识别、文本分块。

```bash
arrow-lake ingest documents papers ./papers/report.pdf ./papers/whitepaper.pdf
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

#### `ingest create <name> --data <file>` — 从文件创建数据集

```bash
arrow-lake ingest create sales --data sales_2024.csv
```

#### `ingest append <name> --data <file>` — 追加数据

```bash
arrow-lake ingest append sales --data new_records.parquet
```

#### `ingest upsert <dataset> --data <file> --on <column>` — 更新或插入

```bash
arrow-lake ingest upsert products --data updated.csv --on product_id
```

#### `ingest delete-rows <dataset> --where <expr>` — 按 WHERE 删除

```bash
arrow-lake ingest delete-rows sales --where "year < 2020"
```

#### `ingest update-rows <dataset> --where <expr> --set <json>` — 按 WHERE 更新

```bash
arrow-lake ingest update-rows products \
    --where "category = 'electronics'" \
    --set '{"price": 99.99}'
```

---

### 4. `arrow-lake search` — 搜索

五种搜索模式，覆盖向量检索、全文检索、混合检索、分面搜索和集成搜索。

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

```text
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
| `--column` | 无（使用配置默认值） | 全文索引列名 |

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
| `--vector-column` | 无（使用配置默认值） | 向量列名 |
| `--fts-column` | 无（使用配置默认值） | 全文索引列名 |
| `--model` | `Qwen/Qwen3-Embedding-0.6B` | 嵌入模型 |

**SDK 等价:**

```python
result = lake.hybrid_search("papers", vec, "attention mechanism",
                            top_k=10, vector_column="text_embedding")
```

#### `search faceted <dataset>` — 分面搜索 (v1.2)

向量搜索 + 分组统计，适用于筛选型场景。

```bash
arrow-lake search faceted products \
    --query "laptop" \
    --facets "category,brand" \
    --top-k 20
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--query` | — (**必填**) | 搜索文本 |
| `--facets` | 无 | 逗号分隔的分面列 |
| `--top-k` | `10` | 返回结果数 |
| `--column` | `text_embedding` | 向量列名 |
| `--model` | `Qwen/Qwen3-Embedding-0.6B` | 嵌入模型 |

输出包含搜索结果和分面计数的两张表。

**SDK 等价:**

```python
result = lake.faceted_search("products", vec, facets=["category", "brand"], top_k=20)
```

#### `search ensemble <dataset>` — 集成搜索 (v1.2)

跨多个嵌入列加权融合搜索。

```bash
arrow-lake search ensemble papers \
    --query "transformer architecture" \
    --columns "text_embedding,title_embedding" \
    --weights '{"text_embedding": 0.7, "title_embedding": 0.3}' \
    --top-k 10
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--columns` | — (**必填**) | 逗号分隔的嵌入列名 |
| `--weights` | 无 | JSON 格式的列权重字典 |
| `--query` | — (**必填**) | 搜索文本 |
| `--top-k` | `10` | 返回结果数 |

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
| `--column` | 无（使用配置默认值） | 向量列名 |
| `--metric` | 无（使用配置默认值） | 距离度量: `l2`, `cosine`, `dot` |
| `--type` | 无（使用配置默认值） | 索引类型: `IVF_PQ`, `IVF_FLAT`, `IVF_HNSW_PQ` |
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

#### `index scalar <dataset>` — 创建标量索引

对单列建标量索引，加速过滤和分面聚合（低基数列用 BITMAP，其余用 BTREE）。

```bash
arrow-lake index scalar papers --column category
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--column` | 无（**必填**） | 目标列名 |
| `--type` | 自动 | 索引类型: `BTREE`, `BITMAP` |
| `--name` | 自动 | 索引名称 |
| `--replace/--no-replace` | `replace` | 是否替换已有索引 |

**SDK 等价:**

```python
lake.create_scalar_index("papers", column="category")
```

#### `index facets <dataset>` — 批量创建分面索引

按 `FacetedSearchConfig.scalar_index_type_map` 对默认分面列批量建标量索引。

```bash
arrow-lake index facets papers
```

**SDK 等价:**

```python
lake.create_facet_indexes("papers")
```

#### `index list-vector <dataset>` — 列出向量索引 (v1.2)

```bash
arrow-lake index list-vector papers
```

#### `index info-vector <dataset>` — 查看向量索引信息 (v1.2)

```bash
arrow-lake index info-vector papers
```

#### `index rebuild-vector <dataset>` — 重建向量索引 (v1.2)

```bash
arrow-lake index rebuild-vector papers --column text_embedding
```

#### `index delete-vector <dataset> <index_name>` — 删除向量索引 (v1.2)

```bash
arrow-lake index delete-vector papers text_embedding_idx
```

| 参数 | 说明 |
|------|------|
| `dataset` | (**位置参数**) 数据集名称 |
| `index_name` | (**位置参数**) 向量索引名称 |

#### `index info-fts <dataset>` — 查看全文索引信息 (v1.2)

```bash
arrow-lake index info-fts papers
```

#### `index delete-fts <dataset>` — 删除全文索引 (v1.2)

```bash
arrow-lake index delete-fts papers
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

```text
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

#### `query meta <dataset>` — 数据集元数据查询 (v1.2)

```bash
arrow-lake query meta papers --sql "SELECT * FROM papers LIMIT 5"
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--sql` | — (**必填**) | 元数据 SQL 查询语句 |
| `--max-rows` | `100` | 最大显示行数 |

#### `query cleanup-materialized` — 清理过期物化视图 (v1.2)

```bash
arrow-lake query cleanup-materialized
arrow-lake query cleanup-materialized --ttl-days 30
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ttl-days` | `7` | 清理超过指定天数的过期物化视图 |

#### `query daft <dataset>` — Daft DataFrame 查询 (v1.2)

将数据集加载为 Daft DataFrame 并显示。

```bash
arrow-lake query daft papers --columns id,title --limit 10
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--columns` | 全部列 | 逗号分隔的列名 |
| `--limit` | `50` | 最大显示行数 |

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

```text
Loading model Qwen/Qwen3-Embedding-0.6B... done
Encoding... done
  Dimension: 1024
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

```text
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
>
> **per-dataset 隔离图 (v1.8.6+)**: 每个数据集对应独立的 HugeGraph 图 `kg_{dataset}`。`query` / `stats` / `neighbors` / `export` / `traverser` / `algo` 等子命令均支持 `--dataset <name>` 指定目标数据集（省略时从配置推断）。

#### `kg build <dataset>` — 构建知识图谱

```bash
arrow-lake kg build papers                 # 默认全量构建
arrow-lake kg build papers --incremental   # 增量：仅喂入自上次构建以来的新 chunk
arrow-lake kg build papers --template project_concept_graph   # v1.10.0：指定抽取模板，覆盖 doc_type 路由
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--incremental` | 否（默认全量） | 增量模式只处理新 chunk（无 KA dump 或模板变更时回退为全量）；append 数据后用 `--incremental`，re-ingest/delete 或改模板后用默认全量重建 |
| `--template` | 无（走 doc_type 路由） | v1.10.0：指定知识抽取模板名（如 `project_concept_graph`），覆盖 doc_type 三层路由；配合 console `extraction-templates.html` 在线管理的模板 |

返回 `task_id`，用于查询构建进度。

#### `kg list-doc-types` — 列出文档类型

```bash
arrow-lake kg list-doc-types
```

列出 hyper-extract 支持的 doc_type 及其映射到的抽取模板（来自 `HugeGraphConfig.he_doc_type_templates`）。

#### `kg list-templates` — 列出抽取模板

```bash
arrow-lake kg list-templates                  # 全部模板
arrow-lake kg list-templates --category general  # 按分类过滤
```

#### `kg describe-template <path>` — 查看模板详情

```bash
arrow-lake kg describe-template general/concept_graph
```

展示指定模板的完整 schema（节点/边类型、必填字段、约束等）。

#### `kg status <task_id>` — 查看构建进度

```bash
arrow-lake kg status task_abc123
```

#### `kg stats` — 图谱统计

```bash
arrow-lake kg stats
```

输出示例：

```text
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

#### `kg export` — 导出知识图谱

```bash
arrow-lake kg export --output graph.json
```

#### `kg import` — 导入知识图谱

```bash
arrow-lake kg import graph.json
```

#### `kg traverser` — 图遍历算法子组 (v1.2 新增)

8 种遍历算法：

```bash
# 所有最短路径
arrow-lake kg traverser all-shortest-paths v1 v2

# 加权最短路径
arrow-lake kg traverser weighted-shortest v1 v2

# 单源最短路径
arrow-lake kg traverser single-source-shortest v1

# 多节点最短路径
arrow-lake kg traverser multi-node-shortest --sources '["v1","v2"]' --targets '["v3","v4"]'

# 射线（非环路径）
arrow-lake kg traverser rays v1 --max-depth 5

# 环检测
arrow-lake kg traverser rings v1 --max-depth 5

# 交叉点
arrow-lake kg traverser crosspoints v1 v2

# 自定义多步遍历
arrow-lake kg traverser customized v1 \
    --steps '[{"labels":["person"],"direction":"OUT"},{"labels":["software"],"direction":"OUT"}]'
```

**Traverser 子命令参数表：**

| 子命令 | 参数 | 默认值 | 说明 |
|--------|------|--------|------|
| `all-shortest-paths` | `--direction` | `OUT` | 遍历方向: `OUT`, `BOTH`, `IN` |
| | `--max-depth` | `10` | 最大搜索深度 |
| `weighted-shortest` | `--direction` | `OUT` | 遍历方向: `OUT`, `BOTH`, `IN` |
| | `--weight-prop` | `weight` | 权重属性名 |
| | `--max-degree` | `10000` | 最大遍历度数 |
| `single-source-shortest` | `--direction` | `OUT` | 遍历方向: `OUT`, `BOTH`, `IN` |
| | `--weight-prop` | `weight` | 权重属性名 |
| | `--max-degree` | `10000` | 最大遍历度数 |
| `multi-node-shortest` | `--sources` | — (**必填**) | 源节点 JSON 数组 |
| | `--targets` | — (**必填**) | 目标节点 JSON 数组 |
| | `--direction` | `OUT` | 遍历方向 |
| | `--weight-prop` | `weight` | 权重属性名 |
| | `--max-degree` | `10000` | 最大遍历度数 |
| `rays` | `--direction` | `OUT` | 遍历方向: `OUT`, `BOTH`, `IN` |
| | `--max-depth` | `5` | 最大搜索深度 |
| `rings` | `--direction` | `OUT` | 遍历方向: `OUT`, `BOTH`, `IN` |
| | `--max-depth` | `5` | 最大搜索深度 |
| `crosspoints` | `--direction` | `OUT` | 遍历方向: `OUT`, `BOTH`, `IN` |
| | `--max-depth` | `10` | 最大搜索深度 |
| `customized` | `--steps` | — (**必填**) | JSON 格式多步遍历定义 |
| | `--with-vertex` | 否 | 结果包含顶点信息 |
| | `--with-edge` | 否 | 结果包含边信息 |

#### `kg algo` — 图 OLAP 算法子组 (v1.2 新增)

9 种算法：

```bash
# PageRank — 识别重要节点
arrow-lake kg algo pagerank

# Louvain — 社区发现
arrow-lake kg algo louvain

# Label Propagation — 社区检测
arrow-lake kg algo label-propagation

# WCC — 弱连通分量
arrow-lake kg algo wcc

# 三角计数
arrow-lake kg algo triangle-count

# 度中心性
arrow-lake kg algo degree-centrality

# 接近中心性
arrow-lake kg algo closeness-centrality

# K-core 分解
arrow-lake kg algo k-core --k 3

# 介数中心性
arrow-lake kg algo betweenness-centrality
```

**Algo 子命令参数表：**

| 子命令 | 参数 | 默认值 | 说明 |
|--------|------|--------|------|
| `pagerank` | `--iterations` | `20` | 最大迭代次数 |
| | `--damping` | `0.85` | 阻尼系数 |
| `louvain` | `--resolution` | `1.0` | 分辨率参数 |
| `degree-centrality` | — | — | 无额外参数 |
| `closeness-centrality` | — | — | 无额外参数 |
| `betweenness-centrality` | — | — | 无额外参数 |
| `wcc` | — | — | 无额外参数 |
| `triangle-count` | — | — | 无额外参数 |
| `k-core` | `--k` | `3` | K 核心层数 |

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
| `--strategy` | 无（使用配置默认值） | 检索策略: `vector`, `fts`, `hybrid` |
| `--template` | 无（使用配置默认值） | 提示词模板: `default_qa`, `graph_qa` |
| `--session-id` | 无 | 会话 ID（用于多轮对话） |

输出示例：

```text
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

#### `rag stream <dataset> <question>` — 流式输出 (v1.2)

逐 chunk 输出 RAG 回答，适合交互式场景。

```bash
arrow-lake rag stream papers "什么是 RAG？" --top-k 5
```

#### `rag batch` — 批量查询 (v1.2)

一次提交多个问题并发查询。

```bash
arrow-lake rag batch papers --questions '["问题1","问题2","问题3"]' --top-k 5
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--questions` | — (**必填**) | JSON 数组格式的问题列表 |
| `--top-k` | `5` | 每个查询的上下文块数量 |
| `--strategy` | 无 | 检索策略 |
| `--concurrency` | `5` | 最大并发数 |

#### `rag extract` — 实体抽取 (v1.2)

```bash
arrow-lake rag extract papers --top-k 20
```

#### `rag feedback` — 提交反馈 (v1.2)

```bash
arrow-lake rag feedback s1 0 positive
arrow-lake rag feedback s1 0 negative --comment "回答不够详细"
```

| 参数 | 说明 |
|------|------|
| `session_id` | (**位置参数**) 会话 ID |
| `turn_id` | (**位置参数**, int) 轮次编号 |
| `rating` | (**位置参数**) 评价: `positive`, `negative`, `neutral` |
| `--comment` | 附加评论 |

#### `rag history` — 查看会话历史 (v1.2)

```bash
arrow-lake rag history s1
```

| 参数 | 说明 |
|------|------|
| `session_id` | (**位置参数**) 会话 ID |

#### `rag cleanup-sessions` — 清理过期会话 (v1.2)

```bash
arrow-lake rag cleanup-sessions
```

#### `rag get-feedback` — 获取会话反馈 (v1.2)

```bash
arrow-lake rag get-feedback s1
```

| 参数 | 说明 |
|------|------|
| `session_id` | (**位置参数**) 会话 ID |

---

### 13. `arrow-lake maintenance` — 系统维护

#### `maintenance status` — 查看维护调度器状态

```bash
arrow-lake maintenance status
```

输出当前维护调度器状态、上次执行时间、下次计划执行时间等信息。

#### `maintenance run` — 执行一次完整维护周期

```bash
arrow-lake maintenance run
arrow-lake maintenance run --json    # JSON 格式输出
```

执行一次完整维护周期，包括数据压缩和版本清理。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--json` | 否 | 以 JSON 格式输出执行结果 |

---

### 14. `arrow-lake config` — 配置管理

#### `config show` — 显示当前配置

```bash
arrow-lake config show
arrow-lake --config prod.yaml config show
```

输出默认配置的完整 JSON（所有 30 个配置分区）。

> `config` 组仅提供 `show` 与 `init` 两个子命令（无 `dump` / `validate`）。

#### `config init` — 生成配置模板

```bash
arrow-lake config init                    # 默认: arrow-lake.yaml
arrow-lake config init --output prod.yaml  # 自定义文件名
```

生成的配置文件包含全部可配置项和注释说明，可直接编辑使用。

---

### 15. `arrow-lake audit` — 审计追踪 (v1.2 新增)

完整的审计日志记录、HMAC 完整性验证、异常检测。

#### `audit record <event_type>` — 记录审计事件

```bash
arrow-lake audit record dataset_ingested --dataset papers --actor admin \
    --payload '{"rows": 500, "format": "parquet"}'
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--dataset` | 无 | 关联的数据集 |
| `--actor` | `cli` | 操作者 |
| `--payload` | 无 | JSON 格式附加数据 |

#### `audit verify <audit_id>` — 验证完整性

```bash
arrow-lake audit verify audit-20260426-001
```

#### `audit query` — 查询审计日志

```bash
arrow-lake audit query --dataset papers --start 2026-01-01 --end 2026-04-01
arrow-lake audit query --event-type dataset_ingested
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--dataset` | 无 | 按数据集过滤 |
| `--start` | 无 | 起始时间 (ISO) |
| `--end` | 无 | 结束时间 (ISO) |
| `--event-type` | 无 | 按事件类型过滤 |

#### `audit export <dataset>` — 导出审计日志

```bash
arrow-lake audit export papers --output audit_trail.json
```

#### `audit analyze` — 异常检测 (v1.2)

自动运行 z-score 异常检测，识别频率尖峰和操作者异常。

```bash
arrow-lake audit analyze
```

输出包含异常类型、严重程度、受影响事件数。

---

### 16. `arrow-lake lineage` — 数据血缘 (v1.2 新增)

#### `lineage record <dataset> <operation>` — 记录血缘事件

```bash
arrow-lake lineage record sales merge \
    --sources "raw_sales,cleaned_sales" \
    --transform-type etl \
    --actor pipeline
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--sources` | 无 | 逗号分隔的源数据集 |
| `--transform-type` | 无 | 转换类型描述 |
| `--actor` | `cli` | 操作者 |
| `--metadata` | 无 | JSON 格式附加元数据 |

#### `lineage history <dataset>` — 查看血缘历史

```bash
arrow-lake lineage history sales
```

#### `lineage query <sql>` — SQL 查询血缘

```bash
arrow-lake lineage query "SELECT * FROM lineage WHERE dataset_name = 'sales'"
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `sql` | — (**必填**) | SQL 查询语句（位置参数） |
| `--max-rows` | `100` | 最大返回行数 |

---

### 17. `arrow-lake lifecycle` — Blob 生命周期 (v1.2 新增)

S3/MinIO 对象的存储分层、Glacier 恢复、成本估算。

#### `lifecycle config` — 查看当前配置

```bash
arrow-lake lifecycle config
```

输出当前生命周期配置：转换天数、排除前缀、Glacier 检索类型。

#### `lifecycle rules [--prefix]` — 预览规则

```bash
arrow-lake lifecycle rules
arrow-lake lifecycle rules --prefix data/archive/
```

预览将应用的 S3 lifecycle 规则，不实际执行。

#### `lifecycle apply [--prefix]` — 应用规则

```bash
arrow-lake lifecycle apply
arrow-lake lifecycle apply --prefix data/archive/
```

#### `lifecycle status [--prefix]` — 查看存储分层

```bash
arrow-lake lifecycle status
arrow-lake lifecycle status --prefix data/
```

输出每个对象的 key、当前分层 (STANDARD/STANDARD_IA/GLACIER/DEEP_ARCHIVE)、大小。

#### `lifecycle restore <key>` — 恢复 Glacier 对象

```bash
arrow-lake lifecycle restore data/old-file.parquet --days 7
arrow-lake lifecycle restore archive/backup.parquet --days 30
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--days` | `7` | 临时副本保留天数 |

#### `lifecycle estimate --size-gb N --target-tier TIER` — 成本估算

```bash
arrow-lake lifecycle estimate --size-gb 1000 --target-tier STANDARD_IA
arrow-lake lifecycle estimate --size-gb 500 --target-tier GLACIER
arrow-lake lifecycle estimate --size-gb 2000 --target-tier DEEP_ARCHIVE
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--size-gb` | — (**必填**) | 数据总大小 (GB) |
| `--target-tier` | `STANDARD_IA` | 目标分层: `STANDARD_IA`, `GLACIER`, `DEEP_ARCHIVE` |

---

### 18. 场景导航别名 (v1.5.0+)

| 别名 | 等效命令组 | 说明 |
|------|-----------|------|
| `arrow-lake knowledge` | rag + kg | 知识构建与管理导航 |
| `arrow-lake connect` | ingest + catalog | 数据连接与摄取导航 |
| `arrow-lake analyze` | query + search + export | 数据分析与检索导航 |
| `arrow-lake govern` | audit + lineage + backup + maintenance | 数据治理与运维导航 |

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

```text
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
- `examples/data/papers/metadata.csv` — 20 条英文论文元数据（Transformer、BERT、CLIP、GPT-4、LoRA 等）
- `examples/data/papers/metadata_zh.csv` — 12 条中文论文元数据（知识图谱、向量数据库、RAG、MinIO 等）
- `examples/data/papers/full_text/` — 18 篇来自 arxiv 的真实论文 PDF

**步骤 1：创建数据集并摄取数据**

```bash
# 摄取英文论文元数据
arrow-lake --base-uri ./paper_lake ingest files papers examples/data/papers/metadata.csv

# 摄取中文论文元数据（jieba 自动分词）
arrow-lake --base-uri ./paper_lake ingest files papers_zh examples/data/papers/metadata_zh.csv

# 摄取 PDF 原文
arrow-lake --base-uri ./paper_lake ingest documents papers examples/data/papers/full_text/*.pdf
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

**示例数据**: `examples/data/photos/` 目录已包含 6 张示例图片。视频需自行放入 `examples/data/videos/`。

**步骤 1：摄取多媒体数据**

```bash
# 图片摄取（自动提取缩略图 + EXIF）
arrow-lake --base-uri ./media_lake ingest files photos examples/data/photos/*.jpg examples/data/photos/*.png

# 视频摄取（自动提取关键帧）
arrow-lake --base-uri ./media_lake ingest videos clips examples/data/videos/lecture_demo.mp4 examples/data/videos/interview_clip.mp4
```

**步骤 2：生成嵌入向量**

```bash
# 单张图片的向量
arrow-lake embed image examples/data/photos/sunset.jpg --model openai/clip-vit-base-patch32

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
- `examples/data/transactions/sales_2024.csv` — 50 条英文交易记录
- `examples/data/transactions/sales_2024_cn.csv` — 50 条中文交易记录（适合中文 FTS 演示）

**步骤 1：摄取原始数据**

```bash
# 英文交易数据
arrow-lake --base-uri ./analytics_lake ingest files transactions examples/data/transactions/sales_2024.csv

# 中文交易数据（jieba 自动分词，适合中文全文搜索演示）
arrow-lake --base-uri ./analytics_lake ingest files transactions_cn examples/data/transactions/sales_2024_cn.csv
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
- `examples/data/kb/knowledge.jsonl` — 10 条英文知识库条目（Arrow、Parquet、DuckDB、LanceDB、RAG、HNSW、MinIO 等）
- `examples/data/kb/knowledge_zh.jsonl` — 10 条中文知识库条目（适合中文 RAG 问答演示）

**步骤 1：摄取知识库数据**

```bash
# 英文知识库
arrow-lake --base-uri ./rag_lake ingest files knowledge examples/data/kb/knowledge.jsonl

# 中文知识库
arrow-lake --base-uri ./rag_lake ingest files knowledge_zh examples/data/kb/knowledge_zh.jsonl
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
| 摄取图片 | `arrow-lake ingest images <ds> <images...>` |
| 摄取 PDF | `arrow-lake ingest documents <ds> <pdfs...>` |
| 远程摄取 | `arrow-lake ingest http <ds> <urls...>` |
| 向量搜索 | `arrow-lake search vector <ds> --query <text>` |
| 全文搜索 | `arrow-lake search fts <ds> --query <text>` |
| 混合搜索 | `arrow-lake search hybrid <ds> --query <text>` |
| 分面搜索 | `arrow-lake search faceted <ds> --query <text> --facets <cols>` |
| 集成搜索 | `arrow-lake search ensemble <ds> --columns <cols> --questions <json>` |
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
| RAG 流式 | `arrow-lake rag stream <ds> <question>` |
| RAG 批量 | `arrow-lake rag batch <ds> --questions <json>` |
| 审计记录 | `arrow-lake audit record <event>` |
| 数据血缘 | `arrow-lake lineage record <ds> <op>` |
| 生命周期规则 | `arrow-lake lifecycle rules --prefix <prefix>` |
| 生命周期恢复 | `arrow-lake lifecycle restore <key>` |
| 维护状态 | `arrow-lake maintenance status` |
| 执行维护周期 | `arrow-lake maintenance run` |
| 生成配置 | `arrow-lake config init --output <file>` |
| 启动服务 | `arrow-lake serve` |
| 版本信息 | `arrow-lake version` |

### B. 配置优先级

```text
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

**Q: 很多参数默认值显示"无（使用配置默认值）"是什么意思？**

CLI 的 `--column`、`--metric`、`--strategy` 等参数默认值为 `None`，此时会回退到 YAML 配置文件或 `arrow-lake config show` 中显示的默认值。如需覆盖，通过命令行参数显式指定即可。

**Q: `rag batch` 和 `rag feedback` 为什么用 JSON / 位置参数而不是普通选项？**

`--questions` 接受 JSON 数组以支持任意数量的问题；`rag feedback` 的 session_id、turn_id、rating 使用位置参数是为了简化最常用的反馈提交操作，避免冗长的 `--session-id --turn --rating` 前缀。

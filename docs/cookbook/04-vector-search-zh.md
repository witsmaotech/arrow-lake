# 向量搜索与索引

向量搜索是 Arrow Lake 的核心检索能力。本文展示从数据摄取、Embedding 生成、索引创建到相似度搜索的完整流程。

> **贯穿数据集**：第 04-09 章共用一个 `aigc_articles` AIGC 主题文章库（`datas/reports/aigc_articles.csv`——144 篇 AIGC 文章，含 `title`/`text_content`/`category`/`year`/`venue`/`authors`/`word_count`）。本章引入该库，后续章节以全文、混合、OLAP、RAG、知识图谱等不同视角继续审视同一语料。

```python
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageConfig, StorageBackend

# 初始化 Lake 实例
config = ArrowLakeConfig()
config.storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./data")
lake = Lake(base_uri="./data", config=config)

# 1. 摄取 AIGC 文章库 — text_content 自动编码为 text_embedding
report = lake.ingest("aigc_articles", ["datas/reports/aigc_articles.csv"])
print(f"摄取 {report.total_rows} 行")

# 2. 创建向量索引
from arrow_lake.config import DistanceMetric, VectorIndexType
info = lake.create_vector_index("aigc_articles", metric="cosine", index_type="IVF_PQ")
print(f"索引类型：{info.index_type}, 距离度量：{info.distance_type}")
print(f"已索引行数：{info.num_indexed_rows}")

# 3. 执行向量搜索
import numpy as np
query_vec = np.random.randn(1024).tolist()  # 替换为真实查询向量
result = lake.search("aigc_articles", query_vec, top_k=5)
print(f"返回 {result.row_count} 条结果，度量：{result.metric}")

for i in range(result.row_count):
    row = result.table.to_pylist()[i]
    distance = row["_distance"]
    print(f"  [{i}] distance={distance:.4f}")
```

***

## 1. Embedding 生成

Arrow Lake 在摄取文本数据时自动生成 Embedding。Embedding 配置通过 `EmbeddingConfig` 控制：

```python
from arrow_lake.config import ArrowLakeConfig, EmbeddingConfig, EmbeddingBackend, ModelSource

config = ArrowLakeConfig()

# 使用本地 HuggingFace 模型生成 embedding
config.embedding = EmbeddingConfig(
    model="Qwen/Qwen3-Embedding-0.6B",
    model_source=ModelSource.HUGGINGFACE,
    backend=EmbeddingBackend.LOCAL,
    batch_size=128,
)

# 使用 OpenAI API 生成 embedding
config.embedding = EmbeddingConfig(
    backend=EmbeddingBackend.OPENAI,
    api_key="sk-...",
    api_base="https://api.openai.com/v1",
)

from arrow_lake import Lake
lake = Lake(base_uri="./data", config=config)
```

数据摄取时，`text_content` 列的文本会自动编码为 `text_embedding` 向量列。可通过 `expected_dim` 字段显式指定预期维度进行校验。

***

## 2. 创建向量索引

在执行高效搜索前，需要先创建向量索引。Arrow Lake 支持七种索引类型：

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# --- 基础用法：使用默认配置 ---
info = lake.create_vector_index("aigc_articles")
# 默认：metric=cosine, index_type=IVF_PQ

# --- 指定度量和索引类型 ---
info = lake.create_vector_index(
    "aigc_articles",
    metric="cosine",          # 距离度量：cosine / l2 / dot
    index_type="IVF_PQ",      # 索引类型
)

# --- 精细控制索引参数 ---
info = lake.create_vector_index(
    "aigc_articles",
    metric="l2",
    vector_column="text_embedding",  # 向量列名
    index_type="IVF_FLAT",           # 更精确但更慢
    num_partitions=512,              # IVF 分区数
    num_sub_vectors=24,              # PQ 子向量数 (须为 8 的倍数，1024 维推荐 24)
    replace=True,                    # 替换已有索引
)
```

`create_vector_index` 返回 `IndexInfo`:

```python
info = lake.create_vector_index("aigc_articles", metric="cosine", index_type="IVF_PQ")
print(f"索引：{info.index_type}, 度量：{info.distance_type}")
print(f"已索引：{info.num_indexed_rows}, 未索引：{info.num_unindexed_rows}")
print(f"覆盖列：{info.columns}")
```

### 索引类型对比

| 类型            | 说明              | 适用场景            | 备注          |
| ------------- | --------------- | --------------- | ----------- |
| `IVF_PQ`      | IVF 倒排 + 乘积量化   | 大规模数据集 (>10K 行) | 默认选择，内存占用小  |
| `IVF_FLAT`    | IVF 倒排 + 精确距离   | 中等规模，需要精度       | 无量化损失，内存占用大 |
| `IVF_HNSW_PQ` | IVF + HNSW + PQ | 大规模 + 低延迟       | 构建成本高      |
| `IVF_HNSW_SQ` | IVF + HNSW + 标量量化 | 大规模 + 低延迟 + 较高精度 | 内存与精度的折中 |
| `IVF_SQ`      | IVF + 标量量化      | 中大规模，精度优于 PQ    | 量化损失小于 PQ  |
| `IVF_RQ`      | IVF + 残差量化      | 超大规模，极致压缩       | 内存最小，精度损失较大 |
| `HNSW`        | 纯 HNSW 图索引     | 中小规模，最低延迟       | 内存占用大，无 IVF 粗筛 |

> **建索引须知（v1.9.6）**：
> - **最少 256 行**：IVF_PQ 等量化索引需 ≥256 行训练数据（`_PQ_MIN_TRAINING_ROWS`），不足抛 `VECTOR_INDEX_TOO_FEW_ROWS`；行数不足时向量检索退化为暴力扫描（仍可用）。auto-index 对 <256 行 WARN 跳过。
> - **`lance_scan_mode: pyarrow_fallback`**：生产环境若 RAG/向量检索遇 DuckDB lance vector stream 的 Rust panic（worker 崩溃/502），设此值绕过（见 [12-部署](./12-deployment-zh.md)）。
> - **多模态以图搜图**：图像用 CLIP/SigLIP 嵌入（`POST /embed/image` 或 SDK `lake.encode_text_clip()` 文搜图），再 `search(vector_column="image_embedding")`。

***

## 3. 向量相似度搜索

使用 `lake.search()` 执行向量相似度搜索。双路径策略：优先 DuckDB 原生 `lance_vector_search()`，失败时回退 LanceDB SDK。

### API 签名

```python
def search(
    self,
    dataset_name: str,
    query_vector: list[float],          # 查询 embedding 向量 (位置参数)
    *,
    top_k: int = 10,                    # 返回数量
    metric: str | None = None,          # 距离度量：cosine / l2 / dot
    vector_column: str = "text_embedding",  # 向量列名
    where: str | None = None,           # 元数据过滤表达式
    nprobes: int | None = None,         # IVF 探测分区数
    version: int | None = None,         # 数据集版本 (时间旅行查询)
) -> VectorSearchResult: ...
```

### 基本用法

```python
from arrow_lake import Lake
import numpy as np

lake = Lake(base_uri="./data")

# 准备查询向量（维度必须与数据集向量列一致）
query_vector = np.random.randn(1024).tolist()

# 基础搜索
result = lake.search("aigc_articles", query_vector, top_k=5)

# 带度量指定
result = lake.search(
    "aigc_articles",
    query_vector,
    top_k=10,
    metric="cosine",
    vector_column="text_embedding",
)

# 带元数据过滤
result = lake.search(
    "aigc_articles",
    query_vector,
    top_k=5,
    where="category = '大语言模型'",
)

# 时间旅行查询 (搜索指定数据集版本)
result = lake.search("aigc_articles", query_vector, top_k=5, version=3)
```

### 返回类型：VectorSearchResult

```python
result = lake.search("aigc_articles", query_vector, top_k=5)
print(f"返回行数：{result.row_count}, 维度：{result.query_vector_dim}")
print(f"度量：{result.metric}, 最大距离：{result.max_distance}")

for row in result.table.to_pylist():
    print(f"  score={row['_distance']:.4f} | {row.get('text_content', '')[:80]}...")
```

> 如果没有向量索引，LanceDB 自动回退到暴力搜索。不需要索引也能搜索，但性能随数据量线性下降。

***

## 4. 元数据过滤

`where` 参数支持 SQL 风格过滤表达式，在向量搜索前预过滤元数据列：

```python
# 等值过滤
result = lake.search("aigc_articles", qv, where="category = '大语言模型'")

# 数值范围 + 组合条件
result = lake.search("aigc_articles", qv, where="category = '大语言模型' AND year >= 2023")

# IN 操作
result = lake.search("aigc_articles", qv, where="venue IN ('NeurIPS', 'ICML')")

# 字符串匹配
result = lake.search("aigc_articles", qv, where="title LIKE '%大模型%'")
```

> **安全**: Arrow Lake 内部会检查危险 SQL 关键字，但不应将未经净化的用户输入直接拼入 where 表达式。

***

## 5. 索引参数调优

### 5.1 num\_partitions -- IVF 分区数

IVF (Inverted File) 将向量空间分为 `num_partitions` 个聚类分区。搜索时只扫描部分分区（由 `nprobes` 控制）。

```python
# Arrow Lake 自动调整策略：
#   < 65,536 行：min(sqrt(rows) * 4, 256)  — 避免空聚类警告
#   65K - 1M 行：使用配置值 (默认 256)
#   >= 1M 行：min(sqrt(rows), 4096)       — 按数据量自动扩展

# 通常无需手动指定，传 None 让系统自动选择
info = lake.create_vector_index("aigc_articles", num_partitions=None)
```

### 5.2 num\_sub\_vectors -- PQ 子向量数

PQ 将高维向量拆分为多个子向量分别量化。`num_sub_vectors` 必须是 8 的倍数。

```python
from arrow_lake.config import VectorSearchConfig

# 嵌入维度 1024, 拆分为 24 个子向量
# 每个子向量 1024/24 ≈ 42 维
config = ArrowLakeConfig()
config.vector.num_sub_vectors = 24  # 1024 / 24 ≈ 42 维/子向量
```

**调优建议**:

| 嵌入维度 | 推荐 num\_sub\_vectors | 子向量维度 |
| ---- | -------------------- | ----- |
| 512  | 16                   | 32    |
| 768  | 24                   | 32    |
| 1024 | 24                   | \~42  |
| 1536 | 32                   | 48    |
| 2048 | 48                   | \~42  |

> 子向量越多，量化越精细，但索引构建时间和内存占用也越大。

### 5.3 nprobes -- 搜索探测分区数

`nprobes` 控制搜索时实际扫描的 IVF 分区数量。值越大，召回率越高，延迟也越大。

```python
# 快速搜索（低召回）
result = lake.search("aigc_articles", qv, top_k=10, nprobes=5)

# 平衡模式（默认）
result = lake.search("aigc_articles", qv, top_k=10, nprobes=20)

# 高召回搜索
result = lake.search("aigc_articles", qv, top_k=10, nprobes=128)

# 注意：nprobes 硬上限为 max_nprobes (默认 256)
```

**nprobes 与 num\_partitions 的关系**:

* `nprobes = 1`: 只扫描最近的 1 个分区，速度最快但召回最低
* `nprobes = num_partitions`: 扫描所有分区，等同暴力搜索
* 推荐起始值：`nprobes = num_partitions // 10`（扫描 10% 的分区）

***

## 6. 支持的度量方式

Arrow Lake 通过 `DistanceMetric` 枚举支持三种距离度量：

```python
from arrow_lake.config import DistanceMetric, VectorSearchConfig

# Cosine 相似度 — 方向相似性，值域 [0,2]，越小越相似
config = VectorSearchConfig(metric=DistanceMetric.COSINE)

# L2 距离 — 欧几里得距离，值域 [0,+inf)，越小越相似
config = VectorSearchConfig(metric=DistanceMetric.L2)

# 点积 — 适合归一化向量，值越大越相似
config = VectorSearchConfig(metric=DistanceMetric.DOT)
```

| 度量       | 适用场景         | 值域           | 越小/大越好 |
| -------- | ------------ | ------------ | ------ |
| `cosine` | 文本语义搜索、RAG   | \[0, 2]      | 越小越好   |
| `l2`     | 图像特征搜索、推荐系统  | \[0, +inf)   | 越小越好   |
| `dot`    | 已归一化的向量、对比学习 | (-inf, +inf) | 越大越好   |

选择建议：不确定时用 cosine（对向量长度不敏感）；已归一化向量用 dot（计算最快）；空间距离有意义时用 l2。

***

## 7. 索引管理

### 7.1 查询索引信息

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 获取指定向量索引的信息
info = lake.get_vector_index_info("aigc_articles", vector_column="text_embedding")
if info is None:
    print("没有向量索引，将使用暴力搜索")
else:
    print(f"索引：{info.index_type}, 度量：{info.distance_type}")
    print(f"已索引：{info.num_indexed_rows}, 未索引：{info.num_unindexed_rows}")
```

### 7.2 列出所有索引

```python
# 列出数据集上的所有向量索引
indexes = lake.list_vector_indexes("aigc_articles")
for idx in indexes:
    print(f"  {idx.index_type} on {idx.columns}, metric={idx.distance_type}")
```

### 7.3 重建索引

重建会删除已有索引并用更新后的参数创建新索引：

```python
# 用相同参数重建 (数据变更后使用)
info = lake.rebuild_vector_index("aigc_articles", vector_column="text_embedding")

# 用新参数重建
info = lake.rebuild_vector_index(
    "aigc_articles",
    metric="cosine",
    vector_column="text_embedding",
    index_type="IVF_PQ",
    num_partitions=512,
    num_sub_vectors=24,              # 1024 维推荐 24
)
print(f"重建完成：{info.index_type}, {info.num_indexed_rows} 行")
```

### 7.4 删除索引

```python
# 按名称删除向量索引
lake.delete_vector_index("aigc_articles", "aigc_articles_text_embedding_idx")
```

### 7.5 FTS 索引管理

```python
# 删除全文搜索索引
lake.delete_fts_index("aigc_articles")

# 获取 FTS 索引信息
fts_info = lake.get_fts_index_info("aigc_articles")
if fts_info is not None:
    print(f"FTS 索引：{fts_info['name']}, 列：{fts_info['columns']}")
```

***

## 8. REST API

```bash
# 创建向量索引
curl -X POST http://localhost:8000/api/v1/datasets/docs/index/vector \
  -H "Content-Type: application/json" \
  -d '{"metric": "cosine", "index_type": "IVF_PQ", "vector_column": "text_embedding"}'

# 向量搜索
curl -X POST http://localhost:8000/api/v1/datasets/docs/search/vector \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2, ...], "top_k": 10, "metric": "cosine"}'

# 文本 Embedding (通过 API 计算向量)
curl -X POST http://localhost:8000/api/v1/embed/text \
  -H "Content-Type: application/json" \
  -d '{"texts": ["检索增强生成", "大语言模型"]}'
```

| 端点                            | 方法 | 说明         |
| ----------------------------- | --- | ---------- |
| `/{name}/index/vector`        | POST | 创建向量索引    |
| `/{name}/search/vector`       | POST | 向量相似度搜索   |
| `/embed/text`                 | POST | 计算文本 embedding |
| `/embed/image`                | POST | 计算图像 embedding |

***

## 9. 完整示例：端到端向量搜索

本例摄取 `aigc_articles` AIGC 文章库，构建 IVF_PQ 索引并执行带过滤的相似度搜索——同一 `aigc_articles` 语料贯穿第 04-09 章。

```python
import numpy as np
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageConfig, StorageBackend

# 1. 配置
config = ArrowLakeConfig()
config.storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./data")
lake = Lake(base_uri="./data", config=config)

# 2. 摄取 AIGC 文章库（144 行；text_content 自动编码为 text_embedding）
report = lake.ingest("aigc_articles", ["datas/reports/aigc_articles.csv"])
print(f"摄取 {report.total_rows} 行")

# 3. 创建 IVF_PQ 向量索引（语料 ≥256 行，PQ 训练有效）
info = lake.create_vector_index("aigc_articles", metric="cosine", index_type="IVF_PQ")
print(f"索引创建完成：{info.index_type}, {info.num_indexed_rows} 行")

# 4. 搜索——语义相似的文章，过滤到「大语言模型」类别
query_vec = np.random.randn(1024).tolist()  # 替换为真实查询向量
result = lake.search("aigc_articles", query_vec, top_k=3, where="category = '大语言模型'")

# 5. 输出结果
for row in result.table.to_pylist():
    print(f"  [{row['_distance']:.4f}] {row['title']}")

# 6. 清理
lake.shutdown()
```

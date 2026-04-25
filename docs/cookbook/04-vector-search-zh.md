# 向量搜索与索引

向量搜索是 Arrow Lake 的核心检索能力。本文展示从数据摄取、Embedding 生成、索引创建到相似度搜索的完整流程。

```python
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageConfig, StorageBackend

# 初始化 Lake 实例
config = ArrowLakeConfig()
config.storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./data")
lake = Lake(base_uri="./data", config=config)

# 1. 摄取数据 — 文本列自动生成 embedding
report = lake.ingest("docs", ["article.txt"])
print(f"摄取 {report.total_rows} 行")

# 2. 创建向量索引
from arrow_lake.config import DistanceMetric, VectorIndexType
info = lake.create_vector_index("docs", metric="cosine", index_type="IVF_PQ")
print(f"索引类型：{info.index_type}, 距离度量：{info.distance_type}")
print(f"已索引行数：{info.num_indexed_rows}")

# 3. 执行向量搜索
import numpy as np
query_vec = np.random.randn(1024).tolist()  # 替换为真实查询向量
result = lake.search("docs", query_vector=query_vec, top_k=5)
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

在执行高效搜索前，需要先创建向量索引。Arrow Lake 支持三种索引类型：

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# --- 基础用法：使用默认配置 ---
info = lake.create_vector_index("docs")
# 默认：metric=cosine, index_type=IVF_PQ

# --- 指定度量和索引类型 ---
info = lake.create_vector_index(
    "docs",
    metric="cosine",          # 距离度量：cosine / l2 / dot
    index_type="IVF_PQ",      # 索引类型
)

# --- 精细控制索引参数 ---
info = lake.create_vector_index(
    "docs",
    metric="l2",
    vector_column="text_embedding",  # 向量列名
    index_type="IVF_FLAT",           # 更精确但更慢
    num_partitions=512,              # IVF 分区数
    num_sub_vectors=32,              # PQ 子向量数 (IVF_PQ 专用)
    replace=True,                    # 替换已有索引
)
```

`create_vector_index` 返回 `IndexInfo`:

```python
info = lake.create_vector_index("docs", metric="cosine", index_type="IVF_PQ")
print(f"索引：{info.index_type}, 度量：{info.distance_type}")
print(f"已索引：{info.num_indexed_rows}, 未索引：{info.num_unindexed_rows}")
print(f"覆盖列：{info.columns}")
```

### 索引类型对比

| 类型            | 说明              | 适用场景            | 备注          |
| ------------- | --------------- | --------------- | ----------- |
| `IVF_PQ`      | IVF 倒排 + 乘积量化   | 大规模数据集 (>10K 行) | 默认选择，内存占用小  |
| `IVF_FLAT`    | IVF 倒排 + 精确距离   | 中等规模，需要精度       | 无量化损失，内存占用大 |
| `IVF_HNSW_PQ` | IVF + HNSW + PQ | 大规模 + 低延迟       | 构建成本最高      |

***

## 3. 向量相似度搜索

使用 `lake.search()` 执行向量相似度搜索。双路径策略：优先 DuckDB 原生 `lance_vector_search()`，失败时回退 LanceDB SDK。

```python
from arrow_lake import Lake
import numpy as np

lake = Lake(base_uri="./data")

# 准备查询向量（维度必须与数据集向量列一致）
query_vector = np.random.randn(1024).tolist()

# 基础搜索
result = lake.search("docs", query_vector=query_vector, top_k=5)

# 带度量指定
result = lake.search(
    "docs",
    query_vector=query_vector,
    top_k=10,
    metric="cosine",
    vector_column="text_embedding",
)

# 带元数据过滤
result = lake.search(
    "docs",
    query_vector=query_vector,
    top_k=5,
    where="category = 'tech'",
)
```

搜索返回 `VectorSearchResult` (包含 PyArrow Table):

```python
result = lake.search("docs", query_vector=query_vector, top_k=5)
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
result = lake.search("docs", query_vector=qv, where="category = 'AI'")

# 数值范围 + 组合条件
result = lake.search("docs", query_vector=qv, where="category = 'AI' AND year >= 2023")

# IN 操作
result = lake.search("docs", query_vector=qv, where="status IN ('published', 'reviewed')")

# 字符串匹配
result = lake.search("docs", query_vector=qv, where="title LIKE '%机器学习%'")
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
info = lake.create_vector_index("docs", num_partitions=None)
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
result = lake.search("docs", query_vector=qv, top_k=10, nprobes=5)

# 平衡模式（默认）
result = lake.search("docs", query_vector=qv, top_k=10, nprobes=20)

# 高召回搜索
result = lake.search("docs", query_vector=qv, top_k=10, nprobes=128)

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

## 7. 查询索引信息

```python
from arrow_lake import Lake
from arrow_lake.query.vector import VectorSearchBridge

lake = Lake(base_uri="./data")
info = lake.create_vector_index("docs", metric="cosine")
print(info)
# IndexInfo(name='...', index_type='IVF_PQ', distance_type='cosine', ...)

# 通过底层 bridge 查询已有索引
bridge = VectorSearchBridge(lake._get_storage())
info = bridge.get_index_info("docs", vector_column="text_embedding")
if info is None:
    print("没有向量索引，将使用暴力搜索")
```

***

## 8. 完整示例：从零开始的向量搜索

```python
import pyarrow as pa
import numpy as np
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageConfig, StorageBackend

# 1. 配置
config = ArrowLakeConfig()
config.storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./demo_data")
lake = Lake(base_uri="./demo_data", config=config)

# 2. 准备数据（模拟 embedding）
texts = ["机器学习入门教程", "深度学习与神经网络", "自然语言处理技术",
         "计算机视觉基础", "强化学习原理"]
vectors = np.random.randn(5, 1024).tolist()

table = pa.table({
    "text_content": texts,
    "text_embedding": vectors,
    "category": ["AI", "AI", "AI", "AI", "AI"],
    "year": [2024, 2024, 2023, 2023, 2024],
})

# 3. 写入数据集
lake.create_dataset("articles", table)

# 4. 创建索引
info = lake.create_vector_index("articles", metric="cosine", index_type="IVF_PQ")
print(f"索引创建完成：{info.index_type}, {info.num_indexed_rows} 行")

# 5. 搜索
query_vec = np.random.randn(1024).tolist()
result = lake.search("articles", query_vector=query_vec, top_k=3, where="year = 2024")

# 6. 输出结果
for row in result.table.to_pylist():
    print(f"  [{row['_distance']:.4f}] {row['text_content']}")

# 7. 清理
lake.shutdown()
```
